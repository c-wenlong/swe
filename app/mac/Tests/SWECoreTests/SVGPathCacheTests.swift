import CoreGraphics
import Testing
@testable import SWECore

/// A glyph's path data parses once; every later layout gets the same path.
@Suite struct SVGPathCacheTests {
    @Test func aPathParsesOnceAndMatchesAFreshParse() {
        let d = "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256Z"
        let first = SVGPath.cached(d)
        #expect(SVGPath.cached(d) === first)
        #expect(first.boundingBoxOfPath == SVGPath.path(d).boundingBoxOfPath)
    }

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
