import Testing
@testable import SWECore

/// Agent messages are markdown with a couple of chat-only blocks:
/// `ChatMarkdown` is the same parser as the phone's
/// `SWERemote/Hub/Markdown.swift` — same inputs, same trees, grouped
/// into the app's `.list`/`.quote` shapes instead of flat items.
@Suite struct ChatMarkdownTests {

    @Test func tableFromACodexMessage() {
        let text = """
        Nothing changed yet.

        | Resource | Action |
        |---|---|
        | Cloud Scheduler `toolbox-v2-db-sync` | Keep paused, then delete. |
        | Secrets | Remove the `*-dsn` secrets \\| after revoking. |

        **Keep both databases.**
        """
        let blocks = ChatMarkdown.blocks(text)
        #expect(blocks.count == 3)
        #expect(blocks[0] == .paragraph("Nothing changed yet."))
        guard case .table(let t) = blocks[1] else { Issue.record("expected a table"); return }
        #expect(t.header == ["Resource", "Action"])
        #expect(t.rows.count == 2)
        #expect(t.rows[0][0] == "Cloud Scheduler `toolbox-v2-db-sync`")
        #expect(t.rows[1][1] == "Remove the `*-dsn` secrets | after revoking.",
                "an escaped pipe stays in its cell")
        #expect(blocks[2] == .paragraph("**Keep both databases.**"))
    }

    @Test func alignmentRow() {
        #expect(ChatMarkdown.separatorAlignments("| :--- | :---: | ---: |") == [.leading, .center, .trailing])
        #expect(ChatMarkdown.separatorAlignments("---") == nil, "a lone rule is not a table separator")
        #expect(ChatMarkdown.separatorAlignments("| a | b |") == nil)
    }

    @Test func headingsListsQuotesRulesAndCode() {
        let blocks = ChatMarkdown.blocks("""
        ## Plan
        1. First
        2. Second
          continued
        - nested parent
          - child
        > quoted
        ---
        ```swift
        let x = 1
        ```
        #not a heading
        """)
        #expect(blocks == [
            .heading(2, "Plan"),
            .list([
                MarkdownListItem(depth: 0, kind: .ordered(1), text: "First"),
                MarkdownListItem(depth: 0, kind: .ordered(2), text: "Second\ncontinued"),
                MarkdownListItem(depth: 0, kind: .bullet, text: "nested parent"),
                MarkdownListItem(depth: 1, kind: .bullet, text: "child"),
            ]),
            .quote([.paragraph("quoted")]),
            .rule,
            .code("let x = 1", "swift"),
            .paragraph("#not a heading"),
        ])
    }

    @Test func reviewFixes() {
        // Only \| is an escape; other backslashes reach the inline parser.
        #expect(ChatMarkdown.cells(#"| `C:\Users\alice` | \d+ \| x |"#) == [#"`C:\Users\alice`"#, #"\d+ | x"#])
        // Hashes in a heading's text survive; a spaced closing run doesn't.
        #expect(ChatMarkdown.heading("## C#")?.1 == "C#")
        #expect(ChatMarkdown.heading("## #123")?.1 == "#123")
        #expect(ChatMarkdown.heading("## Title ##")?.1 == "Title")
        // A fence or table indented under a list item stays its own block.
        let blocks = ChatMarkdown.blocks("1. Run\n   ```\n   make test\n   ```\n2. Then\n   | a | b |\n   |---|---|\n   | 1 | 2 |")
        #expect(blocks.count == 4)
        #expect(blocks[0] == .list([MarkdownListItem(depth: 0, kind: .ordered(1), text: "Run")]))
        #expect(blocks[1] == .code("make test", nil))
        guard case .table(let t) = blocks[3] else { Issue.record("expected a table"); return }
        #expect(t.rows == [["1", "2"]])
    }

    @Test func renderingDefectsFromTheHarnessSurvey() {
        let blocks = ChatMarkdown.blocks("""
        Done.

        ![Backfill outcomes](/Users/k/Downloads/outcomes.png)

        - [ ] **Privacy (your call).**
        - [x] Shipped
        <json-render>{"root":"d",
        "elements":{}}</json-render>
        After.
        """)
        #expect(blocks[1] == .image(alt: "Backfill outcomes", source: "/Users/k/Downloads/outcomes.png"))
        guard case .list(let items) = blocks[2], items.count == 2 else {
            Issue.record("expected a task list"); return
        }
        #expect(items[0] == MarkdownListItem(depth: 0, kind: .task(false), text: "**Privacy (your call).**"))
        #expect(items[1] == MarkdownListItem(depth: 0, kind: .task(true), text: "Shipped"))
        guard case .rendered = blocks[3] else { Issue.record("expected a rendered view block"); return }
        #expect(blocks[4] == .paragraph("After."))
        #expect(ChatMarkdown.inlineImagesAsText("see ![chart](a.png) here") == "see *Image: chart* here")
    }

    @Test func renderingReviewEdgeCases() {
        // Code spans and escaped syntax keep the example as written.
        #expect(ChatMarkdown.inlineImagesAsText("write `![chart](a.png)` or ![b](b.png)") == "write `![chart](a.png)` or *Image: b*")
        #expect(ChatMarkdown.inlineImagesAsText(#"literal \![x](y.png)"#) == #"literal \![x](y.png)"#)
        // Two images on one line are not one image.
        #expect(ChatMarkdown.imageLine("![first](a.png)![second](b.png)") == nil)
        // Text after the closing tag still shows.
        #expect(ChatMarkdown.blocks("<json-render>{}</json-render> Saved successfully.")
            == [.rendered("<json-render>{}</json-render>"), .paragraph("Saved successfully.")])
        // A view or an image under a list item gets its own block.
        #expect(ChatMarkdown.blocks("- Result\n  <json-render>{}</json-render>\n- Chart\n  ![c](c.png)")
            == [.list([MarkdownListItem(depth: 0, kind: .bullet, text: "Result")]),
                .rendered("<json-render>{}</json-render>"),
                .list([MarkdownListItem(depth: 0, kind: .bullet, text: "Chart")]),
                .image(alt: "c", source: "c.png")])
    }

    @Test func localFileLinksBecomeFileNames() {
        let s = ChatMarkdown.inline("Read [AGENTS.md](/Users/k/.swe/AGENTS.md), [ai.py](finance/ai.py) and [PR](https://github.com/x/y/pull/1).")
        #expect(s.runs.compactMap(\.link).map(\.absoluteString) == ["https://github.com/x/y/pull/1"])
        #expect(String(s.characters).contains("AGENTS.md"))
        // The file names carry the code intent, like the phone's.
        let coded = s.runs.filter { $0.inlinePresentationIntent == .code }
            .map { String(s[$0.range].characters) }.joined()
        #expect(coded.contains("AGENTS.md") && coded.contains("ai.py"))
    }

    @Test func unclosedFenceIsCode() {
        #expect(ChatMarkdown.blocks("```\nhalf") == [.code("half", nil)])
    }

    @Test func fenceClosesOnMarkerOnlyLine() {
        // A fence-open line inside a block — the agent quoting a command
        // back — is content, not a close: "```swift build" stays inside.
        let blocks = ChatMarkdown.blocks("```text\n```swift build\n```\ndone")
        #expect(blocks == [.code("```swift build", "text"), .paragraph("done")])
    }

    @Test func tableStopsAtALineThatOpensABlock() {
        // `> Warning: a | b` right after a table is a quote, not a row.
        let blocks = ChatMarkdown.blocks("| a | b |\n|---|---|\n| 1 | 2 |\n> Warning: a | b")
        #expect(blocks == [
            .table(MarkdownTable(header: ["a", "b"], align: [.leading, .leading], rows: [["1", "2"]])),
            .quote([.paragraph("Warning: a | b")]),
        ])
        // A fence whose info string contains `|` opens a code block.
        let fenced = ChatMarkdown.blocks("| a |\n|---|\n| 1 |\n```a|b\nx\n```")
        #expect(fenced == [
            .table(MarkdownTable(header: ["a"], align: [.leading], rows: [["1"]])),
            .code("x", "a|b"),
        ])
    }

    @Test func setextHeadingsKeepTheirFormat() {
        // `Title` + `===` is h1 and `Title` + `---` is h2 — the forms the
        // body's parser already drew before the chat switched parsers.
        #expect(ChatMarkdown.blocks("Title\n===") == [.heading(1, "Title")])
        #expect(ChatMarkdown.blocks("Title\n---") == [.heading(2, "Title")])
        // No paragraph above: `===` is text, `---` still a rule.
        #expect(ChatMarkdown.blocks("- item\n===") == [
            .list([MarkdownListItem(depth: 0, kind: .bullet, text: "item")]),
            .paragraph("==="),
        ])
        #expect(ChatMarkdown.blocks("---") == [.rule])
    }

    @Test func raggedRowsPadToTheWidestRow() {
        guard case .table(let t)? = ChatMarkdown.blocks("| a |\n|---|\n| 1 | 2 | 3 |").first else {
            Issue.record("expected a table"); return
        }
        #expect(t.columnCount == 3)
    }
}
