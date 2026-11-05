import Foundation
import SWECore
import Testing
@testable import SWEApp

/// A failed MCP add must not leave the secrets it stored behind, unless
/// the hub already refers to them.
@Suite @MainActor struct McpAddCommitTests {
    private struct Refused: Error {}

    /// A fake hub and secrets store that records every call.
    @MainActor final class Fake {
        var store: [String: String]
        var hub: McpServer?
        var hubReadable = true
        /// Thrown by the add after `hubAfterAdd` lands, when set.
        var addError: Error?
        var hubAfterAdd: McpServer??
        var failOnSet: String?
        var calls: [String] = []

        init(store: [String: String] = [:], hub: McpServer? = nil) {
            self.store = store
            self.hub = hub
        }

        var commit: McpAddCommit {
            McpAddCommit(
                setSecret: { name, value in
                    self.calls.append("set \(name)")
                    if name == self.failOnSet { throw Refused() }
                    let replaced = self.store[name] != nil
                    self.store[name] = value
                    return replaced
                },
                add: {
                    self.calls.append("add")
                    if case .some(let entry) = self.hubAfterAdd { self.hub = entry }
                    if let error = self.addError { throw error }
                },
                hubEntry: {
                    self.calls.append("list")
                    guard self.hubReadable else { throw Refused() }
                    return self.hub
                },
                removeSecret: { name in
                    self.calls.append("remove \(name)")
                    self.store[name] = nil
                })
        }
    }

    private static func server(_ name: String, harnesses: [String] = []) -> McpServer {
        McpServer(name: name, command: "npx", harnesses: harnesses, inHub: true)
    }

    private let values = [("SRV_TOKEN", "t"), ("SRV_KEY", "k")]

    @Test func aSuccessfulAddKeepsEverySecret() async throws {
        let fake = Fake()
        fake.hubAfterAdd = .some(Self.server("srv"))
        try await fake.commit.run(values: values)
        #expect(fake.store == ["SRV_TOKEN": "t", "SRV_KEY": "k"])
        #expect(!fake.calls.contains { $0.hasPrefix("remove") })
    }

    @Test func aRefusedNewAddRemovesTheSecretsItCreated() async {
        let fake = Fake()
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store.isEmpty)
    }

    /// A name that held a value before is never removed: it was not this
    /// add's to take back.
    @Test func aValueThatExistedBeforeIsNeverRemoved() async {
        let fake = Fake(store: ["SRV_KEY": "old"])
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store["SRV_TOKEN"] == nil)
        #expect(fake.store["SRV_KEY"] != nil)
        #expect(!fake.calls.contains("remove SRV_KEY"))
    }

    /// Replace on: the hub already had the server, and swe refused before
    /// writing anything. The entry is unchanged, so the new secrets go.
    @Test func aRefusedReplaceWithAnUnchangedHubStillCleansUp() async {
        let fake = Fake(hub: Self.server("srv", harnesses: ["claude-code"]))
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store.isEmpty)
    }

    /// swe saved the hub, then a harness file failed: the hub now refers
    /// to the secrets, so they stay.
    @Test func aFailureAfterTheHubChangedKeepsTheSecrets() async {
        let fake = Fake()
        fake.hubAfterAdd = .some(Self.server("srv"))
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store == ["SRV_TOKEN": "t", "SRV_KEY": "k"])
    }

    @Test func aReplacedHubEntryKeepsTheSecrets() async {
        let fake = Fake(hub: Self.server("srv"))
        fake.hubAfterAdd = .some(McpServer(name: "srv", command: "uvx", harnesses: [], inHub: true))
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store.count == 2)
    }

    /// A hub that can't be read can't show the entry is unchanged, so the
    /// secrets stay rather than risk breaking a server that needs them.
    @Test func anUnreadableHubKeepsTheSecrets() async {
        let fake = Fake()
        fake.hubReadable = false
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store.count == 2)
    }

    /// A store that refuses the second value: the first, already stored,
    /// comes back out, and the add never runs.
    @Test func aFailedSecretWriteTakesBackTheEarlierOnes() async {
        let fake = Fake()
        fake.failOnSet = "SRV_KEY"
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store.isEmpty)
        #expect(!fake.calls.contains("add"))
    }

    /// An older swe that doesn't say whether it replaced a value: the
    /// name is kept, since it may have held one.
    @Test func anUnknownReplacedAnswerKeepsTheName() async {
        let fake = Fake()
        fake.addError = Refused()
        let commit = fake.commit
        let unknown = McpAddCommit(
            setSecret: { name, value in _ = try await commit.setSecret(name, value); return nil },
            add: commit.add, hubEntry: commit.hubEntry, removeSecret: commit.removeSecret)
        await #expect(throws: Refused.self) { try await unknown.run(values: values) }
        #expect(fake.store.count == 2)
    }

    @Test func noValuesMeansNoExtraHubRead() async throws {
        let fake = Fake()
        try await fake.commit.run(values: [])
        #expect(fake.calls == ["add"])
    }
}
