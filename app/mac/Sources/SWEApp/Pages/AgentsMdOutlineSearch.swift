import SWECore
import SwiftUI

/// The Edit tab outline's search text, keyboard cursor and focus, kept
/// apart from the rail's drag session. The search never touches the
/// stored folder states or the selected chapter, so clearing it leaves
/// the outline exactly as it was.
@MainActor @Observable
final class OutlineNav {
    var query = ""
    /// The row the arrow keys are on; follows clicks too.
    var cursor: String?
    /// The outline (not the search field or the editor) holds focus.
    var focused = false

    var searching: Bool {
        !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// A folder's stored open/closed state; open unless set.
    static func isExpanded(_ path: String) -> Bool {
        UserDefaults.standard.object(
            forKey: ChapterFolderState.outlinePrefix + path) as? Bool ?? true
    }

    static func setExpanded(_ path: String, _ open: Bool) {
        UserDefaults.standard.set(open, forKey: ChapterFolderState.outlinePrefix + path)
    }
}

/// The search field above the outline. Down Arrow hands focus to the
/// outline; Escape clears.
struct OutlineSearchBar: View {
    @Bindable var nav: OutlineNav
    var onEnterOutline: () -> Void

    var body: some View {
        SearchField(prompt: "Filter chapters", text: $nav.query)
            .onKeyPress(.escape) {
                guard !nav.query.isEmpty else { return .ignored }
                nav.query = ""
                return .handled
            }
            .onKeyPress(.downArrow) {
                onEnterOutline()
                return .handled
            }
    }
}

/// Shown where the outline would be when the filter matches nothing.
struct OutlineNoMatches: View {
    var query: String

    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: "magnifyingglass")
                .font(Theme.font(size: 14)).foregroundStyle(Theme.textMuted)
            Text("No chapter matches \"\(query.trimmingCharacters(in: .whitespacesAndNewlines))\"")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .multilineTextAlignment(.center)
            Text("Filters by file name and path").font(Theme.caption)
                .foregroundStyle(Theme.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 18)
    }
}

/// The grip at a row's trailing edge: it takes its width always so the
/// row never reflows, and fades in under the pointer.
struct OutlineGrip: View {
    var visible: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Image(systemName: "circle.grid.2x3.fill")
            .font(Theme.font(size: 9))
            .foregroundStyle(Theme.textMuted)
            .frame(width: 14, height: 18)
            .opacity(visible ? 1 : 0)
            .animation(reduceMotion ? nil : .easeInOut(duration: Theme.fadeDuration), value: visible)
            .help("Drag to reorder or move")
            .accessibilityHidden(true)
    }
}

/// A 2pt line with a leading dot: where a dragged chapter will land.
struct OutlineInsertionLine: View {
    var body: some View {
        HStack(spacing: 0) {
            Circle().fill(Theme.accentFill).frame(width: 6, height: 6)
            Capsule().fill(Theme.accentFill).frame(height: 2)
        }
        .padding(.horizontal, 4)
        .allowsHitTesting(false)
        .transition(.opacity)
    }
}

extension ChapterRailDrag {
    /// Which edge of the file row carries the insertion line: the row the
    /// gap sits directly above takes it at its top, and a gap past the end
    /// of a level takes it at the last row's bottom.
    func insertionEdge(for path: String) -> VerticalAlignment? {
        guard dragging != nil, path != dragging,
              case .insert(let level, let index) = target,
              fileLevel[path] == level
        else { return nil }
        let others = (levelFiles[level] ?? []).filter { $0 != dragging }
        guard let position = others.firstIndex(of: path) else { return nil }
        if position == index { return .top }
        if index >= others.count && position == others.count - 1 { return .bottom }
        return nil
    }

    /// The folder a file drag will land in when it moves levels, so the
    /// destination folder reads even when the pointer is over its files.
    var landingFolder: String? {
        guard dragging != nil, !draggingFolder,
              case .insert(let level, _) = target,
              !level.isEmpty, level != sourceLevel
        else { return nil }
        return level
    }
}

/// Arrow, Return and focus handling for the outline's scroll area. It
/// acts only while the outline holds focus, so the text editor and the
/// search field keep their own arrow keys.
private struct OutlineKeyboard: ViewModifier {
    var nav: OutlineNav
    var tree: [ChapterNode]
    var selected: String?
    var focus: FocusState<Bool>.Binding
    var open: (String) -> Void

    private var expanded: (String) -> Bool {
        nav.searching ? { _ in true } : OutlineNav.isExpanded
    }

    func body(content: Content) -> some View {
        content
            .focusable()
            .focused(focus)
            .focusEffectDisabled()
            .onChange(of: focus.wrappedValue) { _, focused in
                nav.focused = focused
                if focused && nav.cursor == nil {
                    nav.cursor = selected
                        ?? ChapterOutline.visibleRows(tree, isExpanded: expanded).first?.path
                }
            }
            .onKeyPress(.upArrow) { press(.up) }
            .onKeyPress(.downArrow) { press(.down) }
            .onKeyPress(.leftArrow) { press(.left) }
            .onKeyPress(.rightArrow) { press(.right) }
            .onKeyPress(.return) { activate() }
            .onKeyPress(.escape) {
                guard nav.searching else { return .ignored }
                nav.query = ""
                return .handled
            }
    }

    private func press(_ key: ChapterOutline.Key) -> KeyPress.Result {
        let rows = ChapterOutline.visibleRows(tree, isExpanded: expanded)
        let start = nav.cursor ?? selected
        let step = ChapterOutline.step(key, from: start, rows: rows, isExpanded: expanded)
        switch step {
        case .focus(let path): nav.cursor = path
        // A filter holds every folder open; it never rewrites the stored state.
        case .collapse(let path): if !nav.searching { OutlineNav.setExpanded(path, false) }
        case .expand(let path): if !nav.searching { OutlineNav.setExpanded(path, true) }
        case .none: break
        }
        return .handled
    }

    private func activate() -> KeyPress.Result {
        let rows = ChapterOutline.visibleRows(tree, isExpanded: expanded)
        guard let path = nav.cursor ?? selected,
              let row = rows.first(where: { $0.path == path }) else { return .ignored }
        if row.isFolder {
            if !nav.searching { OutlineNav.setExpanded(path, !OutlineNav.isExpanded(path)) }
        } else {
            open(path)
        }
        return .handled
    }
}

extension View {
    func outlineKeyboard(nav: OutlineNav, tree: [ChapterNode], selected: String?,
                         focus: FocusState<Bool>.Binding,
                         open: @escaping (String) -> Void) -> some View {
        modifier(OutlineKeyboard(nav: nav, tree: tree, selected: selected,
                                 focus: focus, open: open))
    }
}
