import XCTest
@testable import SWERemote

/// A streaming reply's Markdown: parsed again only after its last
/// settled blank line, with the same blocks a full parse gives.
final class MarkdownStreamTests: XCTestCase {
    /// Replies built to cross every block the parser keeps open across a
    /// blank line, plus the #1562 fence and table edge cases.
    static let samples: [String] = [
        """
        # Plan

        First a paragraph with **bold** and a [link](https://example.com/a).
        It runs on.

        - one
        - two
          continued
          ```swift
          let x = 1

          let y = 2
          ```
        - three

        ```text
        ```swift build

        still code
        ````
        after the fence

        | a | b |
        |---|:-:|
        | 1 | 2 |
        > Warning: a | b

        | a |
        |---|
        | 1 |
        ```a|b
        x
        ```

        <json-render>{"root":"d",

        "elements":{}}</json-render> Saved successfully.

        ![chart](out/chart.png)

        ---

        > quoted
        > more

        1. first
        2) second

        Done: see https://github.com/c-wenlong/swe/pull/1 and `https://in.code/x`.
        """,
        "~~~\nunclosed\n\nfence that never closes\n\nso it all stays code",
        "para\r\n\r\nnext\r\n\r\n```\r\ncode\r\n\r\n```\r\n",
        "\n\n\n  \n\ttext after blank and space-only lines\n\n",
        "- [ ] task\n- [x] done\n\n## Heading ##\n\nC# and #123 stay\n",
    ]

    func testEveryPrefixParsesLikeTheWholeText() {
        for sample in Self.samples {
            for step in [1, 3, 17] {
                let stream = Markdown.Stream()
                var end = sample.startIndex
                while true {
                    let prefix = String(sample[..<end])
                    XCTAssertEqual(stream.update(prefix), Markdown.parse(prefix),
                                   "step \(step) at \(prefix.count) characters of \(sample.prefix(20))")
                    if end == sample.endIndex { break }
                    end = sample.index(end, offsetBy: step, limitedBy: sample.endIndex) ?? sample.endIndex
                }
            }
        }
    }

    func testTextThatDoesNotExtendTheLastParsesFromTheStart() {
        let stream = Markdown.Stream()
        stream.update("first paragraph\n\nsecond\n\nthird")
        // An edit above the settled part: nothing from before may survive.
        let edited = "changed\n\nsecond\n\nthird and more"
        XCTAssertEqual(stream.update(edited), Markdown.parse(edited))
        XCTAssertEqual(stream.update("other"), [.paragraph("other")])
        XCTAssertEqual(stream.update(""), [])
    }

    func testAStreamingReplyIsParsedAboutOnce() {
        // A 16 KB reply arriving over a minute, repainted every 80 ms: a
        // full parse per repaint reads every byte again each time.
        let reply = Self.longReply(bytes: 16_000)
        let ticks = 750
        let stream = Markdown.Stream()
        var full = 0
        let utf8 = Array(reply.utf8)
        for t in 1...ticks {
            let end = utf8.count * t / ticks
            let prefix = String(decoding: utf8[..<end], as: UTF8.self)
            full += prefix.utf8.count
            stream.update(prefix)
        }
        XCTAssertEqual(stream.blocks, Markdown.parse(reply))
        // The full re-parses read about 6 MB; the stream reads a little
        // over the reply once plus the open paragraph each time.
        XCTAssertGreaterThan(full, 5_000_000)
        XCTAssertLessThan(stream.parsedLength, full / 20)
    }

    func testStreamedLinkCardsMatchTheWholeText() {
        let text = Self.samples[0]
        XCTAssertEqual(LinkCards.urls(in: Markdown.Stream().update(text)), LinkCards.urls(in: text))
        XCTAssertEqual(LinkCards.urls(in: text).map(\.absoluteString),
                       ["https://example.com/a", "https://github.com/c-wenlong/swe/pull/1"])
    }

    func testInlineStylingIsTheSameFromTheCache() {
        let s = "Read [AGENTS.md](/Users/k/AGENTS.md) and **this** `code`."
        let first = RichText.inline(s)
        XCTAssertEqual(RichText.inline(s), first)
        XCTAssertEqual(String(first.characters), "Read AGENTS.md and this code.")
    }

    /// Paragraphs, lists, a table and fenced code, like an agent's long
    /// summary, about `bytes` long.
    static func longReply(bytes: Int) -> String {
        var out = ""
        var n = 0
        while out.utf8.count < bytes {
            n += 1
            out += "## Step \(n)\n\n"
            out += "I read `src/module\(n).py` and changed how it handles **retries**; "
            out += "the old loop slept a fixed second, see https://example.com/issue/\(n).\n"
            out += "It now backs off instead.\n\n"
            out += "- kept the public API\n- added a test\n  for the slow path\n\n"
            out += "| file | lines |\n|---|--:|\n| a.py | \(n * 3) |\n| b.py | \(n * 7) |\n\n"
            out += "```python\ndef retry(f):\n    for i in range(3):\n\n        f()\n```\n\n"
        }
        return out
    }
}
