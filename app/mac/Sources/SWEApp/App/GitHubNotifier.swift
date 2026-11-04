import Foundation
import Observation
import SWECore
import UserNotifications

/// The GitHub control plane: a background inbox poll that survives page
/// switches and closed windows, turning new notifications into macOS
/// banners and repo-board invalidations. The Inbox page's own poll loop
/// is unchanged — it reads whatever this fetched last.
///
/// Delivery state is the set of thread updates already bannered (a
/// thread's id plus its `updated_at`), persisted like the limit alerts'
/// state file so a relaunch doesn't repeat them. The first poll ever only
/// seeds the set — history is not news.
@MainActor
@Observable
final class GitHubNotifier {
    /// Off until switched on in Settings, and switched off again when the
    /// Inbox page is hidden. Off means banners stop; polling still runs so
    /// the inbox, badge and board invalidations stay live.
    var bannersEnabled: Bool {
        didSet {
            defaults.set(bannersEnabled, forKey: "githubNotifications")
            if bannersEnabled != oldValue { napping?.cancel() }
        }
    }

    /// Whether the main window can be seen; AppModel follows it. Coming
    /// back on screen wakes the poll, which looks at once when its last
    /// look is older than `interval`.
    @ObservationIgnored var windowVisible = false {
        didSet { if windowVisible != oldValue { napping?.cancel() } }
    }

    /// The cadence with the window off screen: banners are the only thing
    /// left to serve, a few minutes late at most.
    static let hiddenInterval: TimeInterval = 180
    /// Off screen with banners off: the inbox only has to be roughly
    /// current for the next time the window opens.
    static let idleInterval: TimeInterval = 900

    /// How long the poll waits now: `interval` on screen, then the two
    /// slower paces.
    static func cadence(base: TimeInterval, windowVisible: Bool, banners: Bool) -> TimeInterval {
        if windowVisible { return base }
        return max(base, banners ? hiddenInterval : idleInterval)
    }

    @ObservationIgnored private var napping: Task<Void, Never>?
    /// Whether the loop sits between polls; tests wait on it.
    var isNapping: Bool { napping != nil }
    /// The wall clock; tests set a fake.
    @ObservationIgnored var now: () -> Date = Date.init

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let model: GitHubModel
    @ObservationIgnored private let stateFile: URL
    @ObservationIgnored private let interval: TimeInterval
    /// Membership check and eviction order (oldest first): a Set's order
    /// is arbitrary, so eviction must not blindly drop ids a re-poll
    /// could still surface.
    @ObservationIgnored private var delivered: Set<String>
    @ObservationIgnored private var deliveredOrder: [String] = []
    /// Persisted, not derived: the first completed poll seeds the set,
    /// so anything that arrived while the app was closed still banners.
    @ObservationIgnored private var primed: Bool
    /// The state file predates `id@updated_at` keys; the next poll migrates.
    @ObservationIgnored private var hasBareIds: Bool
    /// Sends the batch; `postToSystem` in the app, a capture in tests.
    @ObservationIgnored private let post: ([GhNotification]) async -> Void

    /// Same bound as the inbox fetch: the delivered set can never outgrow
    /// what a few polls of the endpoint could re-see.
    private static let deliveredCap = 500

    init(swe: URL, model: GitHubModel, interval: TimeInterval = 60,
         defaults: UserDefaults = .standard,
         post: (([GhNotification]) async -> Void)? = nil) {
        self.defaults = defaults
        self.model = model
        self.interval = interval
        self.post = post ?? { notes in await Self.postToSystem(notes) }
        bannersEnabled = defaults.object(forKey: "githubNotifications") as? Bool ?? false
        stateFile = swe.appending(path: "cache/github-notified.json")
        let saved = (try? Data(contentsOf: stateFile))
            .flatMap { try? JSONDecoder().decode(State.self, from: $0) }
        deliveredOrder = saved?.delivered ?? []
        delivered = Set(deliveredOrder)
        // Pre-primed files (v0.1 wrote ids only): non-empty means a seed
        // already ran.
        primed = saved?.primed ?? !delivered.isEmpty
        hasBareIds = deliveredOrder.contains { !$0.contains("@") }
    }

    /// The AppModel watcher loop: poll, sleep, repeat until the app
    /// tears it down. `swe` is read per poll so a path change in
    /// Settings needs no restart. The wait follows `cadence`: once a
    /// minute while the window shows, slower behind it. A change of
    /// window or banners cuts the nap short and the wait is worked out
    /// again from the last look.
    func keepFresh(swe: @escaping () -> SweClient?) async {
        var last: Date?
        while !Task.isCancelled {
            if let last {
                let wait = Self.cadence(base: interval, windowVisible: windowVisible,
                                        banners: bannersEnabled)
                let left = last.addingTimeInterval(wait).timeIntervalSince(now())
                if left > 0 {
                    await nap(left)
                    continue
                }
            }
            await poll(swe: swe())
            last = now()
        }
    }

    /// Sleeps `seconds`, or less when `napping` is cancelled by a wake.
    private func nap(_ seconds: TimeInterval) async {
        let sleeper = Task<Void, Never> { try? await Task.sleep(for: .seconds(seconds)) }
        napping = sleeper
        await withTaskCancellationHandler {
            await sleeper.value
        } onCancel: {
            sleeper.cancel()
        }
        if napping == sleeper { napping = nil }
    }

    /// One tick: refetch the inbox, banner ids not seen before, and mark
    /// each notified repo's boards stale so the next look refetches —
    /// the row updates from the same poll that produced the banner.
    /// Returns the board refreshes it started, so a test can wait for them.
    @discardableResult
    func poll(swe: SweClient?) async -> [Task<Void, Never>] {
        guard let swe else { return [] }
        await model.loadInbox(swe: swe, force: true)
        guard case .loaded(let notes) = model.inbox else { return [] }

        if !primed {
            primed = true
            for note in notes { record(note) }
            save()
            return []
        }

        // Files written before keys carried `updated_at` hold bare ids. A
        // thread still in the inbox, read or not, counts as seen at its
        // current update, so upgrading doesn't banner every unread thread
        // again; the bare ids left over are threads the inbox no longer
        // shows, dropped so their next update banners rather than migrates.
        var migrated = false
        if hasBareIds {
            for note in notes where delivered.contains(note.id) { record(note) }
            deliveredOrder.removeAll { !$0.contains("@") }
            delivered = Set(deliveredOrder)
            hasBareIds = false
            migrated = true
        }

        let fresh = notes.filter { $0.unread && !delivered.contains(Self.key($0)) }
        var refreshes: [Task<Void, Never>] = []
        for note in fresh {
            record(note)
            refreshes += model.invalidate(repo: note.repo, swe: swe)
        }
        if !fresh.isEmpty, bannersEnabled { await post(fresh) }
        if !fresh.isEmpty || migrated {
            trim()
            save()
        }
        return refreshes
    }

    /// What a banner is owed for: one update of one thread. GitHub keeps a
    /// thread's id when new activity lands and only moves `updated_at` and
    /// marks it unread again, so the id alone would banner a PR once ever.
    static func key(_ note: GhNotification) -> String {
        "\(note.id)@\(Int(note.updatedAt.date?.timeIntervalSince1970 ?? 0))"
    }

    /// Keeps `note`'s key as its thread's only one: a busy thread's old
    /// updates must not fill the cap and evict a quiet thread's key, which
    /// would banner that thread again on the next unchanged poll. Newest
    /// keys go last; eviction drops from the front.
    private func record(_ note: GhNotification) {
        let key = Self.key(note)
        guard delivered.insert(key).inserted else { return }
        let thread = note.id + "@"
        deliveredOrder.removeAll { entry in
            let older = entry.hasPrefix(thread)
            if older { delivered.remove(entry) }
            return older
        }
        deliveredOrder.append(key)
    }

    private func trim() {
        while deliveredOrder.count > Self.deliveredCap {
            delivered.remove(deliveredOrder.removeFirst())
        }
    }

    /// One banner per notification while there are few; a burst (waking
    /// to a backlog) collapses into a single summary instead of stacking.
    private static func postToSystem(_ fresh: [GhNotification]) async {
        let center = UNUserNotificationCenter.current()
        guard (try? await center.requestAuthorization(options: [.alert, .sound])) == true else { return }
        if fresh.count <= 3 {
            for note in fresh {
                let content = UNMutableNotificationContent()
                content.title = note.repo
                content.subtitle = Self.reasonTitle(note.reason)
                content.body = note.title
                content.sound = .default
                content.threadIdentifier = "gh-\(note.repo)"
                content.userInfo = ["gh": true, "repo": note.repo, "type": note.type]
                // One identifier per GitHub thread: a re-poll of an
                // updated thread replaces its banner, never stacks.
                let request = UNNotificationRequest(
                    identifier: "gh-\(note.id)", content: content, trigger: nil)
                try? await center.add(request)
            }
        } else {
            let content = UNMutableNotificationContent()
            content.title = "GitHub"
            let repos = fresh.map(\.repo).uniqued()
            content.body = "\(fresh.count) new notifications in \(repos.joined(separator: ", "))"
            content.sound = .default
            content.threadIdentifier = "gh-inbox"
            // No repo: the click lands on the Inbox.
            content.userInfo = ["gh": true]
            let request = UNNotificationRequest(
                identifier: "gh-burst", content: content, trigger: nil)
            try? await center.add(request)
        }
    }

    private static func reasonTitle(_ reason: String) -> String {
        switch reason {
        case "review_requested": return "Review requested"
        case "mention": return "Mentioned you"
        case "team_mention": return "Team mentioned"
        case "assign": return "Assigned to you"
        case "ci_activity": return "CI activity"
        case "author": return "Author activity"
        case "comment": return "New comment"
        case "state_change": return "State changed"
        default:
            return reason.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func save() {
        do {
            try FileManager.default.createDirectory(
                at: stateFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(State(delivered: deliveredOrder, primed: primed))
                .write(to: stateFile, options: .atomic)
        } catch {
            // Worst case a relaunch repeats a notification.
        }
    }

    private struct State: Codable {
        /// Insertion order, oldest first — `delivered`'s eviction order.
        var delivered: [String]
        /// The first completed poll ran. Files written before this field
        /// existed decode it as `!delivered.isEmpty`.
        var primed: Bool

        init(delivered: [String], primed: Bool) {
            self.delivered = delivered
            self.primed = primed
        }

        init(from decoder: any Decoder) throws {
            let box = try decoder.container(keyedBy: CodingKeys.self)
            delivered = try box.decodeIfPresent([String].self, forKey: .delivered) ?? []
            primed = try box.decodeIfPresent(Bool.self, forKey: .primed) ?? !delivered.isEmpty
        }
    }
}

private extension Array where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
