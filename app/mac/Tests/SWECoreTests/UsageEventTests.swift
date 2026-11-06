import Foundation
import Testing
@testable import SWECore

/// Reading a `UsageEvent` back from stored JSON, including the shape an old
/// snapshot has (the keys later versions added are absent). Made-up values.
@Suite struct UsageEventDecodingTests {
    private static let required = #"""
    "harness": "claude", "accountHome": "/h/.claude", "sessionId": "s-1",
    "model": "model-x", "timestamp": 1000,
    "inputTokens": 10, "outputTokens": 20, "cacheReadTokens": 30,
    "cacheWriteTokens": 40, "cacheWrite1hTokens": 5, "reasoningTokens": 6
    """#

    private func decode(_ body: String) throws -> UsageEvent {
        try JSONDecoder().decode(UsageEvent.self, from: Data("{\(body)}".utf8))
    }

    @Test func anOldShapeWithNoOptionalKeysGetsTheirDefaults() throws {
        let event = try decode(Self.required)
        #expect(event.cwd == nil)
        #expect(event.costUSD == nil)
        #expect(event.serviceTier == nil)
        #expect(event.isSessionTotal == false)
        #expect(event.timestamp == Date(timeIntervalSinceReferenceDate: 1000))
        #expect(event.totalTokens == 10 + 20 + 30 + 40 + 6)
    }

    @Test func theOptionalKeysAreReadWhenPresent() throws {
        let event = try decode(Self.required + #"""
        , "cwd": "/w/app", "costUSD": 0.25, "serviceTier": "fast", "isSessionTotal": true
        """#)
        #expect(event.cwd == "/w/app")
        #expect(event.costUSD == 0.25)
        #expect(event.serviceTier == "fast")
        #expect(event.isSessionTotal)
    }

    @Test func nullOptionalsReadAsMissing() throws {
        let event = try decode(Self.required + #", "cwd": null, "costUSD": null, "serviceTier": null"#)
        #expect(event.cwd == nil && event.costUSD == nil && event.serviceTier == nil)
    }

    @Test func aMissingTokenCountIsAnErrorNotAZero() {
        let withoutReasoning = Self.required.replacingOccurrences(of: #", "reasoningTokens": 6"#, with: "")
        #expect(throws: DecodingError.self) { try decode(withoutReasoning) }
        let withoutModel = Self.required.replacingOccurrences(of: #""model": "model-x","#, with: "")
        #expect(throws: DecodingError.self) { try decode(withoutModel) }
    }

    @Test func anEventRoundTripsThroughItsOwnEncoding() throws {
        let event = UsageEvent(
            harness: "codex", accountHome: "/h/.codex", sessionId: "s-2", cwd: "/w",
            model: "model-y", timestamp: Date(timeIntervalSince1970: 1_700_000_000),
            inputTokens: 1, outputTokens: 2, cacheReadTokens: 3, cacheWriteTokens: 4,
            cacheWrite1hTokens: 1, reasoningTokens: 5, costUSD: 1.5, serviceTier: "priority",
            isSessionTotal: true)
        let back = try JSONDecoder().decode(UsageEvent.self, from: JSONEncoder().encode(event))
        #expect(back == event)
    }
}
