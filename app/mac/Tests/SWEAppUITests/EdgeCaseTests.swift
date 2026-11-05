import AppKit
import SwiftUI
import SWECore
import Testing
@testable import SWEApp

/// Small edge cases in shared app helpers.
@Suite @MainActor struct EdgeCaseTests {
    @Test func homeItselfShowsAsTilde() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        #expect(displayPath(home) == "~")
        #expect(displayPath(home + "/") == "~")
        #expect(displayPath(home + "/code") == "~/code")
        #expect(displayPath(home + "x/code") == home + "x/code")
    }

    /// A flow of chips asked to size at an unbounded width answers with
    /// the rows' own width, never infinity.
    @Test func aFlowLayoutUnderAnInfiniteProposalHasAFiniteWidth() {
        _ = NSApplication.shared
        let controller = NSHostingController(rootView: FlowLayout {
            ForEach(0..<3, id: \.self) { _ in Color.red.frame(width: 40, height: 10) }
        })
        let size = controller.sizeThatFits(in: CGSize(width: CGFloat.infinity, height: .infinity))
        #expect(size.width.isFinite)
        #expect(abs(size.width - (40 * 3 + 6 * 2)) < 1)
    }

    @Test func aNewlineReplacesTheSelection() {
        let text = "hello world"
        let start = text.index(text.startIndex, offsetBy: 5)
        let end = text.index(start, offsetBy: 1)
        let next = Caret.newline(in: text, replacing: TextSelection(range: start..<end))
        #expect(next.text == "hello\nworld")
        #expect(next.cursor == 6)
    }

    /// A caret from an older draft can fall inside a character of the
    /// current one (here mid-way through "é"); it must not be rounded to
    /// some other spot: the break goes at the end.
    @Test func aStaleCaretInsideACharacterAppends() {
        let old = "aaa"
        let caret = old.index(after: old.startIndex)
        let next = Caret.newline(in: "éa", replacing: TextSelection(insertionPoint: caret))
        #expect(next.text == "éa\n")
        #expect(next.cursor == 3)
    }

    @Test func aCaretPastTheEndAppends() {
        let old = "a much longer draft that was sent"
        let caret = old.index(old.startIndex, offsetBy: 20)
        #expect(Caret.newline(in: "hi", replacing: TextSelection(insertionPoint: caret)).text == "hi\n")
    }

    @Test func noSelectionAppends() {
        #expect(Caret.newline(in: "x", replacing: nil).text == "x\n")
    }

    /// A tick skipped behind another window learned nothing about the
    /// hub, so it must not double the retry.
    @Test func skippedTicksDoNotGrowTheRetry() {
        let first = NeedsYouCards.firstRetry
        let afterLoad = NeedsYouCards.step(loaded: true, reachable: false, retry: first)
        #expect(afterLoad.wait == first)
        #expect(afterLoad.retry == first * 2)
        let skipped = NeedsYouCards.step(loaded: false, reachable: false, retry: afterLoad.retry)
        #expect(skipped.wait == first * 2)
        #expect(skipped.retry == first * 2)
        let back = NeedsYouCards.step(loaded: true, reachable: true, retry: skipped.retry)
        #expect(back.wait == 15)
        #expect(back.retry == first)
    }
}
