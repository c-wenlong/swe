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

    /// A disk that blocks each path's lookup until the test releases that
    /// path, the way a stalled file provider blocks getxattr. Records
    /// which thread each lookup ran on and counts arrivals.
    private final class StalledDisk: Sendable {
        let arrivals = CountGate()
        let ranOnMain = Mutex<Bool?>(nil)
        private let gates: [String: DispatchSemaphore]

        init(paths: [String]) {
            gates = Dictionary(uniqueKeysWithValues: paths.map { ($0, DispatchSemaphore(value: 0)) })
        }

        func release(_ path: String) { gates[path]?.signal() }

        func resolve(stored: String) -> String? {
            ranOnMain.withLock { $0 = Thread.isMainThread }
            Task { await arrivals.bump() }
            // Bounded so a lookup wrongly run on the main actor fails the
            // test instead of hanging the suite.
            _ = gates[stored]?.wait(timeout: .now() + 5)
            return stored
        }
    }

    private struct NoNetwork: HTTPTransport {
        func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
            throw URLError(.notConnectedToInternet)
        }
    }

    private func model(stored: String, disk: StalledDisk,
                       stampedExists: @escaping @Sendable (String?) -> String? = { _ in nil })
        -> UpdatesModel {
        let prefs = UpdatePreferences(defaults: TestDefaults("UpdatesCheckoutResolution"))
        prefs.checkoutOverride = stored
        return UpdatesModel(
            build: BuildInfo(channel: .dev, commit: "abc", sourcePath: "/stamped"),
            preferences: prefs,
            releaseCheck: ReleaseCheck(
                transport: NoNetwork(),
                cacheURL: scratch.url.appending(path: "latest-release.json")),
            rebuildDir: scratch.url.appending(path: "rebuild"),
            resolveCheckout: { stored, _ in disk.resolve(stored: stored) },
            stampedExists: stampedExists)
    }

    @Test func buildingTheModelDoesNotWaitOnTheDisk() async {
        let disk = StalledDisk(paths: ["/Users/me/code/swe"])
        let clock = ContinuousClock()
        let started = clock.now
        let updates = model(stored: "/Users/me/code/swe", disk: disk)
        // Init returned while the disk is still stalled: no answer yet.
        #expect(clock.now - started < .seconds(2))
        #expect(updates.checkout == nil)

        disk.release("/Users/me/code/swe")
        await updates.checkoutResolved()
        #expect(updates.checkout == "/Users/me/code/swe")
        #expect(disk.ranOnMain.withLock { $0 } == false)
    }

    @Test func aChangedCheckoutDropsTheOlderAnswer() async {
        let disk = StalledDisk(paths: ["/old", "/new"])
        let updates = model(stored: "/old", disk: disk)
        let dropped = CountGate()
        updates.resolving.onDrop = { Task { await dropped.bump() } }
        updates.setCheckout("/new")
        #expect(updates.checkout == nil)
        // Both lookups are parked on the stalled disk.
        #expect(await disk.arrivals.reach(2))
        // The newer answer lands first; the older one, finishing later,
        // is dropped instead of overwriting it.
        disk.release("/new")
        await updates.checkoutResolved()
        #expect(updates.checkout == "/new")
        disk.release("/old")
        #expect(await dropped.reach(1))
        #expect(updates.checkout == "/new")
    }

    /// The stamped folder's hint is looked up apart from the checkout: a
    /// stalled stamped folder must not hold back a valid saved checkout.
    @Test func aStalledStampedFolderDoesNotHoldBackTheCheckout() async {
        let disk = StalledDisk(paths: ["/saved"])
        let stampedGate = DispatchSemaphore(value: 0)
        let updates = model(stored: "/saved", disk: disk, stampedExists: { path in
            _ = stampedGate.wait(timeout: .now() + 5)
            return path
        })
        disk.release("/saved")
        await updates.checkoutResolved()
        #expect(updates.checkout == "/saved")
        #expect(updates.stampedCheckout == nil)
        stampedGate.signal()
    }

    @Test func resolveSkipsTheStampedFolderWhenASavedPathIsValid() {
        let stamped = scratch.url.path
        #expect(UpdatePreferences.resolve(stored: "~/elsewhere", stamped: "/definitely/not/here")
            == NSHomeDirectory() + "/elsewhere")
        #expect(UpdatePreferences.resolve(stored: "  ", stamped: stamped) == stamped)
        #expect(UpdatePreferences.resolve(stored: "", stamped: "/definitely/not/here") == nil)
        #expect(UpdatePreferences.existing(stamped) == stamped)
        #expect(UpdatePreferences.existing("/definitely/not/here") == nil)
    }
}
