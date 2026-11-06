import Foundation
import Testing
@testable import SWEApp

/// The pure parts of two charts: the temperature axis's range and the
/// account history's solid and dashed stretches.
@Suite @MainActor struct ChartSeriesTests {
    @Test func theTemperatureDomainRoundsToTensWithHeadroom() {
        let domain = TemperatureChart.domain([41, 63])
        #expect(domain.lowerBound == 30)
        #expect(domain.upperBound >= 68)
        #expect(domain.upperBound.truncatingRemainder(dividingBy: 10) == 0)
        // Nothing read yet: a sane default range, never negative.
        let empty = TemperatureChart.domain([])
        #expect(empty.lowerBound >= 0)
        #expect(empty.upperBound - empty.lowerBound >= 30)
        #expect(TemperatureChart.domain([2]).lowerBound == 0)
    }

    private func week(_ n: Int, mac: Int, account: Int? = nil, running: Bool = false) -> HistoryPoint {
        HistoryPoint(start: Date(timeIntervalSince1970: Double(n) * 604_800), account: account, mac: mac,
                     inProgress: running)
    }

    /// A running week is drawn dashed from the last finished week, never
    /// solid, so a part week can't read as a drop.
    @Test func theRunningPeriodIsTheDashedStretch() {
        let points = HistoryChart.points([week(0, mac: 5), week(1, mac: 7), week(2, mac: 1, running: true)])
        #expect(points.solid.map(\.index) == [0, 1])
        #expect(points.dashed.map(\.index) == [1, 2])
        #expect(Set(points.dashed.map(\.series)) == ["This Mac so far"])
        #expect(points.ends.map(\.index) == [2])
    }

    @Test func withNothingRunningEveryPointIsSolidAndTheAccountLineComesFirst() {
        let points = HistoryChart.points([week(0, mac: 5, account: 9), week(1, mac: 7, account: 12)])
        #expect(points.dashed.isEmpty)
        #expect(points.solid.count == 4)
        #expect(points.all.first?.series == "Account")
        #expect(points.ends.map(\.series) == ["Account", "This Mac"])
    }
}
