import Foundation
import SWECore
import Testing
@testable import SWEApp

/// Copy diagnostics masks every name an account goes by, so a paste into
/// a public issue names no one, without masking ordinary words.
@Suite struct FeedbackRedactionTests {
    private let now = Date(timeIntervalSince1970: 0)

    private func profile(_ id: String, _ name: String, isDefault: Bool = false) -> Profile {
        Profile(id: id, vendor: id.hasPrefix("codex") ? .codex : .claude, name: name,
                isDefault: isDefault, color: "#000000")
    }

    @Test func aProfileGivesItsLabelIdAndSlug() {
        let names = Feedback.accountNames(profiles: [profile("claude-work", "Acme Corp")], issues: [])
        #expect(Set(names) == ["claude-work", "Acme Corp", "work"])
    }

    /// The built-in default profiles are called "Default": masking that
    /// would hide the word "default" in every message.
    @Test func theBuiltInDefaultNamesAreNotMasked() {
        let names = Feedback.accountNames(profiles: [
            profile("claude-default", "Default", isDefault: true),
            profile("codex-default", "default", isDefault: true),
        ], issues: [])
        #expect(Set(names) == ["claude-default", "codex-default"])

        let text = IssueReport.diagnostics(
            version: "v", os: "o",
            findings: [.init(check: "mcp.default", severity: .info,
                             message: "the default config is used by claude-default")],
            accounts: names, home: "/Users/x", user: "x")
        #expect(text.hasSuffix("info mcp.default: the default config is used by <account>"))
    }

    @Test func aRenamedDefaultProfileStillHasItsLabelMasked() {
        let names = Feedback.accountNames(profiles: [profile("claude-default", "Kai Personal", isDefault: true)],
                                          issues: [])
        #expect(Set(names) == ["claude-default", "Kai Personal"])
    }

    /// An issue about an extra account adds its label and the account half
    /// of its key; the harness half stays readable.
    @Test func anIssueGivesItsAccountAndKeyButNotTheHarness() {
        let issue = Issue(id: "cursor/team-a/signed-out", harness: "cursor", account: "Team A",
                          accountKey: "cursor/team-a", status: .signedOut, at: now)
        let plain = Issue(id: "codex/signed-out", harness: "codex", status: .signedOut, at: now)
        let names = Feedback.accountNames(profiles: [], issues: [issue, plain])
        #expect(names == ["Team A", "team-a"])
    }

    @Test func endToEndAPasteNamesNoAccount() {
        let names = Feedback.accountNames(
            profiles: [profile("claude-work", "Acme Corp"), profile("claude-default", "Default", isDefault: true)],
            issues: [Issue(id: "cursor/side/x", harness: "cursor", account: "Side Gig",
                           accountKey: "side", status: .signedOut, at: now)])
        let text = IssueReport.diagnostics(
            version: "v", os: "o",
            findings: [
                .init(check: "usage.claude/work/signed-out", severity: .warning,
                      message: "Acme Corp (claude-work) is signed out"),
                .init(check: "usage.cursor/side/signed-out", severity: .warning,
                      message: "Side Gig is signed out; the default account is fine"),
            ],
            accounts: names, home: "/Users/x", user: "x")
        #expect(!text.contains("Acme"))
        #expect(!text.contains("Side Gig"))
        #expect(!text.contains("claude-work"))
        #expect(text.contains("usage.claude/<account>/signed-out"))
        #expect(text.contains("usage.cursor/<account>/signed-out"))
        #expect(text.contains("the default account is fine"))
    }
}
