import Foundation
import SWECore

/// Remembers what a pure function of a string made from it, so a view
/// body that runs again for the same text (a chat row redrawn by a
/// streaming neighbour, a hover, a scroll) reuses the work. Bounded by
/// count; NSCache also drops entries under memory pressure.
@MainActor
final class RenderMemo<Value> {
    private final class Box {
        let value: Value
        init(_ value: Value) { self.value = value }
    }

    private let cache = NSCache<NSString, Box>()

    init(limit: Int) { cache.countLimit = limit }

    func value(for key: String, make: () -> Value) -> Value {
        let key = key as NSString
        if let hit = cache.object(forKey: key) { return hit.value }
        let value = make()
        cache.setObject(Box(value), forKey: key)
        return value
    }
}

/// The chat's parsed Markdown and styled spans, memoized by their text:
/// a transcript's rows are immutable once written, and the row still
/// streaming parses once per new text rather than once per redraw.
@MainActor
enum ChatRender {
    private static let blockMemo = RenderMemo<[MarkdownBlock]>(limit: 400)
    private static let inlineMemo = RenderMemo<AttributedString>(limit: 2000)

    static func blocks(_ text: String) -> [MarkdownBlock] {
        blockMemo.value(for: text) { ChatMarkdown.blocks(text) }
    }

    static func inline(_ text: String) -> AttributedString {
        inlineMemo.value(for: text) { ChatMarkdown.inline(text) }
    }
}

/// Syntax-highlighted code blocks, memoized by language and code.
@MainActor
enum HighlightMemo {
    private static let memo = RenderMemo<AttributedString>(limit: 300)

    static func highlighted(_ code: String, language: String?) -> AttributedString {
        memo.value(for: (language ?? "") + "\u{0}" + code) {
            AttributedString(highlighting: code, language: language)
        }
    }
}
