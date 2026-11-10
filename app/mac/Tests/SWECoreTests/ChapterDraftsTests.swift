import Testing
@testable import SWECore

@Suite struct ChapterDraftsTests {
    @Test func draftsFollowCompileOrderThenAlphabet() {
        let names = ChapterDrafts.ordered(["z.md", "b.md", "a/x.md", "q.md"],
                                          by: ["a/x.md", "b.md", "a/x.md", "other.md"])
        #expect(names == ["a/x.md", "b.md", "q.md", "z.md"])
    }

    @Test func noDraftsIsEmpty() {
        #expect(ChapterDrafts.ordered([], by: ["a.md"]).isEmpty)
        #expect(ChapterDrafts.title(count: 2) == "Drafts (2)")
    }

    @Test func sizesReadInKiBWithOneDecimal() {
        #expect(CompiledSize.label(bytes: 0) == "0 B")
        #expect(CompiledSize.label(bytes: 812) == "812 B")
        #expect(CompiledSize.label(bytes: 1024) == "1.0 KiB")
        #expect(CompiledSize.label(bytes: 12_595) == "12.3 KiB")
    }
}
