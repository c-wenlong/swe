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


    @Test func realPluginCellExposesRestrictionHelp() throws {
        _ = NSApplication.shared
        let detail = "Team policy controls this plugin"
        let state = PluginHarnessState(available: false, installed: true, enabled: true,
                                       allowedActions: [], controlDetail: detail)
        let host = NSHostingView(rootView: PluginCell(harness: "cursor", state: state,
            actions: [], running: false, busy: false, confirm: { _ in }).frame(width: 140, height: 48))
        let window = OffscreenWindow(size: CGSize(width: 140, height: 48), styleMask: [.borderless])
        window.contentView = host
        window.orderBack(nil)
        defer { window.orderOut(nil) }
        host.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        func helpStrings(_ node: Any, depth: Int = 0) -> [String] {
            guard depth < 12, let element = node as? any NSAccessibility else { return [] }
            return [element.accessibilityHelp(), element.accessibilityLabel()].compactMap { $0 }
                + (element.accessibilityChildren() ?? []).flatMap { helpStrings($0, depth: depth + 1) }
        }
        #expect(helpStrings(host).contains { $0.contains(detail) })
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
