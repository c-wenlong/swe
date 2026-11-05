import SWECore
import SwiftUI

/// A launch's progress under its form: the hub's step while it works,
/// then why it stopped. Shared by every form that starts a session on
/// the hub (new session, fork, resolve an issue, start a task).
struct LaunchStatus: View {
    var flow: IssueLaunchFlow

    var body: some View {
        switch flow.phase {
        case .failed(let message):
            Text(message).font(Theme.caption).foregroundStyle(Theme.dangerText)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        case .unconfirmed(let message):
            Text(message).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        default:
            if let note = flow.note {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                }
            }
        }
    }
}

/// A launch form's Cancel and Start, laid out by the row they sit in.
/// Cancel's title says what it stops (`IssueLaunchFlow.cancelTitle`);
/// Start fires on ⌘↩, shows a spinner while the launch works, and stays
/// off until the form is `ready` and nothing runs.
struct LaunchFormButtons: View {
    var flow: IssueLaunchFlow
    var ready: Bool
    /// What Start does, for its hover tip: "Start the session".
    var startTip: String
    var cancel: () -> Void
    var start: () -> Void

    var body: some View {
        Button(flow.cancelTitle, action: cancel)
            .secondaryButton()
            .fixedSize()
            .keyboardShortcut(.cancelAction)
        Button(action: start) {
            HStack(spacing: 6) {
                if flow.busy {
                    ProgressView().controlSize(.small).tint(Theme.primaryForeground)
                }
                Text(flow.busy ? "Starting" : "Start")
            }
        }
        .primaryButton()
        .fixedSize()
        .keyboardShortcut(.return, modifiers: .command)
        .disabled(!ready || flow.busy)
        .hoverTip("\(startTip) (⌘↩)", replacesLabel: false, pinsOnClick: false)
    }
}
