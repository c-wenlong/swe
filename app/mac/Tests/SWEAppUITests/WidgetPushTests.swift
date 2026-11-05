import Foundation
import SWECore
import Testing
@testable import SWEApp

/// The widgets' file takes every change, but WidgetKit is asked to
/// redraw at most once per `reloadFloor`, unless a settings change forces
/// it; an unchanged snapshot is not even written.
@Suite @MainActor struct WidgetPushTests {
    private func section(_ cpu: Double) -> WidgetSnapshot.ComputeSection {
        .init(sampledAt: Date(timeIntervalSince1970: 1_800_000_000),
              harnesses: [.init(harness: "claude", memoryBytes: 1, cpuPercent: cpu)], temperatures: [:])
    }

    @Test func pushesAreThrottledToTheFloor() throws {
        let home = try TempDir()
        let monitor = LimitsMonitor(swe: home.url.appending(path: ".swe"))
        var reloads = 0
        monitor.reloadTimelines = { reloads += 1 }
        let start = Date(timeIntervalSince1970: 1_800_000_000)
        let selection = WidgetSelection()

        monitor.writeWidgets(overview: nil, selection: selection, compute: section(1), now: start)
        #expect(reloads == 1, "the first write pushes")
        monitor.writeWidgets(overview: nil, selection: selection, compute: section(1), now: start.addingTimeInterval(1))
        #expect(reloads == 1, "nothing changed: no write, no push")
        monitor.writeWidgets(overview: nil, selection: selection, compute: section(2), now: start.addingTimeInterval(60))
        #expect(reloads == 1, "a change inside the floor writes the file only")
        #expect(FileManager.default.fileExists(atPath: WidgetSnapshotStore.defaultFile(home: home.url).path))
        monitor.writeWidgets(overview: nil, selection: selection, compute: section(3), force: true,
                             now: start.addingTimeInterval(120))
        #expect(reloads == 2, "a forced write pushes at once")
        monitor.writeWidgets(overview: nil, selection: selection, compute: section(4),
                             now: start.addingTimeInterval(120 + LimitsMonitor.reloadFloor))
        #expect(reloads == 3, "past the floor a change pushes again")
    }
}
