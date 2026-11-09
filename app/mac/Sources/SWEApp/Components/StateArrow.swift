import AppKit
import SWECore
import SwiftUI

/// A bundled state arrow (`StateArrows.Kind`) as the status indicator. It
/// draws the SVG in its own colours: red for critical failure, yellow for
/// partial failure, green for fully operational, grey for interrupted or
/// unknown. The arrow is decorative to VoiceOver; callers provide state
/// text or an accessibility label on the containing view.
struct StateArrow: View {
    var kind: StateArrows.Kind
    var size: CGFloat = Theme.statusDot

    var body: some View {
        Group {
            if let image = Self.image(for: kind) {
                Image(nsImage: image).resizable().interpolation(.high).scaledToFit()
            } else {
                Color.clear
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    @MainActor private static var cache: [StateArrows.Kind: NSImage] = [:]

    @MainActor static func image(for kind: StateArrows.Kind) -> NSImage? {
        if let cached = cache[kind] { return cached }
        guard let url = StateArrows.url(kind), let image = NSImage(contentsOf: url) else { return nil }
        cache[kind] = image
        return image
    }
}
