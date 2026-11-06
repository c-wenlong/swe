import Foundation
import Testing
@testable import SWECore

/// A runner that answers by argv suffix, and can also throw: the seam for
/// the ways `swe gh` fails before a reply exists (a timeout, a cancelled
/// child) as well as for the replies that arrive broken. Routes can be
/// swapped mid-test so a failed read can be retried against a healthy swe.
final class GhScriptedRunner: CommandRunning, @unchecked Sendable {
    enum Reply: Sendable {
        case result(CommandResult)
        case fail(any Error)
    }

    private let lock = NSLock()
    private var routes: [(suffix: String, reply: Reply)]
    private var fallback: Reply
    private var log: [[String]] = []
    private var stdin: [Data?] = []

    /// Every call so far, read under the lock because refreshes a model
    /// spawned can still be appending.
    var calls: [[String]] { lock.withLock { log } }
    var inputs: [Data?] { lock.withLock { stdin } }

    init(_ routes: [(String, Reply)] = [],
         fallback: Reply = .result(.init(status: 1, stdout: Data(), stderr: "no route"))) {
        self.routes = routes.map { (suffix: $0.0, reply: $0.1) }
        self.fallback = fallback
    }

    /// Replaces the routes (and, when given, the answer for the rest).
    func script(_ routes: [(String, Reply)], fallback: Reply? = nil) {
        lock.withLock {
            self.routes = routes.map { (suffix: $0.0, reply: $0.1) }
            if let fallback { self.fallback = fallback }
        }
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        try await run(arguments, input: nil)
    }

    func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
        let line = arguments.joined(separator: " ")
        let reply: Reply = lock.withLock {
            log.append(arguments)
            stdin.append(input)
            return routes.first(where: { line.hasSuffix($0.suffix) })?.reply ?? fallback
        }
        switch reply {
        case .result(let result): return result
        case .fail(let error): throw error
        }
    }
}

/// The ways a read of GitHub through swe goes wrong before any typed
/// status exists. Each one reaches the model as a thrown error whose text
/// ends up in the page, so each names a piece of that text.
enum GhFault: CaseIterable, Sendable, CustomTestStringConvertible {
    /// The runner gave up on the subprocess.
    case timeout
    /// swe exited non-zero with a plain message, `gh`'s own failure text.
    case ghFailed
    /// Exit 0 with something that is not JSON (a proxy's HTML page).
    case notJSON
    /// Exit 0 with JSON cut off mid-document.
    case truncated
    /// A reply from a newer swe than the app speaks.
    case newerSchema

    var testDescription: String { String(describing: self) }

    var reply: GhScriptedRunner.Reply {
        func ok(_ text: String) -> GhScriptedRunner.Reply {
            .result(.init(status: 0, stdout: Data(text.utf8), stderr: ""))
        }
        switch self {
        case .timeout: return .fail(SweError.timedOut)
        case .ghFailed:
            return .result(.init(status: 1, stdout: Data(), stderr: "gh: HTTP 502 Bad Gateway"))
        case .notJSON: return ok("<html>502 Bad Gateway</html>")
        case .truncated: return ok("{\"schema\":1,\"data\":{\"repos\":[{\"name\":")
        case .newerSchema: return ok("{\"schema\":2,\"data\":{}}")
        }
    }

    /// What the thrown error's text contains.
    var fragment: String {
        switch self {
        case .timeout: "took too long"
        case .ghFailed: "HTTP 502"
        case .notJSON, .truncated: "couldn't read"
        case .newerSchema: "schema 2"
        }
    }
}

/// The error branches of `GitHubModel`: every read and write it makes can
/// fail, and each failure has to land where the page looks for it (a
/// typed `.failed`, `probeError`, `pinError`, an action's returned string)
/// without erasing rows that were already good or leaving a busy flag set.
@MainActor
@Suite struct GitHubModelErrorTests {
    private let env = "\"github\":{\"status\":\"ok\",\"login\":\"me\",\"error\":null}"

    private func good(_ data: String) -> GhScriptedRunner.Reply {
        .result(.init(status: 0, stdout: Data("{\"schema\":1,\"data\":{\(data)}}".utf8), stderr: ""))
    }

    /// A swe where every call fails the same way.
    private func failing(_ fault: GhFault) -> (SweClient, GhScriptedRunner) {
        let runner = GhScriptedRunner(fallback: fault.reply)
        return (SweClient(runner: runner), runner)
    }

    private let pins = "\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]"
    private let pullRow = "{\"number\":7,\"title\":\"Fix\",\"state\":\"open\",\"draft\":false,"
        + "\"author\":\"me\",\"branch\":\"f\",\"base\":\"main\",\"labels\":[],"
        + "\"mergeable\":\"mergeable\",\"review_decision\":\"approved\",\"checks\":\"passing\","
        + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/7\"}"
    private let pullsRoute = "gh pr --state=open --limit=50 --repo=o/a --json"

    private func pullsReply() -> GhScriptedRunner.Reply {
        good("\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":[\(pullRow)]}]")
    }

    private func notification(_ id: String) -> String {
        "{\"id\":\"\(id)\",\"repo\":\"o/a\",\"title\":\"T\(id)\",\"type\":\"Issue\","
            + "\"reason\":\"mention\",\"unread\":true,\"updated_at\":1700000000,"
            + "\"url\":\"https://github.com/o/a/issues/\(id)\"}"
    }

    /// The failure text of one slot, nil when it holds anything else.
    private func message<Value>(_ state: Loadable<Value>) -> String? {
        if case .failed(let text) = state { return text }
        return nil
    }

    private func isIdle<Value>(_ state: Loadable<Value>) -> Bool {
        if case .idle = state { return true }
        return false
    }

    /// The failure text on one tab's slot of a board.
    private func failure(_ board: RepoBoard, _ tab: RepoTab) -> String? {
        switch tab {
        case .pulls: message(board.pulls)
        case .issues: message(board.issues)
        case .actions: message(board.runs)
        case .pages: message(board.pages)
        case .releases: message(board.releases)
        case .tags: message(board.tags)
        case .branches: message(board.branches)
        }
    }

    private func idle(_ board: RepoBoard, _ tab: RepoTab) -> Bool {
        switch tab {
        case .pulls: isIdle(board.pulls)
        case .issues: isIdle(board.issues)
        case .actions: isIdle(board.runs)
        case .pages: isIdle(board.pages)
        case .releases: isIdle(board.releases)
        case .tags: isIdle(board.tags)
        case .branches: isIdle(board.branches)
        }
    }

    // MARK: - overview

    /// A swe that cannot answer the overview leaves `probeError` for the
    /// setup card and no pins; the next look retries and a good reply
    /// clears the error.
    @Test(arguments: GhFault.allCases)
    func aFailedOverviewLeavesAProbeErrorUntilARetrySucceeds(_ fault: GhFault) async {
        let (swe, runner) = failing(fault)
        let model = GitHubModel()
        await model.load(swe: swe)
        #expect(model.probed)
        #expect(model.probeError?.contains(fault.fragment) == true)
        // No env yet, so the page is blocked on that very message.
        #expect(model.env == nil)
        #expect(model.envBlocked == model.probeError)
        #expect(model.pins.isEmpty)
        #expect(model.selected == nil)

        runner.script([("gh --json", good("\(env),\(pins)"))])
        await model.loadPinsIfNeeded(swe: swe)
        #expect(model.probeError == nil)
        #expect(model.envBlocked == nil)
        #expect(model.pins.map(\.name) == ["o/a"])
    }

    // MARK: - cancellation

    /// A cancelled read is not a failure: nothing in the page may turn
    /// into an error, and nothing may stay stuck loading.
    @Test func cancelledReadsLeaveNoErrorBehind() async {
        let gone = SweClient(runner: GhScriptedRunner(fallback: .fail(CancellationError())))
        let model = GitHubModel()

        await model.load(swe: gone)
        #expect(!model.probed)
        #expect(model.probeError == nil)

        await model.loadBoards(["o/a"], swe: gone)
        await model.loadTab(.issues, repo: "o/a", swe: gone)
        #expect(model.board(for: "o/a") == RepoBoard())
        #expect(model.loading.isEmpty)

        await model.loadFocusTab(.pulls, repo: "o/a", swe: gone)
        #expect(model.focusBoard(for: "o/a") == RepoBoard())
        #expect(model.focusLoading.isEmpty)

        await model.loadInbox(swe: gone)
        #expect(model.inbox == .idle)

        await model.syncGitHub(swe: gone)
        #expect(model.syncError == nil)
        #expect(!model.syncing)

        await model.search("swe", swe: gone)
        #expect(model.searchError == nil)
        #expect(!model.searching)

        await model.loadPrDetail(repo: "o/a", number: 7, swe: gone)
        #expect(model.prDetails["o/a#7"] == nil)
    }

    /// An action cancelled mid-flight reports "Cancelled." to its caller
    /// and leaves `actionError` alone, so the banner stays empty.
    @Test func cancelledActionsSayCancelledWithoutRaisingTheBanner() async {
        let gone = SweClient(runner: GhScriptedRunner(fallback: .fail(CancellationError())))
        let model = GitHubModel()
        #expect(await model.mergePr(repo: "o/a", ref: "7", method: "squash", auto: false, swe: gone) == "Cancelled.")
        #expect(await model.rerunRun(repo: "o/a", run: 42, failedOnly: false, swe: gone) == "Cancelled.")
        #expect(await model.markInboxRead(["1"], swe: gone) == "Cancelled.")
        #expect(await model.commentOnIssue(repo: "o/a", number: 3, body: "hi", swe: gone) == "Cancelled.")
        #expect(model.actionError == nil)
        #expect(model.actionBusy.isEmpty)
    }

    // MARK: - boards

    /// A failed `swe gh board` marks the surface on screen, leaves the
    /// others idle for their own fetches, and is not retried: the attempt
    /// flag holds, so a broken board costs one call per launch.
    @Test(arguments: GhFault.allCases)
    func aFailedBoardFailsTheShownTabOnceThenDegradesToPerTabReads(_ fault: GhFault) async {
        let (swe, runner) = failing(fault)
        let model = GitHubModel()
        model.tab = .tags
        await model.loadBoards(["o/a"], swe: swe)
        let board = model.board(for: "o/a")
        #expect(failure(board, .tags)?.contains(fault.fragment) == true)
        #expect(idle(board, .pulls))
        #expect(idle(board, .issues))
        #expect(model.loading.isEmpty)

        let attempts = runner.calls.count
        await model.loadBoards(["o/a"], swe: swe)
        #expect(runner.calls.count == attempts)

        // The surfaces the board never filled read on their own.
        runner.script([(pullsRoute, pullsReply())])
        await model.loadTabIfStale(.pulls, repo: "o/a", swe: swe)
        guard case .loaded(let prs) = model.board(for: "o/a").pulls else {
            Issue.record("pulls should load per tab after a failed board"); return
        }
        #expect(prs.map(\.number) == [7])
    }

    /// Each tab's own read puts its failure in its own slot and no other.
    @Test(arguments: RepoTab.allCases)
    func aFailedSurfaceReadFailsOnlyThatSurface(_ tab: RepoTab) async {
        let (swe, _) = failing(.timeout)
        let model = GitHubModel()
        await model.loadTab(tab, repo: "o/a", swe: swe)
        let board = model.board(for: "o/a")
        #expect(failure(board, tab)?.contains("took too long") == true)
        for other in RepoTab.allCases where other != tab {
            #expect(idle(board, other), "\(other) should not inherit the failure")
        }
        #expect(!board.isLoaded(tab))
        #expect(model.loading.isEmpty)
    }

    /// A surface that failed is not fresh: the next look refetches it
    /// instead of treating the failure as a recent answer.
    @Test func aFailedSurfaceIsRefetchedAndRecovers() async {
        let (swe, runner) = failing(.ghFailed)
        let model = GitHubModel(now: { Date(timeIntervalSince1970: 1_000) }, staleAfter: 60)
        await model.loadBoards(["o/a"], swe: swe)
        await model.loadTab(.pulls, repo: "o/a", swe: swe)
        #expect(failure(model.board(for: "o/a"), .pulls)?.contains("HTTP 502") == true)

        runner.script([(pullsRoute, pullsReply())])
        await model.loadTabIfStale(.pulls, repo: "o/a", swe: swe)
        #expect(model.board(for: "o/a").isLoaded(.pulls))
    }

    /// Typed statuses in a board reply: `error` when swe sent one, the
    /// status word when it did not, and a surface the reply left out
    /// altogether is a failure, never an empty list.
    @Test func boardEntryStatusesBecomeTypedFailures() async {
        let runner = GhScriptedRunner([
            ("gh board --repo=o/a --json", good(
                "\(env),\"boards\":[{\"repo\":\"o/a\",\"surfaces\":{"
                + "\"pull_requests\":{\"repo\":\"o/a\",\"status\":\"auth-required\",\"error\":\"run gh auth login\"},"
                + "\"issues\":{\"repo\":\"o/a\",\"status\":\"not-found\"},"
                + "\"runs\":{\"repo\":\"o/a\",\"status\":\"unavailable\",\"error\":\"gh is not installed\"},"
                + "\"releases\":{\"repo\":\"o/a\",\"status\":\"upstream-error\"},"
                + "\"tags\":{\"repo\":\"o/a\",\"status\":\"ok\",\"tags\":[]},"
                + "\"branches\":{\"repo\":\"o/a\",\"status\":\"ok\",\"branches\":[]}"
                + "}}]")),
        ])
        let model = GitHubModel()
        await model.loadBoards(["o/a"], swe: SweClient(runner: runner))
        let board = model.board(for: "o/a")
        #expect(board.pulls == .failed("run gh auth login"))
        #expect(board.issues == .failed("not-found"))
        #expect(board.runs == .failed("gh is not installed"))
        #expect(board.releases == .failed("upstream-error"))
        // `pages` was left out of the reply.
        #expect(board.pages == .failed("swe board returned no pages entry"))
        #expect(board.tags == .loaded([]))
        #expect(board.branches == .loaded([]))
        // A failure is not a fetch: the failed surfaces refetch on the next look.
        #expect(!board.isLoaded(.pulls))
    }

    /// A board reply that does not mention the repo leaves it untouched,
    /// so the per-tab reads can take over.
    @Test func aBoardReplyWithoutTheRepoLeavesItForPerTabReads() async {
        let runner = GhScriptedRunner([
            ("gh board --repo=o/a --json", good("\(env),\"boards\":[]")),
            (pullsRoute, pullsReply()),
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        await model.loadBoards(["o/a"], swe: swe)
        #expect(model.board(for: "o/a") == RepoBoard())
        await model.loadTabIfStale(.pulls, repo: "o/a", swe: swe)
        #expect(model.board(for: "o/a").isLoaded(.pulls))
    }

    /// A surface reply with no entry for the repo asked about is a failure
    /// that names the repo, and the other repo's rows are not borrowed.
    @Test func aSurfaceReplyForTheWrongRepoIsAFailure() async {
        let runner = GhScriptedRunner([
            (pullsRoute, good("\(env),\"results\":[{\"repo\":\"o/other\",\"status\":\"ok\",\"pull_requests\":[\(pullRow)]}]")),
        ])
        let model = GitHubModel()
        await model.loadTab(.pulls, repo: "o/a", swe: SweClient(runner: runner))
        #expect(model.board(for: "o/a").pulls == .failed("swe returned no result for o/a"))
    }

    // MARK: - auth and missing gh

    /// `gh` signed out: the surface reply says so twice, in the probe and
    /// in the entry. The page gets the setup card and the entry's words.
    @Test func aSignedOutGhBlocksThePageAndFailsTheSurface() async {
        let runner = GhScriptedRunner([
            (pullsRoute, good(
                "\"github\":{\"status\":\"auth-required\",\"login\":null,\"error\":\"not logged in\"},"
                + "\"results\":[{\"repo\":\"o/a\",\"status\":\"auth-required\",\"error\":\"gh auth login first\"}]")),
        ])
        let model = GitHubModel()
        await model.loadTab(.pulls, repo: "o/a", swe: SweClient(runner: runner))
        #expect(model.board(for: "o/a").pulls == .failed("gh auth login first"))
        #expect(model.envSetup == .authRequired)
        #expect(model.envBlocked?.contains("Not signed in") == true)
        #expect(model.envBlocked?.contains("gh auth login") == true)
    }

    @Test func aMissingGhBlocksThePageWithAnInstallHint() async {
        let runner = GhScriptedRunner([
            (pullsRoute, good(
                "\"github\":{\"status\":\"unavailable\",\"login\":null,\"error\":null},"
                + "\"results\":[{\"repo\":\"o/a\",\"status\":\"unavailable\"}]")),
        ])
        let model = GitHubModel()
        await model.loadTab(.pulls, repo: "o/a", swe: SweClient(runner: runner))
        #expect(model.board(for: "o/a").pulls == .failed("unavailable"))
        #expect(model.envSetup == .unavailable)
        #expect(model.envBlocked?.contains("isn't installed") == true)
    }

    /// A probe that merely failed (`upstream-error`) is not a signed-out
    /// gh: pages stay up and nothing is blocked.
    @Test func aFailedProbeDoesNotBlockThePage() async {
        let runner = GhScriptedRunner([
            (pullsRoute, good(
                "\"github\":{\"status\":\"upstream-error\",\"login\":null,\"error\":\"timed out\"},"
                + "\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":[]}]")),
        ])
        let model = GitHubModel()
        await model.loadTab(.pulls, repo: "o/a", swe: SweClient(runner: runner))
        #expect(model.envBlocked == nil)
        #expect(model.envSetup == nil)
        #expect(model.board(for: "o/a").isLoaded(.pulls))
    }

    // MARK: - focus page

    @Test(arguments: GhFault.allCases)
    func aFailedFocusReadLandsOnTheFocusBoardOnly(_ fault: GhFault) async {
        let (swe, _) = failing(fault)
        let model = GitHubModel()
        await model.loadFocusTab(.releases, repo: "o/a", swe: swe)
        #expect(failure(model.focusBoard(for: "o/a"), .releases)?.contains(fault.fragment) == true)
        #expect(model.board(for: "o/a") == RepoBoard())
        #expect(model.focusLoading.isEmpty)
        // A failed read is not the end of the listing.
        #expect(model.focusExhausted.isEmpty)
    }

    // MARK: - inbox

    @Test(arguments: GhFault.allCases)
    func aFailedInboxReadIsTypedAndForgottenOnTheNextGoodOne(_ fault: GhFault) async {
        let (swe, runner) = failing(fault)
        let model = GitHubModel()
        await model.loadInbox(swe: swe)
        #expect(model.inbox.failureText.contains(fault.fragment))
        #expect(model.inboxFetchedAt == nil)
        #expect(model.unreadCount == 0)

        runner.script([("gh inbox --limit=50 --all --json",
                        good("\(env),\"status\":\"ok\",\"notifications\":[\(notification("1"))]"))])
        await model.loadInbox(swe: swe, force: true)
        #expect(model.unreadCount == 1)
    }

    @Test func aSignedOutInboxIsAFailureAndBlocksThePage() async {
        let runner = GhScriptedRunner([
            ("gh inbox --limit=50 --all --json", good(
                "\"github\":{\"status\":\"auth-required\",\"login\":null,\"error\":null},"
                + "\"status\":\"auth-required\",\"notifications\":[]")),
        ])
        let model = GitHubModel()
        await model.loadInbox(swe: SweClient(runner: runner))
        #expect(model.inbox == .failed("GitHub returned auth-required"))
        #expect(model.envSetup == .authRequired)
    }

    // MARK: - sync and search

    @Test(arguments: GhFault.allCases)
    func aFailedSyncSetsTheWarningAndKeepsTheCatalog(_ fault: GhFault) async {
        let (swe, runner) = failing(fault)
        let model = GitHubModel()
        await model.syncGitHub(swe: swe)
        #expect(model.syncError?.contains(fault.fragment) == true)
        #expect(model.catalog == nil)
        #expect(!model.syncing)

        runner.script([("gh sync --json", good("\"repos\":3,\"synced_at\":1700000000,\"owners\":[\"o\"],\"failed\":[]"))])
        await model.syncGitHub(swe: swe)
        #expect(model.syncError == nil)
        #expect(model.catalog?.repos == 3)
    }

    private let hit = "{\"name\":\"o/a\",\"description\":null,\"stars\":1,"
        + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a\"}"

    @Test(arguments: GhFault.allCases)
    func aFailedSearchSetsTheErrorAndAGoodOneClearsIt(_ fault: GhFault) async {
        let (swe, runner) = failing(fault)
        let model = GitHubModel()
        await model.search("swe", swe: swe)
        #expect(model.searchError?.contains(fault.fragment) == true)
        #expect(model.searchResults.isEmpty)
        #expect(!model.searching)

        runner.script([("gh repo search swe --limit=10 --json",
                        good("\"query\":\"swe\",\"results\":[\(hit)]"))])
        await model.search("swe", swe: swe)
        #expect(model.searchError == nil)
        #expect(model.searchResults.map(\.name) == ["o/a"])
    }

    /// Clearing the field empties the list and the error without a call.
    @Test func anEmptyQueryClearsResultsAndErrorWithoutCallingSwe() async {
        let (swe, runner) = failing(.timeout)
        let model = GitHubModel()
        await model.search("swe", swe: swe)
        #expect(model.searchError != nil)
        let calls = runner.calls.count
        await model.search("   ", swe: swe)
        #expect(model.searchError == nil)
        #expect(model.searchResults.isEmpty)
        #expect(!model.searching)
        #expect(runner.calls.count == calls)
    }

    // MARK: - pins

    @Test(arguments: GhFault.allCases)
    func aFailedPinChangeKeepsThePinsBoardsAndSelection(_ fault: GhFault) async {
        let runner = GhScriptedRunner([
            ("gh --json", good("\(env),\(pins)")),
            (pullsRoute, pullsReply()),
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadTab(.pulls, repo: "o/a", swe: swe)

        runner.script([], fallback: fault.reply)
        #expect(await model.addRepo("o/b", swe: swe) == nil)
        #expect(model.pinError?.contains(fault.fragment) == true)
        #expect(model.pins.map(\.name) == ["o/a"])
        #expect(model.mutating.isEmpty)

        #expect(await model.removeRepo("o/a", swe: swe) == false)
        #expect(model.pinError?.contains(fault.fragment) == true)
        // The failed unpin must not drop the repo's rows or its selection.
        #expect(model.pins.map(\.name) == ["o/a"])
        #expect(model.selected == "o/a")
        #expect(model.board(for: "o/a").isLoaded(.pulls))
        #expect(model.mutating.isEmpty)

        // The lock was released, so a retry runs.
        runner.script([("gh repo add o/b --json", good("\"repo\":\"o/b\",\"pinned\":[\"o/a\",\"o/b\"]"))])
        #expect(await model.addRepo("o/b", swe: swe) == "o/b")
        #expect(model.pinError == nil)
        #expect(model.pins.map(\.name) == ["o/a", "o/b"])
    }

    // MARK: - details

    @Test(arguments: GhFault.allCases)
    func aFailedPrDetailReadLandsTyped(_ fault: GhFault) async {
        let (swe, _) = failing(fault)
        let model = GitHubModel()
        await model.loadPrDetail(repo: "o/a", number: 7, swe: swe)
        #expect(model.prDetails["o/a#7"]?.failureText.contains(fault.fragment) == true)
        // Not stuck loading: the sheet can ask again.
        if case .loading = model.prDetails["o/a#7"] { Issue.record("detail stuck on .loading") }
    }

    @Test(arguments: GhFault.allCases)
    func aFailedIssueDetailReadLandsTyped(_ fault: GhFault) async {
        let (swe, _) = failing(fault)
        let model = GitHubModel()
        await model.loadIssueDetail(repo: "o/a", number: 3, swe: swe)
        #expect(model.issueDetails["o/a#3"]?.failureText.contains(fault.fragment) == true)
    }

    // MARK: - writes

    @Test(arguments: GhFault.allCases)
    func aFailedCommentReturnsTheErrorAndSkipsTheRead(_ fault: GhFault) async {
        let (swe, runner) = failing(fault)
        let model = GitHubModel()
        let error = await model.commentOnIssue(repo: "o/a", number: 3, body: "hi", swe: swe)
        #expect(error?.contains(fault.fragment) == true)
        #expect(!model.commenting(repo: "o/a", number: 3))
        // Only the post ran; a failed post does not read the issue again.
        #expect(runner.calls.count == 1)
        #expect(model.issueDetails["o/a#3"] == nil)
    }

    /// A reply that carries neither a comment nor an error is still a
    /// failure the composer must show.
    @Test func aCommentReplyWithNoCommentIsAFailure() async {
        let runner = GhScriptedRunner([
            ("gh issue comment 3 --repo=o/a --body-file=- --json",
             good("\(env),\"repo\":\"o/a\",\"status\":\"upstream-error\",\"issue\":3,\"comment\":null,\"error\":null")),
        ])
        let model = GitHubModel()
        let error = await model.commentOnIssue(repo: "o/a", number: 3, body: "hi",
                                               swe: SweClient(runner: runner))
        #expect(error == "GitHub returned upstream-error.")
        #expect(runner.calls.count == 1)
    }

    @Test(arguments: GhFault.allCases)
    func aFailedMergeReturnsTheErrorAndKeepsTheRow(_ fault: GhFault) async {
        let runner = GhScriptedRunner([
            ("gh --json", good("\(env),\(pins)")),
            (pullsRoute, pullsReply()),
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadTab(.pulls, repo: "o/a", swe: swe)

        runner.script([], fallback: fault.reply)
        let error = await model.mergePr(repo: "o/a", ref: "7", method: "squash", auto: false, swe: swe)
        #expect(error?.contains(fault.fragment) == true)
        #expect(model.actionError == error)
        #expect(model.actionBusy.isEmpty)
        guard case .loaded(let prs) = model.board(for: "o/a").pulls else {
            Issue.record("pulls should stay loaded"); return
        }
        #expect(prs.map(\.number) == [7])
    }

    /// A failed rerun leaves the runs surface fresh (nothing changed on
    /// GitHub, so nothing to refetch) and reports the failure.
    @Test(arguments: GhFault.allCases)
    func aFailedRerunReturnsTheErrorWithoutDroppingFreshness(_ fault: GhFault) async {
        let runner = GhScriptedRunner([
            ("gh run --limit=30 --repo=o/a --json",
             good("\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"runs\":[]}]")),
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel(now: { Date(timeIntervalSince1970: 1_000) }, staleAfter: 60)
        // Hydrate first (the board call fails on this fake) so the stale
        // check below makes no board call of its own.
        await model.loadBoards(["o/a"], swe: swe)
        await model.loadTab(.actions, repo: "o/a", swe: swe)
        let loaded = runner.calls.count

        runner.script([], fallback: fault.reply)
        let error = await model.rerunRun(repo: "o/a", run: 42, failedOnly: true, swe: swe)
        #expect(error?.contains(fault.fragment) == true)
        #expect(model.actionError == error)
        #expect(model.actionBusy.isEmpty)
        let afterRerun = runner.calls.count
        await model.loadTabIfStale(.actions, repo: "o/a", swe: swe)
        #expect(runner.calls.count == afterRerun, "a failed rerun must not invalidate the runs surface")
        #expect(afterRerun == loaded + 1)
    }

    @Test(arguments: GhFault.allCases)
    func aFailedMarkReadReturnsTheErrorAndKeepsTheBadges(_ fault: GhFault) async {
        let runner = GhScriptedRunner([
            ("gh inbox --limit=50 --all --json",
             good("\(env),\"status\":\"ok\",\"notifications\":[\(notification("1")),\(notification("2"))]")),
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        await model.loadInbox(swe: swe)
        #expect(model.unreadCount == 2)

        runner.script([], fallback: fault.reply)
        let error = await model.markInboxRead(["1"], swe: swe)
        #expect(error?.contains(fault.fragment) == true)
        #expect(model.actionError == error)
        #expect(model.actionBusy.isEmpty)
        #expect(model.unreadCount == 2)
    }

    /// Some threads marked, some refused: the refusals are named per
    /// thread and no badge flips, since the reply cannot be trusted whole.
    @Test func partlyRefusedMarkReadNamesEachThreadAndFlipsNothing() async {
        let runner = GhScriptedRunner([
            ("gh inbox --limit=50 --all --json",
             good("\(env),\"status\":\"ok\",\"notifications\":[\(notification("1")),\(notification("2"))]")),
            ("gh inbox read 1 2 --json",
             good("\(env),\"all\":false,\"marked\":[\"1\"],\"failed\":[{\"id\":\"2\",\"error\":\"forbidden\"},{\"id\":\"9\",\"error\":\"gone\"}]")),
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        await model.loadInbox(swe: swe)
        let error = await model.markInboxRead(["1", "2"], swe: swe)
        #expect(error == "2: forbidden · 9: gone")
        #expect(model.actionError == error)
        #expect(model.unreadCount == 2)
    }

    // MARK: - session bridge

    @Test(arguments: GhFault.allCases)
    func aFailedWorktreeSetsTheSessionErrorAndFreesTheKey(_ fault: GhFault) async {
        let (swe, runner) = failing(fault)
        let model = GitHubModel()
        #expect(await model.prWorktree(repo: "o/a", pr: 7, swe: swe) == nil)
        #expect(model.sessionError?.contains(fault.fragment) == true)
        #expect(model.worktreeBusy.isEmpty)

        runner.script([("gh pr worktree 7 --repo=o/a --json",
                        good("\"repo\":\"o/a\",\"pr\":7,\"path\":\"/x/o__a-pr7\",\"head\":\"abc\",\"created\":true,\"error\":null"))])
        #expect(await model.prWorktree(repo: "o/a", pr: 7, swe: swe) == "/x/o__a-pr7")
        #expect(model.sessionError == nil)
    }

    @Test func aWorktreeReplyWithNoPathAndNoErrorStillExplainsItself() async {
        let runner = GhScriptedRunner([
            ("gh pr worktree 7 --repo=o/a --json",
             good("\"repo\":\"o/a\",\"pr\":7,\"path\":null,\"created\":false,\"error\":null")),
        ])
        let model = GitHubModel()
        #expect(await model.prWorktree(repo: "o/a", pr: 7, swe: SweClient(runner: runner)) == nil)
        #expect(model.sessionError == "swe returned no worktree path.")
    }

    /// The Open in session picker hides when `swe list` fails; the page
    /// stays up with no error banner.
    @Test(arguments: GhFault.allCases)
    func aFailedHarnessListLeavesAnEmptyPickerAndNoBanner(_ fault: GhFault) async {
        let (swe, _) = failing(fault)
        let model = GitHubModel()
        await model.loadSessionHarnesses(swe: swe)
        #expect(model.sessionHarnesses.isEmpty)
        #expect(model.sessionError == nil)
    }
}

private extension Loadable {
    /// The text of a `.failed`, empty for any other state so a `contains`
    /// check on it fails instead of crashing.
    var failureText: String {
        if case .failed(let text) = self { return text }
        return ""
    }
}
