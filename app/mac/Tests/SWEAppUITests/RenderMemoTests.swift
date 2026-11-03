import Foundation
import Testing
@testable import SWEApp
@testable import SWECore

/// The chat's parse memo: one parse per distinct text, however often a
/// row's body runs, and the same blocks a fresh parse gives.
@MainActor
@Suite struct RenderMemoTests {
    @Test func aTextIsMadeOnceThenServedFromTheMemo() {
        let memo = RenderMemo<Int>(limit: 10)
        var made = 0
        for _ in 0..<5 { _ = memo.value(for: "a") { made += 1; return 1 } }
        #expect(made == 1)
        #expect(memo.value(for: "b") { made += 1; return 2 } == 2)
        #expect(made == 2)
    }

    @Test func chatBlocksMatchAFreshParse() {
        let text = "# Title\n\nSome **bold** text.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```swift\nlet x = 1\n```"
        #expect(ChatRender.blocks(text) == ChatMarkdown.blocks(text))
        #expect(ChatRender.blocks(text) == ChatMarkdown.blocks(text))
        #expect(ChatRender.inline("a `b` c") == ChatMarkdown.inline("a `b` c"))
    }
}
