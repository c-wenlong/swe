import Foundation
import Testing
@testable import SWECore

@Suite struct TimestampTests {
    @Test func readsTheCommonUTCShape() {
        #expect(Timestamp.parse("1970-01-01T00:00:01Z") == Date(timeIntervalSince1970: 1))
        #expect(Timestamp.parse("1970-01-01T00:00:01.5Z") == Date(timeIntervalSince1970: 1.5))
        #expect(Timestamp.parse("2028-02-29T00:00:00Z") != nil)
        #expect(Timestamp.parse("2000-02-29T00:00:00Z") != nil)
    }

    /// An impossible date used to roll over: 2026-02-30 read as 2 March.
    @Test func anImpossibleDateIsUnreadable() {
        for text in ["2026-02-30T10:00:00Z", "2026-04-31T10:00:00Z", "2026-02-29T10:00:00Z",
                     "2100-02-29T10:00:00Z", "2026-02-30T10:00:00.123Z"] {
            #expect(Timestamp.parse(text) == nil, "\(text)")
        }
    }

    @Test func daysInMonthFollowsTheGregorianLeapRule() {
        #expect(Timestamp.daysIn(month: 2, year: 2024) == 29)
        #expect(Timestamp.daysIn(month: 2, year: 1900) == 28)
        #expect(Timestamp.daysIn(month: 2, year: 2000) == 29)
        #expect(Timestamp.daysIn(month: 9, year: 2026) == 30)
        #expect(Timestamp.daysIn(month: 12, year: 2026) == 31)
    }
}
