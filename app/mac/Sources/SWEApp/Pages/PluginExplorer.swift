import AppKit
import SWECore
import SwiftUI

/// The Plugins page's Explore tab: every plugin in every marketplace,
/// installed or not, with its state in each harness and the skills it
/// ships. Actions go through `swe plugins`, each after a confirmation.
struct PluginExplorer: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: PluginCatalogModel
    var openHealth: () -> Void
    var addMarketplace: () -> Void = {}
    var confirm: (PluginAction) -> Void
    /// A master or column switch's set of actions, confirmed as one batch.
    var confirmBatch: ([PluginAction]) -> Void = { _ in }
    /// The plugin open in its detail page, nil while the list shows.
    @State private var opened: ExplorePlugin?
    /// A marketplace being renamed (display name only) when editable.
    @State private var renaming: String?
    @State private var renameText = ""
    @State private var renameError: String?
    private var home: URL { FileManager.default.homeDirectoryForCurrentUser }

    var body: some View {
        // Outside the switch: when the reload after a failed action also
        // fails, the reason the action failed must stay on screen.
        if let error = model.actionError ?? renameError {
            ErrorCard(message: error)
        } else if let done = model.lastResult {
            Card {
                Label(done, systemImage: "checkmark.circle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            }
        }
        switch model.state {
        case .idle, .loading:
            SkeletonRows(rows: 6, shape: .plugin, label: "Reading every marketplace…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded:
            if let opened, let index = model.index {
                // A reload swaps the index wholesale, so the opened plugin
                // is re-derived by ref — like SkillsPage follows its row —
                // or the detail keeps showing a version the catalog no
                // longer has. A ref the reload dropped takes the detail
                // back to the list rather than showing what is gone.
                if let entry = index.entry(for: opened.row.plugin.ref),
                   let market = index.catalog.marketplaces
                       .first(where: { $0.name == entry.row.plugin.marketplace }) {
                    PluginDetailView(entry: entry, market: market, home: home) { self.opened = nil }
                } else {
                    content(index)
                        .onAppear { self.opened = nil }
                }
            } else if let index = model.index {
                content(index)
            } else {
                SkeletonRows(rows: 6, shape: .plugin, label: "Reading every marketplace…")
            }
        }
    }

    @ViewBuilder private func content(_ index: PluginExploreIndex) -> some View {
        let catalog = index.catalog
        let sources = index.sources(harness: model.harness, query: model.query)
        filters(index, shown: PluginExploreIndex.distinctPlugins(in: sources))
        if !index.problems.other.isEmpty { Card { Problems(problems: index.problems.other) } }
        if let line = index.missingNote {
            // One quiet line, not a warning each: a registration synced
            // from another Mac is nothing to fix from here.
            Label(line, systemImage: "info.circle")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .hoverTip(index.problems.missing.joined(separator: ", "), title: "Registered, not on this Mac")
        }
        if index.total == 0 {
            EmptyCard(text: index.emptyText,
                      icon: "puzzlepiece.extension",
                      actionTitle: "Add marketplace", action: addMarketplace)
        } else if sources.isEmpty {
            EmptyCard(text: "No plugins match.", icon: "puzzlepiece.extension")
        } else {
            // One row per plugin, one column per harness: the same grid
            // the MCP servers page uses. A plugin under two sources shows
            // once, tagged by the first source's kind.
            Card {
                PluginTable(entries: index.tableEntries(harness: model.harness, query: model.query),
                            harnesses: index.tableHarnesses,
                            catalog: catalog, running: model.running, busy: model.work.isBusy,
                            openHealth: openHealth, confirm: confirm, confirmBatch: confirmBatch,
                            canRename: { index.isEditable(marketplace: $0) },
                            onRename: { name in
                                renaming = name
                                renameError = nil
                                renameText = app.primitiveNames.name(forMarketplace: name) ?? name
                            },
                            openPlugin: { opened = $0 })
            }
            // A rename's failure goes once the next action has its own
            // result to show; the card above shows one or the other.
            .onChange(of: model.lastResult) { renameError = nil }
            .alert("Rename marketplace", isPresented: .init(
                get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Display name", text: $renameText)
                Button("Rename") {
                    guard let id = renaming else { return }
                    renaming = nil
                    let text = renameText
                    let file = PluginDisplayNames.file(home: home)
                    Task {
                        do {
                            try await app.saveDisplayNames({ $0.set(text, forMarketplace: id) }, to: file)
                            renameError = nil
                        } catch {
                            renameError = "Couldn't save the name: \(error.localizedDescription)"
                        }
                    }
                }
                Button("Cancel", role: .cancel) { renaming = nil }
            } message: {
                Text("Display name only: \(renaming ?? "") keeps its id.")
            }
        }
    }

    /// The tab's one filter bar: the harness select, then search taking
    /// the slack, then the count. No source select: a row's tag already
    /// says where the plugin comes from.
    private func filters(_ index: PluginExploreIndex, shown: Int) -> some View {
        let harnesses = index.catalog.harnesses
        return HStack(spacing: 8) {
            SelectMenu(label: "Harness",
                       options: [(String?.none, "All harnesses", nil)]
                           + harnesses.map { (String?.some($0), HarnessMark($0).name, $0) },
                       selection: $model.harness,
                       title: model.harness.map { HarnessMark($0).name } ?? "All")
            SearchField(prompt: "Search plugins and their skills", text: $model.query)
                .frame(maxWidth: .infinity)
            Text("\(shown) of \(index.total) plugins")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
                .lineLimit(1)
                .fixedSize()
        }
    }
}

/// What swe could not read, one line each.
private struct Problems: View {
    var problems: [PluginCatalog.Problem]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(problems.enumerated()), id: \.offset) { _, problem in
                Label(line(problem), systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func line(_ problem: PluginCatalog.Problem) -> String {
        let where_ = [HarnessMark(problem.harness).name, problem.marketplace].compactMap { $0 }.joined(separator: ", ")
        return "\(where_): \(problem.detail)"
    }
}

/// Where a marketplace comes from (local, github, git, cache): quieter
/// than a skill badge, a hairline and secondary text.
private struct SourceTag: View {
    var text: String

    var body: some View {
        Text(text)
            .font(Theme.font(size: 11, weight: .medium))
            .foregroundStyle(Theme.textSecondary)
            .lineLimit(1)
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
            .fixedSize()
    }
}

/// A hub plugin the Health tab has a finding for. A click opens Health,
/// which has the fix; this tab never runs one itself.
private struct DriftBadge: View {
    var state: PluginDriftState.Kind
    var harnesses: [String]
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Circle().fill(state == .unregistered ? Theme.danger : Theme.warning).frame(width: 6, height: 6)
                Text(state.title)
            }
            .font(Theme.font(size: 12, weight: .medium))
            .foregroundStyle(Theme.textLabel)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 1)
            .background(Theme.subtle, in: .capsule)
            .overlay(Capsule().strokeBorder(Theme.borderStrong, lineWidth: 1))
            .contentShape(.capsule)
        }
        .plainButton(cornerRadius: 999)
        .fixedSize()
        .hoverTip("\(harnessNames(harnesses)): \(state.title.lowercased()). Open Health to fix it.", replacesLabel: false)
    }
}

/// The plugins grid: one row per plugin, one column per harness swe
/// knows. The same mechanics as the MCP servers table: column order is
/// a preference, dragging a header moves the column, a master switch
/// enables or disables a plugin everywhere at once.
private struct PluginTable: View {
    /// One entry per plugin, the first source's pick for a shared ref.
    var entries: [ExplorePlugin]
    /// Every column, in the catalog's order plus extras a state names.
    var harnesses: [String]
    var catalog: PluginCatalog
    var running: PluginAction?
    /// An action or a Health fix is running: one change at a time.
    var busy: Bool
    var openHealth: () -> Void
    var confirm: (PluginAction) -> Void
    var confirmBatch: ([PluginAction]) -> Void
    /// Whether a marketplace's display name can change.
    var canRename: (String) -> Bool
    var onRename: (String) -> Void
    var openPlugin: (ExplorePlugin) -> Void

    static let nameMinWidth: CGFloat = 220
    static let sourceWidth: CGFloat = 96
    static let cellWidth: CGFloat = 84
    /// One column's footprint for the reorder maths: separator plus cell.
    static let pitch: CGFloat = 1 + cellWidth
    /// Rows the table shows before folding behind a Show more row.
    private static let foldLimit = 20
    /// The UserDefaults key the harness column order persists under.
    static let orderKey = "pluginsColumnOrder"

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The harness columns in display order, remembered as a list of ids.
    @State private var order = UserDefaults.standard.stringArray(forKey: PluginTable.orderKey) ?? []
    @State private var showAll = false
    /// The card's inner width: the table fills it, and scrolls only past it.
    @State private var available: CGFloat = 0
    /// The header mid-drag and how far the pointer has carried it.
    @State private var dragging: String?
    @State private var translation: CGFloat = 0

    /// The columns in display order: the saved order, then any harness
    /// the save predates, and none the catalog no longer names.
    private var ordered: [String] {
        order.filter(harnesses.contains) + harnesses.filter { !order.contains($0) }
    }

    /// The widths RowReorder works on: a uniform `pitch` per column.
    private var pitches: [CGFloat] { Array(repeating: Self.pitch, count: ordered.count) }

    /// Everything after the name column: its separator, the Source
    /// column, and every harness column's separator and cell.
    static func fixedTail(columns: Int) -> CGFloat {
        1 + sourceWidth + CGFloat(columns) * pitch
    }

    private var nameWidth: CGFloat {
        max(available - Self.fixedTail(columns: ordered.count), Self.nameMinWidth)
    }

    private var totalWidth: CGFloat { nameWidth + Self.fixedTail(columns: ordered.count) }

    var body: some View {
        ScrollView(.horizontal) {
            VStack(alignment: .leading, spacing: 0) {
                header
                Divider().overlay(Theme.border)
                let shown = RowFold.visible(entries, limit: Self.foldLimit, expanded: showAll)
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, entry in
                    PluginGridRow(entry: entry, harnesses: ordered, catalog: catalog,
                                  running: running, busy: busy,
                                  openHealth: openHealth, confirm: confirm, confirmBatch: confirmBatch,
                                  canRename: canRename, onRename: onRename) { openPlugin(entry) }
                    if index < shown.count - 1 { Divider().overlay(Theme.border) }
                }
                if RowFold.hiddenCount(total: entries.count, limit: Self.foldLimit) > 0 {
                    Divider().overlay(Theme.border)
                    ExpandRow(isExpanded: showAll,
                              hidden: RowFold.hiddenCount(total: entries.count, limit: Self.foldLimit),
                              help: (expand: "Show every row",
                                     collapse: "Show only the first \(Self.foldLimit)"),
                              edge: .inset) {
                        withAnimation(.easeOut(duration: Theme.fadeDuration)) { showAll.toggle() }
                    }
                }
            }
            // Exactly the card's width, or the columns' minimum if wider.
            .frame(width: max(available, Self.nameMinWidth + Self.fixedTail(columns: ordered.count)),
                   alignment: .leading)
            .overlay(alignment: .topLeading) { dropMarker }
        }
        .scrollIndicators(.automatic)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { available = $0 }
        .onChange(of: order) { UserDefaults.standard.set(order, forKey: Self.orderKey) }
    }

    /// "Plugin" over the flexible name column, "Source" over the tag
    /// column, then a draggable header centred over each harness column.
    private var header: some View {
        HStack(spacing: 0) {
            Text("Plugin")
                .frame(minWidth: Self.nameMinWidth, maxWidth: .infinity, alignment: .leading)
            columnSeparator().padding(.bottom, -8)
            Text("Source").frame(width: Self.sourceWidth)
            ForEach(ordered, id: \.self) { harness in
                columnSeparator().padding(.bottom, -8)
                let index = ordered.firstIndex(of: harness) ?? 0
                PluginColumnHeader(harness: harness, width: Self.cellWidth,
                                   entries: entries, catalog: catalog, busy: busy,
                                   confirmBatch: confirmBatch,
                                   carriedBy: dragging == harness
                                       ? RowReorder.clamped(translation, heights: pitches, dragged: index)
                                       : nil,
                                   drag: { dragChanged(harness, $0) },
                                   drop: { finish(move: true) },
                                   cancel: { finish(move: false) },
                                   move: { moveColumn(harness, $0) })
            }
        }
        .font(Theme.tableHeader)
        .tracking(0.12)
        .foregroundStyle(Theme.tableHeaderText)
        .padding(.bottom, 8)
    }

    /// The accent bar at the slot a dragged column would land in.
    @ViewBuilder private var dropMarker: some View {
        if let x = dropEdge {
            Rectangle().fill(Theme.interactive)
                .frame(width: 2)
                .offset(x: x)
        }
    }

    private var dropEdge: CGFloat? {
        guard let id = dragging, let from = ordered.firstIndex(of: id) else { return nil }
        let target = RowReorder.targetIndex(heights: pitches, dragged: from, translation: translation)
        if target >= ordered.count - 1 { return totalWidth - 2 }
        let slot = target < from ? target : target + 1
        return nameWidth + Self.sourceWidth + CGFloat(slot) * Self.pitch
    }

    private func dragChanged(_ harness: String, _ translation: CGFloat) {
        dragging = harness
        self.translation = translation
    }

    /// The VoiceOver and keyboard reorder the grip does by pointer: move
    /// the column one slot left or right.
    private func moveColumn(_ harness: String, _ direction: Int) {
        guard let from = ordered.firstIndex(of: harness) else { return }
        let target = from + direction
        guard target >= 0, target <= ordered.count - 1 else { return }
        withAnimation(reduceMotion ? nil : .easeOut(duration: Theme.fadeDuration)) {
            var next = ordered
            next.remove(at: from)
            next.insert(harness, at: target)
            order = next
        }
    }

    /// Drop the dragged column at the slot the pointer left it over.
    private func finish(move: Bool) {
        guard let id = dragging else { return }
        defer { dragging = nil; translation = 0 }
        guard move, let from = ordered.firstIndex(of: id) else { return }
        let target = RowReorder.targetIndex(heights: pitches, dragged: from, translation: translation)
        guard target != from else { return }
        withAnimation(reduceMotion ? nil : .easeOut(duration: Theme.fadeDuration)) {
            var next = ordered
            next.remove(at: from)
            next.insert(id, at: target)
            order = next
        }
    }
}

/// One harness column's header: a drag grip and the mark, and on click a
/// menu that enables or disables every listed plugin in that harness at
/// once, each action confirmed as one batch.
private struct PluginColumnHeader: View {
    var harness: String
    var width: CGFloat
    var entries: [ExplorePlugin]
    var catalog: PluginCatalog
    var busy: Bool
    var confirmBatch: ([PluginAction]) -> Void
    /// The pointer offset while this is the header being carried, or nil.
    var carriedBy: CGFloat?
    var drag: (CGFloat) -> Void
    var drop: () -> Void
    /// The drag ended without reaching `onEnded`: slide everything back.
    var cancel: () -> Void
    /// VoiceOver and keyboard reorder, one slot in a direction (-1, +1).
    var move: (Int) -> Void
    @State private var hovering = false
    /// True only while the grip's gesture is live: resets on cancel.
    @GestureState private var down = false

    private var name: String { HarnessMark(harness).name }

    /// The plugins a batch in this harness would touch, filtered the
    /// same way the table is: a column flips only what it shows.
    private func batch(_ verb: PluginAction.Verb) -> [PluginAction] {
        entries.compactMap { entry in
            catalog.actions(for: entry.row.plugin, in: harness)
                .first { $0.verb == verb }
        }
    }

    @ViewBuilder private var columnMenu: some View {
        Button("Disable all in \(name)") { confirmBatch(batch(.disable)) }
            .disabled(busy || batch(.disable).isEmpty)
        Button("Enable all in \(name)") { confirmBatch(batch(.enable)) }
            .disabled(busy || batch(.enable).isEmpty)
    }

    var body: some View {
        Menu { columnMenu } label: {
            HarnessMarkView(harness: harness)
                .frame(width: width)
                .contentShape(.rect)
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .fixedSize()
        .contextMenu { columnMenu }
        .overlay(alignment: .leading) {
            Image(systemName: "line.3.horizontal")
                .font(Theme.font(size: 9, weight: .bold))
                .foregroundStyle(Theme.textMuted)
                .frame(width: 14, height: 20)
                .opacity(hovering || carriedBy != nil ? 0.9 : 0.3)
                .contentShape(.rect)
                .hoverTip("Drag to move the \(name) column", pinsOnClick: false)
                .gesture(DragGesture(minimumDistance: 4, coordinateSpace: .local)
                    .updating($down) { _, state, _ in state = true }
                    .onChanged { drag($0.translation.width) }
                    .onEnded { _ in drop() })
        }
        .offset(x: carriedBy ?? 0)
        .zIndex(carriedBy == nil ? 0 : 1)
        .opacity(carriedBy == nil ? 1 : 0.85)
        .onHover { hovering = $0 }
        .onChange(of: down) { _, isDown in
            guard !isDown else { return }
            Task { await Task.yield(); cancel() }
        }
        .accessibilityAction(named: "Move left") { move(-1) }
        .accessibilityAction(named: "Move right") { move(1) }
    }
}

/// A plugin in the grid: a master switch, the name and ref, the source
/// tag, then a cell per harness. The name block opens the detail page;
/// the cells carry their own menus, so they sit outside the open button.
private struct PluginGridRow: View {
    @Environment(AppModel.self) private var app
    var entry: ExplorePlugin
    var harnesses: [String]
    var catalog: PluginCatalog
    var running: PluginAction?
    var busy: Bool
    var openHealth: () -> Void
    var confirm: (PluginAction) -> Void
    var confirmBatch: ([PluginAction]) -> Void
    var canRename: (String) -> Bool
    var onRename: (String) -> Void
    var open: () -> Void
    @State private var hovering = false

    private var plugin: CatalogPlugin { entry.row.plugin }
    private var shown: String {
        app.primitiveNames.name(forPlugin: plugin.ref) ?? plugin.displayName ?? plugin.name
    }

    /// The enables this row could run right now, and the disables: the
    /// master switch flips the first of each it finds per harness.
    private var enables: [PluginAction] {
        harnesses.compactMap { harness in
            catalog.actions(for: plugin, in: harness).first { $0.verb == .enable }
        }
    }

    private var disables: [PluginAction] {
        harnesses.compactMap { harness in
            catalog.actions(for: plugin, in: harness).first { $0.verb == .disable }
        }
    }

    /// Something of the plugin runs: enabled, or installed with no word
    /// either way. Cursor's cached copy does not count, it only exists.
    private var isOn: Bool {
        plugin.harnesses.values.contains { $0.status == .enabled || $0.status == .installed }
    }

    var body: some View {
        HStack(spacing: 0) {
            Button(action: open) {
                HStack(alignment: .top, spacing: 8) {
                    master
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 5) {
                            Text(shown).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                            if let drift = plugin.worstDrift {
                                DriftBadge(state: drift.state, harnesses: drift.harnesses, action: openHealth)
                            }
                        }
                        Text(secondary).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
                    }
                    .padding(.trailing, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .contentShape(.rect)
            }
            .plainButton(cornerRadius: Theme.smallRadius)
            .help("Open \(shown)")
            .frame(minWidth: PluginTable.nameMinWidth, maxWidth: .infinity, alignment: .leading)
            columnSeparator()
            source
                .frame(width: PluginTable.sourceWidth)
            ForEach(harnesses, id: \.self) { harness in
                columnSeparator()
                PluginCell(harness: harness, state: plugin.harnesses[harness],
                           actions: catalog.actions(for: plugin, in: harness),
                           running: running?.ref == plugin.ref && running?.harness == harness,
                           busy: busy, confirm: confirm)
                    .frame(width: PluginTable.cellWidth)
            }
        }
        .padding(.vertical, 8)
        .background(hovering ? Theme.rowHover : .clear)
        .contentShape(.rect)
        .onHover { hovering = $0 }
        .accessibilityLabel("\(shown), \(accessibilityLabelSuffix)")
        .contextMenu { rowMenu }
    }

    /// The ref when a display name covers the name, else the description,
    /// else the marketplace and version the row still owes.
    private var secondary: String {
        if shown != plugin.name { return plugin.ref }
        if let description = plugin.description, !description.isEmpty { return description }
        return plugin.marketplace + (plugin.version.map { " · v\($0)" } ?? "")
    }

    /// Who manages the plugin's copy: the swe hub, the harness that
    /// registered its marketplace, or nothing a rename could reach.
    @ViewBuilder private var source: some View {
        switch entry.source {
        case "local":
            Text("swe")
                .font(Theme.font(size: 10, weight: .semibold))
                .tracking(0.6)
                .foregroundStyle(Theme.accent)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(Theme.accentTint, in: .rect(cornerRadius: 5))
                .hoverTip("From your SWE hub's \(plugin.marketplace) marketplace", pinsOnClick: false)
        case "unregistered":
            Text("unregistered")
                .font(Theme.font(size: 10, weight: .medium))
                .foregroundStyle(Theme.textMuted)
                .lineLimit(1)
                .hoverTip("Installed from a marketplace no harness registers anymore", pinsOnClick: false)
        default:
            HarnessMarkView(harness: entry.source, size: 14)
                .lineLimit(1)
                .hoverTip("From a marketplace \(HarnessMark(entry.source).name) registers", pinsOnClick: false)
        }
    }

    /// The row's right click: the master switch's two directions as menu
    /// items, the rename a folder-sourced marketplace allows, and the
    /// plugin's folder in Finder.
    @ViewBuilder private var rowMenu: some View {
        Button("Turn off everywhere") { confirmBatch(disables) }
            .disabled(disables.isEmpty)
        Button("Turn on everywhere") { confirmBatch(enables) }
            .disabled(enables.isEmpty)
        if canRename(plugin.marketplace) {
            Button("Rename marketplace…") { onRename(plugin.marketplace) }
        }
        if let path = plugin.sourcePath {
            Button("Show in Finder") {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
            }
        }
    }

    /// The master switch left of the name: green while the plugin runs
    /// anywhere. Flipping it collects every harness's enable or disable
    /// and asks once for the batch; a plugin with no such verb anywhere
    /// sits disabled and says why.
    @ViewBuilder private var master: some View {
        let on = isOn
        Toggle(shown, isOn: Binding(
            get: { on },
            set: { value in confirmBatch(value ? enables : disables) }))
        .labelsHidden()
        .toggleStyle(AccentSwitchStyle(tint: on ? Theme.success : Theme.interactive))
        .disabled(busy || (on ? disables : enables).isEmpty)
        .padding(.top, 2)
        .hoverTip(masterTip(on: on), pinsOnClick: false)
    }

    private func masterTip(on: Bool) -> String {
        if on {
            return disables.isEmpty
                ? "On, but no harness here can disable it."
                : "On. Turn off in every harness that allows it."
        }
        return enables.isEmpty
            ? "Nothing installed to enable. A cell's Install puts it in a harness first."
            : "Off. Turn on in every harness that allows it."
    }

    private var accessibilityLabelSuffix: String {
        let names = plugin.harnesses.filter { $0.value.status.isInstalled }.map(\.key)
        return names.isEmpty ? "installed nowhere" : "installed in \(harnessNames(names))"
    }
}

/// One plugin in one harness: the state mark, a menu of what swe can do
/// there where actions exist, and a minus mark where the plugin is absent.
/// Unlike the skills cells these open a menu rather than toggle
/// directly, since a plugin's change is an install or a write to the
/// harness's config, never a silent flip.
private struct PluginCell: View {
    var harness: String
    var state: PluginHarnessState?
    var actions: [PluginAction]
    var running: Bool
    var busy: Bool
    var confirm: (PluginAction) -> Void

    private var name: String { HarnessMark(harness).name }

    var body: some View {
        if running {
            ProgressView().controlSize(.mini)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let state {
            if actions.isEmpty {
                mark(state.status)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .hoverTip(help(for: state), pinsOnClick: false)
            } else {
                Menu {
                    ForEach(actions) { action in
                        Button(action.menuTitle) { confirm(action) }
                    }
                } label: {
                    mark(state.status)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .contentShape(.rect)
                }
                .menuStyle(.button)
                .menuIndicator(.hidden)
                .buttonStyle(.plain)
                .disabled(busy)
                .hoverTip(help(for: state) + " Click for actions.", pinsOnClick: false)
            }
        } else {
            Image(systemName: "minus").foregroundStyle(Theme.borderStrong)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .hoverTip("Not in \(name)", pinsOnClick: false)
        }
    }

    /// The state's glyph: a check on, a power mark off, a grey dot
    /// installed with no on or off known, a ring for listed and for
    /// Cursor's cache.
    @ViewBuilder private func mark(_ status: PluginHarnessState.Status) -> some View {
        switch status {
        case .enabled:
            Image(systemName: "checkmark").font(Theme.font(size: 12, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)
        case .disabled:
            Image(systemName: "power").font(Theme.font(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textMuted)
        case .installed:
            Circle().fill(Theme.textSecondary).frame(width: Theme.statusDot, height: Theme.statusDot)
        case .available:
            Image(systemName: "arrow.down.circle").font(Theme.font(size: 11, weight: .medium))
                .foregroundStyle(Theme.textMuted)
        case .cached:
            Circle().strokeBorder(Theme.textMuted, style: StrokeStyle(lineWidth: 1.2, dash: [2, 2]))
                .frame(width: Theme.statusDot, height: Theme.statusDot)
        case .absent:
            Image(systemName: "minus").foregroundStyle(Theme.borderStrong)
        }
    }

    private func help(for state: PluginHarnessState) -> String {
        var parts = ["\(name): \(state.status.title.lowercased())"]
        if let version = state.version { parts.append("version \(version)") }
        if !state.scopes.isEmpty { parts.append("scope \(state.scopes.joined(separator: ", "))") }
        if actions.isEmpty && state.installed == nil { parts.append("read only") }
        return parts.joined(separator: " · ")
    }
}

extension AppModel {
    /// Renames, one after another: each waits for the last to land and
    /// starts from the names as they are then, so two quick renames (a
    /// marketplace's and a plugin's) can't each write a copy that drops
    /// the other. The write runs off the main actor.
    func saveDisplayNames(_ change: @escaping @MainActor (inout PluginDisplayNames) -> Void,
                          to file: URL) async throws {
        let previous = displayNamesSave
        let mine = Task<String?, Never> { @MainActor in
            await previous?.value
            do {
                primitiveNames = try await primitiveNames.savingOffMain(change, to: file)
                return nil
            } catch {
                return error.localizedDescription
            }
        }
        displayNamesSave = Task { _ = await mine.value }
        if let failure = await mine.value { throw PluginDisplayNames.SaveFailure(message: failure) }
    }
}

extension PluginDisplayNames {
    /// These names with `change` applied, once `write` has saved them.
    /// The write runs off the main actor (the file sits under `~/.swe`,
    /// which may be a synced or slow folder); on failure it throws and
    /// the caller keeps the names it had, so a failed rename changes
    /// nothing.
    @MainActor func savingOffMain(_ change: (inout PluginDisplayNames) -> Void, to url: URL,
                       write: @escaping @Sendable (PluginDisplayNames, URL) throws -> Void
                           = { try $0.save(to: $1) }) async throws -> PluginDisplayNames {
        var next = self
        change(&next)
        let saved = next
        let failure: String? = await offMain {
            do { try write(saved, url); return nil } catch { return error.localizedDescription }
        }
        if let failure { throw SaveFailure(message: failure) }
        return saved
    }

    struct SaveFailure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }
}
