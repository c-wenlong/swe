import Foundation
import Synchronization
import Testing
@testable import SWECore

/// FolderChanges against a real folder (FSEvents), and the two filters the
/// AGENTS.md page and the Hub sync card follow `~/.swe` with.
@Suite struct FolderChangesTests {
    private let scratch: TempDir
    init() throws { scratch = try TempDir() }

    /// Writes in place (no temp file and rename), so each write is one
    /// path in the events.
    private func write(_ relative: String, _ text: String = "x") throws {
        let url = scratch.url.appending(path: relative)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: url, atomically: false, encoding: .utf8)
    }

    /// Starts `probe` watching through `filter` and waits for a first
    /// write to wake it: FSEvents reports only from when the stream
    /// starts, so this proves the watch is live before a test relies on
    /// it, without guessing how long start-up takes.
    private func primed(_ probe: Probe, busyAfterFirst: Bool = false,
                        filter: @escaping @Sendable (String) -> Bool) async throws -> Task<Void, Never> {
        let stream = FolderChanges.stream(root: scratch.url, latency: 0.1, matches: probe.filter(filter))
        let consumer = probe.consume(stream, busyAfterFirst: busyAfterFirst)
        do {
            try write(Probe.prime)
            try #require(await probe.wake(after: Probe.prime), "the stream never woke for its first write")
        } catch {
            // The caller never gets the task to cancel, so stop the watch here.
            consumer.cancel()
            throw error
        }
        return consumer
    }

    @Test func aMatchingWriteWakesTheStream() async throws {
        let probe = Probe()
        let consumer = try await primed(probe) { $0.hasPrefix("AGENTS_MD/") }
        defer { consumer.cancel() }
        try write("AGENTS_MD/01-working.md")
        #expect(await probe.wake(after: "AGENTS_MD/01-working.md"))
    }

    /// The rejected writes reach the filter, and no wake follows from
    /// them: every wake comes after a path the filter let through. The
    /// fence (a write the filter accepts, made once the rejected ones were
    /// seen) marks the end, since FSEvents delivers in order.
    @Test func writesTheFilterRejectsStayQuiet() async throws {
        let probe = Probe()
        let consumer = try await primed(probe) { $0.hasPrefix("AGENTS_MD/") }
        defer { consumer.cancel() }
        try write("data/catalog/catalog.sqlite")
        try write("notes.md")
        #expect(await probe.saw("data/catalog/catalog.sqlite"))
        #expect(await probe.saw("notes.md"))
        try write(Probe.fence)
        #expect(await probe.wake(after: Probe.fence))
        #expect(Set(probe.accepted) == [Probe.prime, Probe.fence])
        #expect(probe.unexplainedWakes == 0, "a rejected write woke the stream")
    }

    /// FSEvents may split a burst into several deliveries; a consumer busy
    /// through all of them still finds one wake waiting, not one each.
    /// Each write here waits for its own delivery, so the burst is ten
    /// deliveries for certain. A second wake left waiting would reach the
    /// consumer at once, with no new path behind it, before the fence was
    /// even delivered.
    @Test func changesWhileTheConsumerIsBusyWakeItOnce() async throws {
        let probe = Probe()
        // The stream asks its filter only until one path passes, so the
        // folder is made up front and kept out: each delivery's new file
        // is then a path the filter is asked about.
        try FileManager.default.createDirectory(at: scratch.url.appending(path: "AGENTS_MD"),
                                                withIntermediateDirectories: true)
        let consumer = try await primed(probe, busyAfterFirst: true) { $0.hasPrefix("AGENTS_MD/") }
        defer { consumer.cancel() }
        for index in 0..<10 {
            try write("AGENTS_MD/\(index).md")
            try #require(await probe.saw("AGENTS_MD/\(index).md"))
        }
        // The filter answering for 9.md comes before that delivery's wake
        // is buffered. FSEvents calls back one delivery at a time, so once
        // a later (rejected) write reaches the filter, 9.md's wake is in.
        try write("after-burst.txt")
        try #require(await probe.saw("after-burst.txt"))
        probe.busy.open()
        #expect(await probe.wake(after: "AGENTS_MD/9.md"))
        try write(Probe.fence)
        #expect(await probe.wake(after: Probe.fence))
        #expect(probe.unexplainedWakes == 0, "the burst left more than one wake waiting")
    }

    @Test func theInstructionsCheckFollowsTheOutputAndItsChapters() {
        #expect(ChaptersModel.affectsCheck("AGENTS.md"))
        #expect(ChaptersModel.affectsCheck("AGENTS_MD"))
        #expect(ChaptersModel.affectsCheck("AGENTS_MD/02-response-style.md"))
        #expect(ChaptersModel.affectsCheck("AGENTS_MD/index.txt"))
        #expect(!ChaptersModel.affectsCheck("AGENTS.md.bak"))
        #expect(!ChaptersModel.affectsCheck("skills/review-checklist/SKILL.md"))
        #expect(!ChaptersModel.affectsCheck("data/catalog/catalog.sqlite"))
    }

    @Test func hubStatusFollowsTrackedFilesAndRefsNotWhatReadingWrites() {
        for path in ["AGENTS.md", "skills/explain-a-diff/SKILL.md", "config/harness.json", ".gitignore",
                     ".git/HEAD", ".git/config", ".git/refs/heads/main", ".git/refs/remotes/origin/main",
                     ".git/packed-refs", "mcp.json", "mcp/README.md"] {
            #expect(HubSyncStatus.affects(path), "\(path)")
        }
        // Reading the status rewrites .git/index; the app writes data/ and
        // cache/ itself; the rest are folders the hub's .gitignore keeps out,
        // some of them busy with builds and agent edits.
        for path in [".git/index", ".git/index.lock", ".git/objects/ab/cdef", ".git/logs/HEAD", ".git/FETCH_HEAD",
                     "data/catalog/catalog.sqlite-wal", "cache/install/lock", "secrets/GITHUB_TOKEN", "data",
                     "backups/2026-11-01.tar", "worktrees/swe-1567/app/mac/.build/x.o",
                     "mcp/servers/github/node_modules/x.js", "skills/.system/imagegen/SKILL.md"] {
            #expect(!HubSyncStatus.affects(path), "\(path)")
        }
    }
}

/// Watches a `FolderChanges` stream from both ends: every path its filter
/// is asked about (and which it let through), and every wake a consumer
/// takes, with how many paths had been let through by then. A wake with
/// no newly accepted path behind it is one the stream should not have
/// given. Waits are bounded by `gateTimeout`.
private final class Probe: Sendable {
    /// Written first, to prove the watch is live; and last, to mark that
    /// everything before it was delivered. Every filter lets them through.
    static let prime = "probe-prime"
    static let fence = "probe-fence"

    private struct State {
        var asked: [String] = []
        var accepted: [String] = []
        /// `accepted.count` when each wake was taken.
        var wakes: [Int] = []
    }
    private let state = Mutex(State())
    private let asks = CountGate()
    private let wakeGate = CountGate()
    /// With `busyAfterFirst`, the consumer parks here after its first wake
    /// until the test opens it, as a consumer busy with other work.
    let busy = HoldGate()

    var accepted: [String] { state.withLock { $0.accepted } }
    var unexplainedWakes: Int {
        state.withLock { s in zip(s.wakes, s.wakes.dropFirst()).filter { $0 == $1 }.count }
    }

    /// The stream's filter: `rule`, plus the probe's own files, recording
    /// each question and answer. A path already let through once is turned
    /// away: one write can show up in two deliveries, and the stream stops
    /// asking at the first path that passes, so a repeat would hide the
    /// new path behind it.
    func filter(_ rule: @escaping @Sendable (String) -> Bool) -> @Sendable (String) -> Bool {
        { path in
            let ok = self.state.withLock { s -> Bool in
                s.asked.append(path)
                let ok = (rule(path) || path == Probe.prime || path == Probe.fence)
                    && !s.accepted.contains(path)
                if ok { s.accepted.append(path) }
                return ok
            }
            Task { await self.asks.bump() }
            return ok
        }
    }

    /// Takes every wake until cancelled, which also ends the stream.
    func consume(_ stream: AsyncStream<Void>, busyAfterFirst: Bool) -> Task<Void, Never> {
        Task {
            for await _ in stream {
                let n = state.withLock { s in
                    s.wakes.append(s.accepted.count)
                    return s.wakes.count
                }
                await wakeGate.bump()
                if busyAfterFirst && n == 1 { try? await busy.pass() }
            }
        }
    }

    /// True once the filter has been asked about `path`.
    func saw(_ path: String) async -> Bool {
        while true {
            let (seen, count) = state.withLock { ($0.asked.contains(path), $0.asked.count) }
            if seen { return true }
            guard await asks.reach(count + 1) else { return false }
        }
    }

    /// True once the filter has let `path` through and a wake was taken
    /// after it.
    func wake(after path: String) async -> Bool {
        guard await saw(path) else { return false }
        while true {
            let (woke, count) = state.withLock { s -> (Bool, Int) in
                guard let index = s.accepted.firstIndex(of: path) else { return (false, s.wakes.count) }
                return (s.wakes.contains { $0 > index }, s.wakes.count)
            }
            if woke { return true }
            guard await wakeGate.reach(count + 1) else { return false }
        }
    }
}
