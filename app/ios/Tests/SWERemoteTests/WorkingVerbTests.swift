import SwiftUI
import XCTest
@testable import SWERemote

/// The working pill's verb: it ticks once per verb instead of every
/// display frame, and its band, now a sliding strip, draws the same as
/// the per-frame gradient it replaced.
@MainActor
final class WorkingVerbTests: XCTestCase {
    func testScheduleTicksOncePerVerbOnTheVerbBoundaries() {
        let now = Date(timeIntervalSinceReferenceDate: 1_000_000.7)
        let ticks = Array(WorkingVerb.schedule(now: now)
            .entries(from: now, mode: .normal).prefix(4))
        XCTAssertEqual(ticks.count, 4)
        for (a, b) in zip(ticks, ticks.dropFirst()) {
            XCTAssertEqual(b.timeIntervalSince(a), WorkingVerb.verbSeconds, accuracy: 1e-6)
        }
        // Each tick sits on a boundary, so each one shows the next verb.
        let indices = ticks.map(WorkingVerb.index(at:))
        for (a, b) in zip(indices, indices.dropFirst()) {
            XCTAssertEqual(b, (a + 1) % WorkingVerbs.all.count)
        }
        for tick in ticks {
            let slots = tick.timeIntervalSinceReferenceDate / WorkingVerb.verbSeconds
            XCTAssertEqual(slots, slots.rounded(), accuracy: 1e-6)
        }
    }

    func testIndexFollowsTheWallClock() {
        let s = WorkingVerb.verbSeconds
        let n = WorkingVerbs.all.count
        XCTAssertEqual(WorkingVerb.index(at: Date(timeIntervalSinceReferenceDate: 0)), 0)
        XCTAssertEqual(WorkingVerb.index(at: Date(timeIntervalSinceReferenceDate: s * 0.99)), 0)
        XCTAssertEqual(WorkingVerb.index(at: Date(timeIntervalSinceReferenceDate: s * 3)), 3 % n)
        XCTAssertEqual(WorkingVerb.index(at: Date(timeIntervalSinceReferenceDate: s * Double(n) + 0.1)), 0)
    }

    func testBandOffsetPutsTheCentreWhereTheOldGradientHadIt() {
        // Sliding the strip right by `offset` moves its gradient along the
        // word's diagonal by offset·w/(w²+h²); the centre must land on the
        // old formula's `-half + (1 + 2·half)·sweep`.
        let size = CGSize(width: 96, height: 17)
        let w = Double(size.width), h = Double(size.height)
        for sweep in stride(from: 0.0, through: 1.0, by: 0.125) {
            let moved = Double(VerbShimmer.offset(sweep: sweep, size: size)) * w / (w * w + h * h)
            let half = WorkingVerb.bandHalf
            XCTAssertEqual(VerbShimmer.stripCentre + moved, -half + (1 + half * 2) * sweep, accuracy: 1e-9)
        }
        // The strip always covers the word wherever the band could show.
        let left = VerbShimmer.offset(sweep: 0, size: size)
        XCTAssertLessThanOrEqual(left, 0)
        XCTAssertGreaterThanOrEqual(left + size.width * VerbShimmer.stripWidths, size.width)
    }

    func testShimmerDrawsLikeThePerFrameGradient() throws {
        let word = "Investigating"
        for sweep in [0.3, 0.5, 0.7] {
            let new = try pixels(VerbShimmer(word: word, sweep: sweep))
            let old = try pixels(LegacyShimmer(word: word, sweep: sweep))
            XCTAssertEqual(new.width, old.width)
            XCTAssertEqual(new.height, old.height)
            let diff = meanDifference(new.bytes, old.bytes)
            XCTAssertLessThan(diff, 0.5, "sweep \(sweep): mean channel difference \(diff)")
        }
        // The comparison can tell bands apart: a band a fifth of the way
        // along differs clearly from the old one at another place.
        let far = meanDifference(try pixels(VerbShimmer(word: word, sweep: 0.3)).bytes,
                                 try pixels(LegacyShimmer(word: word, sweep: 0.7)).bytes)
        XCTAssertGreaterThan(far, 2)
    }

    // MARK: - Helpers

    private struct Pixels {
        let width: Int
        let height: Int
        let bytes: [UInt8]
    }

    private func pixels<V: View>(_ view: V) throws -> Pixels {
        let renderer = ImageRenderer(content: view.font(Theme.caption)
            .padding(6)
            .background(Color.black)
            .environment(\.colorScheme, .dark))
        renderer.scale = 2
        let image = try XCTUnwrap(renderer.cgImage)
        let width = image.width, height = image.height
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = bytes.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: width, height: height,
                                          bitsPerComponent: 8, bytesPerRow: width * 4,
                                          space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
            else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        XCTAssertTrue(drawn)
        return Pixels(width: width, height: height, bytes: bytes)
    }

    private func meanDifference(_ a: [UInt8], _ b: [UInt8]) -> Double {
        guard a.count == b.count, !a.isEmpty else { return .infinity }
        var total = 0
        for i in a.indices { total += abs(Int(a[i]) - Int(b[i])) }
        return Double(total) / Double(a.count)
    }
}

/// The shimmer as it drew before the band became a sliding strip: a
/// gradient whose stops are rebuilt for every `sweep`, aimed corner to
/// corner across the word. Kept here as the reference picture.
private struct LegacyShimmer: View {
    let word: String
    let sweep: Double

    var body: some View {
        Text("\(word)…")
            .foregroundStyle(Theme.textSecondary)
            .overlay {
                Text("\(word)…")
                    .foregroundStyle(Theme.textPrimary)
                    .shadow(color: Theme.textPrimary.opacity(0.5), radius: 3)
                    .mask(band)
            }
    }

    private var band: LinearGradient {
        let half = WorkingVerb.bandHalf
        let centre = -half + (1 + half * 2) * sweep
        return LinearGradient(
            stops: [
                .init(color: .clear, location: max(0, centre - half)),
                .init(color: .white, location: min(1, max(0, centre))),
                .init(color: .clear, location: min(1, centre + half)),
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}
