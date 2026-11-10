import Foundation
import Testing
@testable import SWECore

@Suite struct PluginLocalInventoryTests {
    @Test func onlyOwnedSourcesAndInstalledFilesAppear() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url.appending(path: ".swe/plugins/kit")
        let owned = root.appending(path: "owned")
        let cache = temp.url.appending(path: "cache/plugin")
        let preview = temp.url.appending(path: "marketplace/preview")
        for dir in [owned, cache, preview] {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        let listed = PluginHarnessState(available: true, installed: false)
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "kit", hub: true, path: root.path,
                              harnesses: ["claude": .init(sourceType: "directory", location: root.path)], plugins: [
                CatalogPlugin(name: "owned", marketplace: "kit", sourcePath: owned.path,
                              harnesses: ["claude": listed])]),
            PluginMarketplace(name: "official", harnesses: ["claude": .init(sourceType: "github")], plugins: [
                CatalogPlugin(name: "preview", marketplace: "official", sourcePath: preview.path,
                              harnesses: ["claude": listed]),
                CatalogPlugin(name: "installed", marketplace: "official",
                              harnesses: ["claude": .init(available: true, installed: true, enabled: true, path: cache.path)]),
                CatalogPlugin(name: "missing", marketplace: "official",
                              harnesses: ["claude": .init(available: true, installed: true, path: "/missing-plugin")])])
        ])
        let index = PluginExploreIndex(catalog: catalog, skills: [], home: temp.url)
        #expect(index.localRefs == ["owned@kit", "installed@official"])
        #expect(index.localEntries(query: "installed").map(\.row.id) == ["installed@official"])
        let ownedRow = try #require(index.localEntries().first { $0.row.isHub })
        #expect(ownedRow.row.actions["claude"]?.map(\.verb) == [.install])
        let external = try #require(index.localEntries().first { !$0.row.isHub })
        #expect(external.row.actions["claude"]?.isEmpty == true)
        // A new index rechecks disk; cached derived rows never outlive a reload.
        try FileManager.default.removeItem(at: cache)
        let refreshed = PluginExploreIndex(catalog: catalog, skills: [], home: temp.url)
        #expect(refreshed.localRefs == ["owned@kit"])
    }

    @Test @MainActor func importRunsThroughTheModelAndReloads() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let cache = temp.url.appending(path: "installed")
        try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
        let catalog = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "kit", harnesses: ["claude": .init(sourceType: "github")], plugins: [
                CatalogPlugin(name: "tool", marketplace: "kit", harnesses: [
                    "claude": .init(available: true, installed: true, path: cache.path)])])])
        let encoded = String(decoding: try JSONEncoder().encode(catalog), as: UTF8.self)
        let runner = ScriptedRunner([
            (["plugins", "catalog"], json(encoded)),
            (["plugins", "import"], json(#"{"action":"import","harness":"swe","ref":"tool@kit","changed":true}"#))
        ])
        let swe = SweClient(runner: runner)
        let model = PluginCatalogModel(home: temp.url)
        await model.load(swe: swe)
        await model.run(PluginAction(verb: .adopt, ref: "tool@kit", harness: "swe"), swe: swe)
        #expect(runner.calls.contains(["plugins", "import", "tool@kit", "--json"]))
        #expect(model.lastResult == "Imported tool@kit into SWE")
        #expect(model.actionError == nil)
        try FileManager.default.removeItem(at: cache)
        await model.load(swe: swe)
        #expect(model.index?.localRefs.isEmpty == true)
    }

    @Test func importUsesSweWithoutAHarnessFlag() {
        let action = PluginAction(verb: .adopt, ref: "tool@kit", harness: "swe")
        #expect(action.arguments == ["plugins", "import", "tool@kit", "--json"])
        #expect(action.command == "swe plugins import tool@kit")
        #expect(action.question == "Import tool@kit into SWE?")
    }
}
