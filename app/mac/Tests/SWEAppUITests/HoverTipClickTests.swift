import AppKit
import SwiftUI
import Testing
@testable import SWEApp

/// A tip attaches no tap and opens no window: the control underneath
/// keeps its click and nothing appears. Hosts a button with a tip in a
/// real (offscreen) window and sends it a click.
@Suite @MainActor struct HoverTipClickTests {
    private final class Box { var taps = 0 }

    private struct Host: View {
        var box: Box
        var body: some View {
            Button { box.taps += 1 } label: {
                Text("Row").frame(width: 120, height: 40)
                    .hoverTip("A tip")
            }
            .buttonStyle(.plain)
            .frame(width: 200, height: 100)
        }
    }

    private func click() -> (taps: Int, extraWindows: Int) {
        _ = NSApplication.shared
        let box = Box()
        let window = OffscreenWindow(size: CGSize(width: 200, height: 100), styleMask: [.titled])
        window.contentView = NSHostingView(rootView: Host(box: box))
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
        return (box.taps, window.childWindows?.count ?? 0)
    }

    @Test func theClickReachesTheButtonAndNoTipWindowOpens() {
        let (taps, extraWindows) = click()
        #expect(taps == 1)
        #expect(extraWindows == 0)
    }

    private func settle() {
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    }
}
