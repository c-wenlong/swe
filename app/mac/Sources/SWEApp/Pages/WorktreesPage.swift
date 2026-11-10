import AppKit
import SWECore
import SwiftUI

/// Every git worktree `swe wt` knows, grouped by repository: what state it
/// is in, its pull request, how big it is and which harness made it. A row
/// opens its detail pane; changes (add, lock, move, slim, remove, clean up
/// merged) all go through `swe wt` and the list reloads after each.
struct WorktreesPage: View {
    @Environment(AppModel.self) private var app
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The worktree open in the detail pane, by path.
    @State private var opened: String?
    @State private var sheet: WorktreeSheet?
    /// The board's width, for the compact rows; 0 until the first layout.
    @State private var boardWidth: CGFloat = 0
    /// "" is every repository; otherwise one main checkout's path.
    @AppStorage("worktreesScope") private var scopeStored = ""
    @State private var debugApplied = false

    /// Under this the list and the 440pt pane can't sit side by side.
    private static let sideBySideWidth: CGFloat = 980
    /// Under this a row drops its PR, commit and harness columns.
    private static let wideRowWidth: CGFloat = 840

    private var model: WorktreesModel { app.worktrees }

    var body: some View {
        Page(title: "Worktrees",
             subtitle: "Every git worktree swe knows, by repository: what is merged, what is still in flight, and how much disk each takes.") {
            Button("Clean up merged", systemImage: "sparkles") { sheet = .prune }
                .secondaryButton()
                .disabled(!canPrune)
                .hoverTip(pruneHelp, replacesLabel: false)
            Button("New worktree", systemImage: "plus") { sheet = .new }
                .primaryButton()
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(swe: app.swe, source: app.swePath) }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Read the worktrees again (⌘R)")
        } content: {
            content
        }
        .task(id: "\(app.swePath)|\(scopeStored)") {
            model.scope = scopeStored.isEmpty ? .all : .repo(scopeStored)
            await model.loadIfStale(swe: app.swe, source: app.swePath)
            leaveMissingScope()
            applyDebugState()
        }
        // Lists go stale while the page is open: look again when they do.
        .task(id: app.swePath) {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(model.freshness))
                if Task.isCancelled { break }
                await model.loadIfStale(swe: app.swe, source: app.swePath)
            }
        }
        // Worktrees under ~/.swe come and go from a terminal. Never the
        // cache or .git/index, which the app's own reads write.
        .task {
            let swe = FileManager.default.homeDirectoryForCurrentUser.appending(path: ".swe")
            for await _ in FolderChanges.stream(root: swe, matches: WorktreeFold.affectsList) {
                await model.reloadFromWatcher(swe: app.swe, source: app.swePath)
            }
        }
        .task(id: app.pendingWorktree) { applyPending() }
        .onChange(of: model.worktrees) { _, _ in applyPending() }
        .cardSheet(item: $sheet, modal: false) { item in
            WorktreeSheetHost(sheet: item) { path in opened = path }
        }
    }

    // MARK: Content

    @ViewBuilder private var content: some View {
        if let error = model.actionError { ErrorCard(message: error) }
        if let notice = model.notice {
            Card {
                HStack(spacing: 10) {
                    Label(notice, systemImage: "checkmark.circle")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    Spacer()
                    Button("Dismiss") { model.notice = nil }.secondaryButton()
                }
            }
        }
        switch model.state {
        case .idle, .loading:
            LoadingCard(text: "Reading worktrees…")
        case .failed(let message):
            scopeBar
            ErrorCard(message: Self.loadMessage(message), actionTitle: "Refresh",
                      actionShortcut: .init("r", modifiers: .command)) {
                Task { await model.load(swe: app.swe, source: app.swePath) }
            }
        case .loaded(let list):
            if list.worktrees.isEmpty {
                scopeBar
                EmptyCard(text: model.scope == .all
                          ? "No worktrees yet. A worktree is a second checkout of a repository, one per branch."
                          : "This repository has no worktrees.",
                          icon: "arrow.triangle.branch", actionTitle: "New worktree") { sheet = .new }
            } else {
                loaded
            }
        }
    }

    /// The scope picker alone, for the states that have no filters, so a
    /// repository that fails or is empty never traps the page.
    @ViewBuilder private var scopeBar: some View {
        if !model.repos.isEmpty || !scopeStored.isEmpty {
            HStack {
                SelectMenu(options: scopeOptions, selection: scopeBinding, width: 260)
                Spacer(minLength: 0)
            }
        }
    }

    private var loaded: some View {
        VStack(alignment: .leading, spacing: 16) {
            SummaryCard(summary: model.summary)
            filters
            board
        }
    }

    private var filters: some View {
        @Bindable var model = model
        return VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                SearchField(prompt: "Search branch, repository, path or PR", text: $model.query,
                            focusShortcut: .init("f", modifiers: .command))
                SelectMenu(options: scopeOptions, selection: scopeBinding, width: 210)
            }
            StateChips(counts: model.stateCounts, total: model.visibleTotal, selection: $model.stateFilter)
        }
    }

    private var board: some View {
        let groups = model.groups
        let wide = boardWidth == 0 || boardWidth >= Self.sideBySideWidth
        let panelOpen = opened.flatMap { model.worktree(at: $0) } != nil
        let compact = boardWidth != 0 && (panelOpen || boardWidth < Self.wideRowWidth)
        return Group {
            if let tree = opened.flatMap({ model.worktree(at: $0) }), !wide {
                detail(tree)
            } else {
                HStack(alignment: .top, spacing: 16) {
                    list(groups, compact: compact || panelOpen)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if let tree = opened.flatMap({ model.worktree(at: $0) }) {
                        detail(tree).frame(width: 440)
                            .transition(reduceMotion ? .identity
                                        : .move(edge: .trailing).combined(with: .opacity))
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(reduceMotion ? nil : .easeOut(duration: Theme.fadeDuration), value: opened)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { boardWidth = $0 }
    }

    @ViewBuilder private func list(_ groups: [WorktreeGroup], compact: Bool) -> some View {
        if groups.isEmpty {
            EmptyCard(text: "No worktree matches. Clear the search or the state filter.",
                      icon: "line.3.horizontal.decrease.circle", actionTitle: "Clear filters") {
                model.query = ""
                model.stateFilter = nil
            }
        } else {
            VStack(alignment: .leading, spacing: 16) {
                ForEach(groups) { group in
                    RepoCard(group: group, compact: compact, opened: opened,
                             busy: model.working, open: { toggle($0) }, act: { perform($0, on: $1) })
                }
            }
            .animation(reduceMotion ? nil : .easeOut(duration: Theme.fadeDuration),
                       value: groups.flatMap { $0.worktrees.map(\.path) })
        }
    }

    private func detail(_ tree: Worktree) -> some View {
        WorktreeDetail(tree: tree, busy: model.working.contains(tree.path),
                       close: { opened = nil }, act: { perform($0, on: tree) })
    }

    // MARK: Scope

    private var scopeOptions: [(value: String, title: String)] {
        var options: [(value: String, title: String)] = [("", "All repositories")]
            + model.repos.map { ($0.path, $0.name) }
        // A saved repository the list doesn't know still needs its own entry.
        if !scopeStored.isEmpty, !options.contains(where: { $0.value == scopeStored }) {
            options.append((scopeStored, (scopeStored as NSString).lastPathComponent))
        }
        return options
    }

    private var scopeBinding: Binding<String> {
        Binding(get: { scopeStored }, set: { scopeStored = $0 })
    }

    private var canPrune: Bool { !model.worktrees.isEmpty }

    private var pruneHelp: String {
        model.scope == .all
            ? "Remove every merged worktree that is clean. You see the list first."
            : "Remove this repository's merged, clean worktrees. You see the list first."
    }

    // MARK: Actions

    private func toggle(_ tree: Worktree) {
        opened = opened == tree.path ? nil : tree.path
    }

    private func perform(_ action: WorktreeAction, on tree: Worktree) {
        switch action {
        case .reveal:
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: tree.path)])
        case .copyPath:
            copyToPasteboard(tree.path)
        case .terminal:
            let line = "cd \(LaunchPlanner.shellQuote(tree.path))"
            let terminal = app.terminal
            Task {
                do { try await ProcessLauncher().launch(LaunchPlanner.terminal(terminal, running: line)) }
                catch { model.actionError = error.localizedDescription }
            }
        case .lock: sheet = .lock(tree)
        case .unlock:
            Task {
                do { try await model.unlock(tree, swe: app.swe) }
                catch { model.actionError = WorktreeFailure.describe(error).message }
            }
        case .move: sheet = .move(tree)
        case .slim: sheet = .slim(tree)
        case .remove: sheet = .remove(tree)
        case .copyPruneCommand: copyToPasteboard(tree.pruneCommand)
        }
    }

    /// The ⌘K search's pick: open its row, once a loaded list holds it.
    private func applyPending() {
        guard let path = app.pendingWorktree, model.worktree(at: path) != nil else { return }
        opened = path
        app.pendingWorktree = nil
    }

    /// `SWE_WT_STATE` in a debug build; a no-op otherwise.
    private func applyDebugState() {
        guard !debugApplied, let state = WorktreesDebug.initialState else { return }
        debugApplied = true
        func tree(_ branch: String) -> Worktree? { model.worktrees.first { $0.branch == branch } }
        switch state {
        case "filtered": model.stateFilter = .mergedClean
        case "detail": opened = (tree("mac/worktrees-page") ?? model.worktrees.first { $0.state == .openPr } ?? model.worktrees.first { $0.state != .main })?.path
        case "orphan": opened = model.worktrees.first { $0.state == .orphaned }?.path
        case "new": sheet = .new
        case "remove": sheet = tree("mac/worktrees-page").map { .remove($0) }
        case "removeclean": sheet = tree("fix/hooks-race").map { .remove($0) }
        case "prune": sheet = .prune
        case "slim": sheet = tree("fix/hooks-race").map { .slim($0) }
        case "lock": sheet = tree("cli/wt-commands").map { .lock($0) }
        case "move": sheet = tree("cli/wt-commands").map { .move($0) }
        default: break
        }
    }

    /// A saved repository that has moved or gone: back to All repositories,
    /// or the page would restore the same failing scope on every launch.
    private func leaveMissingScope() {
        guard !scopeStored.isEmpty, case .failed = model.state,
              let code = model.failure?.code, ["not_a_repo", "not_found"].contains(code) else { return }
        scopeStored = ""
    }

    private static func loadMessage(_ message: String) -> String {
        message.localizedCaseInsensitiveContains("unknown command")
            ? "This version of swe has no worktree commands yet. Update swe, or pick a newer swe in Settings › General."
            : message
    }
}

private extension WorktreesModel {
    /// The number of rows the search leaves, before the state chip.
    var visibleTotal: Int { WorktreeFilter.apply(worktrees, state: nil, query: query).count }
}

// MARK: - Actions and sheets

enum WorktreeAction { case reveal, copyPath, terminal, lock, unlock, move, slim, remove, copyPruneCommand }

enum WorktreeSheet: Identifiable {
    case new, prune
    case remove(Worktree), lock(Worktree), move(Worktree), slim(Worktree)

    var id: String {
        switch self {
        case .new: "new"
        case .prune: "prune"
        case .remove(let tree): "remove|\(tree.path)"
        case .lock(let tree): "lock|\(tree.path)"
        case .move(let tree): "move|\(tree.path)"
        case .slim(let tree): "slim|\(tree.path)"
        }
    }
}

// MARK: - Summary and filters

private struct SummaryCard: View {
    var summary: WorktreeSummary

    var body: some View {
        Card {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 0) { stats }
                VStack(alignment: .leading, spacing: 14) { stats }
            }
        }
    }

    @ViewBuilder private var stats: some View {
        stat("Worktrees", String(summary.count), note: nil)
        stat("Total size", WorktreeFormat.bytes(summary.totalBytes), note: "estimate from du")
        stat("Rebuildable", WorktreeFormat.bytes(summary.rebuildableBytes),
             note: "node_modules, venvs, build output")
        stat("Reclaimable", WorktreeFormat.bytes(summary.reclaimableBytes),
             note: summary.reclaimableCount == 0
                 ? "No merged worktrees" : "From \(WorktreeFormat.count(summary.reclaimableCount)) merged and clean",
             tint: summary.reclaimableBytes > 0 ? Theme.accent : nil)
    }

    private func stat(_ label: String, _ value: String, note: String?, tint: Color? = nil) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            Text(verbatim: value).font(Theme.figure).foregroundStyle(tint ?? Theme.textPrimary)
            if let note {
                Text(note).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct StateChips: View {
    var counts: [WorktreeState: Int]
    var total: Int
    @Binding var selection: WorktreeState?

    var body: some View {
        FlowLayout(spacing: 6, lineSpacing: 6) {
            chip("All", count: total, isOn: selection == nil) { selection = nil }
            ForEach(WorktreeState.chipOrder.filter { (counts[$0] ?? 0) > 0 || selection == $0 }, id: \.self) { state in
                chip(state.title, count: counts[state] ?? 0, isOn: selection == state) {
                    selection = selection == state ? nil : state
                }
            }
        }
    }

    private func chip(_ title: String, count: Int, isOn: Bool, action: @escaping () -> Void) -> some View {
        StateChip(title: title, count: count, isOn: isOn, action: action)
    }
}

private struct StateChip: View {
    var title: String
    var count: Int
    var isOn: Bool
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text(title)
                Text(verbatim: "\(count)").foregroundStyle(Theme.textMuted)
            }
            .font(Theme.control)
            .foregroundStyle(isOn ? Theme.textPrimary : Theme.textSecondary)
            .padding(.horizontal, 10)
            .frame(height: Theme.controlHeight)
            .background(isOn ? Theme.selected : hovering ? Theme.hover : Color.clear, in: .capsule)
            .overlay(Capsule().strokeBorder(isOn ? Theme.borderStrong : Theme.border, lineWidth: 1))
            .contentShape(.capsule)
        }
        .plainButton(cornerRadius: Theme.controlHeight / 2)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

// MARK: - State badge

/// The state, in words and a tone: green for safe to remove, amber for in
/// flight, red for something to look at, quiet for the rest.
struct WorktreeStateBadge: View {
    var state: WorktreeState

    var body: some View {
        let (text, fill) = colors
        Text(state.title)
            .font(Theme.font(size: 12, weight: .medium))
            .foregroundStyle(text)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 1)
            .background(fill, in: .capsule)
            .overlay(Capsule().strokeBorder(text.opacity(0.28), lineWidth: 1))
    }

    private var colors: (Color, Color) {
        switch state.tone {
        case .good: (Theme.accent, Theme.accentTint)
        case .caution: (Theme.warningText, Theme.warning.opacity(0.14))
        case .attention: (Theme.dangerText, Theme.danger.opacity(0.12))
        case .neutral: (Theme.textLabel, Theme.subtle)
        }
    }
}

// MARK: - List

private enum Col {
    static let state: CGFloat = 138
    static let pr: CGFloat = 74
    static let commit: CGFloat = 78
    static let size: CGFloat = 124
    static let harness: CGFloat = 88
    static let menu: CGFloat = 28
}

private struct RepoCard: View {
    private static let foldLimit = 12
    @State private var expanded = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var group: WorktreeGroup
    var compact: Bool
    var opened: String?
    var busy: Set<String>
    var open: (Worktree) -> Void
    var act: (WorktreeAction, Worktree) -> Void

    var body: some View {
        Card(title: group.title, subtitle: displayPath(group.id), monoSubtitle: true) {
            Text("\(WorktreeFormat.count(group.worktrees.count)) · \(WorktreeFormat.bytes(group.totalBytes))")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
        } content: {
            VStack(alignment: .leading, spacing: 0) {
                header
                // The open row never folds away behind the toggle.
                let fold = WorktreeFold.visible(group.worktrees, limit: Self.foldLimit,
                                                expanded: expanded, pinned: opened)
                ForEach(fold.shown) { tree in
                    WorktreeRow(tree: tree, compact: compact, isOpen: opened == tree.path,
                                busy: busy.contains(tree.path),
                                open: { open(tree) }, act: { act($0, tree) })
                }
                if group.worktrees.count > Self.foldLimit, expanded || fold.hidden > 0 {
                    ExpandRow(isExpanded: expanded,
                              hidden: fold.hidden,
                              help: (expand: "Show every worktree in this repository",
                                     collapse: "Show fewer worktrees"),
                              edge: .inset) {
                        withAnimation(reduceMotion ? nil : .easeOut(duration: Theme.fadeDuration)) { expanded.toggle() }
                    }
                }
            }
            .padding(.horizontal, -8)
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Text("Branch").frame(maxWidth: .infinity, alignment: .leading)
            Text("State").frame(width: Col.state, alignment: .leading)
            if !compact {
                Text("PR").frame(width: Col.pr, alignment: .leading)
                Text("Last commit").frame(width: Col.commit, alignment: .leading)
            }
            Text("Size").frame(width: Col.size, alignment: .leading)
            if !compact { Text("Harness").frame(width: Col.harness, alignment: .leading) }
            Color.clear.frame(width: Col.menu, height: 1)
        }
        .font(Theme.tableHeader)
        .foregroundStyle(Theme.tableHeaderText)
        .padding(.horizontal, 8)
        .padding(.bottom, 6)
    }
}

private struct WorktreeRow: View {
    var tree: Worktree
    var compact: Bool
    var isOpen: Bool
    var busy: Bool
    var open: () -> Void
    var act: (WorktreeAction) -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 12) {
            branch.frame(maxWidth: .infinity, alignment: .leading)
            WorktreeStateBadge(state: tree.state).frame(width: Col.state, alignment: .leading)
            if !compact {
                PRLink(pr: tree.pr).frame(width: Col.pr, alignment: .leading)
                Text(WorktreeFormat.lastCommit(tree.lastCommitAt))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).frame(width: Col.commit, alignment: .leading)
            }
            size.frame(width: Col.size, alignment: .leading)
            if !compact { harness.frame(width: Col.harness, alignment: .leading) }
            Group {
                if busy {
                    ProgressView().controlSize(.small)
                } else {
                    EllipsisMenu { WorktreeMenuItems(tree: tree, act: act) }
                }
            }
            .frame(width: Col.menu)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 8)
        .background(isOpen ? Theme.selected : hovering ? Theme.rowHover : Color.clear,
                    in: .rect(cornerRadius: Theme.controlRadius))
        .contentShape(.rect(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
        .onTapGesture(perform: open)
        .textSelection(.disabled)
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel("\(tree.title), \(tree.state.title)")
        .accessibilityAction(named: "Show details", open)
    }

    private var branch: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(tree.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.tail)
                if tree.locked {
                    Image(systemName: "lock.fill").font(Theme.font(size: 11))
                        .foregroundStyle(Theme.textSecondary)
                        .help(tree.lockReason.map { "Locked: \($0)" } ?? "Locked")
                }
                if tree.dirty || tree.untracked > 0 {
                    Circle().fill(Theme.warning).frame(width: 6, height: 6)
                        .help("Uncommitted or untracked files")
                }
            }
            if let hold = tree.hold {
                Label("Clean up keeps it: \(hold.prefix(1).lowercased() + hold.dropFirst())", systemImage: "hand.raised")
                    .font(Theme.caption).foregroundStyle(Theme.warningText).lineLimit(1)
            } else if compact, let pr = tree.pr {
                Text(verbatim: "#\(pr.number) · \(WorktreeFormat.lastCommit(tree.lastCommitAt))")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
            } else {
                Text(displayPath(tree.path))
                    .font(Theme.font(size: 11, design: .monospaced)).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
            }
        }
    }

    private var size: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(WorktreeFormat.bytes(tree.sizeBytes)).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            if let rebuildable = tree.rebuildableBytes, rebuildable > 0 {
                Text("\(WorktreeFormat.bytes(rebuildable)) rebuildable")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).minimumScaleFactor(0.85)
            }
        }
    }

    private var harness: some View {
        HStack(spacing: 6) {
            if HarnessIcon.exists(for: tree.harness) { HarnessIcon(harness: tree.harness, size: 14) }
            Text(WorktreeFormat.harness(tree.harness)).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .lineLimit(1)
        }
    }
}

private struct PRLink: View {
    var pr: WorktreePR?

    var body: some View {
        if let pr, let url = URL(string: pr.url) {
            Link(destination: url) {
                HStack(spacing: 4) {
                    Image(systemName: icon(pr)).font(Theme.font(size: 11))
                    Text(verbatim: "#\(pr.number)")
                }
                .font(Theme.caption).foregroundStyle(Theme.link)
            }
            .help("Open pull request #\(pr.number) on GitHub")
        } else {
            Text("None").font(Theme.caption).foregroundStyle(Theme.textMuted)
        }
    }

    private func icon(_ pr: WorktreePR) -> String {
        pr.isMerged ? "checkmark.circle" : pr.isOpen ? "arrow.triangle.pull" : "xmark.circle"
    }
}

/// A row's "more" menu. The main checkout has no lock, move or remove,
/// and a protected one no remove at all, so those items aren't listed.
private struct WorktreeMenuItems: View {
    var tree: Worktree
    var act: (WorktreeAction) -> Void

    var body: some View {
        Button("Open in Terminal") { act(.terminal) }
        Button("Reveal in Finder") { act(.reveal) }
        Button("Copy path") { act(.copyPath) }
        if tree.canLock || tree.canUnlock || tree.state.isRemovable || tree.state == .orphaned { Divider() }
        if tree.locked { if tree.canUnlock { Button("Unlock") { act(.unlock) } } }
        else if tree.canLock { Button("Lock…") { act(.lock) } }
        if tree.state.supportsMoveAndSlim {
            Button("Move…") { act(.move) }.disabled(!tree.canMove)
            Button("Slim…") { act(.slim) }.disabled(!tree.canSlim)
        }
        if tree.state.isRemovable {
            Divider()
            Button("Remove…", role: .destructive) { act(.remove) }.disabled(!tree.canRemove)
        } else if tree.state == .orphaned {
            Divider()
            Button("Copy git worktree prune command") { act(.copyPruneCommand) }
        }
    }
}

// MARK: - Detail

private struct WorktreeDetail: View {
    var tree: Worktree
    var busy: Bool
    var close: () -> Void
    var act: (WorktreeAction) -> Void

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top, spacing: 8) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(tree.title).font(Theme.sectionTitle).tracking(-0.4)
                            .foregroundStyle(Theme.textPrimary).lineLimit(2)
                        HStack(spacing: 8) {
                            WorktreeStateBadge(state: tree.state)
                            if tree.locked {
                                Label("Locked", systemImage: "lock.fill")
                                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            }
                        }
                    }
                    Spacer(minLength: 0)
                    Button("Close", systemImage: "xmark", action: close)
                        .labelStyle(.iconOnly).iconButton().help("Close the details")
                }
                Text(tree.state.explanation).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Divider()
                fields
                Divider()
                actions
            }
        }
    }

    private var fields: some View {
        VStack(alignment: .leading, spacing: 10) {
            field("Repository", tree.repoName)
            field("Path", displayPath(tree.path), mono: true)
            field("Main checkout", displayPath(tree.mainCheckout), mono: true)
            field("Branch", tree.branch ?? "Detached HEAD")
            field("Head", tree.head.isEmpty ? "Unknown" : String(tree.head.prefix(12)), mono: true)
            row("Pull request") {
                if let pr = tree.pr, let url = URL(string: pr.url) {
                    Link(destination: url) { Text(verbatim: "#\(pr.number) · \(pr.state.capitalized)") }
                        .font(Theme.bodySmall).foregroundStyle(Theme.link)
                } else {
                    Text("None").foregroundStyle(Theme.textSecondary)
                }
            }
            field("Last commit", tree.lastCommitAt.map {
                "\(UsageFormat.ago($0, now: Date())) · \($0.formatted(date: .abbreviated, time: .shortened))"
            } ?? "No date")
            if let reason = tree.reason { field("Why", reason) }
            if let hold = tree.hold { field("Clean up", "Keeps it: \(hold.prefix(1).lowercased() + hold.dropFirst())") }
            field("Size", WorktreeFormat.bytes(tree.sizeBytes))
            field("Rebuildable", WorktreeFormat.bytes(tree.rebuildableBytes))
            field("Working tree", workingTree)
            field("Lock", tree.locked ? (tree.lockReason ?? "Locked, no reason given") : "Not locked")
            row("Harness") {
                HStack(spacing: 6) {
                    if HarnessIcon.exists(for: tree.harness) { HarnessIcon(harness: tree.harness, size: 14) }
                    Text(WorktreeFormat.harness(tree.harness))
                }
            }
        }
    }

    private var workingTree: String {
        if !tree.dirty && tree.untracked == 0 { return "Clean" }
        var parts: [String] = []
        if tree.dirty { parts.append("Uncommitted changes") }
        if tree.untracked > 0 { parts.append("\(tree.untracked) untracked") }
        return parts.joined(separator: ", ")
    }

    private func field(_ label: String, _ value: String, mono: Bool = false) -> some View {
        row(label) {
            Text(value)
                .font(mono ? Theme.font(size: 12, design: .monospaced) : Theme.bodySmall)
                .foregroundStyle(Theme.textPrimary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func row<V: View>(_ label: String, @ViewBuilder value: () -> V) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .frame(width: 96, alignment: .leading)
            value().font(Theme.bodySmall).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var actions: some View {
        VStack(alignment: .leading, spacing: 10) {
            FlowLayout(spacing: 8, lineSpacing: 8) {
                Button("Open in Terminal", systemImage: "terminal") { act(.terminal) }.secondaryButton()
                Button("Reveal in Finder", systemImage: "folder") { act(.reveal) }.secondaryButton()
                Button("Copy path", systemImage: "doc.on.doc") { act(.copyPath) }.secondaryButton()
            }
            FlowLayout(spacing: 8, lineSpacing: 8) {
                if tree.locked {
                    if tree.canUnlock {
                        Button("Unlock", systemImage: "lock.open") { act(.unlock) }.secondaryButton()
                    }
                } else if tree.canLock {
                    Button("Lock…", systemImage: "lock") { act(.lock) }.secondaryButton()
                }
                if tree.state.supportsMoveAndSlim {
                    Button("Move…", systemImage: "arrow.right.circle") { act(.move) }
                        .secondaryButton().disabled(!tree.canMove)
                        .hoverTip(tree.locked ? "Unlock it before moving." : "Move the folder somewhere else.",
                                  replacesLabel: false)
                    Button("Slim…", systemImage: "scissors") { act(.slim) }
                        .secondaryButton().disabled(!tree.canSlim)
                        .hoverTip(!tree.canSlim ? "Nothing rebuildable to remove."
                                  : "Delete node_modules, virtual environments and build output. They rebuild.",
                                  replacesLabel: false)
                }
                if tree.state.isRemovable {
                    Button("Remove…", systemImage: "trash", role: .destructive) { act(.remove) }
                        .secondaryButton().disabled(!tree.canRemove)
                        .hoverTip(tree.removalBlock ?? "Delete this worktree's folder.", replacesLabel: false)
                }
            }
            if tree.state == .orphaned {
                VStack(alignment: .leading, spacing: 8) {
                    Text("swe can't remove an orphaned worktree. Run git worktree prune in the main checkout.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(tree.pruneCommand)
                        .font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textPrimary)
                        .textSelection(.enabled)
                        .padding(.vertical, 6).padding(.horizontal, 8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.code, in: .rect(cornerRadius: Theme.smallRadius))
                    Button("Copy command", systemImage: "doc.on.doc") { act(.copyPruneCommand) }.secondaryButton()
                }
            }
            if busy {
                HStack(spacing: 6) { ProgressView().controlSize(.small); Text("Working…").font(Theme.caption) }
                    .foregroundStyle(Theme.textSecondary)
            }
        }
    }
}

// MARK: - Sheets

/// Picks the sheet and hands it the page's model through the environment.
private struct WorktreeSheetHost: View {
    var sheet: WorktreeSheet
    var created: (String) -> Void

    var body: some View {
        switch sheet {
        case .new: NewWorktreeSheet(created: created)
        case .prune: PruneSheet()
        case .remove(let tree): RemoveSheet(tree: tree)
        case .lock(let tree): LockSheet(tree: tree)
        case .move(let tree): MoveSheet(tree: tree)
        case .slim(let tree): SlimSheet(tree: tree)
        }
    }
}

private struct SheetButtons<Extra: View>: View {
    var cancelTitle = "Cancel"
    var working: Bool
    @ViewBuilder var extra: Extra
    @Environment(\.dismissSheet) private var dismiss

    var body: some View {
        HStack(spacing: 8) {
            Spacer()
            Button(cancelTitle) { dismiss() }.secondaryButton().disabled(working)
            extra
        }
    }
}

private struct NewWorktreeSheet: View {
    var created: (String) -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var draft = NewWorktreeDraft()
    @State private var working = false
    @State private var error: String?
    @State private var touched = false
    /// A repository swe hasn't listed yet: typed as a path.
    @State private var otherRepo = false

    private var model: WorktreesModel { app.worktrees }
    private static let otherTag = "\u{0}other"

    private var repoChoice: Binding<String> {
        Binding(get: { otherRepo ? Self.otherTag : draft.repoPath },
                set: { value in
                    if value == Self.otherTag {
                        otherRepo = true
                        draft.repoPath = ""
                    } else {
                        otherRepo = false
                        draft.repoPath = value
                    }
                })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New worktree").font(Theme.sectionTitle).tracking(-0.5)
            FormField("Repository") {
                VStack(alignment: .leading, spacing: 8) {
                    if !model.repos.isEmpty {
                        SelectMenu(options: model.repos.map { ($0.path, $0.name) } + [(Self.otherTag, "Other repository…")],
                                   selection: repoChoice, width: nil)
                    }
                    if otherRepo || model.repos.isEmpty {
                        TextField("/path/to/repository", text: $draft.repoPath).sweField(monospaced: true)
                    }
                }
            }
            Tabs(options: NewWorktreeDraft.Mode.allCases.map { ($0, $0.title) },
                 selection: $draft.mode, style: .segmented, fillWidth: true)
            switch draft.mode {
            case .existingBranch:
                FormField("Branch", hint: "A branch that already exists, local or on the remote.") {
                    TextField("feature/login", text: $draft.branch).sweField()
                }
            case .newBranch:
                FormField("New branch") {
                    TextField("feature/login", text: $draft.branch).sweField()
                }
                FormField("Base ref", hint: "Optional. Leave empty to start from the repository's default branch.") {
                    TextField("origin/main", text: $draft.base).sweField()
                }
            case .pullRequest:
                FormField("Pull request number", hint: "Checks out the pull request's branch.") {
                    TextField("1912", text: $draft.pullRequest).sweField()
                }
            }
            FormField("Folder", hint: "Optional. Leave empty to let swe choose where it goes.") {
                TextField("~/Projects/worktrees/feature-login", text: $draft.path).sweField(monospaced: true)
            }
            if touched, let problem = draft.problem {
                Text(problem).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).textSelection(.enabled) }
            SheetButtons(working: working) {
                Button(working ? "Creating…" : "Create") { create() }
                    .primaryButton().disabled(working)
            }
        }
        .sheetScaffold(width: 480)
        .onSheetDismissRequest { if !working { dismiss() } }
        .onAppear {
            if draft.repoPath.isEmpty { draft.repoPath = model.repos.first?.path ?? "" }
            otherRepo = model.repos.isEmpty
            if case .repo(let path) = model.scope { draft.repoPath = path }
        }
    }

    private func create() {
        touched = true
        guard draft.isComplete, !working else { return }
        working = true
        error = nil
        Task {
            do {
                let tree = try await model.add(draft, swe: app.swe)
                created(tree.path)
                dismiss()
            } catch {
                self.error = WorktreeFailure.describe(error).message
            }
            working = false
        }
    }
}

private struct RemoveSheet: View {
    var tree: Worktree
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var forced = false
    @State private var deleteBranch = false
    @State private var working = false
    @State private var error: String?

    private var model: WorktreesModel { app.worktrees }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Remove \(tree.title)?").font(Theme.sectionTitle).tracking(-0.5)
            VStack(alignment: .leading, spacing: 6) {
                Text(tree.sizeBytes.map { "This deletes the worktree's folder, \(WorktreeFormat.bytes($0)) on disk:" }
                     ?? "This deletes the worktree's folder from disk:")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                Text(displayPath(tree.path))
                    .font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textPrimary)
                    .textSelection(.enabled)
                    .padding(.vertical, 6).padding(.horizontal, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.code, in: .rect(cornerRadius: Theme.smallRadius))
                Text("The repository and every other worktree stay as they are.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            if tree.removalNeedsForce { forceBox } else {
                Label("Merged and clean: nothing in this folder is left to lose.", systemImage: "checkmark.circle")
                    .font(Theme.caption).foregroundStyle(Theme.accent)
            }
            if let branch = tree.branch {
                Toggle("Also delete the branch \(branch)", isOn: $deleteBranch)
                    .toggleStyle(.checkbox).font(Theme.bodySmall)
            }
            if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).textSelection(.enabled) }
            SheetButtons(working: working) {
                Button(working ? "Removing…" : "Remove", role: .destructive) { remove() }
                    .primaryButton()
                    .disabled(working || (tree.removalNeedsForce && !forced))
            }
        }
        .sheetScaffold(width: 480)
        .onSheetDismissRequest { if !working { dismiss() } }
    }

    /// Anything but merged-clean can lose work, so Remove stays off until
    /// the person ticks the second box that says so.
    private var forceBox: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("\(tree.state.title): removing it can lose work", systemImage: "exclamationmark.triangle")
                .font(Theme.bodySmall).foregroundStyle(Theme.dangerText)
            VStack(alignment: .leading, spacing: 4) {
                ForEach(tree.removalLosses, id: \.self) { line in
                    Text("• \(line)").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            Toggle("Remove it anyway (runs swe wt remove --force)", isOn: $forced)
                .toggleStyle(.checkbox).font(Theme.bodySmall)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.danger.opacity(0.08), in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
            .strokeBorder(Theme.danger.opacity(0.3), lineWidth: 1))
    }

    private func remove() {
        working = true
        error = nil
        Task {
            do {
                try await model.remove(tree, force: forced, deleteBranch: deleteBranch, swe: app.swe)
                dismiss()
            } catch {
                self.error = WorktreeFailure.describe(error).message
            }
            working = false
        }
    }
}

private struct LockSheet: View {
    var tree: Worktree
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var reason = ""
    @State private var working = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Lock \(tree.title)").font(Theme.sectionTitle).tracking(-0.5)
            Text("A locked worktree is skipped by clean up and can't be removed until you unlock it.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            FormField("Reason", hint: "Optional. Shown on the row and in the details.") {
                TextField("Waiting on design review", text: $reason).sweField()
                    .disabled(working)
                    .onSubmit(lock)
            }
            if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).textSelection(.enabled) }
            SheetButtons(working: working) {
                Button(working ? "Locking…" : "Lock", action: lock).primaryButton().disabled(working)
            }
        }
        .sheetScaffold(width: 440)
        .onSheetDismissRequest { if !working { dismiss() } }
    }

    private func lock() {
        guard !working else { return }
        working = true
        error = nil
        Task {
            do {
                try await app.worktrees.lock(tree, reason: reason, swe: app.swe)
                dismiss()
            } catch {
                self.error = WorktreeFailure.describe(error).message
            }
            working = false
        }
    }
}

private struct MoveSheet: View {
    var tree: Worktree
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var newPath: String
    @State private var working = false
    @State private var error: String?

    init(tree: Worktree) {
        self.tree = tree
        _newPath = State(initialValue: tree.path)
    }

    private var problem: String? {
        let trimmed = newPath.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return "Enter the new folder." }
        if !trimmed.hasPrefix("/") && !trimmed.hasPrefix("~") { return "Use a full path, starting with / or ~." }
        if trimmed == tree.path { return "That is where it already is." }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Move \(tree.title)").font(Theme.sectionTitle).tracking(-0.5)
            Text("git moves the folder and updates the repository. Open editors and terminals in the old folder lose it.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            FormField("New folder", hint: problem, problem: problem != nil && newPath != tree.path) {
                TextField("/path/to/new/folder", text: $newPath).sweField(monospaced: true)
                    .onSubmit(move)
            }
            if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).textSelection(.enabled) }
            SheetButtons(working: working) {
                Button(working ? "Moving…" : "Move", action: move)
                    .primaryButton().disabled(working || problem != nil)
            }
        }
        .sheetScaffold(width: 480)
        .onSheetDismissRequest { if !working { dismiss() } }
    }

    private func move() {
        guard problem == nil, !working else { return }
        working = true
        error = nil
        Task {
            do {
                try await app.worktrees.move(tree, to: newPath, swe: app.swe)
                dismiss()
            } catch {
                self.error = WorktreeFailure.describe(error).message
            }
            working = false
        }
    }
}

private struct SlimSheet: View {
    var tree: Worktree
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var preview: Loadable<SlimReport> = .loading
    @State private var done: SlimReport?
    @State private var working = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Slim \(tree.title)").font(Theme.sectionTitle).tracking(-0.5)
            Text("Deletes folders that rebuild on the next install or build. Your code and git history stay.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            if let done {
                Label("\(WorktreeFormat.freed(done.freedBytes)).", systemImage: "checkmark.circle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.accent)
            } else {
                switch preview {
                case .idle, .loading:
                    LoadingCard(text: "Checking what would go…", framed: false)
                case .failed(let message):
                    ErrorCard(message: message, framed: false)
                case .loaded(let report):
                    if report.dirs.isEmpty {
                        EmptyCard(text: "Nothing rebuildable to remove here.", icon: "checkmark.circle", framed: false)
                    } else {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(report.dirs) { dir in
                                HStack {
                                    Text(displayPath(dir.path))
                                        .font(Theme.font(size: 12, design: .monospaced))
                                        .lineLimit(1).truncationMode(.middle)
                                    Spacer(minLength: 8)
                                    Text(WorktreeFormat.bytes(dir.bytes))
                                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                }
                            }
                        }
                        .padding(10)
                        .background(Theme.code, in: .rect(cornerRadius: Theme.smallRadius))
                    }
                }
            }
            if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).textSelection(.enabled) }
            SheetButtons(cancelTitle: done == nil ? "Cancel" : "Done", working: working) {
                if done == nil, case .loaded(let report) = preview, !report.dirs.isEmpty {
                    Button(working ? "Slimming…" : report.freedBytes.map { "Free \(WorktreeFormat.bytes($0))" } ?? "Free space") { slim() }
                        .primaryButton().disabled(working)
                }
            }
        }
        .sheetScaffold(width: 480)
        .onSheetDismissRequest { if !working { dismiss() } }
        .task {
            do {
                let report = try await app.worktrees.slimPreview(tree, swe: app.swe)
                if !Task.isCancelled { preview = .loaded(report) }
            } catch is CancellationError {
                return
            } catch {
                if !Task.isCancelled { preview = .failed(WorktreeFailure.describe(error).message) }
            }
        }
    }

    private func slim() {
        working = true
        error = nil
        Task {
            do { done = try await app.worktrees.slim(tree, swe: app.swe) }
            catch { self.error = WorktreeFailure.describe(error).message }
            working = false
        }
    }
}

private struct PruneSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var preview: Loadable<PruneReport> = .loading
    @State private var done: CleanupResult?
    @State private var working = false
    @State private var error: String?

    /// Merged worktrees still holding files: bulk clean up never forces.
    private var leftOut: Int { app.worktrees.worktrees.filter { $0.state == .mergedDirty }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Clean up merged worktrees").font(Theme.sectionTitle).tracking(-0.5)
            Text("Removes worktrees whose pull request merged and whose folder is clean. Locked ones are skipped.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            if let done { result(done) } else { previewBody }
            if let error { Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).textSelection(.enabled) }
            SheetButtons(cancelTitle: done == nil ? "Cancel" : "Done", working: working) {
                if done == nil, case .loaded(let report) = preview, !report.candidates.isEmpty {
                    Button(working ? "Removing…" : "Remove \(WorktreeFormat.count(report.candidates.count))",
                           role: .destructive) { run(report.candidates) }
                        .primaryButton().disabled(working)
                }
            }
        }
        .sheetScaffold(width: 540)
        .onSheetDismissRequest { if !working { dismiss() } }
        .task { await loadPreview() }
    }

    @ViewBuilder private var previewBody: some View {
        switch preview {
        case .idle, .loading:
            LoadingCard(text: "Finding merged worktrees…", framed: false)
        case .failed(let message):
            ErrorCard(message: message, framed: false)
        case .loaded(let report):
            if report.candidates.isEmpty {
                EmptyCard(text: "Nothing to clean up. No merged, clean worktree is left.",
                          icon: "checkmark.circle", framed: false)
            } else {
                Text("\(WorktreeFormat.count(report.candidates.count)) will go, freeing about \(WorktreeFormat.bytes(report.freedBytes)).")
                    .font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(report.candidates) { tree in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(tree.title).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                                    Text("\(tree.repoName) · \(displayPath(tree.path))")
                                        .font(Theme.font(size: 11, design: .monospaced))
                                        .foregroundStyle(Theme.textMuted).lineLimit(1).truncationMode(.middle)
                                }
                                Spacer(minLength: 8)
                                Text(WorktreeFormat.bytes(tree.sizeBytes))
                                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            }
                        }
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 220)
                .background(Theme.code, in: .rect(cornerRadius: Theme.smallRadius))
            }
            if !report.skipped.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("\(WorktreeFormat.count(report.skipped.count)) clean up leaves alone").font(Theme.caption)
                        .foregroundStyle(Theme.textSecondary)
                    ScrollView {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(report.skipped) { skipped in
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(displayPath(skipped.path))
                                        .font(Theme.font(size: 11, design: .monospaced))
                                        .foregroundStyle(Theme.textSecondary)
                                        .lineLimit(1).truncationMode(.middle)
                                    Text(skipped.reason).font(Theme.caption).foregroundStyle(Theme.textMuted)
                                        .lineLimit(2)
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: 110)
                }
            }
        }
        if leftOut > 0 {
            Label("Merged worktrees with uncommitted changes are left out. Delete each one from its own row if you are sure.",
                  systemImage: "info.circle")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func result(_ report: CleanupResult) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Removed \(WorktreeFormat.count(report.removed.count)). \(WorktreeFormat.freed(report.freedBytes)).",
                  systemImage: "checkmark.circle")
                .font(Theme.bodySmall).foregroundStyle(Theme.accent)
            if !report.skipped.isEmpty {
                ForEach(report.skipped) { skipped in
                    Text("Skipped \(displayPath(skipped.path)): \(skipped.reason)")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }

    private func loadPreview() async {
        preview = .loading
        do {
            let report = try await app.worktrees.prunePreview(swe: app.swe)
            // A newer preview (the toggle changed) owns the result.
            if !Task.isCancelled { preview = .loaded(report) }
        } catch is CancellationError {
            return
        } catch {
            if !Task.isCancelled { preview = .failed(WorktreeFailure.describe(error).message) }
        }
    }

    /// Removes exactly what the preview showed.
    private func run(_ candidates: [Worktree]) {
        guard !working else { return }
        working = true
        error = nil
        Task {
            do { done = try await app.worktrees.cleanUp(candidates, swe: app.swe) }
            catch { self.error = WorktreeFailure.describe(error).message }
            working = false
        }
    }
}
