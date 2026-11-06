import Foundation
import Testing
@testable import SWECore

/// An approval asked again under the same request id: a satellite resends a
/// refused batch whole, so the journal holds the ask twice. The chat keeps
/// one card, at the newest ask while it is open; an answered card stays
/// where it was. The iPhone and web chats follow the same rule.
@MainActor
@Suite struct ReaskedApprovalTests {
    private func model() -> SessionChatModel {
        SessionChatModel(key: "claude:s1", hub: LocalHub(locator: FakeLocator(address: nil),
                                                         store: HubDeviceStore(url: URL(filePath: "/dev/null"))))
    }

    private func envelope(_ type: String, _ seq: Int, _ data: String) -> HubEnvelope {
        HubEnvelope(type: type, session: "claude:s1", seq: seq,
                    data: try! HubWire.decoder.decode(HubJSON.self, from: Data(data.utf8)))
    }

    private func ask(_ seq: Int) -> HubEnvelope {
        envelope("approval.requested", seq, #"{"request_id": "r1", "prompt": "Run it?"}"#)
    }

    @Test func anOpenCardMovesToTheNewestAsk() {
        let chat = model()
        chat.apply(envelope("message.appended", 1, #"{"kind": "user", "text": "fix it"}"#))
        chat.apply(ask(2))
        chat.apply(envelope("message.appended", 3, #"{"kind": "user", "text": "fix it"}"#))
        chat.apply(ask(4))
        #expect(chat.items.map(\.id) == ["m1", "m3", "approval-r1"])
    }

    @Test func anAnsweredCardStands() {
        let chat = model()
        chat.apply(ask(1))
        chat.apply(envelope("approval.resolved", 2, #"{"request_id": "r1", "decision": "allow"}"#))
        chat.apply(envelope("message.appended", 3, #"{"kind": "assistant", "text": "done"}"#))
        chat.apply(ask(4))
        #expect(chat.items.map(\.id) == ["approval-r1", "m3"])
        guard case .approval(_, _, _, _, let state) = chat.items[0].kind else {
            Issue.record("expected the approval card first")
            return
        }
        #expect(state == .resolved("allow"))
    }
}
