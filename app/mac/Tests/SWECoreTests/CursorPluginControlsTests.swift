import Foundation
import Testing
@testable import SWECore

@Suite struct CursorPluginControlsTests {
    @Test func cursorActionMasksSurviveDecodeAndSnapshot() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let state = try decoder.decode(PluginHarnessState.self, from: Data(#"{"available":false,"installed":true,"enabled":true,"allowed_actions":["uninstall"],"control_detail":"Configured plugin"}"#.utf8))
        let plugin = CatalogPlugin(name: "example", marketplace: "cursor-public", harnesses: ["cursor": state])
        let catalog = PluginCatalog(harnesses: ["cursor"], actions: ["cursor"], marketplaces: [])
        #expect(catalog.actions(for: plugin, in: "cursor").map(\.verb) == [.uninstall])
        let restored = try decoder.decode(PluginHarnessState.self, from: JSONEncoder().encode(state))
        #expect(restored.allowedActions == ["uninstall"])
        #expect(restored.controlDetail == "Configured plugin")
        let required = CatalogPlugin(name: "required", marketplace: "team", harnesses: [
            "cursor": PluginHarnessState(available: false, installed: true, enabled: true, allowedActions: [])])
        #expect(catalog.actions(for: required, in: "cursor").isEmpty)
    }

    @Test func cursorCommandsUseSweAccountAPIAndResultsExplainRefresh() throws {
        let action = PluginAction(verb: .disable, ref: "example@cursor-public", harness: "cursor")
        #expect(action.command == "swe plugins disable example@cursor-public --harness=cursor")
        #expect(!action.isEdit)
        let decoder = JSONDecoder()
        let result = try decoder.decode(PluginActionResult.self, from: Data(#"{"action":"disable","harness":"cursor","ref":"example@cursor-public","changed":true,"detail":"Refocus Cursor."}"#.utf8))
        #expect(result.summary.contains("Refocus Cursor"))
    }
}
