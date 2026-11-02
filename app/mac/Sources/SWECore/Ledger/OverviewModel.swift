import Foundation
import Observation

/// What the Overview page and the menu bar ask of the ledger.
public protocol LedgerQuerying: Sendable {
    /// `recentLimit` caps the scan to each source's newest files; nil is
    /// the full sweep.
    func scan(roots: LedgerRoots?, recentLimit: Int?) async throws -> ScanReport
    func byHarness(in range: DateInterval) async -> [String: UsageTotals]
    func byAccount(in range: DateInterval) async -> [String: UsageTotals]
    /// The harness each account home's usage came from.
    func harnessByAccount(in range: DateInterval) async -> [String: String]
    func daily(in range: DateInterval, calendar: Calendar) async -> [DailyUsage]
    func setPricing(_ catalog: PricingCatalog) async
    /// How far the running full scan has got; nil when none is running.
    func scanProgress() async -> ScanProgress?
}

extension Ledger: LedgerQuerying {}

/// The Overview page's period picker.
public enum OverviewPeriod: String, CaseIterable, Identifiable, Sendable {
    case today, week, month, thirtyDays, ninetyDays, year, allTime

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .today: "Today"
        case .week: "7 days"
        case .month: "This month"
        case .thirtyDays: "30 days"
        case .ninetyDays: "90 days"
        case .year: "365 days"
        case .allTime: "All time"
        }
    }

    public func range(now: Date, calendar: Calendar) -> DateInterval {
        switch self {
        case .today:
            DateInterval(start: calendar.startOfDay(for: now), end: now)
        case .week:
            DateInterval(start: now.addingTimeInterval(-7 * 86_400), end: now)
        case .month:
            DateInterval(start: calendar.dateInterval(of: .month, for: now)?.start ?? now, end: now)
        case .thirtyDays:
            DateInterval(start: now.addingTimeInterval(-30 * 86_400), end: now)
        case .ninetyDays:
            DateInterval(start: now.addingTimeInterval(-90 * 86_400), end: now)
        case .year:
            DateInterval(start: now.addingTimeInterval(-365 * 86_400), end: now)
        case .allTime:
            DateInterval(start: .distantPast, end: now)
        }
    }
}

/// One harness's slice of the period.
public struct HarnessShare: Sendable, Equatable, Identifiable {
    public var id: String { harness }
    public var harness: String
    public var totals: UsageTotals
    /// Of the period's tokens, 0...1.
    public var share: Double
    /// Distinct models this harness used in the period.
    public var modelCount = 0
}

extension Array where Element == HarnessShare {
    /// Most spend first; harnesses with no priced spend after, most tokens
    /// first; otherwise the current order (Settings') between equals.
    public func sortedBySpend() -> [HarnessShare] {
        enumerated().sorted { a, b in
            if a.element.totals.costUSD != b.element.totals.costUSD {
                return a.element.totals.costUSD > b.element.totals.costUSD
            }
            // Tokens only order harnesses with no priced spend at all; an
            // equal positive spend keeps Settings' order.
            if a.element.totals.costUSD == 0, a.element.totals.totalTokens != b.element.totals.totalTokens {
                return a.element.totals.totalTokens > b.element.totals.totalTokens
            }
            return a.offset < b.offset
        }
        .map(\.element)
    }
}

/// One model's slice of the period, for the top models list.
public struct ModelShare: Sendable, Equatable, Identifiable {
    public var id: String { model }
    public var model: String
    public var totals: UsageTotals
    /// Of the period's tokens, 0...1.
    public var share: Double
    /// The harnesses that ran the model, most tokens first.
    public var harnesses: [String] = []

    public init(model: String, totals: UsageTotals, share: Double) {
        self.model = model
        self.totals = totals
        self.share = share
    }
}

public struct DayRow: Sendable, Equatable, Identifiable {
    public var id: Date { day }
    public var day: Date
    public var totals: UsageTotals

    public init(day: Date, totals: UsageTotals) {
        self.day = day
        self.totals = totals
    }
}

public struct AccountRow: Sendable, Equatable, Identifiable {
    public var id: String { home }
    public var home: String
    public var label: String
    public var totals: UsageTotals
    /// The harness the account's usage came from, for its logo; nil when
    /// the ledger can't say.
    public var harness: String? = nil
}

public struct OverviewSummary: Sendable, Equatable {
    public var period: OverviewPeriod
    public var range: DateInterval
    public var totals: UsageTotals
    public var harnesses: [HarnessShare]
    public var days: [DayRow]
    public var accounts: [AccountRow]
    /// Largest first.
    public var models: [ModelShare] = []
    /// Days in the period with any usage.
    public var activeDays: Int { days.filter { $0.totals.totalTokens > 0 }.count }
}

/// Spend for the menu bar's donut.
public struct SpendSlice: Sendable, Equatable, Identifiable {
    public enum Period: String, Sendable { case today, yesterday, thirtyDays }
    public var id: Period { period }
    public var period: Period
    public var byHarness: [String: UsageTotals]

    public var costUSD: Double { byHarness.values.reduce(0) { $0 + $1.costUSD } }
}

@MainActor
@Observable
public final class OverviewModel {
    public var period: OverviewPeriod = .month
    public private(set) var summary: OverviewSummary?
    public private(set) var spend: [SpendSlice]?
    /// Days of the last half year with their token totals, for the heatmap.
    public private(set) var activity: [DayRow] = []
    /// `activity` before the days are combined: one row per harness, model
    /// and day, so the widgets can count a chosen set of harnesses.
    public private(set) var activityRows: [DailyUsage] = []
    /// The last 30 days the same way, the span `recentModels` covers.
    public private(set) var recentRows: [DailyUsage] = []
    /// The last 30 days' models, largest first, whatever the period picker
    /// says. For the widgets.
    public private(set) var recentModels: [ModelShare] = []
    public private(set) var isLoading = false
    /// The running full scan's files done out of total, once it has run
    /// longer than `scanWatchInterval`; nil otherwise.
    public private(set) var scanProgress: ScanProgress?
    public private(set) var error: String?
    /// Sources the last scan skipped, e.g. a database its app had locked or
    /// was rebuilding. Shown as a note, not a failure: everything else read.
    public private(set) var scanNotes: [String] = []
    /// Scans that may have moved the ledger's figures: every scan a `load`
    /// ran (launch, a refresh button), and a `backgroundScan` only when it
    /// read new usage or a new price list. Other views reload their ledger
    /// figures when it changes, so a background tick that read nothing
    /// costs them nothing.
    public private(set) var completedScans = 0
    /// Every successful scan, whether or not it read anything. Tests read it.
    private(set) var scansRun = 0
    /// Whether the Overview page is on screen. The page sets it on appear
    /// and disappear. While it is, a background scan that read new usage
    /// rebuilds the selected period too; while it isn't, the period waits
    /// for `refreshForPage()`.
    @ObservationIgnored public var pageVisible = false
    /// `activityRows` before the last 30 days were last read before a
    /// background scan wrote new usage. Those scans refresh only the recent
    /// days; the page's next `refreshForPage()` (or a new day's first
    /// background scan) reads the half year whole again.
    private(set) var activityStale = false
    /// The local day the spend and activity figures were built on, so a
    /// background scan that read nothing still rebuilds them after midnight.
    private var figuresDay: Date?
    /// Whether a full scan (no `recentLimit`) has finished. The search
    /// index's capped reconcile scans also count in `completedScans` and can
    /// finish while the first full scan is still running, so the launch
    /// scan's progress card and partial figures key on this instead.
    public private(set) var fullScanFinished = false
    /// Ledger writes outside the scan path (the Cursor feed's ingest);
    /// views that refresh on `completedScans` watch this too.
    public private(set) var ledgerWrites = 0
    /// When the figures were last rebuilt.
    public private(set) var updatedAt: Date?

    private let ledger: LedgerQuerying
    private let roots: @Sendable () async -> LedgerRoots
    private let accountLabel: (String) -> String
    private let now: () -> Date
    private let calendar: Calendar
    private let refreshPricing: @Sendable () async -> PricingCatalog?
    /// How often a running full scan's progress is read; nil never reads it.
    private let scanWatchInterval: Duration?
    /// Waits out one `scanWatchInterval` between progress reads. Tests
    /// replace it to let each read happen at a point they pick, instead of
    /// racing a wall-clock interval.
    @ObservationIgnored var scanWatchSleep: @Sendable (Duration) async throws -> Void = {
        try await Task.sleep(for: $0)
    }
    /// Bumped as each load or background rebuild starts reading: only the
    /// newest writes spend and activity.
    private var loadGeneration = 0
    /// Bumped by every load and period reload: only the newest writes the summary.
    private var summaryGeneration = 0
    private var inFlight = 0 { didSet { isLoading = inFlight > 0 } }

    /// One computed period, tagged so a stale one is never mistaken for fresh.
    struct CachedSummary {
        var summary: OverviewSummary
        var revision: Int
        /// `startOfDay` of the day it was computed on.
        var day: Date
        var computedAt: Date
    }

    /// Bumped when the ledger's content or pricing changes: a scan that read
    /// anything, or a new price list. Invalidates every cached summary.
    /// Tests read it, and the Sessions page rereads usage when it moves.
    public private(set) var revision = 0
    private var summaryCache: [OverviewPeriod: CachedSummary] = [:]
    /// The background pass that fills every period after a load.
    var warming: Task<Void, Never>?
    /// Which periods currently hold a cached summary; tests read this.
    var cachedPeriods: Set<OverviewPeriod> { Set(summaryCache.keys) }

    /// The cache entry for a period, and whether it is still fresh. Valid
    /// means same revision and same day. Rolling windows (7, 30, 90 and 365
    /// days) slide continuously, so they are only fresh for ten minutes;
    /// calendar-anchored ones stay fresh until a day or revision change.
    private func cacheEntry(for period: OverviewPeriod, now: Date)
        -> (entry: CachedSummary, fresh: Bool)? {
        guard let entry = summaryCache[period],
              entry.revision == revision,
              entry.day == calendar.startOfDay(for: now) else { return nil }
        let rolling: Set<OverviewPeriod> = [.week, .thirtyDays, .ninetyDays, .year]
        let fresh = !rolling.contains(period) || now.timeIntervalSince(entry.computedAt) < 600
        return (entry, fresh)
    }

    /// Stores a summary tagged with the revision it was queried under.
    /// Callers capture `revision` before awaiting the ledger; a query that
    /// began before a scan committed must not tag its pre-scan figures as
    /// post-scan, so the write is dropped when the stamp no longer matches.
    /// Internal for tests.
    func storeCached(_ summary: OverviewSummary, at now: Date, stamped stampedRevision: Int) {
        guard stampedRevision == revision else { return }
        summaryCache[summary.period] = CachedSummary(
            summary: summary, revision: revision,
            day: calendar.startOfDay(for: now), computedAt: now)
    }

    /// - Parameter refreshPricing: a newer price list when one is due, else nil;
    ///   asked before every scan.
    /// - Parameter scanWatchInterval: how often a full scan's progress is
    ///   read while it runs, and the launch scan's partial figures rebuilt
    ///   (every third read). Nil, the default, leaves both off.
    public init(ledger: LedgerQuerying, roots: @escaping @Sendable () async -> LedgerRoots,
                accountLabel: @escaping (String) -> String,
                refreshPricing: @escaping @Sendable () async -> PricingCatalog? = { nil },
                scanWatchInterval: Duration? = nil,
                now: @escaping () -> Date = Date.init, calendar: Calendar = .current) {
        self.ledger = ledger
        self.refreshPricing = refreshPricing
        self.scanWatchInterval = scanWatchInterval
        self.roots = roots
        self.accountLabel = accountLabel
        self.now = now
        self.calendar = calendar
    }

    /// A writer outside the scan path (the Cursor feed's ingest) changed
    /// the ledger: bump the revision so every cached summary is stale,
    /// then rebuild exactly like a scan that read files did.
    public func reloadAfterLedgerWrite() async {
        revision += 1
        ledgerWrites += 1
        await load(scan: false)
    }

    /// The ledger's price list changed (a saved user price, already set
    /// there by the caller): drop every cached summary and rebuild. Costs
    /// are computed at query time, so the new catalog reprices past usage.
    /// Takes no catalog, so a late call can never put back an older one.
    public func pricesChanged() async {
        revision += 1
        await load(scan: false)
    }

    /// Optionally scan the logs for new usage, then rebuild every figure
    /// and warm every period. For launch and the refresh buttons; the
    /// timers use `backgroundScan`, which rebuilds only what changed.
    /// `recentLimit` caps the scan to each source's newest files; nil
    /// scans everything.
    public func load(scan: Bool, recentLimit: Int? = nil) async {
        let fullScan = scan && recentLimit == nil
        if fullScan {
            guard await beginFullScan() else { return }
        }
        defer { if fullScan { endFullScan() } }
        summaryGeneration += 1
        warming?.cancel()
        let mySummary = summaryGeneration
        inFlight += 1
        defer { inFlight -= 1 }
        if scan {
            guard await runScan(recentLimit: recentLimit) != nil else { return }
            completedScans += 1
        }
        // Taken after the scan: of two loads, or a load and a background
        // rebuild, the one that read the ledger last writes the figures.
        loadGeneration += 1
        let mine = loadGeneration

        let now = now()
        let ledger = self.ledger
        let calendar = self.calendar
        let todayStart = calendar.startOfDay(for: now)
        let yesterdayStart = calendar.date(byAdding: .day, value: -1, to: todayStart) ?? todayStart
        let halfYear = Self.halfYear(now: now, calendar: calendar)
        // The same rolling range as the Overview's "30 days", to the minute.
        let thirtyDays = OverviewPeriod.thirtyDays.range(now: now, calendar: calendar)
        // The ledger answers these on read connections of their own, so
        // they run side by side with the period's figures below.
        async let todaySpend = ledger.byHarness(in: DateInterval(start: todayStart, end: now))
        async let yesterdaySpend = ledger.byHarness(in: DateInterval(start: yesterdayStart, end: todayStart))
        async let thirtyDaySpend = ledger.byHarness(in: thirtyDays)
        async let halfYearRead = ledger.daily(in: halfYear, calendar: calendar)
        async let recentRead = ledger.daily(in: thirtyDays, calendar: calendar)

        // The selected period is reused when nothing changed underneath it.
        let summary: OverviewSummary
        if let cached = cacheEntry(for: period, now: now), cached.fresh {
            summary = cached.entry.summary
        } else {
            let stamped = revision
            summary = await summarize(period, now: now)
            if mySummary == summaryGeneration { storeCached(summary, at: now, stamped: stamped) }
        }
        // Shown before the rest arrives: it is what the page waits on.
        if mySummary == summaryGeneration || firstFigures(summary) { self.summary = summary }
        let spend = [
            SpendSlice(period: .today, byHarness: await todaySpend),
            SpendSlice(period: .yesterday, byHarness: await yesterdaySpend),
            SpendSlice(period: .thirtyDays, byHarness: await thirtyDaySpend),
        ]
        let halfYearRows = await halfYearRead
        let activity = Self.combineDays(halfYearRows)
        let recentRows = await recentRead
        let recentModels = Self.models(recentRows)

        guard mine == loadGeneration else { return }
        self.spend = spend
        self.activity = activity
        self.activityRows = halfYearRows
        self.recentRows = recentRows
        self.recentModels = recentModels
        activityStale = false
        figuresDay = calendar.startOfDay(for: now)
        updatedAt = now
        warmCache()
    }

    /// What one scan changed, for the caller to decide what to rebuild.
    private struct ScanOutcome {
        /// The scan read a file or database: the ledger may hold new usage.
        var read = false
        /// A newer price list was set: every past cost may have moved.
        var repriced = false
        var changed: Bool { read || repriced }
    }

    /// Applies a newer price list when one is due, then scans. Nil when
    /// the scan failed or was cancelled; the revision moves then too,
    /// since files may have committed before the throw.
    private func runScan(recentLimit: Int?) async -> ScanOutcome? {
        var outcome = ScanOutcome()
        if let catalog = await refreshPricing() {
            await ledger.setPricing(catalog)
            revision += 1
            outcome.repriced = true
        }
        let watcher = recentLimit == nil ? watchScan() : nil
        if watcher != nil { scanWatchers += 1 }
        defer {
            if let watcher {
                watcher.cancel()
                scanWatchers -= 1
                if scanWatchers == 0 { scanProgress = nil }
            }
        }
        do {
            let report = try await ledger.scan(roots: roots(), recentLimit: recentLimit)
            error = nil
            scanNotes = report.notes
            scansRun += 1
            if recentLimit == nil { fullScanFinished = true }
            if report.filesRead + report.filesRescanned + report.databasesRead > 0 {
                revision += 1
                outcome.read = true
            }
            return outcome
        } catch is CancellationError {
            // Stopped partway: files may have committed before the throw.
            revision += 1
            return nil
        } catch {
            revision += 1
            self.error = "Couldn't read your usage logs: \(error.localizedDescription)"
            return nil
        }
    }

    /// The timers' scan: the search index's reconcile tick (capped by
    /// `recentLimit`) and `keepFresh`'s full sweep. It reads new usage and
    /// rebuilds only what is on show and changed, so an idle tick costs
    /// the scan alone (#1542):
    ///
    /// - Read nothing: no figure query, no `completedScans` bump, no
    ///   warming. After midnight the spend and recent days are rebuilt
    ///   anyway, and a shown rolling period older than ten minutes.
    /// - Read new usage: `completedScans` moves for the other views, and
    ///   the menu bar's spend, the last 30 days (widgets, the heatmap's
    ///   recent days) and, while the page shows, the selected period are
    ///   rebuilt. Other periods and the heatmap's older days wait for the
    ///   page (`refreshForPage`) instead of being warmed every tick.
    ///
    /// Before the first figures exist it is a plain `load`.
    public func backgroundScan(recentLimit: Int? = nil) async {
        guard updatedAt != nil else {
            await load(scan: true, recentLimit: recentLimit)
            return
        }
        let fullScan = recentLimit == nil
        if fullScan {
            guard await beginFullScan() else { return }
        }
        defer { if fullScan { endFullScan() } }
        inFlight += 1
        defer { inFlight -= 1 }
        guard let outcome = await runScan(recentLimit: recentLimit) else { return }
        let now = now()
        let newDay = figuresDay != calendar.startOfDay(for: now)
        if outcome.changed || newDay {
            if outcome.changed { completedScans += 1 }
            await refreshShown(wroteUsage: outcome.read, wholeActivity: outcome.repriced || newDay, now: now)
        } else if pageVisible, cacheEntry(for: period, now: now)?.fresh != true {
            // Nothing new, but a rolling period on show has slid.
            await reloadPeriod()
            updatedAt = now
        } else {
            // The figures shown are still the ledger's, as of now.
            updatedAt = now
        }
    }

    /// The background scan's rebuild: spend, the last 30 days and the
    /// heatmap, plus the selected period while the page shows. The half
    /// year is read whole only when `wholeActivity`; otherwise its last
    /// 30 days are replaced from the recent read and the older days kept.
    private func refreshShown(wroteUsage: Bool, wholeActivity: Bool, now: Date) async {
        loadGeneration += 1
        let mine = loadGeneration
        // Its entries would be stamped with the old revision and dropped.
        warming?.cancel()
        let ledger = self.ledger
        let calendar = self.calendar
        let todayStart = calendar.startOfDay(for: now)
        let yesterdayStart = calendar.date(byAdding: .day, value: -1, to: todayStart) ?? todayStart
        let halfYear = Self.halfYear(now: now, calendar: calendar)
        let thirtyDays = OverviewPeriod.thirtyDays.range(now: now, calendar: calendar)
        async let todaySpend = ledger.byHarness(in: DateInterval(start: todayStart, end: now))
        async let yesterdaySpend = ledger.byHarness(in: DateInterval(start: yesterdayStart, end: todayStart))
        async let thirtyDaySpend = ledger.byHarness(in: thirtyDays)
        async let recentRead = ledger.daily(in: thirtyDays, calendar: calendar)
        async let halfYearRead: [DailyUsage]? = wholeActivity
            ? ledger.daily(in: halfYear, calendar: calendar) : nil

        if pageVisible {
            summaryGeneration += 1
            let mySummary = summaryGeneration
            let summary: OverviewSummary
            if let cached = cacheEntry(for: period, now: now), cached.fresh {
                summary = cached.entry.summary
            } else {
                let stamped = revision
                summary = await summarize(period, now: now)
                if mySummary == summaryGeneration { storeCached(summary, at: now, stamped: stamped) }
            }
            if mySummary == summaryGeneration { self.summary = summary }
        }
        let spend = [
            SpendSlice(period: .today, byHarness: await todaySpend),
            SpendSlice(period: .yesterday, byHarness: await yesterdaySpend),
            SpendSlice(period: .thirtyDays, byHarness: await thirtyDaySpend),
        ]
        let recentRows = await recentRead
        let wholeRows = await halfYearRead

        guard mine == loadGeneration else { return }
        let activityRows = wholeRows ?? Self.merging(
            activityRows, recent: recentRows, recentStart: thirtyDays.start,
            since: halfYear.start, calendar: calendar)
        self.spend = spend
        self.activity = Self.combineDays(activityRows)
        self.activityRows = activityRows
        self.recentRows = recentRows
        self.recentModels = Self.models(recentRows)
        if wholeRows != nil {
            activityStale = false
        } else if wroteUsage {
            activityStale = true
        }
        figuresDay = calendar.startOfDay(for: now)
        updatedAt = now
    }

    /// The page came on screen or its period changed: the selected period
    /// (from the cache while it is still valid), the heatmap's older days
    /// if background scans refreshed only its recent ones, and the other
    /// periods warmed if a scan moved the ledger since they were cached.
    public func refreshForPage() async {
        let now = now()
        // Until the first load lands, its own (and the launch scan's
        // partial) figures are on their way; only a period switch asks.
        if summary?.period != period
            || (updatedAt != nil && cacheEntry(for: period, now: now)?.fresh != true) {
            await reloadPeriod()
        }
        if activityStale { await reloadActivity() }
        guard updatedAt != nil,
              OverviewPeriod.allCases.contains(where: { cacheEntry(for: $0, now: now) == nil })
        else { return }
        warmCache()
    }

    /// Reads the heatmap's half year whole again.
    private func reloadActivity() async {
        let mine = loadGeneration
        let rows = await ledger.daily(in: Self.halfYear(now: now(), calendar: calendar), calendar: calendar)
        // A newer load or background rebuild wrote the rows meanwhile.
        guard mine == loadGeneration else { return }
        activityRows = rows
        activity = Self.combineDays(rows)
        activityStale = false
    }

    /// The heatmap's span: the last 182 days and today.
    nonisolated static func halfYear(now: Date, calendar: Calendar) -> DateInterval {
        let todayStart = calendar.startOfDay(for: now)
        return DateInterval(start: calendar.date(byAdding: .day, value: -182, to: todayStart) ?? todayStart,
                            end: now)
    }

    /// `rows` with every day `recent` covers whole replaced by its fresh
    /// rows, and the days before `since` dropped. `recent` starts at
    /// `recentStart`, mid-day for a rolling range, so its first day is
    /// partial and the older rows keep it.
    nonisolated static func merging(_ rows: [DailyUsage], recent: [DailyUsage], recentStart: Date,
                                    since start: Date, calendar: Calendar) -> [DailyUsage] {
        let firstWhole = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: recentStart))
            ?? recentStart
        return rows.filter { $0.day >= start && $0.day < firstWhole }
            + recent.filter { $0.day >= firstWhole }
    }

    /// Full-scan loads run one at a time. Two at once (the timer's and the
    /// search index's, both at launch) interleave on the ledger and each
    /// starves the other, so the progress count stalls.
    private var fullScanRunning = false
    private var fullScanWaiters: [CheckedContinuation<Void, Never>] = []
    private var fullScansStarted = 0
    /// Full-scan loads waiting behind the running one, so a test can tell
    /// a load queued without waiting and hoping.
    var queuedFullScans: Int { fullScanWaiters.count }

    /// Waits for the running full-scan load, if any. False when another
    /// full scan started after this one asked and has finished: it read
    /// everything this one would, so there is nothing left to do.
    private func beginFullScan() async -> Bool {
        let asked = fullScansStarted
        while fullScanRunning {
            await withCheckedContinuation { fullScanWaiters.append($0) }
        }
        guard fullScansStarted == asked else { return false }
        fullScanRunning = true
        fullScansStarted += 1
        return true
    }

    /// Wakes every waiter, not just the next: each checks again, so one
    /// runs, the ones it covers return, and the rest wait for it. Waking
    /// one alone left the load behind a covered one asleep for good.
    private func endFullScan() {
        fullScanRunning = false
        let waiters = fullScanWaiters
        fullScanWaiters = []
        for waiter in waiters { waiter.resume() }
    }

    /// Full-scan loads currently watching progress; the last to finish
    /// clears it. Tests read it to know no watcher is running.
    private(set) var scanWatchers = 0

    /// While a load's full scan runs: reads its progress every
    /// `scanWatchInterval` (the first read after one interval, so a quick
    /// scan never shows any), and until the first scan has completed,
    /// rebuilds the selected period from what has committed so far on the
    /// third read that finds the scan running and every third after it.
    /// The ledger commits per file, so those figures only grow; they are
    /// never cached, and the scan's own rebuild replaces them.
    ///
    /// Not sooner: the rebuild's queries share the ledger with the scan and
    /// take seconds, so one on the first read froze a launch scan that
    /// otherwise skips its unchanged files in about a second.
    private func watchScan() -> Task<Void, Never>? {
        guard let interval = scanWatchInterval else { return nil }
        let sleep = scanWatchSleep
        return Task { [weak self] in
            var reads = 0
            while !Task.isCancelled {
                do { try await sleep(interval) } catch { return }
                guard let self else { return }
                let progress = await self.ledger.scanProgress()
                guard !Task.isCancelled else { return }
                self.scanProgress = progress
                guard progress != nil else { continue }
                reads += 1
                guard !self.fullScanFinished, reads % 3 == 0 else { continue }
                let generation = self.summaryGeneration
                let partial = await self.summarize(self.period, now: self.now())
                guard !Task.isCancelled, generation == self.summaryGeneration else { continue }
                self.summary = partial
            }
        }
    }

    /// Fills the per-period cache in the background so a range switch later
    /// is instant: the selected period first (usually already stored), then
    /// the rest in picker order, skipping entries still valid and fresh.
    private func warmCache() {
        warming?.cancel()
        warming = Task(priority: .utility) { [weak self] in
            guard let self else { return }
            let warmNow = self.now()
            let order = [self.period] + OverviewPeriod.allCases.filter { $0 != self.period }
            for period in order {
                guard !Task.isCancelled else { return }
                if let cached = self.cacheEntry(for: period, now: warmNow), cached.fresh { continue }
                let stamped = self.revision
                let summary = await self.summarize(period, now: warmNow)
                guard !Task.isCancelled else { return }
                self.storeCached(summary, at: warmNow, stamped: stamped)
                await Task.yield()
            }
        }
    }

    /// Rebuild only the period's figures, after the picker changes. The menu
    /// bar's spend and the heatmap don't depend on the period. A cached entry
    /// is shown at once; a stale one is then recomputed behind it.
    public func reloadPeriod() async {
        summaryGeneration += 1
        let mine = summaryGeneration
        let now = now()
        if let cached = cacheEntry(for: period, now: now) {
            self.summary = cached.entry.summary
            if cached.fresh { return }
        }
        inFlight += 1
        defer { inFlight -= 1 }
        let stamped = revision
        let summary = await summarize(period, now: now)
        if mine == summaryGeneration {
            storeCached(summary, at: now, stamped: stamped)
        } else if !firstFigures(summary) {
            return
        }
        self.summary = summary
    }

    /// The page still shows no figures and `summary` is for the period it
    /// wants. A newer request superseding the one that built it would
    /// otherwise throw it away, and at launch the page's own reload and a
    /// queued scan can each supersede the one before, holding the figures
    /// back until the last of them finishes.
    private func firstFigures(_ summary: OverviewSummary) -> Bool {
        self.summary == nil && summary.period == period
    }

    private func summarize(_ period: OverviewPeriod, now: Date) async -> OverviewSummary {
        let range = period.range(now: now, calendar: calendar)
        let ledger = self.ledger
        let calendar = self.calendar
        // Side by side: the ledger reads each on its own connection.
        async let harnessRead = ledger.byHarness(in: range)
        async let accountRead = ledger.byAccount(in: range)
        async let accountHarnessRead = ledger.harnessByAccount(in: range)
        async let dailyRead = ledger.daily(in: range, calendar: calendar)
        let harnesses = await harnessRead
        let accounts = await accountRead
        let accountHarnesses = await accountHarnessRead
        let daily = await dailyRead
        var total = UsageTotals()
        for value in harnesses.values { total.add(value) }
        let grand = Double(max(total.totalTokens, 1))
        var modelsByHarness: [String: Set<String>] = [:]
        for row in daily { modelsByHarness[row.harness, default: []].insert(row.model) }
        return OverviewSummary(
            period: period,
            range: range,
            totals: total,
            harnesses: harnesses
                .map { HarnessShare(harness: $0.key, totals: $0.value, share: Double($0.value.totalTokens) / grand,
                                    modelCount: modelsByHarness[$0.key]?.count ?? 0) }
                .sorted { ($0.totals.totalTokens, $1.harness) > ($1.totals.totalTokens, $0.harness) },
            days: Self.combineDays(daily).sorted { $0.day > $1.day },
            accounts: accounts
                .map { AccountRow(home: $0.key, label: accountLabel($0.key), totals: $0.value,
                                  harness: accountHarnesses[$0.key]) }
                .sorted { $0.totals.costUSD > $1.totals.costUSD },
            models: Self.models(daily))
    }

    /// Each model's share of the rows' tokens, largest first, with the
    /// harnesses that ran it, most tokens first.
    nonisolated static func models(_ rows: [DailyUsage]) -> [ModelShare] {
        var byModel: [String: UsageTotals] = [:]
        var harnessTokens: [String: [String: Int]] = [:]
        for row in rows {
            byModel[row.model, default: UsageTotals()].add(row.totals)
            harnessTokens[row.model, default: [:]][row.harness, default: 0] += row.totals.totalTokens
        }
        let grand = Double(max(byModel.values.reduce(0) { $0 + $1.totalTokens }, 1))
        return byModel
            .map { model, totals in
                var share = ModelShare(model: model, totals: totals,
                                       share: Double(totals.totalTokens) / grand)
                share.harnesses = (harnessTokens[model] ?? [:])
                    .sorted { ($0.value, $1.key) > ($1.value, $0.key) }
                    .map(\.key)
                return share
            }
            .sorted { ($0.totals.totalTokens, $1.model) > ($1.totals.totalTokens, $0.model) }
    }

    /// Load once, then scan for new usage on a timer until cancelled,
    /// rebuilding only what each scan changed (`backgroundScan`).
    public func keepFresh(every interval: Duration = .seconds(300)) async {
        guard !Task.isCancelled else { return }
        await load(scan: true)
        while !Task.isCancelled {
            do { try await Task.sleep(for: interval) } catch { return }
            await backgroundScan()
        }
    }

    static func combineDays(_ rows: [DailyUsage]) -> [DayRow] {
        var byDay: [Date: UsageTotals] = [:]
        for row in rows { byDay[row.day, default: UsageTotals()].add(row.totals) }
        return byDay.map { DayRow(day: $0.key, totals: $0.value) }.sorted { $0.day < $1.day }
    }
}
