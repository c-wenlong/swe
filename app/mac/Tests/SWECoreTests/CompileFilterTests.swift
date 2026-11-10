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
}
