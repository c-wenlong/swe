import SWECore
import SwiftUI

/// The ⌘K card: one field, then the rows `GlobalSearch` keeps for the
/// needle — pages and their tabs, then the loaded sessions, harnesses,
/// pins, tasks, skills, plugins, MCP servers and hooks. Up and Down move
/// the lit row, Return opens it, Esc or a scrim click closes the card
/// (the sheet host's own keys). The row's trailing chip says what the
/// pick is: Page, Tab, Session and so on.
struct SearchPalette: View {
    @Environment(AppModel.self) private var app
    @State private var query = ""
    /// The lit row's index into `rows`; hovering a row also sets it.
    @State private var active = 0
    /// The rows offered, read once at open from the live page models: a
    /// session ending mid-search shouldn't shuffle the lit row under the
    /// cursor.
    @State private var items: [GlobalSearchItem] = []

    /// The rows the arrows and Return can pick: the navigation targets
    /// before a needle, the matches after.
    private var rows: [GlobalSearchItem] {
        query.isEmpty
            ? GlobalSearch.browse(items)
            : GlobalSearch.matches(query, items: items)
    }

    private var rowHeight: CGFloat {
        TextZoom.pointSize(34, at: TextZoomModel.shared.zoom)
    }
    private static let visibleRows = 9
    private static let width: CGFloat = 560

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SearchField(prompt: "Jump to a page, session, harness or anything else", text: $query)
                .padding(.horizontal, 16)
                .padding(.top, 14)
            results
                .padding(.top, 10)
                .padding(.bottom, 12)
        }
        .frame(width: Self.width)
        .onKeyPress(.upArrow, phases: .down) { _ in move(-1); return .handled }
        .onKeyPress(.downArrow, phases: .down) { _ in move(1); return .handled }
        // Return goes to the field's submit, not to onKeyPress.
        .onSubmit { open(active) }
        .onChange(of: query) { _, _ in active = 0 }
        .task { items = app.searchItems() }
    }

    private func move(_ offset: Int) {
        guard !rows.isEmpty else { return }
        active = min(max(active + offset, 0), rows.count - 1)
    }

    private func open(_ index: Int) {
        guard rows.indices.contains(index) else { return }
        app.open(rows[index].target)
    }

    @ViewBuilder private var results: some View {
        let shown = rows
        if shown.isEmpty {
            note(query.isEmpty ? "Nothing to show yet" : "No matches")
        } else if shown.count > Self.visibleRows {
            ScrollViewReader { proxy in
                ScrollView(.vertical) { stack(shown) }
                    .defaultScrollAnchor(.top)
                    .frame(height: CGFloat(Self.visibleRows) * rowHeight)
                    .onChange(of: active) { _, i in
                        proxy.scrollTo(min(i, shown.count - 1))
                    }
            }
        } else {
            stack(shown)
        }
    }

    /// A muted line in place of rows.
    private func note(_ text: String) -> some View {
        HStack(spacing: 10) {
            Text(text)
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 28)
        .frame(minHeight: rowHeight)
    }

    private func stack(_ shown: [GlobalSearchItem]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(shown.enumerated()), id: \.offset) { i, item in
                Button { open(i) } label: { row(item) }
                    .buttonStyle(.plain)
                    .background(i == min(active, shown.count - 1) ? Theme.hover : .clear)
                    .onHover { if $0 { active = i } }
                    .accessibilityAddTraits(i == min(active, shown.count - 1) ? .isSelected : [])
                    .id(i)
            }
        }
        .padding(.horizontal, 8)
    }

    private func row(_ item: GlobalSearchItem) -> some View {
        HStack(spacing: 10) {
            if let harness = item.harnessIcon {
                HarnessIcon(harness: harness, size: 16).frame(width: 18)
            } else if let symbol = item.symbol {
                Image(systemName: symbol)
                    .font(Theme.font(size: 13)).foregroundStyle(Theme.textSecondary)
                    .frame(width: 18)
            } else {
                Color.clear.frame(width: 18)
            }
            Text(item.title).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .layoutPriority(1)
            if let detail = item.detail, !detail.isEmpty {
                Text(detail).font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 8)
            Badge(item.kind.title)
        }
        .padding(.horizontal, 12)
        .frame(height: rowHeight)
        .contentShape(.rect)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([item.title, item.detail, item.kind.title]
            .compactMap { $0 }.joined(separator: ", "))
        .accessibilityHint("Opens \(item.kind.title.lowercased())")
    }
}
