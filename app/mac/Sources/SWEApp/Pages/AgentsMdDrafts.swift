import SwiftUI
import SWECore

/// The Edit and Compile tabs' "Drafts (N)" menu: every open chapter with
/// unsaved text, each a jump back to its editor. Shown only while there
/// is at least one; the tree's dots and the editor's Unsaved label stay.
struct DraftsMenu: View {
    @Environment(AppModel.self) private var app
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// A draft was picked: the page switches to the Edit tab.
    var onOpen: () -> Void

    var body: some View {
        let chapters = app.chapters
        // Reading `selected` re-evaluates the list as chapters open.
        let _ = chapters.selected
        let names = chapters.draftNames
        Group {
            if !names.isEmpty {
                Menu {
                    ForEach(names, id: \.self) { name in
                        Button {
                            chapters.selected = name
                            onOpen()
                        } label: {
                            Text(name == chapters.selected ? "\(name) (open)" : name)
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Circle().fill(Theme.warning)
                            .frame(width: Theme.statusDot, height: Theme.statusDot)
                        Text(ChapterDrafts.title(count: names.count)).font(Theme.bodySmall)
                    }
                }
                .menuStyle(.button)
                .secondaryButton()
                .fixedSize()
                .help("Chapters with unsaved changes")
                .accessibilityLabel(ChapterDrafts.title(count: names.count))
                .transition(.opacity)
            }
        }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: names)
    }
}

/// The size of a generated output as last built, in KiB. Read from the
/// bytes the preview loaded, never from the chapter sources: their total
/// leaves out the headings the build adds.
struct CompiledSizeLabel: View {
    @Environment(AppModel.self) private var app
    /// The generated file; nil is the check's shared AGENTS.md.
    var path: String?
    /// "12.3 KiB, last built" rather than the bare size.
    var detailed = false

    private var document: InstructionDocument? {
        guard let path, case .loaded(let check) = app.chapters.status,
              path != check.output else { return app.chapters.output }
        return app.chapters.outputDocument(for: path)
    }

    var body: some View {
        if let bytes = document?.diskByteCount {
            Text(detailed
                 ? "\(CompiledSize.label(bytes: bytes)) as last built"
                 : CompiledSize.label(bytes: bytes))
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .lineLimit(1)
                .help("Size of the generated file as last built, not of unsaved edits")
        }
    }
}
