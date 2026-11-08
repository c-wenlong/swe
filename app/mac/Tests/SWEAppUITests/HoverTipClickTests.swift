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

    private func click(pins: Bool, viaApp: Bool = false) -> Int {
        _ = NSApplication.shared
        let box = Box()
        let window = OffscreenWindow(size: CGSize(width: 200, height: 100), styleMask: [.titled])
        window.contentView = NSHostingView(rootView: Host(box: box, pins: pins))
        window.orderFront(nil)
        defer { window.orderOut(nil) }
        settle()
        // window.sendEvent skips the app's local event monitors, so the
        // pin check can never eat the click: this asserts the control
        // always gets its press. Events through NSApp.sendEvent do reach
        // the monitor — how pinning itself is exercised below. A
        // synthesized mouseEvent keeps its location verbatim either
        // way: window coordinates, not screen.
        let point = NSPoint(x: 100, y: 50)
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                                           timestamp: ProcessInfo.processInfo.systemUptime,
                                           windowNumber: window.windowNumber, context: nil,
                                           eventNumber: 0, clickCount: 1, pressure: 1)!
            (viaApp ? NSApp.sendEvent : window.sendEvent)(event)
            settle()
        }
        return box.taps
    }

    @Test func aTipThatDoesNotPinLeavesTheClickToTheButton() {
        #expect(click(pins: false) == 1)
    }

    /// The regression test: a pinning tip's tap used to win the race and
    /// spend the Button's first click. The monitor only watches now.
    @Test func aTipThatPinsStillLeavesTheClickToTheButton() {
        #expect(click(pins: true) == 1)
    }

    /// A click dispatched through the app still pins: the tip's press
    /// monitor sees the event, claims it inside the anchor and pins —
    /// without taking the click from the view.
    @Test func aClickThroughTheAppPinsTheTip() {
        #expect(pins(onClickThroughApp: true) == 1)
    }

    @Test func aClickThroughTheAppDoesNotPinANonPinningTip() {
        #expect(pins(onClickThroughApp: false) == 0)
    }

    private func pins(onClickThroughApp pins: Bool) -> Int {
        final class Probe: @unchecked Sendable { var pinned = 0 }
        let probe = Probe()
        let observer = NotificationCenter.default.addObserver(
            forName: .hoverTipPinned, object: nil, queue: .main
        ) { _ in probe.pinned += 1 }
        defer { NotificationCenter.default.removeObserver(observer) }
        _ = click(pins: pins, viaApp: true)
        return probe.pinned
    }

    private func settle() {
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    }
}
