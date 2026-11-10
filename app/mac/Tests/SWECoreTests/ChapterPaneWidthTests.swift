import Testing
@testable import SWECore

@Suite struct ChapterPaneWidthTests {
    @Test func standardSitsInsideTheBounds() {
        #expect(ChapterPaneWidth.clamped(ChapterPaneWidth.standard) == ChapterPaneWidth.standard)
    }

    @Test func widthsClampToTheBounds() {
        #expect(ChapterPaneWidth.clamped(0) == ChapterPaneWidth.minimum)
        #expect(ChapterPaneWidth.clamped(2000) == ChapterPaneWidth.maximum)
        #expect(ChapterPaneWidth.clamped(300) == 300)
    }

    @Test func minimumGrowsWithTextZoom() {
        #expect(ChapterPaneWidth.clamped(0, zoom: 1.5) == ChapterPaneWidth.minimum * 1.5)
        #expect(ChapterPaneWidth.clamped(0, zoom: 0.75) == ChapterPaneWidth.minimum * 0.75)
    }

    @Test func theEditorKeepsItsMinimumInANarrowWindow() {
        let available = 1100.0
        let pane = ChapterPaneWidth.clamped(500, available: available)
        #expect(available - ChapterPaneWidth.dividerWidth - pane
            == ChapterPaneWidth.editorMinimum)
    }

    @Test func aTooNarrowContainerKeepsThePaneAtItsMinimum() {
        #expect(ChapterPaneWidth.clamped(400, available: 300) == ChapterPaneWidth.minimum)
    }

    @Test func nonFiniteInputFallsBackToStandard() {
        #expect(ChapterPaneWidth.clamped(.nan) == ChapterPaneWidth.standard)
        #expect(ChapterPaneWidth.clamped(.infinity) == ChapterPaneWidth.maximum)
    }
}

@Suite struct EditorHeaderTests {
    @Test func aNarrowCardGoesCompact() {
        #expect(EditorHeader.isCompact(width: 500))
        #expect(!EditorHeader.isCompact(width: 900))
    }

    @Test func anUnmeasuredCardKeepsItsWords() {
        #expect(!EditorHeader.isCompact(width: 0))
    }

    @Test func theThresholdScalesWithTextZoom() {
        let width = EditorHeader.compactBelow + 50
        #expect(!EditorHeader.isCompact(width: width))
        #expect(EditorHeader.isCompact(width: width, zoom: 1.5))
    }
}
