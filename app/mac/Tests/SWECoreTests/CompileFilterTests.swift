import Foundation
import Testing
@testable import SWECore

/// The Compile tab's harness filter lives in UserDefaults under
/// `agentsMd.compile.filter` as a comma-joined list. A value holding
/// several names once read back as one harness called
/// "claude,codex,cursor,devin,opencode"; these pin the parse.
@Suite struct CompileFilterTests {
    private static let known = ["claude", "codex", "cursor", "devin", "opencode"]

    @Test func commaJoinedListRoundTrips() {
        let stored = "claude,codex,cursor,devin,opencode"
        let names = CompileFilter.parse(stored)
        #expect(names == Self.known)
        #expect(CompileFilter.serialise(names) == stored)
        #expect(CompileFilter.parse(CompileFilter.serialise(names)) == names)
    }

    @Test func parseTrimsDropsEmptiesAndDuplicates() {
        #expect(CompileFilter.parse(" claude , ,codex,claude,") == ["claude", "codex"])
        #expect(CompileFilter.parse("") == [])
        #expect(CompileFilter.serialise([]) == "")
        #expect(CompileFilter.serialise([" codex", "codex", ""]) == "codex")
    }

    /// Values written before the list form, a single name or "", keep
    /// meaning what they meant.
    @Test func oldSingleValuesStillWork() {
        #expect(CompileFilter.parse("claude") == ["claude"])
        #expect(CompileFilter.harness(stored: "claude", known: Self.known) == "claude")
        #expect(CompileFilter.harness(stored: "", known: Self.known) == nil)
        #expect(CompileFilter.serialise(["claude"]) == "claude")
    }

    /// The tab shows one harness or all of them: a list naming several
    /// harnesses is the All view, never a harness called "a,b".
    @Test func severalHarnessesMeanAll() {
        #expect(CompileFilter.harness(stored: "claude,codex,cursor,devin,opencode",
                                      known: Self.known) == nil)
        #expect(CompileFilter.harness(stored: "codex,", known: Self.known) == "codex")
    }

    /// A harness that is no longer linked falls back to All; before the
    /// link report loads, the stored name is trusted.
    @Test func unknownNamesAreDropped() {
        #expect(CompileFilter.harness(stored: "gone", known: Self.known) == nil)
        #expect(CompileFilter.harness(stored: "gone,codex", known: Self.known) == "codex")
        #expect(CompileFilter.harness(stored: "codex", known: []) == "codex")
    }

    // MARK: - the dropdown

    @Test func labelSummarisesTheSelection() {
        #expect(CompileFilter.label(stored: "", known: Self.known) == "All harnesses")
        #expect(CompileFilter.label(stored: "codex", known: Self.known) == HarnessMark("codex").name)
        #expect(CompileFilter.label(stored: "claude,codex", known: Self.known) == "2 harnesses")
        #expect(CompileFilter.label(stored: "claude,codex,cursor,devin", known: Self.known) == "4 harnesses")
        // Every linked harness picked is the same as none: All.
        #expect(CompileFilter.label(stored: Self.known.joined(separator: ","),
                                    known: Self.known) == "All harnesses")
        // A name that is no longer linked does not count.
        #expect(CompileFilter.label(stored: "gone", known: Self.known) == "All harnesses")
        #expect(CompileFilter.label(stored: "gone,codex", known: Self.known) == HarnessMark("codex").name)
    }

    @Test func storingASelectionFollowsLinkOrderAndCollapsesAll() {
        #expect(CompileFilter.stored(picking: ["devin", "claude"], known: Self.known) == "claude,devin")
        #expect(CompileFilter.stored(picking: [], known: Self.known) == "")
        #expect(CompileFilter.stored(picking: Set(Self.known), known: Self.known) == "")
        #expect(CompileFilter.stored(picking: ["gone"], known: Self.known) == "")
        let stored = CompileFilter.stored(picking: ["codex", "cursor"], known: Self.known)
        #expect(CompileFilter.picked(stored: stored, known: Self.known) == ["codex", "cursor"])
    }

    @Test func matrixColumnsFollowThePicks() {
        #expect(CompileFilter.columns(stored: "", known: Self.known) == Self.known)
        #expect(CompileFilter.columns(stored: "devin,claude", known: Self.known) == ["claude", "devin"])
        #expect(CompileFilter.columns(stored: "gone", known: Self.known) == Self.known)
    }
}
