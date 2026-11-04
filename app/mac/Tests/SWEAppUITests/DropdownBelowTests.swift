import AppKit
import SwiftUI
import Testing
@testable import SWEApp

/// The suggestion list must float *below* its field: PR5's
/// alignment-guide version grew upward over the field in the installed
/// build. This hosts the modifier in a real (offscreen) window and
/// measures the frames.
@Suite @MainActor struct DropdownBelowTests {
    private final class Box {
        var field = CGRect.zero
        var drop = CGRect.zero
    }

    private struct Host: View {
        var box: Box
        var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                Color.blue.frame(width: 200, height: 30)
                    .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) }
                        action: { box.field = $0 }
                    .dropdownBelow(isPresented: true) {
                        Color.red.frame(height: 120)
                            .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) }
                                action: { box.drop = $0 }
                    }
                Color.green.frame(width: 200, height: 30)
            }
        }
    }

    @Test func theDropdownSitsBelowTheField() throws {
        _ = NSApplication.shared
        let box = Box()
        let window = OffscreenWindow(size: CGSize(width: 300, height: 300),
                                     styleMask: [.titled])
        let hosting = NSHostingView(rootView: Host(box: box))
        window.contentView = hosting
        window.orderFront(nil)
        defer { window.orderOut(nil) }
        #expect(spin { box.drop != .zero && box.field != .zero })

        // NSHostingView is flipped: y grows downward, so the list's top
        // must land 4pt under the field's bottom edge.
        #expect(abs(box.drop.minY - (box.field.maxY + 4)) < 1.5)
        #expect(box.drop.minY > box.field.minY)
        #expect(abs(box.drop.width - box.field.width) < 1.5)

        FileManager.default.createFile(
            atPath: "/tmp/agents-dropdown/below.png", contents: nil)
        if let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) {
            hosting.cacheDisplay(in: hosting.bounds, to: rep)
            if let png = rep.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: "/tmp/agents-dropdown/below.png"))
            }
        }
        #expect(window.screen == nil)
    }

    private func spin(timeout: TimeInterval = 60, until done: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !done() {
            RunLoop.current.run(until: Date().addingTimeInterval(0.01))
            if Date() > deadline { return false }
        }
        return true
    }
}
