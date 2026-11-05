import Foundation
import Synchronization
import Testing
@testable import SWECore

extension HubStubbed {
/// `HubClient.events`: the cursor each reconnect asks from, and which
/// answers end the stream rather than reconnect. Each test uses a session
/// key of its own, so no other test's asks land in its list.
@Suite struct HubClientEventsTests {
    let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "secret",
                           session: HubStub.session(), streamSession: HubStub.session())

    /// What each ask for `path` sent as `last`, in order.
    private func lasts(_ path: String) -> [String] {
        HubStub.requests.filter { $0.url?.path == path }.compactMap { HubStub.query($0)["last"] }
    }

    /// A short name per stream event, so a test can compare the order.
    private func name(_ event: HubStreamEvent) -> String {
        switch event {
        case .connected: "connected"
        case .disconnected: "disconnected"
        case .unauthorized: "unauthorized"
        case .truncated(let seq): "truncated \(seq.map(String.init) ?? "nil")"
        case .event(let env): "\(env.type) \(env.seq.map(String.init) ?? "nil")"
        }
    }

    @Test(.timeLimit(.minutes(1))) func aReconnectAsksFromTheHighestSeqSeen() async throws {
        HubStub.requests = []
        let path = "/v1/sessions/claude:cursor/events"
        HubStub.handler = { req in
            guard req.url?.path == path else { return (404, Data()) }
            // The first connection gets three events, one of them out of
            // order, a keepalive, a frame that isn't JSON and the
            // caught-up marker; then it closes. The second is caught up.
            if HubStub.query(req)["last"] == "10" {
                return (200, Data("""
                    : ka

                    data: {"type": "message.appended", "seq": 12, "data": {}}

                    data: {"type": "message.appended", "seq": 15, "data": {}}

                    data: not json

                    data: {"type": "message.appended", "seq": 13, "data": {}}

                    data: {"type": "journal.caught-up", "seq": 15}


                    """.utf8))
            }
            return (200, Data("data: {\"type\": \"journal.caught-up\", \"seq\": 15}\n\n".utf8))
        }
        var seen: [String] = []
        var connects = 0
        for await event in client.events("claude:cursor", last: 10) {
            seen.append(name(event))
            if case .connected = event {
                connects += 1
                if connects == 2 { break }
            }
        }
        // A lower seq after a higher one never moves the cursor back, and
        // the caught-up marker is the stream's own business.
        #expect(seen == ["connected", "message.appended 12", "message.appended 15",
                         "message.appended 13", "disconnected", "connected"])
        #expect(lasts(path) == ["10", "15"])
        let first = try #require(HubStub.requests.first { $0.url?.path == path })
        #expect(first.value(forHTTPHeaderField: "Authorization") == "Bearer secret")
        #expect(first.value(forHTTPHeaderField: "Accept") == "text/event-stream")
    }

    @Test(.timeLimit(.minutes(1))) func aRefusedCredentialEndsTheStreamWithoutReconnecting() async {
        HubStub.requests = []
        let path = "/v1/sessions/claude:refused/events"
        HubStub.handler = { req in req.url?.path == path ? (401, Data()) : (404, Data()) }
        var seen: [String] = []
        // No break: the stream itself has to finish for the loop to end.
        for await event in client.events("claude:refused", last: 3) { seen.append(name(event)) }
        #expect(seen == ["unauthorized"])
        #expect(lasts(path) == ["3"])
    }

    @Test(.timeLimit(.minutes(1))) func aServerErrorReconnectsWithoutClaimingConnected() async {
        HubStub.requests = []
        let path = "/v1/sessions/claude:flaky/events"
        let asks = Mutex(0)
        HubStub.handler = { req in
            guard req.url?.path == path else { return (404, Data()) }
            if asks.withLock({ n in n += 1; return n }) == 1 { return (503, Data()) }
            return (200, Data("data: {\"type\": \"message.appended\", \"seq\": 4, \"data\": {}}\n\n".utf8))
        }
        var seen: [String] = []
        for await event in client.events("claude:flaky", last: 0) {
            seen.append(name(event))
            if case .event = event { break }
        }
        #expect(seen == ["disconnected", "connected", "message.appended 4"])
        // Nothing arrived before the error, so the retry asks from the start.
        #expect(lasts(path) == ["0", "0"])
    }
}
}
