import SwiftUI
import XCTest
@testable import SWERemote

/// The rows and controls that rearrange above the default text size:
/// unchanged at the default, wrapping or stacking above it.
@MainActor
final class LargeTextLayoutTests: XCTestCase {
    func testFlowLayoutWrapsWhatDoesNotFit() {
        // Five 50pt boxes in 120pt: two a line, three lines of 10pt
        // with 4pt between.
        let flow = FlowLayout(spacing: 6, lineSpacing: 4) {
            ForEach(0..<5, id: \.self) { _ in Color.black.frame(width: 50, height: 10) }
        }
        XCTAssertEqual(size(flow.frame(width: 120, alignment: .leading)).height, 38)
        // All five fit one line of 274pt.
        XCTAssertEqual(size(flow.fixedSize()), CGSize(width: 274, height: 10))
    }

    func testFlowLayoutGivesAnOverlongViewTheLine() {
        let sentence = Text("A sentence far too long to fit on one line of this width").font(Theme.caption)
        let flow = FlowLayout(spacing: 6, lineSpacing: 6) {
            Color.black.frame(width: 30, height: 10)
            sentence
        }
        // The sentence gets a line of its own, 100pt wide, and wraps in it.
        let wrapped = size(sentence.frame(width: 100, alignment: .leading).fixedSize(horizontal: false, vertical: true))
        XCTAssertGreaterThan(wrapped.height, size(sentence.fixedSize()).height)
        XCTAssertEqual(size(flow.frame(width: 100, alignment: .leading).fixedSize(horizontal: false, vertical: true)),
                       CGSize(width: 100, height: 10 + 6 + wrapped.height))
    }

    func testRowsAreUnchangedAtTheDefaultSizeAndWrapAbove() {
        let wrapping = WrappingHStack(spacing: 6) { details }.lineLimit(1).frame(width: 140, alignment: .leading)
        let plain = HStack(spacing: 6) { details }.lineLimit(1).frame(width: 140, alignment: .leading)
        XCTAssertEqual(png(wrapping, .large), png(plain, .large))
        XCTAssertGreaterThan(size(wrapping.dynamicTypeSize(.xxxLarge)).height,
                             size(plain.dynamicTypeSize(.xxxLarge)).height)
    }

    func testTitleLinesGrowOnlyAboveTheDefaultSize() {
        let title = "Fix the flaky checkout test that times out on CI when the mock is slow"
        let growing = Text(title).font(Theme.rowTitle).lineLimit(1, larger: 3).frame(width: 200)
        let one = Text(title).font(Theme.rowTitle).lineLimit(1).frame(width: 200)
        XCTAssertEqual(png(growing, .large), png(one, .large))
        XCTAssertGreaterThan(size(growing.dynamicTypeSize(.xxLarge)).height,
                             size(one.dynamicTypeSize(.xxLarge)).height)
    }

    func testStackingRowStacksOnlyAtAccessibilitySizes() {
        let row = StackingRow { Color.black.frame(width: 50, height: 10); Color.black.frame(width: 50, height: 10) }
        XCTAssertEqual(size(row.fixedSize().dynamicTypeSize(.xxxLarge)), CGSize(width: 108, height: 10))
        XCTAssertEqual(size(row.fixedSize().dynamicTypeSize(.accessibility1)), CGSize(width: 50, height: 28))
    }

    func testAStaleNoticeGrowsPastTwoLinesAtLargerSizes() {
        let error = "Can't reach the hub. Check that Tailscale is on, then pull to refresh the list again."
        let notice = StaleNotice(error: error).frame(width: 200)
        let twoLines = Text("A\nB").font(Theme.caption).lineLimit(2).frame(width: 200)
        // Capped at two lines, the advice at the end was what got cut.
        XCTAssertGreaterThan(size(notice.dynamicTypeSize(.accessibility1)).height,
                             size(twoLines.dynamicTypeSize(.accessibility1)).height)
    }

    /// The `@` and `/` menu rows and the queued tray's rows keep their
    /// height at the default size and grow with the text above it; a
    /// fixed height clipped their words at accessibility sizes.
    func testPromptMenuAndTrayRowsGrowWithTheText() {
        let mention = MentionRow(entry: FileEntry(path: "src/app/main.py"))
        let command = CommandRow(command: SlashCommand(name: "review", description: "Review the open diff",
                                                       source: "project", argumentHint: "[file]"))
        let queued = QueuedRow(send: QueuedSend(clientId: "a", text: "run the tests again", phase: .waiting),
                               editing: false, onEdit: {}, onRemove: {}, onSendNow: {})
        XCTAssertEqual(size(mention.frame(width: 340).dynamicTypeSize(.large)).height, MentionMenu.rowHeight)
        XCTAssertEqual(size(command.frame(width: 340).dynamicTypeSize(.large)).height, CommandMenuView.rowHeight)
        XCTAssertEqual(size(queued.frame(width: 340).dynamicTypeSize(.large)).height, QueuedTray.rowHeight)
        for (name, large, huge) in [
            ("mention", size(mention.frame(width: 340).dynamicTypeSize(.large)).height,
             size(mention.frame(width: 340).dynamicTypeSize(.accessibility3)).height),
            ("command", size(command.frame(width: 340).dynamicTypeSize(.large)).height,
             size(command.frame(width: 340).dynamicTypeSize(.accessibility3)).height),
            ("queued", size(queued.frame(width: 340).dynamicTypeSize(.large)).height,
             size(queued.frame(width: 340).dynamicTypeSize(.accessibility3)).height),
        ] {
            XCTAssertGreaterThan(huge, large * 1.5, name)
        }
    }

    /// The menu over the keyboard grows for a grown row or two, but
    /// never past its default height: more grown rows scroll inside it.
    func testThePromptMenuWindowNeverOutgrowsItsDefaultHeight() {
        func card(_ n: Int) -> some View {
            PromptMenuCard(count: n, rowHeight: CommandMenuView.rowHeight, visibleRows: 5) {
                ForEach(0..<n, id: \.self) { i in
                    CommandRow(command: SlashCommand(name: "command\(i)", description: "What it does"))
                }
            }
            .frame(width: 340)
        }
        let full = size(card(5).dynamicTypeSize(.large)).height
        XCTAssertLessThanOrEqual(size(card(5).dynamicTypeSize(.accessibility5)).height, full)
        XCTAssertGreaterThan(size(card(1).dynamicTypeSize(.accessibility3)).height,
                             size(card(1).dynamicTypeSize(.large)).height)
    }

    @ViewBuilder private var details: some View {
        Text("#1214")
        Text("· c-wenlong")
        Text("· 2 comments")
        Text("· 6h")
    }

    private func size<V: View>(_ view: V) -> CGSize {
        let renderer = ImageRenderer(content: view)
        renderer.scale = 1
        return renderer.uiImage?.size ?? .zero
    }

    private func png<V: View>(_ view: V, _ type: DynamicTypeSize) -> Data? {
        let renderer = ImageRenderer(content: view.font(Theme.caption).background(.white).dynamicTypeSize(type))
        renderer.scale = 2
        return renderer.uiImage?.pngData()
    }
}
