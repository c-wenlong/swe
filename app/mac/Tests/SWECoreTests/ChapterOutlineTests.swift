import Foundation
import Testing
@testable import SWECore

@Suite struct ChapterOutlineTests {
    private func file(_ path: String) -> ChapterNode {
        ChapterNode(name: (path as NSString).lastPathComponent, path: path, isFolder: false)
    }

    private func folder(_ path: String, _ children: [ChapterNode]) -> ChapterNode {
        let count = children.reduce(0) { $0 + ($1.isFolder ? $1.fileCount : 1) }
        return ChapterNode(name: (path as NSString).lastPathComponent, path: path,
                           isFolder: true, children: children, fileCount: count)
    }

    private var tree: [ChapterNode] {
        [
            folder("engineering", [
                folder("engineering/review", [file("engineering/review/Checklist.md")]),
                file("engineering/style.md"),
            ]),
            folder("empty", []),
            file("intro.md"),
            file("verification.md"),
        ]
    }

    // MARK: - filter

    @Test func blankQueryKeepsTheTree() {
        #expect(ChapterOutline.filter(tree, query: "  ") == tree)
    }

    @Test func matchesFilenameCaseInsensitivelyAndKeepsParents() {
        let result = ChapterOutline.filter(tree, query: "CHECKLIST")
        #expect(result.map(\.path) == ["engineering"])
        #expect(result[0].children.map(\.path) == ["engineering/review"])
        #expect(result[0].children[0].children.map(\.path) == ["engineering/review/Checklist.md"])
        #expect(result[0].fileCount == 1)
    }

    @Test func matchesTheRelativePath() {
        let result = ChapterOutline.filter(tree, query: "review/")
        #expect(result.flatMap(\.children).map(\.path) == ["engineering/review"])
    }

    @Test func aFolderNameKeepsEveryFileInside() {
        let result = ChapterOutline.filter(tree, query: "engineering")
        #expect(result == [tree[0]])
    }

    @Test func noMatchIsEmpty() {
        #expect(ChapterOutline.filter(tree, query: "zzz").isEmpty)
    }

    @Test func aMatchingEmptyFolderStays() {
        #expect(ChapterOutline.filter(tree, query: "empt").map(\.path) == ["empty"])
    }

    // MARK: - keyboard

    private func open(_ closed: Set<String> = []) -> (String) -> Bool { { !closed.contains($0) } }

    @Test func visibleRowsHideCollapsedChildren() {
        let all = ChapterOutline.visibleRows(tree, isExpanded: open()).map(\.path)
        #expect(all == ["engineering", "engineering/review", "engineering/review/Checklist.md",
                        "engineering/style.md", "empty", "intro.md", "verification.md"])
        let folded = ChapterOutline.visibleRows(tree, isExpanded: open(["engineering"])).map(\.path)
        #expect(folded == ["engineering", "empty", "intro.md", "verification.md"])
    }

    @Test func upAndDownMoveAndStopAtTheEnds() {
        let rows = ChapterOutline.visibleRows(tree, isExpanded: open())
        #expect(ChapterOutline.step(.down, from: "intro.md", rows: rows, isExpanded: open())
                == .focus("verification.md"))
        #expect(ChapterOutline.step(.down, from: "verification.md", rows: rows, isExpanded: open()) == .none)
        #expect(ChapterOutline.step(.up, from: "engineering", rows: rows, isExpanded: open()) == .none)
        #expect(ChapterOutline.step(.up, from: "empty", rows: rows, isExpanded: open())
                == .focus("engineering/style.md"))
    }

    @Test func noCursorStartsAtTheFirstRow() {
        let rows = ChapterOutline.visibleRows(tree, isExpanded: open())
        #expect(ChapterOutline.step(.down, from: nil, rows: rows, isExpanded: open()) == .focus("engineering"))
        #expect(ChapterOutline.step(.down, from: "gone.md", rows: rows, isExpanded: open()) == .focus("engineering"))
        #expect(ChapterOutline.step(.down, from: nil, rows: [], isExpanded: open()) == .none)
    }

    @Test func rightExpandsThenEnters() {
        let closed: Set<String> = ["engineering"]
        let rows = ChapterOutline.visibleRows(tree, isExpanded: open(closed))
        #expect(ChapterOutline.step(.right, from: "engineering", rows: rows, isExpanded: open(closed))
                == .expand("engineering"))
        let all = ChapterOutline.visibleRows(tree, isExpanded: open())
        #expect(ChapterOutline.step(.right, from: "engineering", rows: all, isExpanded: open())
                == .focus("engineering/review"))
        #expect(ChapterOutline.step(.right, from: "intro.md", rows: all, isExpanded: open()) == .none)
    }

    @Test func rightOnAnEmptyOpenFolderStays() {
        let rows = ChapterOutline.visibleRows(tree, isExpanded: open())
        #expect(ChapterOutline.step(.right, from: "empty", rows: rows, isExpanded: open()) == .none)
    }

    @Test func aHiddenCursorFallsBackToItsVisibleFolder() {
        let closed: Set<String> = ["engineering/review"]
        let rows = ChapterOutline.visibleRows(tree, isExpanded: open(closed))
        #expect(ChapterOutline.nearestVisible("engineering/review/Checklist.md", rows: rows)
                == "engineering/review")
        let folded = ChapterOutline.visibleRows(tree, isExpanded: open(["engineering"]))
        #expect(ChapterOutline.nearestVisible("engineering/review/Checklist.md", rows: folded)
                == "engineering")
        #expect(ChapterOutline.nearestVisible("intro.md", rows: folded) == nil)
    }

    @Test func leftCollapsesThenClimbs() {
        let rows = ChapterOutline.visibleRows(tree, isExpanded: open())
        #expect(ChapterOutline.step(.left, from: "engineering/review", rows: rows, isExpanded: open())
                == .collapse("engineering/review"))
        #expect(ChapterOutline.step(.left, from: "engineering/review/Checklist.md", rows: rows,
                                    isExpanded: open()) == .focus("engineering/review"))
        let closed: Set<String> = ["engineering/review"]
        #expect(ChapterOutline.step(.left, from: "engineering/review", rows: rows,
                                    isExpanded: open(closed)) == .focus("engineering"))
        #expect(ChapterOutline.step(.left, from: "intro.md", rows: rows, isExpanded: open()) == .none)
    }
}
