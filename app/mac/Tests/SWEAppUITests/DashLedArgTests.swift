import Testing
@testable import SWEApp

/// A command or source word that starts with "-" reads as a flag on
/// swe's command line, so the add sheets refuse it up front.
@Suite struct DashLedArgTests {
    @Test func aHarnessCommandCantStartWithADash() {
        #expect(AddHarnessSheet.commandProblem("--version") != nil)
        #expect(AddHarnessSheet.commandProblem("  -x") != nil)
        #expect(AddHarnessSheet.commandProblem("mycli") == nil)
        #expect(AddHarnessSheet.commandProblem("/opt/bin/my-cli") == nil)
    }

    @Test func aMarketplaceSourceCantStartWithADash() {
        #expect(AddMarketplaceSheet.sourceProblem("-rf") != nil)
        #expect(AddMarketplaceSheet.sourceProblem(" --harness=x") != nil)
        #expect(AddMarketplaceSheet.sourceProblem("owner/repo") == nil)
        #expect(AddMarketplaceSheet.sourceProblem("~/plugins-dir") == nil)
    }
}
