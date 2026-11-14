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

    @Test func bareKiBNumbersKeepOneDecimalBelowOneKiB() {
        #expect(CompiledSize.kibNumber(bytes: 0) == "0.0")
        #expect(CompiledSize.kibNumber(bytes: 410) == "0.4")
        #expect(CompiledSize.kibNumber(bytes: 3072) == "3.0")
        #expect(CompiledSize.kibNumber(bytes: 12_595) == "12.3")
        #expect(CompiledSize.kibNumber(bytes: -5) == "0.0")
        #expect(CompiledSize.label(bytes: 3072) == "3.0 KiB")
    }

    /// A folder row sums only the files its filter checks; an unchecked
    /// file and a file with no known size add nothing.
    @Test func folderSumCountsOnlyCheckedFiles() {
        let sizes = ["a.md": 1024, "b.md": 2048, "c.md": 512]
        let files = ["a.md", "b.md", "c.md", "gone.md"]
        #expect(IncludeSizes.bytes(of: files, sizes: sizes, included: { _ in true }) == 3584)
        #expect(IncludeSizes.bytes(of: files, sizes: sizes, included: { $0 != "b.md" }) == 1536)
        #expect(IncludeSizes.bytes(of: files, sizes: sizes, included: { _ in false }) == 0)
        #expect(IncludeSizes.bytes(of: [], sizes: sizes, included: { _ in true }) == 0)
        #expect(IncludeSizes.cell(bytes: 3584) == "3.5")
        #expect(IncludeSizes.cell(bytes: nil) == "")
    }
}
