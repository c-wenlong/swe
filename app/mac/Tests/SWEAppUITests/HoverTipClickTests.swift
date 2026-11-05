import AppKit
import SwiftUI
import Testing
@testable import SWEApp

/// A tip inside a Button's label must leave the click to the Button.
/// Hosts the button in a real (offscreen) window and sends it a click.
@Suite @MainActor struct HoverTipClickTests {
    private final class Box { var taps = 0 }

    private struct Host: View {
        var box: Box
        var pins: Bool
        var body: some View {
            Button { box.taps += 1 } label: {
                Text("Row").frame(width: 120, height: 40)
                    .hoverTip("A tip", pinsOnClick: pins)
            }
            .buttonStyle(.plain)
            .frame(width: 200, height: 100)
        }
    }

    private func click(pins: Bool) -> Int {
        _ = NSApplication.shared
        let box = Box()
        let window = OffscreenWindow(size: CGSize(width: 200, height: 100), styleMask: [.titled])
        window.contentView = NSHostingView(rootView: Host(box: box, pins: pins))
        window.orderFront(nil)
        defer { window.orderOut(nil) }
        settle()
        let point = NSPoint(x: 100, y: 50)
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                                           timestamp: ProcessInfo.processInfo.systemUptime,
                                           windowNumber: window.windowNumber, context: nil,
                                           eventNumber: 0, clickCount: 1, pressure: 1)!
            window.sendEvent(event)
            settle()
        }
        return box.taps
    }

    @Test func aTipThatDoesNotPinLeavesTheClickToTheButton() {
        #expect(click(pins: false) == 1)
    }

    private func settle() {
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    }
}
