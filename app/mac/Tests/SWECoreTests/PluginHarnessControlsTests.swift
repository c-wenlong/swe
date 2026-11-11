import Foundation
import Testing
@testable import SWECore

@Suite struct PluginHarnessControlsTests {
    @Test func explicitMasksConstrainControlsAndFailClosed() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        for (mask, expected) in [(#"["uninstall"]"#, [PluginAction.Verb.uninstall]),
                                 (#"["enable","uninstall"]"#, [.enable, .uninstall]),
                                 (#"[]"#, []), (#""uninstall""#, []),
                                 (#"["install","import"]"#, [])] {
            let text = "{\"available\":true,\"installed\":true,\"enabled\":true,\"allowed_actions\":\(mask)}"
            let state = try decoder.decode(PluginHarnessState.self, from: Data(text.utf8))
            let plugin = CatalogPlugin(name: "review", marketplace: "local", harnesses: ["devin": state])
            let catalog = PluginCatalog(harnesses: ["devin"], actions: ["devin"], marketplaces: [])
            #expect(catalog.actions(for: plugin, in: "devin").map(\.verb) == expected)
        }
    }

    @Test func filePluginRemainsVisibleWhenSuspended() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let file = temp.url.appending(path: "review.ts")
        try Data("export const review = () => ({})".utf8).write(to: file)
        let state = PluginHarnessState(available: true, installed: true, enabled: false,
                                      path: file.path, allowedActions: ["enable", "uninstall"])
        let plugin = CatalogPlugin(name: "review", marketplace: "opencode-local", harnesses: ["opencode": state])
        let catalog = PluginCatalog(harnesses: ["opencode"], actions: ["opencode"], marketplaces: [
            PluginMarketplace(name: "opencode-local", harnesses: [:], plugins: [plugin])])
        let index = PluginExploreIndex(catalog: catalog, skills: [], home: temp.url)
        #expect(index.localRefs == [plugin.ref])
        #expect(index.localEntries().first?.row.actions["opencode"]?.map(\.verb) == [.enable, .uninstall])
        // A relative symlink can be dangling while parked outside discovery.
        try FileManager.default.removeItem(at: file)
        try FileManager.default.createSymbolicLink(atPath: file.path, withDestinationPath: "../missing.ts")
        let parked = PluginExploreIndex(catalog: catalog, skills: [], home: temp.url)
        #expect(parked.localRefs == [plugin.ref])
    }

    @Test func successSummaryIncludesRestartNotice() throws {
        let raw = #"{"action":"disable","harness":"opencode","ref":"review@local","changed":true,"detail":"Restart existing OpenCode sessions"}"#
        let result = try JSONDecoder().decode(PluginActionResult.self, from: Data(raw.utf8))
        #expect(result.summary.contains("Restart existing OpenCode sessions"))
    }

    @Test func controlCommandsUseSweAndExplainScope() {
        let devin = PluginAction(verb: .uninstall, ref: "review@devin-local", harness: "devin")
        #expect(devin.command == "swe plugins uninstall review@devin-local --harness=devin")
        #expect(devin.question.contains("personal"))
        let openCode = PluginAction(verb: .disable, ref: "review@opencode-local", harness: "opencode")
        #expect(openCode.question.contains("global"))
    }
}
