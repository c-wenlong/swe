import CoreGraphics
import Testing
@testable import SWEApp
import SWECore

/// The AGENTS.md rail's drag targets, read from row frames laid out by
/// hand: one 20pt row per path, top to bottom.
@Suite @MainActor struct ChapterRailDragTests {
    private let tree = ChapterTree.build(
        files: ["a.md", "devin/x.md", "harnesses/h.md", "harnesses/inner/i.md"],
        folders: ["devin/sub"],
        order: ["a.md", "devin/x.md", "harnesses/h.md"])
    private let rows = ["devin", "devin/sub", "devin/x.md", "harnesses", "harnesses/inner",
                        "harnesses/inner/i.md", "harnesses/h.md", "a.md"]

    private func rail(dragging path: String) -> ChapterRailDrag {
        let rail = ChapterRailDrag()
        for (index, row) in rows.enumerated() {
            rail.frames[row] = CGRect(x: 0, y: CGFloat(index) * 20, width: 200, height: 20)
        }
        rail.rootZone = CGRect(x: 0, y: CGFloat(rows.count) * 20, width: 200, height: 20)
        rail.begin(path: path, tree: tree,
                   indexed: ["a.md", "devin/x.md", "harnesses/h.md"])
        return rail
    }

    private func point(on row: String) -> CGPoint {
        CGPoint(x: 10, y: CGFloat(rows.firstIndex(of: row)!) * 20 + 10)
    }

    private let strip = CGPoint(x: 10, y: 8 * 20 + 10)

    @Test func aFolderDragNestsIntoTheFolderUnderThePointer() {
        let rail = rail(dragging: "devin")
        #expect(rail.draggingFolder)
        rail.update(point: point(on: "harnesses"))
        #expect(rail.target == .folder("harnesses"))
        rail.update(point: point(on: "harnesses/inner"))
        #expect(rail.target == .folder("harnesses/inner"))
        // A file row means the folder that holds it.
        rail.update(point: point(on: "harnesses/h.md"))
        #expect(rail.target == .folder("harnesses"))
        rail.update(point: point(on: "harnesses/inner/i.md"))
        #expect(rail.target == .folder("harnesses/inner"))
    }

    @Test func aFolderDragNeverTargetsItselfItsSubfoldersOrItsOwnParent() {
        let rail = rail(dragging: "devin")
        for row in ["devin", "devin/sub", "devin/x.md", "a.md"] {
            rail.update(point: point(on: row))
            #expect(rail.target == .none, "\(row)")
        }
        // Already at the top level: the strip is no move either.
        rail.update(point: strip)
        #expect(rail.target == .none)
    }

    @Test func aNestedFolderDragTakesTheTopLevelStrip() {
        let rail = rail(dragging: "harnesses/inner")
        rail.update(point: strip)
        #expect(rail.target == .root)
        rail.update(point: point(on: "a.md"))
        #expect(rail.target == .root)
        rail.update(point: point(on: "harnesses/h.md"))
        #expect(rail.target == .none)
        rail.update(point: point(on: "devin"))
        #expect(rail.target == .folder("devin"))
        let done = rail.finish()
        #expect(done?.path == "harnesses/inner")
        #expect(done?.target == .folder("devin"))
        #expect(done?.isFolder == true)
        #expect(!rail.draggingFolder)
    }

    @Test func aFileDroppedOnAnotherFoldersRowLandsInThatFolder() {
        let rail = rail(dragging: "a.md")
        #expect(!rail.draggingFolder)
        rail.update(point: point(on: "harnesses/h.md"))
        guard case .insert(let level, _) = rail.target else {
            Issue.record("no insert target: \(rail.target)")
            return
        }
        #expect(level == "harnesses")
        #expect(rail.finish()?.isFolder == false)
    }
}
