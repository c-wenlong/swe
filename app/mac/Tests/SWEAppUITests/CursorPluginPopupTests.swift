import AppKit
import SwiftUI
import Testing
import SWECore
@testable import SWEApp

@Suite @MainActor struct CursorPluginPopupTests {
    @Test func cursorPersonalAndTeamStatesRender() throws {
        _ = NSApplication.shared
        let cases: [(String, Bool, [String], String?)] = [
            ("enabled", true, ["disable", "uninstall"], nil),
            ("disabled", false, ["enable", "uninstall"], nil),
            ("configured", true, ["uninstall"], "Change configured plugin on/off settings in Cursor."),
            ("team", true, [], "Controlled by Cursor team policy.")]
        for (label, enabled, allowed, detail) in cases {
            let state = PluginHarnessState(available: false, installed: true, enabled: enabled,
                                           allowedActions: allowed, controlDetail: detail)
            let plugin = CatalogPlugin(name: "example", marketplace: "cursor-public", harnesses: ["cursor": state])
            let catalog = PluginCatalog(harnesses: ["cursor"], actions: ["cursor"], marketplaces: [])
            let actions = catalog.actions(for: plugin, in: "cursor")
            let view = VStack(alignment: .leading, spacing: 10) {
                Text("Cursor · \(enabled ? "Enabled" : "Disabled")").font(Theme.cardTitle)
                if let detail { Text(detail).font(Theme.caption).foregroundStyle(Theme.textSecondary) }
                if !actions.isEmpty {
                    ActionOptionList(actions: actions.map {
                        PopupAction(id: $0.id, title: $0.menuTitle,
                                    symbol: $0.verb == .uninstall ? "trash" : "power", destructive: $0.verb == .uninstall)
                    }, choose: { _ in })
                }
            }.padding(12).frame(width: 310, height: 150).background(Theme.card)
            let host = NSHostingView(rootView: view)
            let window = OffscreenWindow(size: CGSize(width: 310, height: 150), styleMask: [.borderless])
            window.contentView = host
            window.orderBack(nil)
            defer { window.orderOut(nil) }
            host.layoutSubtreeIfNeeded()
            #expect(actions.map { $0.verb.rawValue } == allowed)
            if let directory = ProcessInfo.processInfo.environment["SWE_CURSOR_POPUP_SNAPSHOTS"],
               let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
                host.cacheDisplay(in: host.bounds, to: rep)
                let png = try #require(rep.representation(using: .png, properties: [:]))
                try png.write(to: URL(fileURLWithPath: directory).appending(path: "cursor-\(label).png"))
            }
        }
    }
}
