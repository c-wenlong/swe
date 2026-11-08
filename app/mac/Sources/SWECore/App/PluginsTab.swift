/// The Plugins page's tab, so another page can open one by name. AppModel
/// keeps the pick; the page and the ⌘K search both read it.
public enum PluginsTab: String, CaseIterable, Hashable, Sendable {
    /// Every plugin in every marketplace, with install, uninstall, enable
    /// and disable per harness.
    case explore
    /// Where the copies Claude Code and Codex run no longer match
    /// `~/.swe/plugins`, each with the fix swe would run.
    case health

    public var title: String {
        switch self {
        case .explore: "Explore"
        case .health: "Health"
        }
    }
}
