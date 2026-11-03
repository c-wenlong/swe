import Observation
import XCTest
@testable import SWERemote

/// The 15 s poll writes a list only when it changed, so an unchanged poll
/// re-renders nothing that reads it and doesn't rerun an open search.
@MainActor
final class ListWriteTests: XCTestCase {
    private let now = 1_800_000_000.0

    private func row(_ key: String, at: Double? = nil, status: String? = "done") -> SessionRow {
        SessionRow(key: key, harness: "claude", agent: nil, cwd: "/w", title: key, lastActiveAt: at ?? now,
                   status: status, machineId: "m1", ownership: nil, attachable: nil)
    }

    private func item(_ key: String) -> InboxItem {
        InboxItem(key: key, machineId: "m1", harness: "codex", agent: nil, title: key, cwd: "/w",
                  status: "approval", lastActiveAt: now, requestId: nil, kind: nil)
    }

    private let machines = [Machine(id: "m1", name: "mac", os: nil, online: true, lastSeenAt: nil, harnesses: nil)]

    private func app() -> AppModel {
        AppModel(hub: PairedHub(baseURL: URL(string: "http://mac:8477")!,
                                credential: Credential(deviceId: "d", credential: "c", name: nil)))
    }

    /// Whether reading `read` would be told of a change by `change`.
    private func fires(_ read: @escaping () -> Void, after change: () -> Void) -> Bool {
        var fired = false
        withObservationTracking(read) { fired = true }
        change()
        return fired
    }

    func testAnUnchangedPollWritesNothingTheListsShow() {
        let app = app()
        app.applyLists(sessions: [row("a"), row("b")], machines: machines, inbox: [item("a")], attachedAtStart: [])
        let fired = fires({
            _ = app.sessions
            _ = app.inbox
            _ = app.machines
            _ = app.unread
            _ = app.pendingDetaches
            _ = app.connection
            _ = app.loadError
            _ = app.loadedOnce
        }, after: {
            app.applyLists(sessions: [row("a"), row("b")], machines: machines, inbox: [item("a")], attachedAtStart: [])
        })
        XCTAssertFalse(fired, "the same lists again must not re-render the Sessions page or the Inbox")
        XCTAssertEqual(app.sessions.map(\.key), ["a", "b"])
        XCTAssertEqual(app.connection, .online)
    }

    func testAChangedPollStillLands() {
        let app = app()
        app.applyLists(sessions: [row("a")], machines: machines, inbox: [], attachedAtStart: [])
        XCTAssertTrue(fires({ _ = app.sessions }, after: {
            app.applyLists(sessions: [row("a", at: now + 5)], machines: machines, inbox: [], attachedAtStart: [])
        }))
        XCTAssertEqual(app.sessions.first?.lastActiveAt, now + 5)
        XCTAssertTrue(fires({ _ = app.inbox }, after: {
            app.applyLists(sessions: [row("a", at: now + 5)], machines: machines, inbox: [item("a")], attachedAtStart: [])
        }))
        XCTAssertEqual(app.inbox.map(\.key), ["a"])
        // A poll without machines keeps the ones the hub sent before.
        app.applyLists(sessions: [row("a", at: now + 5)], machines: nil, inbox: [item("a")], attachedAtStart: [])
        XCTAssertEqual(app.machines, machines)
    }

    func testRefreshedAtStillMovesEveryPoll() {
        // The Model menu's pending switch gives up by it.
        let app = app()
        let t = Date(timeIntervalSince1970: now)
        app.applyLists(sessions: [], machines: nil, inbox: [], attachedAtStart: [], now: t)
        app.applyLists(sessions: [], machines: nil, inbox: [], attachedAtStart: [], now: t.addingTimeInterval(15))
        XCTAssertEqual(app.refreshedAt, t.addingTimeInterval(15))
    }

    func testSearchRerunsOnlyWhenTheQueryOrSessionsChange() {
        let a = SearchTrigger(query: "fix", sessions: [row("a")])
        XCTAssertEqual(a, SearchTrigger(query: "fix", sessions: [row("a")]), "an unchanged poll keeps the search")
        XCTAssertNotEqual(a, SearchTrigger(query: "fix", sessions: [row("a"), row("b")]))
        XCTAssertNotEqual(a, SearchTrigger(query: "fix", sessions: [row("a", at: now + 1)]))
        XCTAssertNotEqual(a, SearchTrigger(query: "fixes", sessions: [row("a")]))
        XCTAssertNotEqual(a, SearchTrigger(query: "fix", sessions: [row("a")], pulls: 1), "pull to refresh reruns it")
    }

    func testAnUnchangedListStillSearchesAgainEachMinuteAndRetriesFailures() {
        // 15 s polls: four in a minute share a tick, so the hub's
        // transcript index still gets asked once a minute.
        let start = Date(timeIntervalSinceReferenceDate: 60 * 1000)
        let ticks = (0..<8).map { SearchTrigger.tick(refreshedAt: start.addingTimeInterval(Double($0) * 15), failed: false) }
        XCTAssertEqual(Set(ticks).count, 2)
        // After a failed search, every poll tries again.
        let failing = (0..<4).map { SearchTrigger.tick(refreshedAt: start.addingTimeInterval(Double($0) * 15), failed: true) }
        XCTAssertEqual(Set(failing).count, 4)
        XCTAssertEqual(SearchTrigger.tick(refreshedAt: nil, failed: true), 0)
    }
}
