import Foundation
import Testing
@testable import SWECore

@Suite struct InfraListTests {
    @Test func aVariableTheCatalogueClaimsIsInfra() {
        #expect(InfraList.isInfra(inferenceKey("VERCEL_TOKEN"), marked: []))
        #expect(InfraList.isInfra(inferenceKey("DIGITALOCEAN_TOKEN"), marked: []))
    }

    @Test func anInferenceVariableStaysEvenUnderAnInfraHeading() {
        let row = inferenceKey("VERCEL_GATEWAY_API_KEY", provider: "Vercel")
        #expect(!InfraList.isInfra(row, marked: []))
        // A mark can't override a catalogue variable either way.
        #expect(!InfraList.isInfra(row, marked: ["VERCEL_GATEWAY_API_KEY"]))
        #expect(!InfraList.isInfra(inferenceKey("OPENAI_API_KEY"), marked: ["OPENAI_API_KEY"]))
    }

    /// A saved plan key claims its name ahead of the heading: Token
    /// Pass files VERCEL_TOKEN_PASS_KEY under "# Vercel" and must stay
    /// on Inference, or the plan row would read missing.
    @Test func aPlanKeyStaysInferenceEvenUnderAnInfraHeading() {
        let row = inferenceKey("VERCEL_TOKEN_PASS_KEY", provider: "Vercel")
        #expect(!InfraList.isInfra(row, marked: []))
        #expect(!InfraList.isInfra(row, marked: ["VERCEL_TOKEN_PASS_KEY"]))
        let items = InfraList.items(keys: [row])
        #expect(!items.contains { $0.key?.name == "VERCEL_TOKEN_PASS_KEY" })
    }

    @Test func aHeadingNamingAnInfraServiceIsInfra() {
        #expect(InfraList.isInfra(inferenceKey("SOME_TOKEN", provider: "Vercel"), marked: []))
        #expect(InfraList.isInfra(inferenceKey("SOME_TOKEN", provider: "FLY_IO"), marked: []))
        #expect(!InfraList.isInfra(inferenceKey("SOME_TOKEN", provider: "MyHosting"), marked: []))
    }

    @Test func anUnclaimedNameNeedsAMark() {
        let row = inferenceKey("MYHOSTING_TOKEN", provider: "MyHosting")
        #expect(!InfraList.isInfra(row, marked: []))
        #expect(InfraList.isInfra(row, marked: ["MYHOSTING_TOKEN"]))
    }

    @Test func itemsKeepFileOrderThenServicesWithNoKey() {
        let items = InfraList.items(keys: [
            inferenceKey("VERCEL_TOKEN", provider: "Deploy"),
            inferenceKey("CUSTOM_TOKEN", provider: "Mine"),
            inferenceKey("OPENAI_API_KEY"),
        ], marked: ["CUSTOM_TOKEN"])
        #expect(items.map(\.id).prefix(2).elementsEqual(["VERCEL_TOKEN", "CUSTOM_TOKEN"]))
        #expect(items[0].provider?.id == "vercel")
        #expect(items[0].title == "Vercel")
        // The custom row has no service; its heading is the title.
        #expect(items[1].provider == nil)
        #expect(items[1].title == "Mine")
        let missing = items.dropFirst(2)
        #expect(missing.count == APIKeyProviders.infra.count - 1)
        #expect(missing.allSatisfy { $0.key == nil && !$0.hasKey })
        #expect(!missing.contains { $0.id == "VERCEL_TOKEN" })
        // A key for a non-first variable still covers the service.
        let alt = InfraList.items(keys: [inferenceKey("DIGITALOCEAN_TOKEN")])
        #expect(!alt.contains { $0.id == "DIGITALOCEAN_ACCESS_TOKEN" && $0.key == nil })
    }

    @Test func matchesReadsStateNameAndHeading() {
        let item = InfraList.items(keys: [inferenceKey("VERCEL_TOKEN", provider: "Deploy")],
                                   marked: [])[0]
        let state: (InfraItem) -> InferenceKeyState = { $0.hasKey ? .valid : .noKey }
        #expect(InfraList.matches(item, state: .valid, query: "", stateOf: state))
        #expect(!InfraList.matches(item, state: .invalid, query: "", stateOf: state))
        #expect(InfraList.matches(item, state: nil, query: "vercel", stateOf: state))
        #expect(InfraList.matches(item, state: nil, query: "vercel_token", stateOf: state))
        #expect(InfraList.matches(item, state: nil, query: "deploy", stateOf: state))
        #expect(!InfraList.matches(item, state: nil, query: "netlify", stateOf: state))
    }

    @MainActor @Test func dragOrderPersistsAndNewRowsGoLast() {
        let store = MemoryStore()
        let order = InfraOrder(store: store)
        let items = InfraList.items(keys: [
            inferenceKey("VERCEL_TOKEN"), inferenceKey("NEON_API_KEY"),
        ], providers: [])
        order.move("NEON_API_KEY", before: "VERCEL_TOKEN", in: items)
        #expect(order.sorted(items).map(\.id) == ["NEON_API_KEY", "VERCEL_TOKEN"])
        #expect(store.values["infraOrder"] == ["NEON_API_KEY", "VERCEL_TOKEN"])
        let more = InfraList.items(keys: [
            inferenceKey("VERCEL_TOKEN"), inferenceKey("NEON_API_KEY"), inferenceKey("TURSO_API_TOKEN"),
        ], providers: [])
        #expect(InfraOrder(store: store).sorted(more).map(\.id)
            == ["NEON_API_KEY", "VERCEL_TOKEN", "TURSO_API_TOKEN"])
        order.move("NEON_API_KEY", before: nil, in: more)
        #expect(order.sorted(more).map(\.id) == ["VERCEL_TOKEN", "TURSO_API_TOKEN", "NEON_API_KEY"])
    }

    /// A key saved under a sibling variable takes the catalogue row's
    /// dragged place instead of landing at the end as a new id.
    @MainActor @Test func aSiblingVariableKeepsTheDraggedPlace() {
        let store = MemoryStore()
        let order = InfraOrder(store: store)
        let rows = InfraList.items(keys: [inferenceKey("VERCEL_TOKEN"), inferenceKey("NEON_API_KEY")])
        order.move("DIGITALOCEAN_ACCESS_TOKEN", before: "VERCEL_TOKEN", in: rows)
        order.substitute("DIGITALOCEAN_ACCESS_TOKEN", with: "DIGITALOCEAN_TOKEN")
        #expect(store.values["infraOrder"]?.prefix(3)
            == ["DIGITALOCEAN_TOKEN", "VERCEL_TOKEN", "NEON_API_KEY"])
        let moved = InfraList.items(keys: [
            inferenceKey("VERCEL_TOKEN"), inferenceKey("DIGITALOCEAN_TOKEN"), inferenceKey("NEON_API_KEY"),
        ])
        #expect(order.sorted(moved).prefix(3).map(\.id)
            == ["DIGITALOCEAN_TOKEN", "VERCEL_TOKEN", "NEON_API_KEY"])
    }

    @MainActor @Test func marksPersistSorted() {
        let store = MemoryStore()
        let marks = InfraMarks(store: store)
        marks.mark("MYHOSTING_TOKEN")
        marks.mark("ACME_TOKEN")
        #expect(marks.names == ["MYHOSTING_TOKEN", "ACME_TOKEN"])
        #expect(store.values["infraKeys"] == ["ACME_TOKEN", "MYHOSTING_TOKEN"])
        #expect(InfraMarks(store: store).names == ["MYHOSTING_TOKEN", "ACME_TOKEN"])
        marks.unmark("ACME_TOKEN")
        #expect(marks.names == ["MYHOSTING_TOKEN"])
    }
}

@Suite struct InfraCatalogueTests {
    @Test func everyServiceInOrder() {
        let entries = InfraCatalogue.entries(keys: [])
        #expect(entries.count == APIKeyProviders.infra.count)
        #expect(entries.map(\.name).elementsEqual(APIKeyProviders.infra.map(\.name)))
        #expect(entries.allSatisfy { !$0.hasKey && $0.kind == .apiKey })
    }

    @Test func aSavedKeyMarksItsEntry() {
        let entries = InfraCatalogue.entries(
            keys: [inferenceKey("VERCEL_TOKEN"), inferenceKey("DIGITALOCEAN_TOKEN"),
                   inferenceKey("NEON_API_KEY", "")])
        #expect(entries.first { $0.id == "provider:vercel" }?.hasKey == true)
        // Any of a service's variables marks it, not only the first.
        #expect(entries.first { $0.id == "provider:digitalocean" }?.hasKey == true)
        // An empty value reads as no key, like the page's own rows.
        #expect(entries.first { $0.id == "provider:neon" }?.hasKey == false)
        // The table's own claim applies: a key under "# Vercel" fills
        // Vercel in the catalogue too, while an inference variable under
        // the same heading does not.
        let byHeading = InfraCatalogue.entries(
            keys: [inferenceKey("DEPLOY_TOKEN", provider: "Vercel"),
                   inferenceKey("OPENAI_API_KEY", provider: "Cloudflare")])
        #expect(byHeading.first { $0.id == "provider:vercel" }?.hasKey == true)
        #expect(byHeading.first { $0.id == "provider:cloudflare" }?.hasKey == false)
    }

    /// The sheet's mark column: every row's logo names a bundled
    /// harness-*.svg, as the inference catalogue's own test pins.
    @Test func everyEntryResolvesABundledMark() {
        for entry in InfraCatalogue.entries(keys: []) {
            let logo = entry.logo
            #expect(logo != nil, "\(entry.id) has no bundled mark")
            #expect(logo.flatMap { HarnessIcons.icon(for: $0) } != nil,
                    "\(entry.id) names \(logo ?? "?") with no harness-*.svg")
        }
    }
}

@Suite struct InfraProbeTests {
    /// Each infra service's first variable either has a probe with an
    /// auth-reading host, or an explicit no-check reason; a silent nil
    /// on the probe table would land as the generic message instead.
    @Test func everyInfraServiceStatesItsCheck() {
        for provider in APIKeyProviders.infra {
            let name = provider.envVars[0]
            let probed = APIKeyProbe.probe(forName: name) != nil
            let reasoned = ["fly", "railway", "heroku"].contains(provider.id)
            #expect(probed || reasoned,
                    "\(provider.id) has no probe and no stated no-check reason")
            if reasoned {
                #expect(!APIKeyProbe.noCheckReason(forName: name).contains("doesn't know how"),
                        "\(provider.id) should name why there's no check")
            }
        }
    }

    @Test func infraProbesAreBearerGets() {
        let probe = APIKeyProbe.probe(forName: "VERCEL_TOKEN")
        #expect(probe?.auth == .bearer)
        #expect(probe?.method == "GET")
        #expect(probe?.hosts == [URL(string: "https://api.vercel.com/v2/user")!])
        // Both DigitalOcean spellings share one probe.
        #expect(APIKeyProbe.probe(forName: "DIGITALOCEAN_TOKEN")
            == APIKeyProbe.probe(forName: "DIGITALOCEAN_ACCESS_TOKEN"))
    }
}
