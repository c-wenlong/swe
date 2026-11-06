import Foundation
import Synchronization
import Testing
@testable import SWECore

/// The Source checkout is resolved off the main actor. AppModel builds the
/// Updates model before the first window, and resolving the saved path
/// walks it on disk (PathSafety, fileExists): a stalled Desktop folder once
/// held the window back for minutes with the main thread in getxattr.
@Suite("Updates checkout resolution")
@MainActor
struct UpdatesCheckoutResolutionTests {
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    /// A resolver that blocks its thread until released, the way a stalled
    /// file provider blocks getxattr, and records which thread it ran on.
    private final class StalledDisk: Sendable {
        let release = DispatchSemaphore(value: 0)
        let ranOnMain = Mutex<Bool?>(nil)
        let calls = Mutex<[String]>([])
        let answers: [String: String?]

        init(answers: [String: String?]) { self.answers = answers }

        func resolve(stored: String, stamped: String?) -> ResolvedCheckout {
            ranOnMain.withLock { $0 = Thread.isMainThread }
            calls.withLock { $0.append(stored) }
            // Bounded so a resolver wrongly run on the main actor fails the
            // test below instead of hanging the suite.
            _ = release.wait(timeout: .now() + 5)
            return ResolvedCheckout(checkout: answers[stored] ?? nil, stamped: nil)
        }
    }

    private struct NoNetwork: HTTPTransport {
        func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
            throw URLError(.notConnectedToInternet)
        }
    }

    private func model(stored: String, disk: StalledDisk) -> UpdatesModel {
        let prefs = UpdatePreferences(defaults: TestDefaults("UpdatesCheckoutResolution"))
        prefs.checkoutOverride = stored
        return UpdatesModel(
            build: BuildInfo(channel: .dev, commit: "abc"),
            preferences: prefs,
            releaseCheck: ReleaseCheck(
                transport: NoNetwork(),
                cacheURL: scratch.url.appending(path: "latest-release.json")),
            rebuildDir: scratch.url.appending(path: "rebuild"),
            resolveCheckout: { disk.resolve(stored: $0, stamped: $1) })
    }

    @Test func buildingTheModelDoesNotWaitOnTheDisk() async {
        let disk = StalledDisk(answers: ["~/code/swe": "/Users/me/code/swe"])
        let clock = ContinuousClock()
        let started = clock.now
        let updates = model(stored: "~/code/swe", disk: disk)
        // Init returned while the disk is still stalled: no answer yet.
        #expect(clock.now - started < .seconds(2))
        #expect(updates.checkout == nil)

        disk.release.signal()
        await updates.checkoutResolved()
        #expect(updates.checkout == "/Users/me/code/swe")
        #expect(disk.ranOnMain.withLock { $0 } == false)
    }

    @Test func aChangedCheckoutDropsTheOlderAnswer() async {
        let disk = StalledDisk(answers: ["/old": "/old", "/new": "/new"])
        let updates = model(stored: "/old", disk: disk)
        updates.setCheckout("/new")
        #expect(updates.checkout == nil)
        // Both resolutions are parked on the stalled disk; let them go in
        // either order and only the newer path may land.
        disk.release.signal()
        disk.release.signal()
        await updates.checkoutResolved()
        #expect(updates.checkout == "/new")
        #expect(disk.calls.withLock { Set($0) } == ["/old", "/new"])
    }

    @Test func resolveKeepsTheStampedFolderAsAHint() throws {
        let stamped = scratch.url.path
        let both = UpdatePreferences.resolve(stored: "~/elsewhere", stamped: stamped)
        #expect(both.checkout == NSHomeDirectory() + "/elsewhere")
        #expect(both.stamped == stamped)
        let fallback = UpdatePreferences.resolve(stored: "  ", stamped: stamped)
        #expect(fallback == ResolvedCheckout(checkout: stamped, stamped: stamped))
        let gone = UpdatePreferences.resolve(stored: "", stamped: "/definitely/not/here")
        #expect(gone == ResolvedCheckout(checkout: nil, stamped: nil))
    }
}
