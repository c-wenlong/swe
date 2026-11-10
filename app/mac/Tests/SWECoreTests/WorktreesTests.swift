import Foundation
import Testing
@testable import SWECore

/// The `swe wt` payloads follow the pinned contract (the swe-wt contract
/// shared by the CLI, the macOS app and the web app). `WorktreesFixture`
/// answers them from memory, so no real swe or repository is involved.
@MainActor
@Suite struct WorktreesTests {
    let listJSON = #"""
    {"scope": "all", "generated_at": "2026-11-10T09:00:00Z", "worktrees": [
      {"path": "/r/swe", "repo": "c-wenlong/swe", "main_checkout": "/r/swe", "branch": "main",
       "head": "7156ada8b2c1", "state": "main", "pr": null, "last_commit_at": "2026-11-10T08:00:00Z",
       "size_bytes": 2400000000, "rebuildable_bytes": 1100000000, "dirty": false, "untracked": 0,
       "locked": false, "lock_reason": null, "harness": "other"},
      {"path": "/r/swe/.wt/fix", "repo": "c-wenlong/swe", "main_checkout": "/r/swe", "branch": "fix/hooks",
       "head": "c5d6e7f80912", "state": "merged-clean",
       "pr": {"number": 1890, "url": "https://github.com/c-wenlong/swe/pull/1890", "state": "MERGED"},
       "last_commit_at": "2026-11-07T08:00:00Z", "size_bytes": 810000000, "rebuildable_bytes": 700000000,
       "dirty": false, "untracked": 0, "locked": false, "lock_reason": null, "harness": "claude"},
      {"path": "/r/swe/.wt/detached", "repo": null, "main_checkout": "/r/swe", "branch": null,
       "head": "123456789abc", "state": "from-the-future", "pr": null, "last_commit_at": "not a date",
       "size_bytes": 5, "rebuildable_bytes": 0, "dirty": true, "untracked": 3,
       "locked": true, "lock_reason": "keep", "harness": "codex"},
      {"broken": true}
    ]}
    """#

    func main(_ path: String = "/r/swe") -> Worktree {
        Worktree(path: path, repo: "o/r", mainCheckout: path, branch: "main", state: .main)
    }

    func tree(_ path: String, _ state: WorktreeState, branch: String? = nil, repo: String = "o/r",
              checkout: String = "/r", size: Int = 100, rebuildable: Int = 10, locked: Bool = false,
              days: Double = 1, pr: Int? = nil, harness: String = "claude") -> Worktree {
        Worktree(path: path, repo: repo, mainCheckout: checkout, branch: branch ?? (path as NSString).lastPathComponent,
                 head: "abcdef0123456", state: state,
                 pr: pr.map { WorktreePR(number: $0, url: "https://github.com/o/r/pull/\($0)", state: "OPEN") },
                 lastCommitAt: Date(timeIntervalSince1970: 1_800_000_000 - days * 86_400),
                 sizeBytes: size, rebuildableBytes: rebuildable, locked: locked, harness: harness)
    }

    // MARK: Decoding

    @Test func decodesTheContractList() async throws {
        let runner = ScriptedRunner([(["wt", "list"], ok(listJSON))])
        let list = try await SweClient(runner: runner).worktrees(.all)
        // The malformed row is dropped; the other three stay.
        #expect(list.worktrees.count == 3)
        #expect(list.scope == "all")
        #expect(list.generatedAt != nil)
        let fix = list.worktrees[1]
        #expect(fix.state == .mergedClean)
        #expect(fix.pr == WorktreePR(number: 1890, url: "https://github.com/c-wenlong/swe/pull/1890", state: "MERGED"))
        #expect(fix.pr?.isMerged == true)
        #expect(fix.mainCheckout == "/r/swe")
        #expect(fix.sizeBytes == 810_000_000)
        #expect(fix.rebuildableBytes == 700_000_000)
        #expect(fix.lastCommitAt == Timestamp.parse("2026-11-07T08:00:00Z"))
        #expect(fix.harness == "claude")
    }

    @Test func nullsAndNewStatesDegradeInsteadOfFailing() async throws {
        let list = try await SweClient(runner: ScriptedRunner([(["wt", "list"], ok(listJSON))])).worktrees(.all)
        let detached = list.worktrees[2]
        #expect(detached.branch == nil)
        #expect(detached.title == "detached 1234567")
        #expect(detached.repo == nil)
        #expect(detached.repoName == "swe")
        #expect(detached.state == .unknown)
        #expect(detached.lastCommitAt == nil)
        #expect(detached.locked && detached.lockReason == "keep")
        #expect(detached.untracked == 3 && detached.dirty)
        // An unknown state never offers a plain delete.
        #expect(detached.removalNeedsForce)
    }

    // MARK: Argument lists

    @Test func listArguments() {
        #expect(WorktreeCommand.list(.all) == ["wt", "list", "--all", "--json"])
        #expect(WorktreeCommand.list(.repo("/r/swe")) == ["wt", "list", "--repo=/r/swe", "--json"])
    }

    @Test func addArguments() {
        let existing = NewWorktreeDraft(repoPath: "/r/swe", mode: .existingBranch, branch: " fix/a ")
        #expect(WorktreeCommand.add(existing) == ["wt", "add", "/r/swe", "--branch=fix/a", "--json"])
        let fresh = NewWorktreeDraft(repoPath: "/r/swe", mode: .newBranch, branch: "feat/b",
                                     base: "origin/main", path: "~/wt/b")
        #expect(WorktreeCommand.add(fresh) ==
                ["wt", "add", "/r/swe", "--branch=feat/b", "--new", "--base=origin/main", "--path=~/wt/b", "--json"])
        let noBase = NewWorktreeDraft(repoPath: "/r/swe", mode: .newBranch, branch: "feat/b")
        #expect(WorktreeCommand.add(noBase) == ["wt", "add", "/r/swe", "--branch=feat/b", "--new", "--json"])
        let pr = NewWorktreeDraft(repoPath: "/r/swe", mode: .pullRequest, branch: "ignored", pullRequest: "#1912")
        #expect(WorktreeCommand.add(pr) == ["wt", "add", "/r/swe", "--pr=1912", "--json"])
    }

    @Test func removeLockUnlockMoveArguments() {
        #expect(WorktreeCommand.remove("/p", force: false, deleteBranch: false) == ["wt", "remove", "/p", "--json"])
        #expect(WorktreeCommand.remove("/p", force: true, deleteBranch: true) ==
                ["wt", "remove", "/p", "--force", "--delete-branch", "--json"])
        #expect(WorktreeCommand.lock("/p", reason: "  waiting  ") == ["wt", "lock", "/p", "--reason=waiting", "--json"])
        #expect(WorktreeCommand.lock("/p", reason: "  ") == ["wt", "lock", "/p", "--json"])
        #expect(WorktreeCommand.unlock("/p") == ["wt", "unlock", "/p", "--json"])
        #expect(WorktreeCommand.move("/p", to: "/q") == ["wt", "move", "/p", "/q", "--json"])
    }

    @Test func pruneAndSlimArguments() {
        #expect(WorktreeCommand.prune(scope: .all, includeDirty: false, confirmed: false) == ["wt", "prune", "--all", "--json"])
        #expect(WorktreeCommand.prune(scope: .all, includeDirty: true, confirmed: true) ==
                ["wt", "prune", "--all", "--include=merged-dirty", "--yes", "--json"])
        #expect(WorktreeCommand.prune(scope: .repo("/r"), includeDirty: false, confirmed: true) == ["wt", "prune", "--repo=/r", "--yes", "--json"])
        #expect(WorktreeCommand.slim("/p", confirmed: false) == ["wt", "slim", "/p", "--json"])
        #expect(WorktreeCommand.slim("/p", confirmed: true) == ["wt", "slim", "/p", "--yes", "--json"])
    }

    // MARK: New worktree form

    @Test func theFormChecksWhatSweWouldRefuse() {
        var draft = NewWorktreeDraft()
        #expect(draft.problem == "Pick a repository.")
        draft.repoPath = "/r"
        #expect(draft.problem != nil)
        draft.branch = "two words"
        #expect(draft.problem?.contains("spaces") == true)
        draft.branch = "--force"
        #expect(draft.problem?.contains("dash") == true)
        draft.branch = "feat/ok"
        #expect(draft.isComplete)
        draft.base = "bad..ref"
        #expect(draft.problem != nil)
        draft.base = ""
        draft.path = "relative/dir"
        #expect(draft.problem?.contains("full path") == true)
        draft.path = "~/wt/ok"
        #expect(draft.isComplete)
        draft.mode = .pullRequest
        #expect(draft.problem == "Enter the pull request number.")
        draft.pullRequest = "abc"
        #expect(!draft.isComplete)
        draft.pullRequest = "42"
        #expect(draft.isComplete)
    }

    // MARK: Grouping, filters, summary

    @Test func groupsByRepoWithTheMainCheckoutFirstThenNewest() {
        let trees = [
            tree("/b/old", .noPr, repo: "z/zed", checkout: "/b", days: 9),
            tree("/a/new", .openPr, repo: "a/alpha", checkout: "/a", days: 1),
            tree("/b/new", .noPr, repo: "z/zed", checkout: "/b", days: 2),
            Worktree(path: "/b", repo: "z/zed", mainCheckout: "/b", branch: "main", state: .main,
                     lastCommitAt: Date(timeIntervalSince1970: 1)),
        ]
        let groups = WorktreeFilter.group(trees)
        #expect(groups.map(\.title) == ["a/alpha", "z/zed"])
        #expect(groups[1].worktrees.map(\.path) == ["/b", "/b/new", "/b/old"])
        #expect(groups[1].totalBytes == 200)
    }

    @Test func filtersByStateAndSearch() {
        let trees = [
            tree("/r/a", .mergedClean, branch: "fix/hooks", pr: 12),
            tree("/r/b", .openPr, branch: "feat/search", pr: 99, harness: "codex"),
            tree("/r/c", .noPr, branch: "spike", harness: "devin"),
        ]
        #expect(WorktreeFilter.apply(trees, state: .openPr, query: "").map(\.path) == ["/r/b"])
        #expect(WorktreeFilter.apply(trees, state: nil, query: "HOOKS").map(\.path) == ["/r/a"])
        #expect(WorktreeFilter.apply(trees, state: nil, query: "#99").map(\.path) == ["/r/b"])
        #expect(WorktreeFilter.apply(trees, state: nil, query: "devin").map(\.path) == ["/r/c"])
        #expect(WorktreeFilter.apply(trees, state: .noPr, query: "hooks").isEmpty)
        #expect(WorktreeFilter.apply(trees, state: nil, query: "  ").count == 3)
    }

    @Test func summaryCountsReclaimableFromUnlockedMergedClean() {
        let trees = [
            main(),
            tree("/r/a", .mergedClean, size: 500, rebuildable: 400),
            tree("/r/b", .mergedClean, size: 300, rebuildable: 100, locked: true),
            tree("/r/c", .mergedDirty, size: 200, rebuildable: 50),
        ]
        let summary = WorktreeFilter.summary(trees)
        #expect(summary.count == 4)
        #expect(summary.totalBytes == 1000)
        #expect(summary.reclaimableBytes == 500)
        #expect(summary.reclaimableCount == 1)
        #expect(summary.rebuildableBytes == 0 + 400 + 100 + 50)
    }

    // MARK: Delete rules

    @Test func mainAndProtectedRowsOfferNoDelete() {
        #expect(!WorktreeState.main.isRemovable)
        #expect(!WorktreeState.protected.isRemovable)
        #expect(main().removalBlock != nil && !main().canRemove)
        #expect(!tree("/r/p", .protected).canRemove)
        // Locked rows must be unlocked first.
        #expect(!tree("/r/l", .mergedClean, locked: true).canRemove)
        #expect(tree("/r/m", .mergedClean).canRemove)
        #expect(!tree("/r/m", .mergedClean).removalNeedsForce)
        // swe refuses an orphan even with force: git has to forget it first.
        let orphan = tree("/r/o", .orphaned)
        #expect(!orphan.canRemove && !orphan.canLock && !orphan.canMove && !orphan.canSlim)
        #expect(orphan.pruneCommand == "git -C '/r' worktree prune")
        #expect(!tree("/r/p", .protected).canMove && !tree("/r/p", .protected).canSlim)
        #expect(tree("/r/p", .protected).canLock)
        for state in [WorktreeState.mergedDirty, .openPr, .noPr, .unknown] {
            #expect(tree("/r/x", state).canRemove)
            #expect(tree("/r/x", state).removalNeedsForce)
        }
    }

    @Test func theModelNeverSendsARemoveForMainOrProtected() async throws {
        let runner = ScriptedRunner([(["wt"], ok("{}"))])
        let model = WorktreesModel()
        for blocked in [main(), tree("/r/p", .protected)] {
            do {
                try await model.remove(blocked, force: true, deleteBranch: false, swe: SweClient(runner: runner))
                Issue.record("removed \(blocked.path)")
            } catch {
                #expect(WorktreeFailure.describe(error).message.isEmpty == false)
            }
        }
        #expect(runner.calls.isEmpty)
    }

    @Test func forceOnlyGoesOutForAStateThatNeedsIt() async throws {
        let reply = ok(#"{"removed": "/r/x", "freed_bytes": 100}"#)
        let list = ok(#"{"scope": "all", "generated_at": "2026-11-10T09:00:00Z", "worktrees": []}"#)
        let runner = ScriptedRunner([(["wt", "remove"], reply), (["wt", "list"], list)])
        let swe = SweClient(runner: runner)
        let model = WorktreesModel()
        try await model.remove(tree("/r/x", .mergedClean), force: true, deleteBranch: false, swe: swe)
        try await model.remove(tree("/r/x", .openPr), force: true, deleteBranch: true, swe: swe)
        try await model.remove(tree("/r/x", .openPr), force: false, deleteBranch: false, swe: swe)
        let removes = runner.calls.filter { $0.starts(with: ["wt", "remove"]) }
        #expect(removes == [
            ["wt", "remove", "/r/x", "--json"],
            ["wt", "remove", "/r/x", "--force", "--delete-branch", "--json"],
            ["wt", "remove", "/r/x", "--json"],
        ])
        // Each change reloads the list afterwards.
        #expect(runner.calls.filter { $0 == ["wt", "list", "--all", "--json"] }.count == 3)
    }

    // MARK: Errors

    @Test func readsSwesErrorObject() {
        let text = #"{"error": {"code": "not_safe", "message": "fix is open-pr. Pass --force."}}"#
        #expect(WorktreeFailure.parse(text) == WorktreeFailure(code: "not_safe", message: "fix is open-pr. Pass --force."))
        let wrapped = #"{"schema": 1, "data": {"error": {"code": "x", "message": "wrapped"}}}"#
        #expect(WorktreeFailure.parse(wrapped)?.message == "wrapped")
        #expect(WorktreeFailure.parse("plain words") == nil)
        let error = SweError.commandFailed(status: 1, message: text)
        #expect(WorktreeFailure.describe(error).code == WorktreeFailure.notSafe)
        #expect(WorktreeFailure.describe(SweError.commandFailed(status: 1, message: "boom")).message == "boom")
        for code in ["bad_args", "bad_config", "io_error", "not_a_repo", "not_found", "not_safe", "exists", "git_failed", "gh_unavailable", "busy"] {
            let friendly = WorktreeFailure.friendly(code: code, message: "detail")
            #expect(friendly != "detail" && friendly.hasSuffix(" detail"), "\(code)")
        }
        #expect(WorktreeFailure.friendly(code: "busy", message: "").contains("Another swe"))
        #expect(WorktreeFailure.friendly(code: "novel", message: "as is") == "as is")
    }

    @Test func anErrorOnStdoutWithTheEnvelopeIsRead() async {
        let body = #"{"schema": 1, "error": {"code": "busy", "message": "lock held by pid 9"}}"#
        let runner = ScriptedRunner([(["wt", "list"], CommandResult(status: 1, stdout: Data(body.utf8), stderr: ""))])
        let model = WorktreesModel()
        await model.load(swe: SweClient(runner: runner))
        guard case .failed(let message) = model.state else { Issue.record("\(model.state)"); return }
        #expect(message.hasPrefix("Another swe worktree command is running."))
        #expect(message.hasSuffix("lock held by pid 9"))
    }

    @Test func nullSizesAndTheExtraFieldsDecode() async throws {
        let json = #"""
        {"scope": "all", "worktrees": [
          {"path": "/r/a", "main_checkout": "/r", "branch": "a", "head": "abc", "state": "no-pr",
           "size_bytes": null, "rebuildable_bytes": null, "harness": "swe-pr",
           "reason": "no PR yet", "hold": "locked", "changes": 2, "detached": false,
           "rebuildable": [{"path": "/r/a/node_modules", "bytes": 10}]},
          {"path": "/r/b", "main_checkout": "/r", "branch": "b", "head": "abd", "state": "merged-clean"}
        ]}
        """#
        let list = try await SweClient(runner: ScriptedRunner([(["wt", "list"], ok(json))])).worktrees(.all)
        let a = list.worktrees[0]
        #expect(a.sizeBytes == nil && a.rebuildableBytes == nil)
        #expect(WorktreeFormat.bytes(a.sizeBytes) == "Not measured")
        #expect(a.reason == "no PR yet" && a.hold == "locked" && a.changes == 2 && a.detached == false)
        #expect(a.rebuildable == [.init(path: "/r/a/node_modules", bytes: 10)])
        #expect(WorktreeFormat.harness(a.harness) == "swe PR checkout")
        #expect(WorktreeFormat.harness("other") == "Other")
        let b = list.worktrees[1]
        #expect(b.reason == nil && b.hold == nil && b.rebuildable == nil)
        #expect(WorktreeFilter.summary(list.worktrees).totalBytes == 0)
    }

    @Test func aFailedListShowsTheMessageNotTheJSON() async {
        let model = WorktreesModel()
        let runner = ScriptedRunner([(["wt", "list"], CommandResult(
            status: 1, stdout: Data(),
            stderr: #"{"error": {"code": "git_missing", "message": "git isn't installed."}}"#))])
        await model.load(swe: SweClient(runner: runner))
        #expect(model.state == .failed("git isn't installed."))
    }

    // MARK: Model against the fixture

    @Test func loadsTheFixtureAndKnowsItsRepos() async throws {
        let fixture = WorktreesFixture()
        let model = WorktreesModel()
        await model.load(swe: SweClient(runner: fixture))
        #expect(model.worktrees.count == fixture.worktrees.count)
        #expect(model.repos.map(\.name) == ["c-wenlong/swe", "kai/agent-site", "kaichen/dotfiles"])
        #expect(model.groups.count == 3)
        #expect(model.summary.reclaimableCount == 3)
        #expect(model.stateCounts[.mergedClean] == 3)
        model.stateFilter = .mergedClean
        #expect(model.visible.allSatisfy { $0.state == .mergedClean })
        model.stateFilter = nil
        model.query = "agent-site"
        #expect(model.visible.map(\.repoName).allSatisfy { $0 == "kai/agent-site" })
    }

    @Test func aRepoScopeAsksForThatRepoAndKeepsTheOthersKnown() async throws {
        let fixture = WorktreesFixture()
        let model = WorktreesModel()
        let swe = SweClient(runner: fixture)
        await model.load(swe: swe)
        model.scope = .repo("/Users/demo/Projects/dotfiles")
        await model.load(swe: swe)
        #expect(fixture.calls.last == ["wt", "list", "--repo=/Users/demo/Projects/dotfiles", "--json"])
        #expect(model.worktrees.allSatisfy { $0.mainCheckout == "/Users/demo/Projects/dotfiles" })
        #expect(model.repos.count == 3)
    }

    @Test func removingAnOpenPRWithoutForceIsRefusedAndTheListStays() async throws {
        let fixture = WorktreesFixture()
        let model = WorktreesModel()
        let swe = SweClient(runner: fixture)
        await model.load(swe: swe)
        let open = try #require(model.worktrees.first { $0.state == .openPr })
        // The model sends no --force for it unless the caller says so; the
        // fixture, like swe, answers not_safe.
        do {
            try await model.remove(open, force: false, deleteBranch: false, swe: swe)
            Issue.record("removed without force")
        } catch {
            #expect(WorktreeFailure.describe(error).code == WorktreeFailure.notSafe)
        }
        #expect(model.worktree(at: open.path) != nil)
        try await model.remove(open, force: true, deleteBranch: false, swe: swe)
        #expect(model.worktree(at: open.path) == nil)
        #expect(model.notice?.hasPrefix("Removed ") == true)
    }

    @Test func lockMoveAndAddChangeTheList() async throws {
        let fixture = WorktreesFixture()
        let model = WorktreesModel()
        let swe = SweClient(runner: fixture)
        await model.load(swe: swe)
        let target = try #require(model.worktrees.first { $0.state == .noPr })
        try await model.lock(target, reason: "hold", swe: swe)
        #expect(model.worktree(at: target.path)?.locked == true)
        #expect(model.worktree(at: target.path)?.lockReason == "hold")
        try await model.unlock(target, swe: swe)
        #expect(model.worktree(at: target.path)?.locked == false)
        try await model.move(target, to: "/elsewhere/spike", swe: swe)
        #expect(model.worktree(at: target.path) == nil)
        #expect(model.worktree(at: "/elsewhere/spike") != nil)
        let draft = NewWorktreeDraft(repoPath: "/Users/demo/Projects/swe", mode: .newBranch, branch: "feat/new")
        let added = try await model.add(draft, swe: swe)
        #expect(model.worktree(at: added.path)?.branch == "feat/new")
        #expect(fixture.calls.contains(["wt", "lock", target.path, "--reason=hold", "--json"]))
        #expect(fixture.calls.contains(["wt", "move", target.path, "/elsewhere/spike", "--json"]))
    }

    @Test func pruneRunsADryRunFirstAndOnlyThenRemoves() async throws {
        let fixture = WorktreesFixture()
        let model = WorktreesModel()
        let swe = SweClient(runner: fixture)
        await model.load(swe: swe)
        let before = model.worktrees.count
        let preview = try await model.prunePreview(includeDirty: false, swe: swe)
        #expect(preview.dryRun)
        #expect(preview.candidates.count == 3)
        #expect(preview.candidates.allSatisfy { $0.state == .mergedClean })
        #expect(model.worktrees.count == before)
        #expect(fixture.calls.last == ["wt", "prune", "--all", "--json"])
        let report = try await model.prune(includeDirty: false, swe: swe)
        #expect(!report.dryRun)
        #expect(report.removed.count == 3)
        #expect(fixture.calls.contains(["wt", "prune", "--all", "--yes", "--json"]))
        #expect(model.worktrees.count == before - 3)
        // Including merged-dirty offers the extra one.
        let withDirty = try await model.prunePreview(includeDirty: true, swe: swe)
        #expect(withDirty.candidates.map(\.state) == [.mergedDirty])
    }

    @Test func slimPreviewsThenFrees() async throws {
        let fixture = WorktreesFixture()
        let model = WorktreesModel()
        let swe = SweClient(runner: fixture)
        await model.load(swe: swe)
        let target = try #require(model.worktrees.first { $0.state == .mergedClean })
        let preview = try await model.slimPreview(target, swe: swe)
        #expect(preview.dryRun && !preview.dirs.isEmpty)
        #expect(preview.freedBytes == target.rebuildableBytes)
        #expect(model.worktree(at: target.path)?.rebuildableBytes == target.rebuildableBytes)
        try await model.slim(target, swe: swe)
        #expect(model.worktree(at: target.path)?.rebuildableBytes == 0)
        #expect(fixture.calls.contains(["wt", "slim", target.path, "--yes", "--json"]))
    }

    @Test func emptyAndErrorScenarios() async {
        let empty = WorktreesModel()
        await empty.load(swe: SweClient(runner: WorktreesFixture(.empty)))
        #expect(empty.worktrees.isEmpty)
        guard case .loaded = empty.state else { Issue.record("\(empty.state)"); return }
        let failing = WorktreesModel()
        await failing.load(swe: SweClient(runner: WorktreesFixture(.error)))
        guard case .failed(let message) = failing.state else { Issue.record("\(failing.state)"); return }
        #expect(message.contains("git isn't on this Mac"))
    }

    // MARK: Formatting

    @Test func formatsSizesAndAges() {
        #expect(WorktreeFormat.bytes(0) == "0 KB")
        #expect(WorktreeFormat.bytes(810_000_000) == "810 MB")
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        #expect(WorktreeFormat.lastCommit(now.addingTimeInterval(-3 * 86_400), now: now) == "3d ago")
        #expect(WorktreeFormat.lastCommit(nil, now: now) == "No date")
        #expect(WorktreeFormat.count(1) == "1 worktree")
        #expect(WorktreeFormat.count(2) == "2 worktrees")
    }
}
