import Foundation

/// ISO 8601 timestamps as the harnesses write them. The common UTC shape
/// (`2026-10-03T10:00:03.123Z`) is parsed by hand because a full history has
/// millions of them; anything else goes through Foundation.
enum Timestamp {
    static func parse(_ text: String) -> Date? {
        switch parseUTC(Array(text.utf8)) {
        case .date(let date): return date
        // Foundation would roll an impossible date over, as the sum did.
        case .impossible: return nil
        case .otherShape: break
        }
        if let date = try? Date(text, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true)) {
            return date
        }
        return try? Date(text, strategy: Date.ISO8601FormatStyle())
    }

    private enum Fast {
        case date(Date)
        /// The UTC shape with a day its month does not have.
        case impossible
        /// Not the UTC shape: Foundation gets to try it.
        case otherShape
    }

    private static func parseUTC(_ b: [UInt8]) -> Fast {
        // YYYY-MM-DDTHH:MM:SS[.fraction]Z
        guard b.count >= 20, b[4] == 0x2D, b[7] == 0x2D, b[10] == 0x54 || b[10] == 0x20,
              b[13] == 0x3A, b[16] == 0x3A, b.last == 0x5A
        else { return .otherShape }
        func digits(_ start: Int, _ count: Int) -> Int? {
            var value = 0
            for index in start..<(start + count) {
                let c = b[index]
                guard c >= 0x30, c <= 0x39 else { return nil }
                value = value * 10 + Int(c - 0x30)
            }
            return value
        }
        guard let year = digits(0, 4), let month = digits(5, 2), let day = digits(8, 2),
              let hour = digits(11, 2), let minute = digits(14, 2), let second = digits(17, 2),
              (1...12).contains(month), (1...31).contains(day), hour < 24, minute < 60, second < 61
        else { return .otherShape }
        // The civil-day sum below rolls an impossible date over silently,
        // so 2026-02-30 would read as 2 March; such a line is unreadable.
        guard day <= daysIn(month: month, year: year) else { return .impossible }
        var fraction = 0.0
        if b.count > 20 {
            guard b[19] == 0x2E else { return .otherShape }
            var scale = 0.1
            for index in 20..<(b.count - 1) {
                let c = b[index]
                guard c >= 0x30, c <= 0x39 else { return .otherShape }
                fraction += Double(c - 0x30) * scale
                scale /= 10
            }
        } else if b[19] != 0x5A {
            return .otherShape
        }
        // Days from civil (Howard Hinnant's algorithm).
        let y = month <= 2 ? year - 1 : year
        let era = (y >= 0 ? y : y - 399) / 400
        let yoe = y - era * 400
        let mp = (month + 9) % 12
        let doy = (153 * mp + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        let days = era * 146_097 + doe - 719_468
        let seconds = Double(days * 86_400 + hour * 3_600 + minute * 60 + second) + fraction
        return .date(Date(timeIntervalSince1970: seconds))
    }

    /// Days in a month of the proleptic Gregorian calendar.
    static func daysIn(month: Int, year: Int) -> Int {
        switch month {
        case 2:
            let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
            return leap ? 29 : 28
        case 4, 6, 9, 11: return 30
        default: return 31
        }
    }
}
