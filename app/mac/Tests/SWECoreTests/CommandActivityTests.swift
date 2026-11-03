import Foundation
import Testing
@testable import SWECore

/// Only runs the user asked for hold `.userInitiated`, which keeps the Mac
/// from idle sleep while the child runs; timers' runs hold nothing (#1540).
@Suite struct CommandActivityTests {
    @Test func aRunOutsideBackgroundIsUserInitiated() {
        #expect(CommandActivity.options == .userInitiated)
    }

    @Test func aRunInsideBackgroundHoldsNoActivity() async {
        let inside = await CommandActivity.background { CommandActivity.options }
        #expect(inside == nil)
        // Leaving it restores the default for the same task.
        #expect(CommandActivity.options == .userInitiated)
    }

    /// A poll loop started inside `background` keeps it in the tasks it
    /// starts, as a model's keepFresh does for its fetches.
    @Test func tasksStartedInsideBackgroundStayBackground() async {
        let child = await CommandActivity.background {
            await Task { CommandActivity.options }.value
        }
        #expect(child == nil)
    }
}
