import Foundation
import Testing
@testable import SWECore

/// Which running apps count as instances of a given app bundle. That
/// decides which processes Quit and Relaunch act on, so a sibling app, or a
/// copy of the same app somewhere else, must never match. The app list is
/// injected as plain values; nothing here touches NSWorkspace.
@MainActor
@Suite struct RunningInstancesTests {
    private let target = "/Applications/Fixture.app"

    private func app(_ pid: Int32, url: String? = nil, id: String? = nil) -> RunningApp {
        RunningApp(pid: pid, bundleURL: url.map { URL(fileURLWithPath: $0) }, bundleIdentifier: id)
    }

    private func pids(_ apps: [RunningApp], at path: String? = nil) -> [Int32] {
        AppRunningInstances(apps: { apps }, arguments: { _ in [] })
            .instances(ofAppAt: path ?? target).map(\.pid)
    }

    @Test func anAppWhoseBundleIsTheTargetMatches() {
        #expect(pids([app(100, url: target), app(200, url: "/Applications/Other.app")]) == [100])
    }

    @Test func everyProcessOfTheBundleIsListedInTheOrderGiven() {
        // A second `--user-data-dir` instance is another process of the same bundle.
        #expect(pids([app(300, url: target), app(100, url: "/Applications/Other.app"),
                      app(200, url: target)]) == [300, 200])
    }

    @Test func aCopyOfTheSameAppElsewhereIsNotAnInstance() {
        #expect(pids([app(100, url: "/Users/me/Downloads/Fixture.app")]).isEmpty)
        #expect(pids([app(100, url: "/Applications/Fixture.app.bak")]).isEmpty)
    }

    @Test func pathsAreComparedAfterStandardisingThem() {
        let apps = [app(100, url: "/Applications/Fixture.app")]
        #expect(pids(apps, at: "/Applications/./Fixture.app") == [100])
        #expect(pids(apps, at: "/Applications/Sub/../Fixture.app") == [100])
        #expect(pids([app(100, url: "/Applications/Sub/../Fixture.app")]) == [100])
    }

    @Test func aProcessWithoutAValidPidIsLeftOut() {
        #expect(pids([app(0, url: target), app(-1, url: target), app(7, url: target)]) == [7])
    }

    @Test func nothingRunningMeansNoInstances() {
        #expect(pids([]).isEmpty)
    }

    @Test func aBundleSavedOverWhileRunningMatchesByIdentifierFromItsPlist() throws {
        let temp = try TempDir()
        let bundle = temp.path("Fixture.app")
        let contents = bundle.appendingPathComponent("Contents", isDirectory: true)
        try FileManager.default.createDirectory(at: contents, withIntermediateDirectories: true)
        let plist: [String: Any] = ["CFBundleIdentifier": "test.example.fixture", "CFBundleName": "Fixture"]
        try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
            .write(to: contents.appendingPathComponent("Info.plist"))

        let apps = [
            // No bundle URL (the app was replaced under it): the identifier decides.
            app(100, id: "test.example.fixture"),
            app(200, id: "test.example.other"),
            app(300),
            // A known URL elsewhere wins over the identifier.
            app(400, url: "/Applications/Elsewhere.app", id: "test.example.fixture"),
        ]
        withExtendedLifetime(temp) {
            #expect(pids(apps, at: bundle.path) == [100])
        }
    }

    @Test func aTargetWithNoReadableBundleMatchesNoIdentifierlessApp() {
        // /nonexistent has no Info.plist, so there is no identifier to fall back on;
        // an app with neither a URL nor an identifier must not match it.
        #expect(pids([app(100), app(200, id: "test.example.fixture")], at: "/nonexistent/Fixture.app").isEmpty)
    }

    @Test func eachInstanceCarriesItsOwnArguments() {
        let instances = AppRunningInstances(
            apps: { [self.app(100, url: self.target), self.app(200, url: self.target)] },
            arguments: { pid in pid == 200 ? ["Fixture", "--user-data-dir=/tmp/alt"] : ["Fixture"] }
        ).instances(ofAppAt: target)
        #expect(instances == [
            RunningInstance(pid: 100, arguments: ["Fixture"]),
            RunningInstance(pid: 200, arguments: ["Fixture", "--user-data-dir=/tmp/alt"]),
        ])
    }
}
