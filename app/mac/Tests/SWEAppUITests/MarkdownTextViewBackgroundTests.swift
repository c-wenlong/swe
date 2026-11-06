import AppKit
import SwiftUI
import Testing
@testable import SWEApp

/// The Rich editor sits on the instruction field's own fill. Its text
/// view must stay transparent: NSTextView's default background drew a
/// second, darker rectangle inside the field in dark mode.
@Suite @MainActor struct MarkdownTextViewBackgroundTests {
    private func textViews(in view: NSView) -> [NSTextView] {
        (view as? NSTextView).map { [$0] } ?? view.subviews.flatMap(textViews)
    }

    @Test func theRichEditorDrawsNoBackgroundOfItsOwn() throws {
        _ = NSApplication.shared
        let window = OffscreenWindow(size: CGSize(width: 300, height: 200),
                                     styleMask: [.titled])
        let hosting = NSHostingView(rootView:
            MarkdownTextView(text: .constant("# Title\n\nBody"), isFocused: .constant(false))
                .frame(width: 280, height: 180))
        window.contentView = hosting
        window.orderFront(nil)
        defer { window.orderOut(nil) }
        #expect(spinMainRunLoop { !textViews(in: hosting).isEmpty })
        let editors = textViews(in: hosting)
        #expect(editors.count == 1)
        #expect(editors.allSatisfy { !$0.drawsBackground })
        let scrolls = editors.compactMap(\.enclosingScrollView)
        #expect(scrolls.allSatisfy { !$0.drawsBackground })
    }
}
