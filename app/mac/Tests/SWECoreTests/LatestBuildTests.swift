import Foundation
import Testing
@testable import SWECore

/// Holds a build on its background thread until the test lets it go.
/// The build blocks a GCD thread, as the slow filesystem calls it stands
/// in for do; the test side waits on a `CountGate`, never polling.
private final class BuildGate: Sendable {
    private let release = DispatchSemaphore(value: 0)
    private let starts = CountGate()

    /// Called by the build: says it has started, then waits for `open`.
    /// A gate never opened lets go after twice `gateTimeout`, only so the
    /// thread is not held for good: every test-side wait gives up first,
    /// so the test has already failed by then.
    func hold() {
        let starts = self.starts
        Task { await starts.bump() }
        let (seconds, _) = gateTimeout.components
        _ = release.wait(timeout: .now() + .seconds(Int(seconds) * 2))
    }

    /// True once the build is inside `hold`, false after `gateTimeout`.
    func waitForStart() async -> Bool { await starts.reach(1) }

    func open() { release.signal() }
}

/// True once `task` finishes, false after `gateTimeout`. A task's value
/// can't be cancelled out of, so the wait runs beside the test and the
/// test waits on a gate.
@MainActor
private func finishes(_ task: Task<Void, Never>) async -> Bool {
    let done = CountGate()
    Task {
        await task.value
        await done.bump()
    }
    return await done.reach(1)
}

@MainActor
@Suite struct LatestBuildTests {
    /// Opens `gate` and waits for the build it held to finish and be
    /// thrown away, so a check after it sees the drop, not a result still
    /// on its way. False if no drop comes (the build was published
    /// instead) within the gate timeout.
    private func openAndAwaitDrop(_ gate: BuildGate, on build: LatestBuild<some Sendable>) async -> Bool {
        let drops = CountGate()
        build.onDrop = { Task { await drops.bump() } }
        defer { build.onDrop = nil }
        gate.open()
        return await drops.reach(1)
    }

    /// True once `build.settled()` returns, false after `gateTimeout`.
    private func settles(_ build: LatestBuild<some Sendable>) async -> Bool {
        await finishes(Task { await build.settled() })
    }

    @Test func theWorkRunsOffTheMainThreadAndPublishesOnIt() async {
        let build = LatestBuild<Bool>()
        var published: [Bool] = []
        build.start({ Thread.isMainThread }) { value in
            MainActor.assertIsolated()
            published.append(value)
        }
        #expect(build.isRunning)
        #expect(await settles(build))
        #expect(published == [false])
        #expect(!build.isRunning)
    }

    @Test func aSlowOlderBuildNeverOverwritesANewerOne() async {
        let build = LatestBuild<String>()
        let gate = BuildGate()
        var published: [String] = []
        build.start({ gate.hold(); return "old" }) { published.append($0) }
        #expect(await gate.waitForStart())
        build.start({ "new" }) { published.append($0) }
        #expect(await settles(build))
        #expect(published == ["new"])
        // The old one finishes late, and is dropped.
        #expect(await openAndAwaitDrop(gate, on: build))
        #expect(published == ["new"])
    }

    @Test func settledWaitsThroughASupersededBuild() async {
        let build = LatestBuild<String>()
        let gate = BuildGate()
        let second = BuildGate()
        var published: [String] = []
        build.start({ gate.hold(); return "first" }) { published.append($0) }
        #expect(await gate.waitForStart())
        build.start({ second.hold(); return "second" }) { published.append($0) }
        #expect(await second.waitForStart())
        // The first one finishing does not end the wait: the second is
        // the one on show next.
        gate.open()
        second.open()
        #expect(await settles(build))
        #expect(published == ["second"])
    }

    /// Starts a `settled()` caller and returns it once it is waiting.
    private func parkedWaiter(on build: LatestBuild<String>) async -> Task<Void, Never> {
        let parked = CountGate()
        build.onWait = {
            build.onWait = nil
            Task { await parked.bump() }
        }
        let waiter = Task { await build.settled() }
        #expect(await parked.reach(1), "settled() never started waiting")
        return waiter
    }

    @Test func aWaiterGoesOnWhenANewerBuildPublishesWhileTheOldOneIsStuck() async {
        let build = LatestBuild<String>()
        let stuck = BuildGate()
        var published: [String] = []
        build.start({ stuck.hold(); return "stuck" }) { published.append($0) }
        #expect(await stuck.waitForStart())
        let waiter = await parkedWaiter(on: build)
        // Waiting on the stuck build's own task would never end.
        build.start({ "new" }) { published.append($0) }
        #expect(await finishes(waiter))
        #expect(published == ["new"])
        stuck.open()
    }

    @Test func aWaiterGoesOnWhenTheStuckBuildIsCancelled() async {
        let build = LatestBuild<String>()
        let stuck = BuildGate()
        build.start({ stuck.hold(); return "stuck" }) { _ in }
        #expect(await stuck.waitForStart())
        let waiter = await parkedWaiter(on: build)
        build.cancel()
        #expect(await finishes(waiter))
        #expect(!build.isRunning)
        stuck.open()
    }

    @Test func aCancelledBuildIsNeverPublished() async {
        let build = LatestBuild<Int>()
        let gate = BuildGate()
        var published: [Int] = []
        build.start({ gate.hold(); return 1 }) { published.append($0) }
        #expect(await gate.waitForStart())
        build.cancel()
        #expect(!build.isRunning)
        // Nothing left to wait for.
        #expect(await settles(build))
        // The cancelled build finishes and is dropped, unpublished.
        #expect(await openAndAwaitDrop(gate, on: build))
        #expect(published.isEmpty)
        // A new one after it is published as usual.
        build.start({ 2 }) { published.append($0) }
        #expect(await settles(build))
        #expect(published == [2])
    }
}
