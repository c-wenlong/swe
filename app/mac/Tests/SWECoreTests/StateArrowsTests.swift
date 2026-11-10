import AppKit
import Foundation
import Testing
@testable import SWECore

@Suite struct StateArrowsTests {
    /// CoreSVG can load an SVG and draw nothing, so each arrow has to
    /// resolve, load and paint a fair share of its box in its own colour.
    @Test func everyArrowResolvesAndDraws() throws {
        let expected: [StateArrows.Kind: (r: Double, g: Double, b: Double)] = [
            .red: (0xfa, 0x32, 0x28), .yellow: (0xff, 0xd2, 0x00),
            .green: (0x14, 0xe6, 0x3c), .grey: (0x8a, 0x8a, 0x93),
        ]
        for kind in StateArrows.Kind.allCases {
            let url = try #require(StateArrows.url(kind), "\(kind) is not bundled")
            let image = try #require(NSImage(contentsOf: url), "\(kind) does not load")
            let rep = try #require(render(image))
            let painted = paintedPixels(rep)
            let share = Double(painted.count) / Double(rep.pixelsWide * rep.pixelsHigh)
            #expect(share > 0.08, "\(kind) draws almost nothing")
            let want = try #require(expected[kind])
            let matching = painted.filter { c in
                abs(c.redComponent * 255 - want.r) < 40 && abs(c.greenComponent * 255 - want.g) < 40
                    && abs(c.blueComponent * 255 - want.b) < 40
            }
            #expect(Double(matching.count) / Double(max(painted.count, 1)) > 0.3, "\(kind) is not drawn in its colour")
        }
    }

    func render(_ image: NSImage) -> NSBitmapImageRep? {
        let side = 64
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: side, pixelsHigh: side, bitsPerSample: 8,
                                         samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                         bytesPerRow: 0, bitsPerPixel: 0) else { return nil }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        image.draw(in: NSRect(x: 0, y: 0, width: side, height: side))
        NSGraphicsContext.restoreGraphicsState()
        return rep
    }

    func paintedPixels(_ rep: NSBitmapImageRep) -> [NSColor] {
        var out: [NSColor] = []
        for x in 0..<rep.pixelsWide {
            for y in 0..<rep.pixelsHigh {
                if let c = rep.colorAt(x: x, y: y), c.alphaComponent > 0.5 { out.append(c) }
            }
        }
        return out
    }
}
