import AppKit
import SwiftUI
import Testing
@testable import SWEApp

/// A collapsed sidebar takes clicks only over its icon rail (#1892). The
/// list is laid out at the expanded width, so `SidebarListFrame` has to cut
/// its hit area as well as its drawing. Real mouse events go through an
/// offscreen window onto a page button with a wide stand-in list over it.
@Suite @MainActor struct SidebarHitAreaTests {
    private final class Box { var page = 0; var list = 0 }

    private struct Host: View {
        var box: Box
        var body: some View {
            ZStack(alignment: .topLeading) {
                Button { box.page += 1 } label: { Color.green.frame(width: 300, height: 100) }
                    .buttonStyle(.plain)
                Button { box.list += 1 } label: { Color.blue.frame(width: 200, height: 100) }
                    .buttonStyle(.plain)
                    .frame(width: 200, alignment: .leading)
                    .modifier(SidebarListFrame(visibleWidth: 70))
            }
            .frame(width: 300, height: 100)
        }
    }

    private func click(at x: CGFloat) -> Box {
        _ = NSApplication.shared
        let box = Box()
        let window = OffscreenWindow(size: CGSize(width: 300, height: 100), styleMask: [.titled])
        window.contentView = NSHostingView(rootView: Host(box: box))
        window.orderFront(nil)
        defer { window.orderOut(nil) }
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        let point = NSPoint(x: x, y: 50)
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                                           timestamp: ProcessInfo.processInfo.systemUptime,
                                           windowNumber: window.windowNumber, context: nil,
                                           eventNumber: 0, clickCount: 1, pressure: 1)!
            window.sendEvent(event)
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        return box
    }

    @Test func clickPastTheRailReachesThePage() {
        let box = click(at: 140)
        #expect(box.page == 1)
        #expect(box.list == 0)
    }

    @Test func clickInsideTheRailReachesTheList() {
        let box = click(at: 30)
        #expect(box.list == 1)
        #expect(box.page == 0)
    }

    @Test func titleBarReaderTakesNoHits() {
        let view = TitleBarReader.View(frame: NSRect(x: 0, y: 0, width: 100, height: 30))
        #expect(view.hitTest(NSPoint(x: 10, y: 10)) == nil)
    }
}
