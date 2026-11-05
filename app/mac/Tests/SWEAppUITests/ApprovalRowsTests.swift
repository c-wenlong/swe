import SWECore
import Testing
@testable import SWEApp

/// The approval card offers a pane's menu rows only when the hub would
/// take the decision each one posts.
@Suite struct ApprovalRowsTests {
    private func rows(_ options: [HubApprovalOption]) -> [String] {
        ApprovalRows.menu(options).map { "\($0.n) \($0.label)" }
    }

    @Test func keepsLabelledRowsWithOneOrTwoDigits() {
        #expect(rows([
            .init(n: "1", label: "Yes"),
            .init(n: "2", label: "Yes, and don't ask again"),
            .init(n: "12", label: "No"),
        ]) == ["1 Yes", "2 Yes, and don't ask again", "12 No"])
    }

    /// Every one of these would post a decision the hub refuses.
    @Test func dropsRowsTheHubWouldRefuse() {
        #expect(rows([
            .init(n: nil, label: "No number"),
            .init(n: "", label: "Empty number"),
            .init(n: "123", label: "Three digits"),
            .init(n: "a", label: "Letter"),
            .init(n: "1a", label: "Mixed"),
            .init(n: "٣", label: "Arabic-Indic digit"),
            .init(n: "²", label: "Superscript"),
            .init(n: " 1", label: "Padded"),
        ]).isEmpty)
    }

    @Test func dropsUnlabelledRows() {
        #expect(rows([.init(n: "1", label: nil), .init(n: "2", label: ""), .init(n: "3", label: "  ")]).isEmpty)
    }

    /// A menu with nothing usable leaves the card on Allow / Always /
    /// Deny, which every hub takes.
    @Test func noUsableRowsMeansTheFallback() {
        #expect(ApprovalRows.menu([]).isEmpty)
        #expect(ApprovalRows.menu([.init(n: nil, label: "Yes")]).isEmpty)
    }
}
