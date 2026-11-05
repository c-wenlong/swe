import Foundation
import Testing
@testable import SWECore

@Suite struct HoverSafeRegionTests {
    /// A chip at (500, 300), its card to the left at (100, 250): the union
    /// covers the arrow gap between them.
    private let chip = CGRect(x: 500, y: 300, width: 60, height: 24)
    private let card = CGRect(x: 100, y: 250, width: 300, height: 200)

    private func region() -> HoverSafeRegion {
        HoverSafeRegion(anchor: chip, card: card)
    }

    /// Mutating `update` cannot sit inside `#expect`; unwrap the result.
    private func closing(_ region: inout HoverSafeRegion, _ point: CGPoint,
                         _ now: ContinuousClock.Instant) -> Bool {
        region.update(point: point, now: now)
    }

    @Test func theArrowGapBetweenChipAndCardCountsAsInside() {
        var region = region()
        let gap = CGPoint(x: 460, y: 350) // between the card's right edge and the chip
        #expect(!closing(&region, gap, ContinuousClock.now))
    }

    @Test func pausingInTheGapKeepsTheCardOpen() {
        var region = region()
        let gap = CGPoint(x: 460, y: 350)
        let t0 = ContinuousClock.now
        for step in 0..<20 {
            #expect(!closing(&region, gap, t0.advanced(by: .milliseconds(50 * step))))
        }
    }

    @Test func aShortTripOutsideThenBackKeepsTheCardOpen() {
        var region = region()
        let t0 = ContinuousClock.now
        let away = CGPoint(x: 900, y: 900)
        #expect(!closing(&region, away, t0))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(100))))
        #expect(!closing(&region, CGPoint(x: 550, y: 310), t0.advanced(by: .milliseconds(150))))
        // The timer reset: another 100 ms outside still does not close.
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(200))))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(290))))
    }

    @Test func stayingOutsideForTheGraceClosesTheCard() {
        var region = region()
        let t0 = ContinuousClock.now
        let away = CGPoint(x: 900, y: 900)
        #expect(!closing(&region, away, t0))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(149))))
        #expect(closing(&region, away, t0.advanced(by: .milliseconds(150))))
    }
}

extension HoverSafeRegionTests {
    @Test func anUnregisteredCardRectCountsAsInsideWhileItAppears() {
        // The popover's window has not reported yet: the pointer cannot
        // be outside a rect that does not exist.
        var region = HoverSafeRegion()
        let t0 = ContinuousClock.now
        let away = CGPoint(x: 900, y: 900)
        for step in 0..<20 {
            #expect(!closing(&region, away, t0.advanced(by: .milliseconds(50 * step))))
        }
    }

    @Test func aKnownAnchorDoesNotSkipTheCardPendingGrace() {
        // Anchor reported, card frame still unknown: the popover is
        // mid-flight, and the pointer outside the anchor still gets the
        // pending grace rather than an instant close.
        var region = HoverSafeRegion(anchor: chip)
        let t0 = ContinuousClock.now
        let away = CGPoint(x: 900, y: 900)
        #expect(!closing(&region, away, t0))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(500))))
        region.card = card
        #expect(!closing(&region, CGPoint(x: 550, y: 310),
                        t0.advanced(by: .milliseconds(600))))
    }

    @Test func aMissingRectStopsProtectingPastThePendingGrace() {
        var region = HoverSafeRegion()
        let t0 = ContinuousClock.now
        let away = CGPoint(x: 900, y: 900)
        #expect(!closing(&region, away, t0))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(999))))
        // The second is up: the pointer is now outside, and the normal
        // 150 ms grace still has to elapse before the card closes.
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(1149))))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(1298))))
        #expect(closing(&region, away, t0.advanced(by: .milliseconds(1299))))
    }

    @Test func aRectArrivingMidFlightResumesNormalRules() {
        var region = HoverSafeRegion()
        let t0 = ContinuousClock.now
        let away = CGPoint(x: 900, y: 900)
        #expect(!closing(&region, away, t0))
        region.anchor = chip
        region.card = card
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(100))))
        #expect(!closing(&region, CGPoint(x: 550, y: 310),
                        t0.advanced(by: .milliseconds(150))))
        // The step inside reset the outside timer, which restarted here.
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(400))))
        #expect(closing(&region, away, t0.advanced(by: .milliseconds(550))))
    }

    @Test func crossingANeighbourWaitsBeforeSwappingTheOpenCard() {
        // No card up: the usual show delay.
        #expect(HoverCardTiming.delay(entering: "a", open: nil) == HoverCardTiming.show)
        // Back on the open card's own anchor: nothing to schedule.
        #expect(HoverCardTiming.delay(entering: "a", open: "a") == nil)
        // A neighbour while "a" is up: a swap waits instead of firing on
        // entry, so a pointer passing through on its way into the card
        // leaves before it fires.
        let swap = HoverCardTiming.delay(entering: "b", open: "a")
        #expect(swap == HoverCardTiming.swap)
        #expect(HoverCardTiming.swap > .zero)
    }
}

extension HoverSafeRegionTests {
    /// The worst case: the pointer leaves just after a poll, so the
    /// grace only starts on the next one and the close waits for the
    /// first poll past it.
    @MainActor @Test func aCardClosesWithin300msOfThePointerLeaving() throws {
        let poll = HoverCardState.pollInterval
        let start = ContinuousClock.now
        var region = HoverSafeRegion(anchor: CGRect(x: 0, y: 0, width: 10, height: 10),
                                     card: CGRect(x: 0, y: 20, width: 10, height: 10))
        let closedInside = region.update(point: CGPoint(x: 5, y: 5), now: start)
        #expect(!closedInside)
        // Left a moment after that poll; every later poll sees outside.
        let left = start + .milliseconds(1)
        var now = start
        var closedAt: ContinuousClock.Instant?
        for _ in 0..<10 where closedAt == nil {
            now += poll
            if region.update(point: CGPoint(x: 100, y: 100), now: now) { closedAt = now }
        }
        let after = try #require(closedAt) - left
        #expect(after <= .milliseconds(300))
    }
}
