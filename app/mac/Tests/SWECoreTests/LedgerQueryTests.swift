import Foundation
import Testing
@testable import SWECore

@Suite struct LedgerQueryTests {
    /// Keep the `TempDir` for the whole test: dropping it deletes the
    /// ledger's folder, and the off-actor queries open their own read
    /// connections by path.
    func scannedFixture() async throws -> (TempDir, Ledger) {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let claude = try temp.copyFixture("claude-home")
        let codex = try temp.copyFixture("codex-home")
        let ledger = try Ledger(directory: temp.path("cache"), pricing: .empty)
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [claude], codexHomes: [codex]))
        return (temp, ledger)
    }

    /// The Accounts history sums one harness over chosen homes, or every
    /// home but those other cards own, all keys from one read.
    @Test func dailyTokensSumChosenHomesOrAllButExcluded() async throws {
        let (temp, ledger) = try await scannedFixture()
        defer { withExtendedLifetime(temp) {} }
        let home = temp.path("claude-home").path
        let range = DateInterval(start: Date(timeIntervalSince1970: 0), end: Date(timeIntervalSince1970: 4_000_000_000))
        let calendar = Calendar(identifier: .gregorian)
        let all = AccountHistory.Key(harness: "claude", homes: nil)
        let mine = AccountHistory.Key(harness: "claude", homes: [home])
        let rest = AccountHistory.Key(harness: "claude", homes: nil, excluding: [home])
        let other = AccountHistory.Key(harness: "claude", homes: ["/elsewhere"])
        let read = await ledger.dailyTokens(for: [all, mine, rest, other], in: range, calendar: calendar)
        #expect(read[all]?.isEmpty == false)
        #expect(read[mine] == read[all])
        #expect(await ledger.dailyTokens(harness: "claude", accountHome: home,
                                         in: range, calendar: calendar) == read[all])
        #expect(read[rest] == [:])
        #expect(read[other] == [:])
    }

    /// Range reads come off the covering index without a table lookup per
    /// row, and the plain `ts` index it replaces is dropped (#1568).
    @Test func rangeReadsUseTheCoveringIndex() throws {
        let temp = try TempDir()
        // The store holds a path, not the folder: keep the folder alive.
        defer { withExtendedLifetime(temp) {} }
        let store = try LedgerStore(url: temp.path("ledger.sqlite"))
        let names = try store.db.prepare("SELECT name FROM sqlite_master WHERE type = 'index'")
        var indexes: Set<String> = []
        while try names.step() { indexes.insert(names.text(0) ?? "") }
        #expect(indexes.contains("events_ts_cover"))
        #expect(!indexes.contains("events_ts"))
        let plan = try store.db.prepare(
            "EXPLAIN QUERY PLAN SELECT dim, SUM(input), TOTAL(cost) FROM events e WHERE ts >= 0 AND ts < 1 GROUP BY dim")
        var detail = ""
        while try plan.step() { detail += (plan.text(3) ?? "") + "\n" }
        #expect(detail.contains("COVERING INDEX events_ts_cover"))
    }

    /// One harness's keys read only that harness, with the same days as
    /// a read across every harness.
    @Test func oneHarnessDailyTokensMatchTheWholeRead() async throws {
        let (temp, ledger) = try await scannedFixture()
        defer { withExtendedLifetime(temp) {} }
        let range = DateInterval(start: Date(timeIntervalSince1970: 0), end: Date(timeIntervalSince1970: 4_000_000_000))
        let calendar = Calendar(identifier: .gregorian)
        let claude = AccountHistory.Key(harness: "claude", homes: nil)
        let codex = AccountHistory.Key(harness: "codex", homes: nil)
        let both = await ledger.dailyTokens(for: [claude, codex], in: range, calendar: calendar)
        #expect(both[codex]?.isEmpty == false)
        #expect(await ledger.dailyTokens(for: [codex], in: range, calendar: calendar)[codex] == both[codex])
        #expect(await ledger.dailyTokens(for: [claude], in: range, calendar: calendar)[claude] == both[claude])
    }

    @Test func dailyBucketsUseTheCallersCalendar() async throws {
        let (temp, ledger) = try await scannedFixture()
        defer { withExtendedLifetime(temp) {} }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try #require(TimeZone(identifier: "Asia/Singapore"))
        let start = try #require(calendar.date(from: DateComponents(year: 2026, month: 10, day: 1)))
        let end = try #require(calendar.date(from: DateComponents(year: 2026, month: 10, day: 5)))
        let days = await ledger.daily(in: DateInterval(start: start, end: end), calendar: calendar)

        func day(_ d: Int) -> Date { calendar.date(from: DateComponents(year: 2026, month: 10, day: d))! }
        let rows = days.map { "\(calendar.component(.day, from: $0.day)) \($0.harness) \($0.model) \($0.totals.totalTokens)" }
        // The legacy line logged at 23:30 UTC on 30 September is 1 October in Singapore.
        #expect(rows == [
            "1 claude claude-sonnet-4-5-20250929 200",
            "3 claude claude-fable-5 4350",
            "3 claude claude-opus-5-5 42693",
            "3 claude claude-sonnet-5 32",
            "4 claude claude-opus-5 340",
            "4 claude claude-opus-5-5 22094",
            "4 codex gpt-5.6-sol 4200",
        ])
        #expect(days.first?.day == day(1))
        #expect(days.first?.totals.costUSD == 0.25)
        // The Codex record session ran on 5 October, outside the range.
        #expect(!rows.contains { $0.contains("gpt-6.1-sol") })
    }

    @Test func sessionsComeMostRecentFirstWithTheirSpan() async throws {
        let (temp, ledger) = try await scannedFixture()
        defer { withExtendedLifetime(temp) {} }
        let sessions = await ledger.bySession(in: october2026)
        #expect(sessions.first?.sessionId == "rollout-2026-10-05T10-00-00-cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        #expect(sessions.first?.cwd == "/tmp/codex-new")
        let main = try #require(sessions.first { $0.sessionId == "11111111-1111-4111-8111-111111111111" })
        #expect(main.firstTimestamp == utc("2026-10-03T10:00:03Z"))
        #expect(main.lastTimestamp == utc("2026-10-03T10:05:01Z"))
        #expect(main.models == ["claude-fable-5", "claude-opus-5-5", "claude-sonnet-5"])
        #expect(main.totals.eventCount == 4)
    }

    @Test func bySessionRestrictsToOneHarness() async throws {
        let (temp, ledger) = try await scannedFixture()
        defer { withExtendedLifetime(temp) {} }
        let claude = await ledger.bySession(in: october2026, harness: "claude")
        #expect(!claude.isEmpty)
        #expect(claude.allSatisfy { $0.harness == "claude" })
        let codex = await ledger.bySession(in: october2026, harness: "codex")
        #expect(!codex.isEmpty)
        #expect(codex.allSatisfy { $0.harness == "codex" })
        #expect(await ledger.bySession(in: october2026, harness: "pi").isEmpty)
    }

    @Test func rangesIncludeTheirStartAndExcludeTheirEnd() async throws {
        let (temp, ledger) = try await scannedFixture()
        defer { withExtendedLifetime(temp) {} }
        let atA2 = utc("2026-10-03T10:01:00Z")
        #expect(await ledger.totals(in: DateInterval(start: atA2, duration: 1)).totalTokens == 21_563)
        #expect(await ledger.totals(in: DateInterval(start: atA2.addingTimeInterval(-60), end: atA2)).totalTokens == 21_130)
    }

    @Test func harnessesAreSplitOut() async throws {
        let (temp, ledger) = try await scannedFixture()
        defer { withExtendedLifetime(temp) {} }
        let byHarness = await ledger.byHarness(in: october2026)
        #expect(byHarness["claude"]?.totalTokens == 69_509)
        #expect(byHarness["codex"]?.totalTokens == 309_500)
    }
}
