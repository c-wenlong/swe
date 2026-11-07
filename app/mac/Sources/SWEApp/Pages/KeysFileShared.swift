import AppKit
import SWECore
import SwiftUI

/// The pieces Settings' two keys tabs share, since both list rows from
/// the same `.api_keys` file: the subtitle under the title, the banners
/// when new terminals don't get the file, the card for lines SWE can't
/// read, the count footer, the startup-file prompt and a concealed copy.

/// The caption under the title: where the file is, and whether new
/// terminals pick changes up.
@MainActor func keysSubtitle(_ model: APIKeysModel) -> String {
    switch model.sourcing {
    case .sourced:
        return "From \(displayPath(model.path)). New terminals pick up changes."
    case .notSourced, .notWritable:
        return "From \(displayPath(model.path)). New terminals don't read this file yet."
    case .unsupportedShell:
        return "From \(displayPath(model.path)). Add \"source \(displayPath(model.path))\" to your shell's startup file so new terminals get these keys."
    case .unknown:
        return "From \(displayPath(model.path))."
    }
}

/// The banners between the title row and the table: a prompt to source
/// the file when the startup file doesn't, a read-only notice when it
/// can't be edited, and the lines SWE leaves alone. Nothing when the
/// file is empty or everything is fine.
struct KeysFileNotices: View {
    let model: APIKeysModel
    /// Runs when the user confirms the "Add to" button; the caller owns
    /// the prompt that follows (confirmingSource).
    var askAddSource: (URL) -> Void

    var body: some View {
        if case .notSourced(let rc) = model.sourcing, !model.rows.isEmpty {
            Card {
                VStack(alignment: .leading, spacing: 8) {
                    Text("New terminals don't get these keys. SWE can add one line to \(rc.lastPathComponent).")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    HStack {
                        Spacer()
                        Button("Add to \(rc.lastPathComponent)") { askAddSource(rc) }
                            .primaryButton()
                    }
                }
            }
        }
        if case .notWritable(let rc, let real) = model.sourcing, !model.rows.isEmpty {
            Card {
                VStack(alignment: .leading, spacing: 8) {
                    Text("New terminals don't get these keys. \(rc.lastPathComponent) resolves to \(real.path), which is read-only, so SWE can't edit it.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("Add this line to the file that generates it: \(model.sourceLine)")
                            .font(Theme.font(size: 12, design: .monospaced))
                            .foregroundStyle(Theme.textSecondary)
                            .textSelection(.enabled)
                        Spacer()
                        CopyButton(help: "Copy the line") { copyToPasteboard(model.sourceLine) }
                    }
                }
            }
        }
        if !model.problems.isEmpty {
            Card(title: "Lines that set nothing") {
                ForEach(model.problems, id: \.line) { problem in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Line \(problem.line): \(problem.reason)")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                        // Already redacted by APIKeysFile: the value never reaches the page.
                        Text(problem.text)
                            .font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                    }
                }
                Text("Fix these in an editor; SWE leaves lines it can't read untouched.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }
}

/// "N keys from M providers · saved Xh ago", and the note that open
/// terminals keep old values once the file changed.
struct KeysFileFooter: View {
    let model: APIKeysModel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Text("\(model.rows.count) \(model.rows.count == 1 ? "key" : "keys") from \(model.providers.count) \(model.providers.count == 1 ? "provider" : "providers")")
                if let saved = model.savedAt {
                    Text("· saved \(saved, format: .relative(presentation: .named))")
                }
            }
            if model.savedAt != nil && model.sourcing == .sourced {
                Text("Terminals already open keep the old values until you run source \(displayPath(model.path)).")
            }
        }
        .font(Theme.caption).foregroundStyle(Theme.textMuted)
    }
}

/// The "add the source line" prompt both tabs attach: confirms the
/// append to the user's startup file, runs `addSourceLine`, and reports
/// through the caller's two state strings.
extension View {
    func keysSourceDialog(model: APIKeysModel, confirming: Binding<URL?>,
                          added: Binding<String?>, error: Binding<String?>) -> some View {
        confirmationDialog(
            "Add a line to \(confirming.wrappedValue?.lastPathComponent ?? "the startup file")?",
            isPresented: Binding(get: { confirming.wrappedValue != nil },
                                 set: { if !$0 { confirming.wrappedValue = nil } })) {
            Button("Add line") {
                let error = error
                error.wrappedValue = nil
                Task {
                    do {
                        let result = try await model.addSourceLine()
                        let file = result.file.lastPathComponent
                        added.wrappedValue = "Added to \(file)."
                            + (result.backup.map { " Backup at \($0.path)." } ?? "")
                            + " Open a new terminal to use the keys."
                    } catch let failure {
                        error.wrappedValue = failure.localizedDescription
                    }
                    confirming.wrappedValue = nil
                }
            }
            Button("Cancel", role: .cancel) { confirming.wrappedValue = nil }
        } message: {
            if let rc = confirming.wrappedValue {
                Text("SWE appends \(model.sourceLine) to \(rc.path). SWE backs up \(rc.lastPathComponent) first.")
            }
        }
    }
}

/// A `.task` that follows the keys file for terminal edits while the
/// tab is on screen, per the FolderChanges rule for `~/.swe` views. A
/// custom path outside `~/.swe` can't be watched, so Reload stays its
/// answer there.
extension View {
    func keysFileWatch(_ model: APIKeysModel) -> some View {
        task {
            let swe = FileManager.default.homeDirectoryForCurrentUser.appending(path: ".swe")
            let file = model.path
            guard file.hasPrefix(swe.path + "/") else { return }
            let relative = String(file.dropFirst(swe.path.count + 1))
            for await _ in FolderChanges.stream(root: swe) { $0 == relative }
            where !model.isLoading {
                await model.load()
            }
        }
    }
}

/// Copies a key marked concealed, so clipboard managers skip it.
func copySecret(_ value: String) {
    let board = NSPasteboard.general
    board.clearContents()
    board.declareTypes([.string, NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType")], owner: nil)
    board.setString(value, forType: .string)
    board.setString("", forType: NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType"))
}
