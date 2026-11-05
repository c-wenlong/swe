import Foundation
import SWECore
import Testing
@testable import SWEApp

/// A rename's write leaves the main actor, and a failed one changes nothing.
@Suite @MainActor struct DisplayNamesSaveTests {
    private struct Denied: Error {}

    @Test func theWriteRunsOffTheMainThread() async throws {
        let wroteOnMain = Box(true)
        let names = try await PluginDisplayNames().savingOffMain(
            { $0.set("Mine", forMarketplace: "m") }, to: URL(fileURLWithPath: "/unused"),
            write: { _, _ in wroteOnMain.set(Thread.isMainThread) })
        #expect(names.name(forMarketplace: "m") == "Mine")
        #expect(!wroteOnMain.get())
    }

    @Test func aFailedWriteThrowsAndKeepsTheOldNames() async {
        let before = PluginDisplayNames(marketplaces: ["m": "Old"])
        await #expect(throws: (any Error).self) {
            _ = try await before.savingOffMain({ $0.set("New", forMarketplace: "m") },
                                               to: URL(fileURLWithPath: "/unused"),
                                               write: { _, _ in throw Denied() })
        }
        #expect(before.name(forMarketplace: "m") == "Old")
    }

    @Test func itWritesTheFileItReturns() async throws {
        let dir = try TempDir()
        let file = dir.url.appending(path: "config/names.json")
        let names = try await PluginDisplayNames().savingOffMain(
            { $0.set("Mine", forPlugin: "p@m") }, to: file)
        #expect(PluginDisplayNames.load(from: file) == names)
    }

    private final class Box: @unchecked Sendable {
        private let lock = NSLock()
        private var value: Bool
        init(_ value: Bool) { self.value = value }
        func set(_ v: Bool) { lock.withLock { value = v } }
        func get() -> Bool { lock.withLock { value } }
    }
}
