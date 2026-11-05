import Foundation

// The bounded waits this target's tests share. Test targets can't share
// sources, so `gateTimeout` and `CountGate` repeat
// `SWECoreTests/TestGates.swift`; keep the two in step.

/// How long a test waits on a gate before giving up, so a regression
/// fails the test instead of hanging the suite. A passing test never
/// waits this long, so it is sized for a starved machine, not a typical
/// one.
let gateTimeout: Duration = .seconds(60)

/// Counts events (`bump`) and lets a test wait for the count to reach a
/// target (`reach`). Checking the count and parking the caller happen in
/// one actor step, so a `bump` can never slip between them and leave a
/// waiter stranded.
actor CountGate {
    private(set) var count = 0
    private var nextID = 0
    private var waiters: [Int: (target: Int,
                              cont: CheckedContinuation<Bool, Never>)] = [:]

    func bump() {
        count += 1
        let ready = waiters.filter { count >= $0.value.target }
        for id in ready.keys { waiters[id] = nil }
        for waiter in ready.values { waiter.cont.resume(returning: true) }
    }

    /// True once the count reaches `target`, false after `gateTimeout`.
    func reach(_ target: Int) async -> Bool {
        await withCheckedContinuation { cont in
            if count >= target {
                cont.resume(returning: true)
                return
            }
            nextID += 1
            let id = nextID
            waiters[id] = (target, cont)
            Task { await self.expire(id) }
        }
    }

    /// The timeout resumes only its own waiter, and only if a `bump` has
    /// not already answered it.
    private func expire(_ id: Int) async {
        try? await Task.sleep(for: gateTimeout)
        waiters.removeValue(forKey: id)?.cont.resume(returning: false)
    }
}

/// Runs the main run loop until `done` holds, true if it did before
/// `gateTimeout`. SwiftUI lays out and AppKit delivers events only from
/// run loop turns, and a synchronous test on the main thread has no other
/// way to give it those turns, so this loop stays. Each turn returns as
/// soon as the run loop handles a source.
@MainActor
func spinMainRunLoop(until done: () -> Bool) -> Bool {
    let (seconds, attoseconds) = gateTimeout.components
    let deadline = Date().addingTimeInterval(Double(seconds) + Double(attoseconds) / 1e18)
    while !done() && Date() < deadline {
        RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.02))
    }
    return done()
}
