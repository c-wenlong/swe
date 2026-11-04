import SWECore
import SwiftUI

/// The PR body, segmented by `GitHubMarkdown`: prose between `<details>`
/// folds renders as markdown; each fold collapses to its `<summary>`
/// line — the way GitHub renders them.
struct MarkdownBody: View {
    let segments: [MarkdownSegment]

    init(body: String) { segments = GitHubMarkdown.segments(body) }
    init(segments: [MarkdownSegment]) { self.segments = segments }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, seg in
                switch seg {
                case .markdown(let md):
                    MarkdownBlocks(blocks: GitHubMarkdown.blocks(md))
                case .details(let title, let inner):
                    DetailsSection(title: title, segments: inner)
                }
            }
        }
    }

    /// One line of inline markdown → styled runs. Apple's parser does
    /// spans fine; what it can't do is the pre-pass here: `![alt](src)`
    /// and `[![alt](badge)](href)` become plain links — nested
    /// link-image brackets parse as garbage, and an image badge is a
    /// label anyway. Lines parse one at a time so a soft break stays a
    /// rendered newline, matching how GitHub wraps bodies.
    static func inline(_ md: String) -> AttributedString {
        var md = md
        md = GitHubMarkdown.flatLinks(md)
        var out = AttributedString()
        for (i, line) in md.split(separator: "\n",
                                  omittingEmptySubsequences: false).enumerated() {
            if i > 0 { out += AttributedString("\n") }
            out += (try? AttributedString(
                markdown: String(line),
                options: .init(interpretedSyntax: .full,
                               failurePolicy: .returnPartiallyParsedIfPossible)))
                ?? AttributedString(String(line))
        }
        return webLinksOnly(out)
    }

    /// PR, issue and transcript text is someone else's: a link it holds
    /// opens through the default handler on click, so a `file:` or
    /// custom-scheme link would launch whatever it names. Only web links
    /// stay links, as every other open in the app checks `isWebURL`.
    static func webLinksOnly(_ text: AttributedString) -> AttributedString {
        var text = text
        for run in text.runs {
            if let url = run.link, !url.isWebURL {
                text[run.range].link = nil
            }
        }
        return text
    }
}

/// The body's blocks as real views: sized headings, monospaced fences,
/// a quote rail, and list markers that draw — the structures Apple's
/// fused-text render could only hint at with characters. `inline`
/// styles one span: PR and issue text takes `MarkdownBody.inline`,
/// agent chat takes `ChatMarkdown.inline`, which also folds inline
/// images and code-styles links to the agent's own files.
struct MarkdownBlocks: View {
    let blocks: [MarkdownBlock]
    var inline: @MainActor (String) -> AttributedString = MarkdownBody.inline

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case .heading(let level, let text):
                    Text(inline(text))
                        .font(Theme.font(size: headingSize(level), weight: .semibold))
                        .foregroundStyle(Theme.textPrimary)
                case .paragraph(let text):
                    Text(inline(text))
                case .code(let code, let lang):
                    VStack(alignment: .leading, spacing: 4) {
                        if let lang {
                            Text(lang).font(Theme.caption)
                                .foregroundStyle(Theme.textMuted)
                        }
                        Text(HighlightMemo.highlighted(code, language: lang))
                            .font(Theme.font(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.textPrimary)
                            .textSelection(.enabled)
                    }
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.code)
                    .clipShape(.rect(cornerRadius: 6))
                case .quote(let inner):
                    HStack(alignment: .top, spacing: 0) {
                        Capsule().fill(Theme.border).frame(width: 3)
                        MarkdownBlocks(blocks: inner, inline: inline)
                            .padding(.leading, 8)
                    }
                    .foregroundStyle(Theme.textSecondary)
                case .list(let items):
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                            HStack(alignment: .firstTextBaseline, spacing: 6) {
                                marker(item)
                                Text(inline(item.text))
                                    .foregroundStyle(Theme.textPrimary)
                            }
                            .padding(.leading, CGFloat(item.depth) * 14)
                        }
                    }
                case .table(let table):
                    MarkdownTableView(table: table, inline: inline)
                case .rule:
                    Divider().overlay(Theme.border)
                case .image(let alt, let source):
                    MarkdownNote(icon: "photo", title: alt.isEmpty ? "Image" : alt,
                                 detail: (source as NSString).lastPathComponent + " · on the machine")
                case .rendered:
                    MarkdownNote(icon: "rectangle.3.group", title: "Rendered view",
                                 detail: "Open the session on the machine to see it")
                }
            }
        }
    }

    private func headingSize(_ level: Int) -> CGFloat {
        [17, 15, 14, 13, 13, 13][min(max(level, 1), 6) - 1]
    }

    @ViewBuilder
    private func marker(_ item: MarkdownListItem) -> some View {
        switch item.kind {
        case .bullet:
            Text("•").foregroundStyle(Theme.textSecondary)
        case .ordered(let n):
            Text("\(n).").foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
        case .task(let done):
            Image(systemName: done ? "checkmark.square.fill" : "square")
                .foregroundStyle(done ? Theme.success : Theme.textMuted)
                .font(Theme.font(size: 11))
        }
    }
}

/// A `<details>` block: the `<summary>` line behind the same fold
/// affordance as Checks, its segments rendered inside when opened.
private struct DetailsSection: View {
    let title: String
    let segments: [MarkdownSegment]
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { open.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "chevron.right")
                        .font(Theme.font(size: 9, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                        .rotationEffect(open ? .degrees(90) : .zero)
                    Text(MarkdownBody.inline(title))
                        .font(Theme.bodySmall)
                        .foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                }
            }
            .plainButton()
            if open {
                MarkdownBody(segments: segments)
                    .padding(.leading, 12)
            }
        }
    }
}

/// A Markdown table in a hairline box, the phone's `TableBlock`.
/// Columns size to their content up to a cap and wrap past it; when the
/// table is wider than the view it scrolls sideways inside the box, so
/// the chat itself never does.
private struct MarkdownTableView: View {
    let table: MarkdownTable
    var inline: @MainActor (String) -> AttributedString = MarkdownBody.inline

    static let maxColumnWidth: CGFloat = 240
    /// The grid is built eagerly, so a malformed or huge table is cut to
    /// this many columns and rows, with a note saying what was left out.
    static let maxColumns = 24
    static let maxRows = 200

    var body: some View {
        let columns = min(table.columnCount, Self.maxColumns)
        let rows = table.rows.prefix(Self.maxRows)
        let hiddenRows = table.rows.count - rows.count
        let hiddenColumns = table.columnCount - columns
        VStack(alignment: .leading, spacing: 4) {
            ScrollView(.horizontal, showsIndicators: true) {
                Grid(alignment: .topLeading, horizontalSpacing: 0, verticalSpacing: 0) {
                    GridRow {
                        ForEach(0..<columns, id: \.self) { c in
                            cell(c < table.header.count ? table.header[c] : "", column: c, header: true)
                        }
                    }
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                        Rectangle().fill(Theme.border).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                        GridRow {
                            ForEach(0..<columns, id: \.self) { c in
                                cell(c < row.count ? row[c] : "", column: c, header: false)
                            }
                        }
                    }
                }
            }
            .scrollBounceBehavior(.basedOnSize, axes: .horizontal)
            // The chat sets .defaultScrollAnchor(.bottom) so it opens at
            // the newest message; scroll views inside inherit it, and for
            // a sideways one "bottom" is the horizontal middle. Tables
            // start at their left.
            .defaultScrollAnchor(.leading)
            .background(Theme.card, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
            .clipShape(.rect(cornerRadius: Theme.controlRadius))
            if hiddenRows > 0 || hiddenColumns > 0 {
                Text([hiddenRows > 0 ? "\(hiddenRows) more rows" : nil,
                      hiddenColumns > 0 ? "\(hiddenColumns) more columns" : nil]
                    .compactMap { $0 }.joined(separator: " and ") + " not shown")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }

    private func cell(_ text: String, column: Int, header: Bool) -> some View {
        let align = column < table.align.count ? table.align[column] : .leading
        return CappedWidth(max: Self.maxColumnWidth) {
            Text(inline(text))
                .font(header ? Theme.control : Theme.caption)
                .foregroundStyle(header ? Theme.textPrimary : Theme.textLabel)
                .multilineTextAlignment(align == .trailing ? .trailing : align == .center ? .center : .leading)
        }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            // Fill the column so the header shading spans the whole row.
            .frame(maxWidth: .infinity, alignment: align == .trailing ? .trailing : align == .center ? .center : .leading)
            .background(header ? Theme.subtle : .clear)
            .gridColumnAlignment(align == .trailing ? .trailing : align == .center ? .center : .leading)
    }
}

/// A quiet one-line card for content the app can't show itself — the
/// phone's `NoteChip`: a `![alt](src)` the agent wrote or a harness's
/// `<json-render>` view.
private struct MarkdownNote: View {
    let icon: String
    let title: String
    let detail: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .font(Theme.font(size: 14))
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(2)
                Text(detail).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

/// Its content at its natural width, up to `max`, wrapping past it. A
/// horizontal ScrollView offers unlimited width, and a plain
/// `.frame(maxWidth:)` would let a long cell run on one line forever.
private struct CappedWidth: Layout {
    var max: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let child = subviews.first else { return .zero }
        let ideal = child.sizeThatFits(.unspecified)
        let width = min(ideal.width, max)
        let size = child.sizeThatFits(ProposedViewSize(width: width, height: nil))
        return CGSize(width: width, height: size.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(width: bounds.width, height: bounds.height))
    }
}
