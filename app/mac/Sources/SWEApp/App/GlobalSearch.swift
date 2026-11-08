import Foundation
import SWECore

/// The ⌘K global search's rows and routes. `searchItems()` reads the live
/// page models — every list the app already holds in memory, nothing read
/// from disk — once per open of the card.
extension AppModel {
    /// Every row the ⌘K card can land on: navigation first (sidebar
    /// order, then the pages' own tabs), then the data pages' contents.
    func searchItems() -> [GlobalSearchItem] {
        var items: [GlobalSearchItem] = []
        items += searchNavigationItems()
        items += searchEntityItems()
        return items
    }

    /// The pages and their own tabs. A page hidden in the sidebar still
    /// lands — it is the ⌘K search that brings you to it.
    private func searchNavigationItems() -> [GlobalSearchItem] {
        var items: [GlobalSearchItem] = SidebarGroup.order.map { section in
            GlobalSearchItem(kind: .page, title: section.title,
                             detail: section.group.title + (sidebar.isHidden(section) ? " · hidden" : ""),
                             symbol: section.symbol,
                             keywords: [section.rawValue],
                             target: .page(section))
        }
        for section in SettingsSection.allCases {
            items.append(GlobalSearchItem(kind: .tab, title: section.title,
                                          detail: "Settings", symbol: section.symbol,
                                          keywords: [section.rawValue],
                                          target: .settings(section)))
        }
        for tab in ProvidersSection.Tab.allCases {
            items.append(GlobalSearchItem(kind: .tab, title: tab.title,
                                          detail: "Settings › Providers", symbol: "square.stack.3d.up",
                                          keywords: [tab.rawValue],
                                          target: .providersTab(tab.rawValue)))
        }
        for tab in PluginsTab.allCases {
            items.append(GlobalSearchItem(kind: .tab, title: tab.title,
                                          detail: "Plugins", symbol: "puzzlepiece.extension",
                                          keywords: [tab.rawValue],
                                          target: .pluginsTab(tab)))
        }
        for tab in MenuBarPage.Tab.allCases {
            items.append(GlobalSearchItem(kind: .tab, title: tab.title,
                                          detail: "Menu bar", symbol: "menubar.rectangle",
                                          keywords: [tab.rawValue],
                                          target: .menuBarTab(tab.rawValue)))
        }
        for widget in WidgetCatalog.all {
            items.append(GlobalSearchItem(kind: .tab, title: widget.title,
                                          detail: "Widgets", symbol: widget.symbol,
                                          keywords: [widget.kind],
                                          target: .widgetTab(widget.kind)))
        }
        for tab in GitHubModel.tabs(oneRepo: false) {
            items.append(GlobalSearchItem(kind: .tab, title: tab.title,
                                          detail: "Repositories", symbol: "shippingbox",
                                          keywords: [tab.rawValue],
                                          target: .repoTab(tab)))
        }
        return items
    }

    /// The data pages' rows: sessions, harnesses, pins, tasks, and the
    /// four primitives' lists. A model that has not loaded yet simply
    /// contributes nothing.
    private func searchEntityItems() -> [GlobalSearchItem] {
        var items: [GlobalSearchItem] = []
        if let sessions {
            for row in sessions.rows {
                let harness = row.session.harness
                let project = sessions.project(for: row.session)
                let title = sessions.hubOverlay.title(for: row)
                    ?? row.session.title ?? project ?? "Untitled session"
                let detail = [HarnessMark(harness).name, project, row.account]
                    .compactMap { $0 }.joined(separator: " · ")
                items.append(GlobalSearchItem(
                    kind: .session, title: title,
                    detail: detail.isEmpty ? nil : detail,
                    harnessIcon: harness,
                    keywords: [row.session.id, row.session.agent,
                               row.session.cwd ?? "", row.session.title ?? "",
                               HarnessMark(harness).name],
                    target: .session(key: HubSessionRow.key(harness: harness, id: row.session.id))))
            }
        }
        var seen: Set<String> = []
        for id in ProviderCatalog.all.map(\.id) + (sessions?.harnesses ?? [])
        where seen.insert(id).inserted {
            let name = HarnessMark(id).name
            items.append(GlobalSearchItem(kind: .harness, title: name,
                                          harnessIcon: id, keywords: [id],
                                          target: .harness(id: id, query: name)))
        }
        for pin in github.pins {
            items.append(GlobalSearchItem(kind: .repo, title: pin.name,
                                          symbol: "shippingbox",
                                          target: .repo(name: pin.name)))
        }
        for task in tasks.tasks {
            let detail = [task.refLabel, TaskGrouping.sectionName(task)]
                .filter { !$0.isEmpty }.joined(separator: " · ")
            items.append(GlobalSearchItem(kind: .task,
                                          title: task.title ?? task.refLabel,
                                          detail: detail.isEmpty ? nil : detail,
                                          symbol: "checklist",
                                          keywords: [task.id, task.ref ?? "",
                                                     task.repo ?? "", task.project ?? ""],
                                          target: .task(id: task.id)))
        }
        let skillList = skills.placement?.standalone
            ?? (skills.inventory.loaded?.skills.filter { $0.source != .plugin } ?? [])
        for skill in skillList {
            items.append(GlobalSearchItem(kind: .skill, title: skill.name,
                                          detail: skill.description.isEmpty ? skill.source.title : skill.description,
                                          symbol: "sparkles",
                                          keywords: skill.harnesses + [skill.path],
                                          target: .skill(name: skill.name)))
        }
        if let catalog = pluginCatalog.state.loaded {
            for plugin in catalog.plugins {
                items.append(GlobalSearchItem(kind: .plugin,
                                              title: plugin.displayName ?? plugin.name,
                                              detail: plugin.marketplace,
                                              symbol: "puzzlepiece.extension",
                                              keywords: [plugin.name, plugin.ref,
                                                         plugin.description ?? ""],
                                              target: .plugin(name: plugin.name)))
            }
        }
        if let inventory = mcp.state.loaded {
            for server in inventory.servers {
                items.append(GlobalSearchItem(kind: .server, title: server.name,
                                              detail: server.isRemote ? "Remote" : "Local",
                                              symbol: "powerplug",
                                              keywords: server.harnesses
                                                  + [server.command ?? "", server.url ?? ""],
                                              target: .server(name: server.name)))
            }
        }
        for hook in hooks.hooks {
            items.append(GlobalSearchItem(kind: .hook, title: hook.script,
                                          detail: hook.harness == "shared"
                                              ? "Shared" : HarnessMark(hook.harness).name,
                                          symbol: "bolt",
                                          keywords: [hook.harness, hook.summary],
                                          target: .hook(key: "\(hook.harness)|\(hook.id)",
                                                        harness: hook.harness)))
        }
        return items
    }

    /// One pick's route. Runs once the window is uncovered: `selection`
    /// stands down under a card, so `open(_:)` holds this until the card
    /// is gone.
    func routeSearchTarget(_ target: GlobalSearchItem.Target) {
        switch target {
        case .page(let section):
            select(section)
        case .settings(let section):
            UserDefaults.standard.set(section.rawValue, forKey: "settingsSection")
            select(.settings)
        case .providersTab(let raw):
            UserDefaults.standard.set(SettingsSection.providers.rawValue, forKey: "settingsSection")
            UserDefaults.standard.set(raw, forKey: "providersTab")
            select(.settings)
        case .pluginsTab(let tab):
            pluginsTab = tab
            select(.plugins)
        case .menuBarTab(let raw):
            UserDefaults.standard.set(raw, forKey: "menuBarPageTab")
            select(.menuBar)
        case .widgetTab(let kind):
            UserDefaults.standard.set(kind, forKey: "desktopWidgetTab")
            select(.widgets)
        case .repoTab(let tab):
            // Only the all-repos dashboard takes these tabs; a pick there
            // also shows every repo again.
            github.shownRepo = nil
            github.tab = tab
            select(.repositories)
        case .session(let key):
            pendingSession = key
            select(.sessions)
        case .harness(_, let query):
            pendingHarnessQuery = query
            select(.harnesses)
        case .repo(let name):
            openGitHub(repo: name, type: "PullRequest")
        case .task(let id):
            pendingTask = id
            select(.tasks)
        case .skill(let name):
            skills.query = name
            select(.skills)
        case .plugin(let name):
            pluginsTab = .explore
            pluginCatalog.query = name
            select(.plugins)
        case .server:
            select(.mcpServers)
        case .hook(let key, let harness):
            pendingHook = key
            UserDefaults.standard.set(harness, forKey: "hooksHarness")
            select(.hooks)
        }
    }
}

private extension Loadable {
    /// The loaded value, nil in every other state.
    var loaded: Value? {
        guard case .loaded(let value) = self else { return nil }
        return value
    }
}
