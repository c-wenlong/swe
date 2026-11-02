import Foundation
import Testing
@testable import SWECore

/// FolderChanges against a real folder (FSEvents), and the two filters the
/// AGENTS.md page and the Hub sync card follow `~/.swe` with.
@Suite struct FolderChangesTests {
    private let scratch: TempDir
    init() throws { scratch = try TempDir() }

    /// The next element, or false once `seconds` pass without one.
    private func next(_ stream: AsyncStream<Void>, within seconds: Double) async -> Bool {
        await withTaskGroup(of: Bool.self) { group in
            group.addTask {
                var iterator = stream.makeAsyncIterator()
                return await iterator.next() != nil
            }
            group.addTask {
                try? await Task.sleep(for: .seconds(seconds))
                return false
            }
            let first = await group.next() ?? false
            group.cancelAll()
            return first
        }
    }

    private func write(_ relative: String, _ text: String = "x") throws {
        let url = scratch.url.appending(path: relative)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }

    @Test func aMatchingWriteWakesTheStream() async throws {
        let stream = FolderChanges.stream(root: scratch.url, latency: 0.1) { $0.hasPrefix("AGENTS_MD/") }
        // FSEvents reports from when the stream starts; give it a moment.
        try await Task.sleep(for: .milliseconds(300))
        try write("AGENTS_MD/01-working.md")
        #expect(await next(stream, within: 5))
    }

    @Test func writesTheFilterRejectsStayQuiet() async throws {
        let stream = FolderChanges.stream(root: scratch.url, latency: 0.1) { $0.hasPrefix("AGENTS_MD/") }
        try await Task.sleep(for: .milliseconds(300))
        try write("data/catalog/catalog.sqlite")
        try write("notes.md")
        #expect(await next(stream, within: 1.5) == false)
    }

    /// FSEvents may split a burst into several deliveries; a consumer busy
    /// through all of them still finds one wake waiting, not one each.
    @Test func changesWhileTheConsumerIsBusyWakeItOnce() async throws {
        let stream = FolderChanges.stream(root: scratch.url, latency: 0.1) { _ in true }
        try await Task.sleep(for: .milliseconds(300))
        for index in 0..<10 {
            try write("AGENTS_MD/\(index).md")
            try await Task.sleep(for: .milliseconds(50))
        }
        try await Task.sleep(for: .seconds(1.5))
        #expect(await next(stream, within: 1))
        #expect(await next(stream, within: 1) == false)
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
                     ".git/HEAD", ".git/refs/heads/main", ".git/refs/remotes/origin/main", ".git/packed-refs"] {
            #expect(HubSyncStatus.affects(path), "\(path)")
        }
        // Reading the status rewrites .git/index; the app writes data/ and
        // cache/ itself; secrets/ never reaches git.
        for path in [".git/index", ".git/index.lock", ".git/objects/ab/cdef", ".git/logs/HEAD", ".git/FETCH_HEAD",
                     "data/catalog/catalog.sqlite-wal", "cache/install/lock", "secrets/GITHUB_TOKEN", "data"] {
            #expect(!HubSyncStatus.affects(path), "\(path)")
        }
    }
}
