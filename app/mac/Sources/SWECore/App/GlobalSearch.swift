import Foundation

/// One row in the ⌘K global search card: a place in the app or a thing on
/// it that Return or a click jumps to. The card rebuilds the rows each
/// time it opens from the live page models; `GlobalSearch` does the
/// matching and ranking.
public struct GlobalSearchItem: Identifiable, Hashable, Sendable {
    /// The chip the row's trailing edge shows.
    public enum Kind: String, CaseIterable, Sendable {
        case page, tab, session, harness, repo, task, skill, plugin, server, hook, worktree

        public var title: String {
            switch self {
            case .page: "Page"
            case .tab: "Tab"
            case .session: "Session"
            case .harness: "Harness"
            case .repo: "Repo"
            case .task: "Task"
            case .skill: "Skill"
            case .plugin: "Plugin"
            case .server: "Server"
            case .hook: "Hook"
            case .worktree: "Worktree"
            }
        }

        /// Equal-score rows sort in this order: places before things.
        var rank: Int {
            switch self {
            case .page: 0
            case .tab: 1
            case .session: 2
            case .harness: 3
            case .repo: 4
            case .task: 5
            case .skill: 6
            case .plugin: 7
            case .server: 8
            case .hook: 9
            case .worktree: 10
            }
        }
    }

    /// Where the row goes; `AppModel.routeSearchTarget` applies it.
    public enum Target: Hashable, Sendable {
        /// A sidebar page, hidden in the sidebar or not.
        case page(AppSection)
        /// Settings' sub-nav pick (`settingsSection`).
        case settings(SettingsSection)
        /// Settings › Providers' tab by its rawValue (`providersTab`).
        case providersTab(String)
        case pluginsTab(PluginsTab)
        /// The Menu bar page's tab by its rawValue (`menuBarPageTab`).
        case menuBarTab(String)
        /// A widget kind on the Widgets page (`desktopWidgetTab`).
        case widgetTab(String)
        /// A Repositories dashboard tab. The one-repo tabs stay out: they
        /// mean nothing without a repo chosen.
        case repoTab(RepoTab)
        /// A session, opened like a row click on Sessions. `key` is the
        /// hub's `harness:id`, `rowId` the list row's id — the same
        /// session under two accounts is two rows, so the lookup needs it.
        case session(key: String, rowId: String)
        /// The Harnesses page filtered to the harness's registry id.
        case harness(id: String)
        /// A pinned repo's board.
        case repo(name: String)
        /// A task's detail on the Tasks page.
        case task(id: String)
        /// The Skills page filtered to the skill's name.
        case skill(name: String)
        /// The Plugins page's Explore tab filtered to the plugin's name.
        case plugin(name: String)
        /// The MCP servers page.
        case server(name: String)
        /// A hook's `harness|source` row key and its harness tab pick.
        case hook(key: String, harness: String)
        /// A worktree's detail on the Worktrees page, by its path.
        case worktree(path: String)
    }

    public var id: String
    public var kind: Kind
    public var title: String
    /// The quieter second line: where the row lives or what it belongs to.
    public var detail: String?
    /// A registry harness id whose logo leads the row.
    public var harnessIcon: String?
    /// The SF Symbol leading the row when `harnessIcon` is nil.
    public var symbol: String?
    /// Extra text the needle also matches: ids, paths, aliases.
    public var keywords: [String]
    public var target: Target

    public init(kind: Kind, title: String, detail: String? = nil,
                harnessIcon: String? = nil, symbol: String? = nil,
                keywords: [String] = [], target: Target) {
        self.id = "\(kind.rawValue)|\(title)|\(detail ?? "")|\(keywords.first ?? "")"
        self.kind = kind
        self.title = title
        self.detail = detail
        self.harnessIcon = harnessIcon
        self.symbol = symbol
        self.keywords = keywords
        self.target = target
    }
}

/// Matching for the ⌘K card: every whitespace token must hit somewhere,
/// and the better the hit, the higher the row.
public enum GlobalSearch {
    /// The rows an empty field offers: the navigation targets, so the
    /// card reads as a jump list before a needle is typed.
    public static func browse(_ items: [GlobalSearchItem], limit: Int = 20) -> [GlobalSearchItem] {
        Array(items.filter { $0.kind == .page || $0.kind == .tab }.prefix(limit))
    }

    /// `items` matching every token of `needle`, best first, at most
    /// `limit`. A token must hit the title, the detail or a keyword; a
    /// title prefix beats a title substring, which beats the detail,
    /// which beats a keyword-only hit. Kind rank then title order break
    /// ties. An empty or all-space needle matches nothing (see `browse`).
    public static func matches(_ needle: String, items: [GlobalSearchItem],
                               limit: Int = 50) -> [GlobalSearchItem] {
        let tokens = needle.lowercased().split { $0.isWhitespace }.map(String.init)
        guard !tokens.isEmpty else { return [] }
        var scored: [(score: Int, item: GlobalSearchItem)] = []
        scored.reserveCapacity(min(items.count, 1024))
        for item in items {
            var score = 0
            var failed = false
            for token in tokens {
                let tier = Self.tier(token, in: item)
                if tier == 0 { failed = true; break }
                score += tier
            }
            if !failed { scored.append((score, item)) }
        }
        scored.sort { a, b in
            if a.score != b.score { return a.score > b.score }
            if a.item.kind.rank != b.item.kind.rank { return a.item.kind.rank < b.item.kind.rank }
            return a.item.title.localizedCaseInsensitiveCompare(b.item.title) == .orderedAscending
        }
        return scored.prefix(limit).map(\.item)
    }

    /// A token's best hit on the item: 4 a title prefix, 3 inside the
    /// title, 2 the detail line, 1 a keyword, 0 nowhere.
    static func tier(_ token: String, in item: GlobalSearchItem) -> Int {
        let title = item.title.lowercased()
        if title.hasPrefix(token) { return 4 }
        if title.contains(token) { return 3 }
        if item.detail?.lowercased().contains(token) == true { return 2 }
        if item.keywords.contains(where: { $0.lowercased().contains(token) }) { return 1 }
        return 0
    }
}
