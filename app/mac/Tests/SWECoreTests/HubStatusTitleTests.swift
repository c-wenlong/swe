import Testing
@testable import SWECore

/// The chat badge and the task page's session rows say the same words for
/// the hub's statuses, never the raw value.
@Suite struct HubStatusTitleTests {
    @Test func everyHubStatusHasItsWord() {
        #expect(HubSessionRow.statusTitle("active") == "Active")
        #expect(HubSessionRow.statusTitle("done") == "Done")
        #expect(HubSessionRow.statusTitle("followup") == "Waiting")
        #expect(HubSessionRow.statusTitle("error") == "Error")
        #expect(HubSessionRow.statusTitle("interrupted") == "Interrupted")
        #expect(HubSessionRow.statusTitle("approval") == "Approval needed")
    }

    @Test func anUnknownOrMissingStatusHasNone() {
        #expect(HubSessionRow.statusTitle("idle") == nil)
        #expect(HubSessionRow.statusTitle(nil) == nil)
    }
}
