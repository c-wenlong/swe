import XCTest
@testable import SWERemote

/// An approval asked again under the same request id (a satellite resends a
/// refused batch whole): one card, at the newest ask while it is open; an
/// answered card stays put. The Mac and web chats follow the same rule.
@MainActor
final class ReaskedApprovalTests: XCTestCase {
    func testAnOpenCardMovesToTheNewestAsk() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.apply(env(#"{"type":"message.appended","seq":1,"data":{"kind":"user","text":"fix it"}}"#))
        chat.apply(env(#"{"type":"approval.requested","seq":2,"data":{"request_id":"r1","prompt":"Run it?"}}"#))
        chat.apply(env(#"{"type":"message.appended","seq":3,"data":{"kind":"user","text":"fix it"}}"#))
        chat.apply(env(#"{"type":"approval.requested","seq":4,"data":{"request_id":"r1","prompt":"Run it?"}}"#))
        XCTAssertEqual(chat.items.map(\.id), ["m1", "m3", "approval-r1"])
    }

    func testAnAnsweredCardStands() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.apply(env(#"{"type":"approval.requested","seq":1,"data":{"request_id":"r1","prompt":"Run it?"}}"#))
        chat.apply(env(#"{"type":"approval.resolved","seq":2,"data":{"request_id":"r1","decision":"allow"}}"#))
        chat.apply(env(#"{"type":"message.appended","seq":3,"data":{"kind":"assistant","text":"done"}}"#))
        chat.apply(env(#"{"type":"approval.requested","seq":4,"data":{"request_id":"r1","prompt":"Run it?"}}"#))
        XCTAssertEqual(chat.items.map(\.id), ["approval-r1", "m3"])
        XCTAssertEqual(chat.items.first?.kind, .approval(requestId: "r1", prompt: "Run it?", state: .resolved("allow")))
    }
}
