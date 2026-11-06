import Foundation
import Testing
@testable import SWECore

/// `CalendarFormat`: the one set of rules every block, card, row and
/// sheet prints times, durations and session labels with.
@Suite struct CalendarFormatTests {
    /// A wall-clock moment in the test machine's own time zone, so the
    /// expected text is a literal whatever zone the suite runs in.
    private func local(_ day: Int, _ hour: Int, _ minute: Int, _ second: Int = 0) -> Date {
        Calendar.current.date(from: DateComponents(
            year: 2026, month: 10, day: day, hour: hour, minute: minute, second: second))!
    }

    @Test func aBlockInsideOneMinutePrintsOneTime() {
        #expect(CalendarFormat.range(start: local(13, 9, 5), end: local(13, 9, 5, 45))
                == "09:05")
    }

    @Test func aRangeSpanningMinutesPrintsBothTimes() {
        // Zero-padded, 24-hour, en dash.
        #expect(CalendarFormat.range(start: local(13, 9, 5), end: local(13, 14, 47, 30))
                == "09:05–14:47")
    }

    @Test func matchingClockTimesAcrossDaysStayARange() {
        // 14:02 today to 14:02 tomorrow is not one moment.
        #expect(CalendarFormat.range(start: local(13, 14, 2), end: local(14, 14, 2))
                == "14:02–14:02")
    }

    @Test func activeTimeUnderAMinuteIsLessThanOne() {
        #expect(CalendarFormat.duration(0) == "<1m")
        #expect(CalendarFormat.duration(37) == "<1m")
        #expect(CalendarFormat.duration(60) == "1m")
        #expect(CalendarFormat.duration(59 * 60 + 30) == "59m")
        #expect(CalendarFormat.duration(2 * 3600 + 5 * 60) == "2h 5m")
        #expect(CalendarFormat.duration(47 * 3600 + 59 * 60) == "47h 59m")
        #expect(CalendarFormat.duration(48 * 3600) == "2d 0h")
        #expect(CalendarFormat.duration(169 * 3600 + 49 * 60) == "7d 1h")
    }

    @Test func aMissingTitleKeepsAReadableSessionId() {
        #expect(CalendarFormat.sessionTitle(nil, sessionId: "natural-riverbed")
                == "natural-riverbed")
        #expect(CalendarFormat.sessionTitle("", sessionId: "s") == "s")
    }

    /// A UUID-like id, or any id past 24 characters, is cut to its first
    /// eight; a short readable slug stays whole.
    @Test func aMissingTitleShortensUUIDLikeAndLongIds() {
        let uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        let packed = "a1b2c3d4e5f67890abcdef1234567890"
        #expect(CalendarFormat.sessionTitle(nil, sessionId: uuid) == "a1b2c3d4…")
        #expect(CalendarFormat.sessionTitle(nil, sessionId: packed) == "a1b2c3d4…")
        let long = "natural-riverbed-shared-kayak-more"
        #expect(CalendarFormat.sessionTitle(nil, sessionId: long) == "natural-…")
        #expect(CalendarFormat.sessionTitle("Real title", sessionId: packed)
                == "Real title")
    }
}
