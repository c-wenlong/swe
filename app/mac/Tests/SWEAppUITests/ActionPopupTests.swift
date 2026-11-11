import AppKit
import SwiftUI
import SWECore
import Testing
@testable import SWEApp

@Suite @MainActor struct ActionPopupTests {
    private final class Clicks { var primary = 0; var secondary = 0 }

    @Test func contextClicksPreservePrimaryButtons() throws {
        _ = NSApplication.shared
        let clicks = Clicks()
        let host = NSHostingView(rootView:
            Button { clicks.primary += 1 } label: {
                Text("Plugin").frame(width: 160, height: 60)
            }
            .buttonStyle(.plain)
            .background(SecondaryClickTarget { _ in clicks.secondary += 1 })
            .frame(width: 200, height: 100))
        let window = OffscreenWindow(size: CGSize(width: 200, height: 100), styleMask: [.titled])
        window.contentView = host
        window.orderFront(nil)
        defer { window.orderOut(nil) }
        host.layoutSubtreeIfNeeded()
        // SwiftUI installs the gesture during the next layout turn.
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        let events: [(NSEvent.EventType, NSEvent.ModifierFlags)] = [
            (.leftMouseDown, []), (.leftMouseUp, []), (.rightMouseDown, []), (.rightMouseUp, []),
            (.leftMouseDown, .control), (.leftMouseUp, .control)]
        for (type, flags) in events {
            let event = try #require(NSEvent.mouseEvent(with: type, location: NSPoint(x: 100, y: 50),
                modifierFlags: flags, timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1))
            if let event = SecondaryClickRouter.shared.route(event) { window.sendEvent(event) }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        #expect(clicks.primary == 1)
        #expect(clicks.secondary == 2)
    }


    @Test func realPluginCellRendersRestrictionHelp() throws {
        _ = NSApplication.shared
        let detail = "Team policy controls this plugin"
        let state = PluginHarnessState(available: false, installed: true, enabled: true,
                                       allowedActions: [], controlDetail: detail)
        let cell = PluginCell(harness: "cursor", state: state,
            actions: [], running: false, busy: false, confirm: { _ in })
        // This is the actual cell's text used by .help and the accessibility hint.
        #expect(cell.help(for: state).contains(detail))
        let configured = PluginHarnessState(available: false, installed: true, enabled: false,
            allowedActions: ["uninstall"], controlDetail: "Configured plugin; removal only")
        #expect(cell.help(for: configured).contains("Configured plugin; removal only"))
        let host = NSHostingView(rootView: cell.frame(width: 140, height: 48))
        let window = OffscreenWindow(size: CGSize(width: 140, height: 48), styleMask: [.borderless])
        window.contentView = host
        window.orderBack(nil)
        defer { window.orderOut(nil) }
        host.layoutSubtreeIfNeeded()
        let rep = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: rep)
        #expect(rep.pixelsWide >= 140)
    }

    @Test func actionListRendersWithDestructiveRemoval() throws {
        _ = NSApplication.shared
        let view = ActionOptionList(actions: [
            .init(id: "off", title: "Turn off everywhere", symbol: "power"),
            .init(id: "import", title: "Import to SWE…", symbol: "square.and.arrow.down"),
            .init(id: "remove", title: "Remove from Claude Code…", symbol: "trash", destructive: true)
        ], choose: { _ in })
        .padding(6).frame(width: 300, height: 140).background(Theme.card)
        let host = NSHostingView(rootView: view)
        let window = OffscreenWindow(size: CGSize(width: 300, height: 140), styleMask: [.borderless])
        window.contentView = host
        window.orderBack(nil)
        defer { window.orderOut(nil) }
        host.layoutSubtreeIfNeeded()
        #expect(host.fittingSize.width >= 200)
        if let path = ProcessInfo.processInfo.environment["SWE_ACTION_POPUP_SNAPSHOT"],
           let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
            host.cacheDisplay(in: host.bounds, to: rep)
            let png = try #require(rep.representation(using: .png, properties: [:]))
            try png.write(to: URL(fileURLWithPath: path))
        }
    }
}
