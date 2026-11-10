import Foundation
import SWECore

/// Debug builds only: `SWE_WT_FIXTURE=standard|empty|error` points the
/// Worktrees page at `WorktreesFixture` instead of `swe`, and
/// `SWE_WT_STATE=filtered|detail|new|remove|removeclean|prune|slim|lock|move`
/// opens the page already in that state, so each one can be captured
/// without clicking. A release build ignores both.
enum WorktreesDebug {
    static func fixtureClient() -> SweClient? {
        #if DEBUG
        guard let raw = ProcessInfo.processInfo.environment["SWE_WT_FIXTURE"] else { return nil }
        return SweClient(runner: WorktreesFixture(WorktreesFixture.Scenario(rawValue: raw) ?? .standard))
        #else
        return nil
        #endif
    }

    static var initialState: String? {
        #if DEBUG
        ProcessInfo.processInfo.environment["SWE_WT_STATE"]
        #else
        nil
        #endif
    }
}
