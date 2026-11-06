import Testing
@testable import SWEApp

/// A sleep the test fires by hand. Each call parks until `fire` or until
/// its task is cancelled, which throws as `Task.sleep` does. A `fire`
/// that lands before its sleep has parked is kept, so that sleep returns
/// at once instead of waiting for a fire that already happened.
@MainActor
private final class ManualSleep {
    private(set) var durations: [Duration] = []
    private var parked: [Int: CheckedContinuation<Void, Error>] = [:]
    private var cancelled: Set<Int> = []
    private var fired: Set<Int> = []
    private let starts = CountGate()
    private let cancellations = CountGate()

    /// True once `count` sleeps have been cancelled, false after
    /// `gateTimeout`. A cancellation lands through its own task, so a
    /// test waits for it rather than guessing with yields.
    func cancelled(_ count: Int) async -> Bool { await cancellations.reach(count) }

    /// True once `count` sleeps have started, false after `gateTimeout`:
    /// `enter` and `leave` only schedule the wait, so a test fires a sleep
    /// after it exists.
    func started(_ count: Int) async -> Bool { await starts.reach(count) }

    func sleep(_ duration: Duration) async throws {
        let id = durations.count
        durations.append(duration)
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if cancelled.contains(id) { continuation.resume(throwing: CancellationError()) }
                else if fired.remove(id) != nil { continuation.resume() }
                else { parked[id] = continuation }
                // Counted once parked, so a test that fires it after
                // `started` always finds it there.
                Task { await starts.bump() }
            }
        } onCancel: {
            Task { @MainActor in await self.cancel(id) }
        }
    }

    var waiting: Int { parked.count }

    func fire(_ id: Int) {
        if let continuation = parked.removeValue(forKey: id) { continuation.resume() }
        else { fired.insert(id) }
    }

    private func cancel(_ id: Int) async {
        cancelled.insert(id)
        parked.removeValue(forKey: id)?.resume(throwing: CancellationError())
        await cancellations.bump()
    }
}

@MainActor
/// `settle()` waits on the hover's own task; a sleep parked past the ones a
/// test fires would leave it waiting, so the suite is bounded.
@Suite(.timeLimit(.minutes(1))) struct ChartHoverTests {
    private func make() -> (ChartHover, ManualSleep) {
        let sleep = ManualSleep()
        return (ChartHover(sleep: { try await sleep.sleep($0) }), sleep)
    }

    @Test func theFirstCardWaitsTheOpenDelay() async {
        let (hover, sleep) = make()
        hover.enter("2")
        #expect(await sleep.started(1))
        #expect(hover.shown == nil)
        #expect(sleep.durations == [ChartHover.openDelay])
        sleep.fire(0)
        await hover.settle()
        #expect(hover.shown == "2")
    }

    @Test func theCardFollowsItsModelWhenRowsChange() async {
        let (hover, sleep) = make()
        hover.enter("b")
        #expect(await sleep.started(1))
        sleep.fire(0)
        await hover.settle()
        #expect(hover.index(in: ["a", "b", "c"]) == 1)
        // A rebuild drops "a": the card moves with "b", not index 1.
        #expect(hover.index(in: ["b", "c"]) == 0)
        #expect(hover.index(in: ["new", "c", "b"]) == 2)
        // "b" is gone: no bar, so no card.
        #expect(hover.index(in: ["a", "c"]) == nil)
    }

    @Test func withACardUpTheNextBarSwitchesAtOnce() async {
        let (hover, sleep) = make()
        hover.enter("2")
        #expect(await sleep.started(1))
        sleep.fire(0)
        await hover.settle()
        hover.enter("3")
        #expect(hover.shown == "3")
        #expect(sleep.durations.count == 1, "no second wait")
    }

    @Test func enteringTheNextBarInsideTheCloseWindowSwitchesAndCancelsTheClose() async {
        let (hover, sleep) = make()
        hover.enter("2")
        #expect(await sleep.started(1))
        sleep.fire(0)
        await hover.settle()
        hover.leave("2")
        #expect(await sleep.started(2))
        #expect(sleep.durations.last == ChartHover.closeDelay)
        #expect(hover.shown == "2", "the card outlives its bar")
        hover.enter("3")
        #expect(hover.shown == "3")
        #expect(await sleep.cancelled(1))
        #expect(sleep.waiting == 0, "the close was cancelled")
        await hover.settle()
        #expect(hover.shown == "3")
    }

    @Test func leavingClosesAfterTheCloseDelay() async {
        let (hover, sleep) = make()
        hover.enter("2")
        #expect(await sleep.started(1))
        sleep.fire(0)
        await hover.settle()
        hover.leave("2")
        #expect(await sleep.started(2))
        sleep.fire(1)
        await hover.settle()
        #expect(hover.shown == nil)
    }

    @Test func aLateLeaveFromTheLastBarIsIgnored() async {
        let (hover, sleep) = make()
        hover.enter("2")
        #expect(await sleep.started(1))
        sleep.fire(0)
        await hover.settle()
        hover.enter("3")
        hover.leave("2")
        #expect(hover.shown == "3")
        #expect(sleep.durations.count == 1)
    }

    @Test func leavingBeforeTheCardOpensCancelsIt() async {
        let (hover, sleep) = make()
        hover.enter("2")
        #expect(await sleep.started(1))
        hover.leave("2")
        #expect(await sleep.started(2))
        #expect(await sleep.cancelled(1))
        // The open wait is gone; only the close wait is left.
        #expect(sleep.waiting == 1)
        #expect(sleep.durations == [ChartHover.openDelay, ChartHover.closeDelay])
        sleep.fire(1)
        await hover.settle()
        #expect(hover.shown == nil)
    }
}
