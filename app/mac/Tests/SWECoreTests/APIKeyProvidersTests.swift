import Foundation
import Testing
@testable import SWECore

@Suite struct APIKeyProvidersTests {
    /// The (slug, url, env_vars) of every entry one defaults.py dict
    /// holds, in file order: `from` is `DEFAULT_PROVIDERS = {`,
    /// `DEFAULT_INFRA = {` or `DEFAULT_SERVICES = {`. Reading straight
    /// out of the file keeps the
    /// Swift tables honest when the Python one changes.
    private static func pythonProviders(from marker: String) throws -> [(slug: String, url: String, envVars: [String])] {
        let file = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // SWECoreTests
            .deletingLastPathComponent() // Tests
            .deletingLastPathComponent() // app/mac
            .appending(path: "../../src/swe/providers/defaults.py")
            .standardizedFileURL
        let text = try String(contentsOf: file, encoding: .utf8)
        let start = try #require(text.range(of: marker), "defaults.py has no \(marker)")
        // The dict runs to the next top-level definition.
        let rest = text[start.upperBound...]
        let end = rest.firstMatch(of: #/(?m)^[A-Z_]+ = /#)?.range.lowerBound ?? rest.endIndex
        let body = String(rest[..<end])
        let entryPattern = #/(?m)^    "([a-z0-9_]+)": \{\n(?:(?!^    \},)[\s\S])*^    \},?$/#
        let urlPattern = #/"url": "([^"]+)"/#
        let envPattern = #/"env_vars": \[([^\]]*)\]/#
        let quoted = #/"([^"]*)"/#
        var parsed: [(String, String, [String])] = []
        for entry in body.matches(of: entryPattern) {
            let block = entry.output.0
            let slug = String(entry.output.1)
            let url = try #require(block.firstMatch(of: urlPattern)).1
            let list = try #require(block.firstMatch(of: envPattern)).1
            // # comments inside the list can hold quoted words; cut them first.
            let envVars = list.components(separatedBy: "\n").flatMap { line in
                let code = line.prefix { $0 != "#" }
                return code.matches(of: quoted).map { String($0.1) }
            }
            parsed.append((slug, String(url), envVars))
        }
        return parsed
    }

    @Test func theTableMatchesDefaultsPy() throws {
        let python = try Self.pythonProviders(from: "DEFAULT_PROVIDERS = {")
        #expect(python.count > 20, "defaults.py parsed almost empty; the sync regex broke")
        expect(APIKeyProviders.all, match: python)
        let infra = try Self.pythonProviders(from: "DEFAULT_INFRA = {")
        #expect(infra.count >= 10, "DEFAULT_INFRA parsed almost empty; the sync regex broke")
        expect(APIKeyProviders.infra, match: infra)
        let services = try Self.pythonProviders(from: "DEFAULT_SERVICES = {")
        #expect(services.count >= 5, "DEFAULT_SERVICES parsed almost empty; the sync regex broke")
        expect(APIKeyProviders.services, match: services)
    }

    /// One Swift table against the parsed entries of one defaults.py
    /// dict: same slugs in the same order, then url and env vars equal.
    private func expect(_ swift: [APIKeyProvider], match python: [(slug: String, url: String, envVars: [String])]) {
        let swiftIDs = swift.map(\.id), pythonIDs = python.map(\.slug)
        #expect(swiftIDs == pythonIDs, """
            The Swift table and defaults.py list different providers. Only in Swift: \
            \(Set(swiftIDs).subtracting(pythonIDs).sorted()); only in defaults.py: \
            \(Set(pythonIDs).subtracting(swiftIDs).sorted()). If both look the same, \
            defaults.py was likely reformatted and pythonProviders() needs its new layout.
            """)
        for (provider, expected) in zip(swift, python) {
            #expect(provider.keyURL.absoluteString == expected.url,
                    "\(provider.id) url differs from defaults.py")
            #expect(provider.envVars == expected.envVars,
                    "\(provider.id) env vars differ from defaults.py")
        }
    }

    /// Hand-picked answers: a loop comparing `canCheck` with the probe
    /// lookup would only restate `canCheck`'s own body.
    @Test func canCheckFollowsTheProbeTable() {
        #expect(APIKeyProviders.provider(forID: "openai")?.canCheck == true)
        #expect(APIKeyProviders.provider(forID: "anthropic")?.canCheck == true)
        // Cohere has no probe, so no check.
        #expect(APIKeyProviders.provider(forID: "cohere")?.canCheck == false)
    }

    @Test func aVariableFindsItsProviderByAnyEnvVar() {
        #expect(APIKeyProviders.provider(forVariable: "TOGETHER_API_KEY")?.id == "together_ai")
        #expect(APIKeyProviders.provider(forVariable: "TOGETHER_AI_API_KEY")?.id == "together_ai")
        #expect(APIKeyProviders.provider(forVariable: "GOOGLE_API_KEY")?.id == "gemini")
        #expect(APIKeyProviders.provider(forVariable: "GH_TOKEN")?.id == "github")
        #expect(APIKeyProviders.provider(forVariable: "NOPE_API_KEY") == nil)
    }

    @Suite @MainActor struct HeadingTests {
        private func rows(_ pairs: [(provider: String, name: String)]) -> [APIKeysModel.Row] {
            pairs.map { APIKeysModel.Row(provider: $0.provider, name: $0.name,
                                         value: "v", status: .loaded) }
        }

        @Test func aGroupHoldingAnotherOfItsVariablesWins() {
            let rows = rows([("GOOGLE", "GOOGLE_API_KEY")])
            let gemini = APIKeyProviders.provider(forID: "gemini")!
            #expect(APIKeyProviders.heading(for: gemini, in: rows) == "GOOGLE")
        }

        @Test func aHeadingMatchingTheNameIgnoringCaseWins() {
            let anthropic = APIKeyProviders.provider(forID: "anthropic")!
            #expect(APIKeyProviders.heading(for: anthropic,
                                          in: rows([("ANTHROPIC", "OTHER_KEY")])) == "ANTHROPIC")
        }

        @Test func aHeadingMatchingIgnoringSpacesAndUnderscoresWins() {
            let together = APIKeyProviders.provider(forID: "together_ai")!
            #expect(APIKeyProviders.heading(for: together,
                                          in: rows([("Together AI", "OTHER_KEY")])) == "Together AI")
            #expect(APIKeyProviders.heading(for: together,
                                          in: rows([("TOGETHER_AI", "OTHER_KEY")])) == "TOGETHER_AI")
        }

        @Test func noMatchMeansTheProviderName() {
            let openai = APIKeyProviders.provider(forID: "openai")!
            #expect(APIKeyProviders.heading(for: openai,
                                          in: rows([("ANTHROPIC", "ANTHROPIC_API_KEY")])) == "OpenAI")
            #expect(APIKeyProviders.heading(for: openai, in: []) == "OpenAI")
        }
    }
}
