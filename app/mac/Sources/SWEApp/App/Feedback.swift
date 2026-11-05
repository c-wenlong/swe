import AppKit
import SWECore

/// Help › Report a Bug and Settings › Feedback: both open the same GitHub
/// forms and copy the same diagnostics.
@MainActor
enum Feedback {
    /// Opens the form in the browser with the versions filled in. A bug
    /// report reads swe's version first when nothing has asked for it yet,
    /// so it never says "swe unknown" just because Settings was never open.
    static func openIssueForm(_ kind: IssueReport.Kind, app: AppModel) {
        Task {
            if kind == .bug, !app.updates.sweVersionKnown { await app.updates.refreshSweVersion() }
            NSWorkspace.shared.open(IssueReport.newIssueURL(
                kind, version: app.updates.versionLine, os: IssueReport.osLine()))
        }
    }

    /// What the Doctor card shows right now, live issue rows included.
    static func diagnostics(app: AppModel) -> String {
        let report: DoctorReport? = if case .loaded(let report) = app.doctor.state { report } else { nil }
        return IssueReport.diagnostics(
            version: app.updates.versionLine, os: IssueReport.osLine(),
            findings: IssueReport.merged(issues: app.issues.findings, report: report),
            accounts: accountNames(app: app))
    }

    private static func accountNames(app: AppModel) -> [String] {
        accountNames(profiles: app.accounts.cards.map(\.profile), issues: app.issues.open)
    }

    /// Every name an account goes by in a check id or message: the profile's
    /// label, id and slug, and the label and key of each extra account an
    /// open issue is about. The harness half of a key stays. A default
    /// profile's built-in "Default" label and "default" slug stay too:
    /// masking them would hide the ordinary word "default" in every
    /// message, and they name no one. A default profile the user renamed
    /// still has its new label masked.
    nonisolated static func accountNames(profiles: [Profile], issues: [Issue]) -> [String] {
        var names: [String] = []
        for profile in profiles {
            names.append(profile.id)
            if !(profile.isDefault && profile.name.caseInsensitiveCompare("default") == .orderedSame) {
                names.append(profile.name)
            }
            if !profile.isDefault,
               let slug = profile.id.split(separator: "-", maxSplits: 1).dropFirst().first {
                names.append(String(slug))
            }
        }
        for issue in issues {
            if let account = issue.account { names.append(account) }
            if let key = issue.accountKey {
                names += key.split(separator: "/").map(String.init).filter { $0 != issue.harness }
            }
        }
        return names
    }

    /// The menu item has no button to show progress on, so it runs the
    /// checks when they have not run, and copies once they finish. It is
    /// disabled while checks or a fix are in flight.
    static func copyDiagnostics(app: AppModel) {
        Task {
            if !app.updates.sweVersionKnown { await app.updates.refreshSweVersion() }
            if case .loaded = app.doctor.state {} else { await app.doctor.load() }
            copyToPasteboard(diagnostics(app: app))
        }
    }
}
