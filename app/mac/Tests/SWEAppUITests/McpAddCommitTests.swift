import Foundation
import SWECore
import Testing
@testable import SWEApp

/// A failed MCP add must not leave the secrets it stored behind, unless
/// the hub's saved entry refers to them.
@Suite @MainActor struct McpAddCommitTests {
    private struct Refused: Error {}

    /// A fake hub and secrets store that records every call. The hub
    /// entry is the `${NAME}`s it refers to; the row the fake lists, like
    /// swe's, reports the referred names with no value as missing.
    @MainActor final class Fake {
        var store: [String: String]
        /// nil: the hub has no entry for the server.
        var hubRefs: Set<String>?
        var hubReadable = true
        /// What the add writes to the hub before failing or succeeding.
        var hubAfterAdd: Set<String>??
        var addError: Error?
        var failOnSet: String?
        var calls: [String] = []

        init(store: [String: String] = [:], hubRefs: Set<String>? = nil) {
            self.store = store
            self.hubRefs = hubRefs
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
                    if case .some(let refs) = self.hubAfterAdd { self.hubRefs = refs }
                    if let error = self.addError { throw error }
                },
                hubEntry: {
                    self.calls.append("list")
                    guard self.hubReadable else { throw Refused() }
                    guard let refs = self.hubRefs else { return nil }
                    return McpServer(name: "srv", command: "npx", harnesses: ["claude-code"],
                                     secretsMissing: refs.filter { self.store[$0] == nil }.sorted(),
                                     inHub: true)
                },
                removeSecret: { name in
                    self.calls.append("remove \(name)")
                    self.store[name] = nil
                })
        }
    }

    private let values = [("SRV_TOKEN", "t"), ("SRV_KEY", "k")]
    private let both: Set<String> = ["SRV_TOKEN", "SRV_KEY"]

    @Test func aSuccessfulAddKeepsEverySecretAndNeverListsTheHub() async throws {
        let fake = Fake()
        fake.hubAfterAdd = .some(both)
        try await fake.commit.run(values: values)
        #expect(fake.store == ["SRV_TOKEN": "t", "SRV_KEY": "k"])
        #expect(fake.calls == ["set SRV_TOKEN", "set SRV_KEY", "add"])
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
    /// writing anything. The old entry doesn't use the new names, so they
    /// go. (The old rule kept them whenever the hub held the server.)
    @Test func aRefusedReplaceCleansUpWhatTheOldEntryDoesNotUse() async {
        let fake = Fake(hubRefs: [])
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store.isEmpty)
    }

    /// swe saved the hub, then a harness file failed: the hub now refers
    /// to the secrets, so they end up stored, even though the listed row
    /// (built from the harness's old copy) looks the same as before.
    @Test func aFailureAfterTheHubWasSavedKeepsWhatItRefersTo() async {
        let fake = Fake(hubRefs: [])
        fake.hubAfterAdd = .some(both)
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store == ["SRV_TOKEN": "t", "SRV_KEY": "k"])
    }

    /// Only the names the saved entry uses come back.
    @Test func aHubThatUsesSomeNamesGetsOnlyThose() async {
        let fake = Fake()
        fake.hubAfterAdd = .some(["SRV_TOKEN"])
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store == ["SRV_TOKEN": "t"])
    }

    /// A hub that can't be read can't show a name is unused, so every
    /// value goes back rather than risk breaking a server that needs it.
    @Test func anUnreadableHubKeepsTheSecrets() async {
        let fake = Fake()
        fake.hubReadable = false
        fake.addError = Refused()
        await #expect(throws: Refused.self) { try await fake.commit.run(values: values) }
        #expect(fake.store == ["SRV_TOKEN": "t", "SRV_KEY": "k"])
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
        #expect(!fake.calls.contains { $0.hasPrefix("remove") })
    }
}
