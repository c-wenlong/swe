import AppKit
import SWECore
import SwiftUI

/// The shared `~/.swe/AGENTS.md`, where swe has linked it, and the
/// instruction files of one project folder.
struct AgentsMdPage: View {
    // Edit, then Project: the Compile tab joins between them later.
    enum Scope: Hashable { case edit, compile, project }

    @Environment(AppModel.self) private var app
    @State private var scope: Scope = .edit

    var body: some View {
        let model = app.agentsMd
        // The editor fills the page and scrolls itself, so the page doesn't.
        Page(title: "AGENTS.md", subtitle: "The instructions every harness reads, and each project's own.",
             scrolls: false) {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(swe: app.swe) }
                Task { await model.checkDisk() }
                Task { await app.chapters.load(swe: app.swe) }
                Task { await app.chapters.checkDisk() }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Check links and reread the file (⌘R)")
        } content: {
            HStack {
                Tabs(options: [(Scope.edit, "Edit"), (Scope.project, "Project")],
                     selection: $scope, style: .underline)
                Spacer(minLength: 8)
                if scope == .edit && app.chapters.chapterMode { ViewCompiledButton() }
            }
            switch scope {
            case .edit: GlobalInstructions()
            case .compile, .project: ProjectInstructions()
            }
        }
        .task(id: app.swePath) {
            async let links: () = model.load(swe: app.swe)
            async let chaptered: () = app.chapters.load(swe: app.swe)
            _ = await (links, chaptered)
        }
        // A build or chapter edit made in a terminal, while the page is on
        // screen: the Generated strip and the preview follow it.
        .task {
            let swe = FileManager.default.homeDirectoryForCurrentUser.appending(path: ".swe")
            for await _ in FolderChanges.stream(root: swe, matches: ChaptersModel.affectsCheck) {
                await app.chapters.load(swe: app.swe)
                await app.chapters.checkDisk()
            }
        }
        // Agents rewrite these files while they work; pick that up on
        // return. The chapter report refreshes too: an `instructions init`
        // run elsewhere swaps which editor this page should be showing.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await model.checkDisk() }
            Task { await app.chapters.checkDisk() }
            Task { await app.chapters.load(swe: app.swe) }
        }
    }
}

private struct GlobalInstructions: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        let model = app.agentsMd
        let chapters = app.chapters
        switch model.check {
        case .idle, .loading:
            SkeletonRows(rows: 5, shape: .link, label: "Checking links…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let report):
            VStack(alignment: .leading, spacing: 12) {
                LinksStrip(report: report)
                switch chapters.status {
                case .loaded(let check) where check.indexExists:
                    ChaptersEditor(check: check)
                case .idle, .loading:
                    SkeletonRows(rows: 8, shape: .file, label: "Reading chapters…")
                default:
                    // A failed check means an swe without instructions: the
                    // flat file is the only thing there is to edit.
                    FlatInstructions(report: report, document: model.global)
                }
            }
        }
    }
}

/// The flat-file editing path: the shared AGENTS.md itself, plus the offer
/// to split it into chapters once `swe instructions check` says none exist.
private struct FlatInstructions: View {
    @Environment(AppModel.self) private var app
    var report: InitCheck
    var document: InstructionDocument?

    var body: some View {
        let chapters = app.chapters
        // A loaded, non-chapter check is the only state where converting is
        // meaningful; a failed one is an old swe that cannot build anyway.
        if case .loaded(let check) = chapters.status, !check.indexExists {
            Strip {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Chapters").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                        Text("Split AGENTS.md into ordered sections you edit separately; swe rebuilds the file every harness reads.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if chapters.converting { ProgressView().controlSize(.small) }
                    let unsaved = document?.isDirty == true
                    Button(chapters.converting ? "Converting…" : "Convert to chapters", systemImage: "square.split.2x1") {
                        Task { await chapters.convert(swe: app.swe) }
                    }
                    .secondaryButton()
                    // init folds the file on disk; an unsaved draft in the
                    // editor would never reach the chapter it becomes.
                    .disabled(chapters.converting || unsaved)
                    .hoverTip(unsaved ? AgentsModel.saveFirst
                              : "Runs swe instructions init: your AGENTS.md becomes main.md under ~/.swe/AGENTS_MD.",
                              replacesLabel: false)
                }
                if let error = chapters.convertError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
            }
        }
        if let document {
            let linked = report.instructionRows.filter { $0.instructions?.state == .linked }.count
            InstructionEditor(
                document: document,
                title: displayPath(document.url),
                subtitle: linked == 1 ? "Linked into 1 harness" : "Linked into \(linked) harnesses")
        }
    }
}

/// Chapter mode: the indented AGENTS_MD outline beside the selected
/// chapter's editor. The rail only navigates and creates — what compiles
/// into AGENTS.md is the Compile page's job.
private struct ChaptersEditor: View {
    @Environment(AppModel.self) private var app
    var check: InstructionsCheck
    @State private var creating: NewChapterSheet.Initial?

    var body: some View {
        let chapters = app.chapters
        HStack(alignment: .top, spacing: 12) {
            rail
            VStack(alignment: .leading, spacing: 12) {
                if let name = chapters.selected {
                    let chapter = InstructionsCheck.Chapter(
                        name: name, path: check.directory + "/" + name,
                        exists: true)
                    if let document = chapters.document(for: chapter) {
                        InstructionEditor(
                            document: document,
                            title: name,
                            subtitle: subtitle(for: name)) {
                            Task { await chapters.build(swe: app.swe) }
                        }
                        .id(name)
                    } else {
                        Card(title: name, subtitle: "The path leaves AGENTS_MD") {
                            EmptyView()
                        } content: {
                            Text("This file sits outside the chapter folder. Fix or remove it in Finder.")
                                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        }
                    }
                } else {
                    Card(title: "No chapters", subtitle: "AGENTS_MD has no .md files yet") {
                        EmptyView()
                    } content: {
                        Text("Create a chapter to start the shared instruction file.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxHeight: .infinity)
        .quiverSheet(item: $creating) { initial in
            NewChapterSheet(initial: initial)
        }
    }

    /// "Chapter N of M in AGENTS.md", or why this file isn't compiled.
    private func subtitle(for name: String) -> String {
        let active = check.chapters.map(\.name)
        if let index = active.firstIndex(of: name) {
            return "Chapter \(index + 1) of \(active.count) in AGENTS.md"
        }
        return "Not in AGENTS.md"
    }

    /// The AGENTS_MD outline: folders first, files after, each level
    /// alphabetical. Select a file to edit it.
    private var rail: some View {
        let chapters = app.chapters
        return Strip {
            HStack(spacing: 6) {
                Text("Chapters").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Text("\(fileCount(chapters.tree))").font(Theme.caption).foregroundStyle(Theme.textMuted)
                Spacer(minLength: 4)
                Menu {
                    Button("New chapter…") {
                        creating = NewChapterSheet.Initial(mode: .chapter, folder: "")
                    }
                    Button("New folder…") {
                        creating = NewChapterSheet.Initial(mode: .folder, folder: "")
                    }
                } label: {
                    Image(systemName: "plus").font(Theme.font(size: 12))
                        .foregroundStyle(Theme.textMuted)
                        .frame(width: 22, height: 22).contentShape(.rect)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .frame(width: 22)
                .hoverTip("New chapter or folder", replacesLabel: false)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(chapters.tree) { node in
                        OutlineNode(node: node, depth: 0, compiled: check.compiledNames) { folder in
                            creating = NewChapterSheet.Initial(mode: .chapter, folder: folder)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(width: 260)
        .frame(maxHeight: .infinity)
    }

    private func fileCount(_ nodes: [ChapterNode]) -> Int {
        nodes.reduce(0) { $0 + ($1.isFolder ? fileCount($1.children) : 1) }
    }

    /// One outline row: a disclosure folder or a selectable file.
    private struct OutlineNode: View {
        var node: ChapterNode
        var depth: Int
        var compiled: Set<String>
        var onNewChapter: (String) -> Void

        @Environment(AppModel.self) private var app
        @State private var hovering = false
        @AppStorage private var expanded: Bool

        init(node: ChapterNode, depth: Int, compiled: Set<String>,
             onNewChapter: @escaping (String) -> Void) {
            self.node = node
            self.depth = depth
            self.compiled = compiled
            self.onNewChapter = onNewChapter
            // Expanded by default; each folder's state is its own key.
            _expanded = AppStorage(wrappedValue: true,
                                   "agentsMd.outline.\(node.path)")
        }

        var body: some View {
            VStack(alignment: .leading, spacing: 2) {
                if node.isFolder { folderRow } else { fileRow }
                if node.isFolder && expanded {
                    ForEach(node.children) { child in
                        OutlineNode(node: child, depth: depth + 1,
                                    compiled: compiled, onNewChapter: onNewChapter)
                    }
                }
            }
        }

        private var folderRow: some View {
            HStack(spacing: 5) {
                Image(systemName: expanded ? "chevron.down" : "chevron.right")
                    .font(Theme.font(size: 9, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
                    .frame(width: 10)
                Image(systemName: "folder")
                    .font(Theme.font(size: 12))
                    .foregroundStyle(Theme.textSecondary)
                Text(node.name).font(Theme.nav).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.tail)
                Text("\(node.fileCount)").font(Theme.caption).foregroundStyle(Theme.textMuted)
                Spacer(minLength: 4)
                if hovering {
                    Button { onNewChapter(node.path) } label: {
                        Image(systemName: "plus")
                            .font(Theme.font(size: 10, weight: .semibold))
                            .foregroundStyle(Theme.textMuted)
                            .frame(width: 18, height: 18).contentShape(.rect)
                    }
                    .plainButton(cornerRadius: Theme.controlRadius)
                    .hoverTip("New chapter here", replacesLabel: false)
                }
            }
            .padding(.leading, CGFloat(depth) * 14 + 8)
            .padding(.trailing, 8).padding(.vertical, 4)
            .background(hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect)
            .onTapGesture { expanded.toggle() }
            .onHover { hovering = $0 }
            .contextMenu {
                Button("New chapter here…") { onNewChapter(node.path) }
                Divider()
                Button("Reveal in Finder") {
                    reveal(node)
                }
            }
        }

        private var fileRow: some View {
            let chapters = app.chapters
            let selected = chapters.selected == node.path
            let isCompiled = compiled.contains(node.path)
            return HStack(spacing: 6) {
                Text(node.name)
                    .font(Theme.nav)
                    .lineLimit(1).truncationMode(.tail)
                    .foregroundStyle(isCompiled
                        ? (selected ? Theme.textPrimary : Theme.textNav)
                        : Theme.textMuted)
                if chapters.isDirty(node.path) {
                    Circle().fill(Theme.warning)
                        .frame(width: Theme.statusDot, height: Theme.statusDot)
                }
                Spacer(minLength: 4)
            }
            .padding(.leading, CGFloat(depth) * 14 + 23)
            .padding(.trailing, 8).padding(.vertical, 5)
            .background(selected ? Theme.navActive : hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect)
            .onTapGesture { chapters.selected = node.path }
            .onHover { hovering = $0 }
            .hoverTip(isCompiled ? node.path
                      : "Not in AGENTS.md. Include it on the Compile page",
                      replacesLabel: false)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
            .accessibilityAction(.default) { chapters.selected = node.path }
            .contextMenu {
                Button("Reveal in Finder") { reveal(node) }
                Button("Copy path") {
                    copyToPasteboard(absolutePath(node))
                }
            }
        }

        private func absolutePath(_ node: ChapterNode) -> String {
            guard case .loaded(let check) = app.chapters.status else { return node.path }
            return check.directory + "/" + node.path
        }

        private func reveal(_ node: ChapterNode) {
            NSWorkspace.shared.selectFile(
                absolutePath(node), inFileViewerRootedAtPath: "")
        }
    }
}

/// The New chapter / New folder popup: both fields always, the file field
/// deciding which thing is made.
private struct NewChapterSheet: View {
    /// How the sheet was opened; a folder path prefills the Folder field.
    struct Initial: Identifiable {
        enum Mode { case chapter, folder }
        var mode: Mode
        var folder: String
        var id: String { "\(mode)-\(folder)" }
    }

    var initial: Initial
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var folder: String
    @State private var file = ""
    @State private var include = true
    @State private var working = false
    @State private var error: String?

    init(initial: Initial) {
        self.initial = initial
        _folder = State(initialValue: initial.folder)
    }

    private var folderProblem: String? {
        ChapterIndex.folderProblem(folder.trimmingCharacters(in: .whitespaces))
    }

    private var fileName: String {
        var name = file.trimmingCharacters(in: .whitespaces)
        if !name.isEmpty && !name.lowercased().hasSuffix(".md") { name += ".md" }
        return name
    }

    private var fileProblem: String? {
        if fileName.isEmpty { return nil }  // folder-only create
        if fileName.contains("/") { return "The file is a name, not a path." }
        return ChapterIndex.nameProblem(
            folder.isEmpty ? fileName : folder + "/" + fileName)
    }

    private var fullPath: String {
        let rel = [folder.trimmingCharacters(in: .whitespaces), fileName]
            .filter { !$0.isEmpty }.joined(separator: "/")
        return rel.isEmpty ? "" : "~/.swe/AGENTS_MD/\(rel)"
    }

    private var canSubmit: Bool {
        folderProblem == nil && fileProblem == nil
            && !(folder.trimmingCharacters(in: .whitespaces).isEmpty && fileName.isEmpty)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(initial.mode == .folder ? "New folder" : "New chapter")
                .font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Text("Folders group the chapters so you and agents can navigate without reading everything.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            FormField("Folder") {
                HStack(spacing: 6) {
                    TextField("engineering/review, or empty for top level", text: $folder)
                        .sweField(monospaced: true)
                    Menu {
                        Button("Top level") { folder = "" }
                        if !app.chapters.folders.filter({ !$0.isEmpty }).isEmpty {
                            Divider()
                            ForEach(app.chapters.folders.filter { !$0.isEmpty }, id: \.self) { existing in
                                Button(existing) { folder = existing }
                            }
                        }
                    } label: {
                        Image(systemName: "chevron.down")
                            .font(Theme.font(size: 10))
                            .foregroundStyle(Theme.textMuted)
                            .frame(width: 22, height: 22).contentShape(.rect)
                    }
                    .menuStyle(.borderlessButton).menuIndicator(.hidden).frame(width: 22)
                }
            }
            if initial.mode == .chapter {
                FormField("File") {
                    TextField("name (.md added)", text: $file)
                        .sweField(monospaced: true)
                }
            }
            // Only a chapter goes in the index; a folder alone does not.
            if !fileName.isEmpty {
                Toggle("Include in AGENTS.md", isOn: $include)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .toggleStyle(.checkbox)
            }
            if let problem = folderProblem ?? fileProblem {
                Text(problem).font(Theme.caption).foregroundStyle(Theme.dangerText)
            } else if !fullPath.isEmpty {
                Text(fullPath).font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.middle)
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .secondaryButton().keyboardShortcut(.cancelAction)
                Button(working ? "Creating…" : submitTitle) { submit() }
                    .primaryButton().keyboardShortcut(.defaultAction)
                    .disabled(working || !canSubmit)
            }
        }
        .sheetScaffold(width: 480)
    }

    private var submitTitle: String {
        fileName.isEmpty ? "Create folder" : "Create chapter"
    }

    private func submit() {
        working = true
        error = nil
        let folder = self.folder.trimmingCharacters(in: .whitespaces)
        let file = fileName
        Task {
            let problem: String?
            if file.isEmpty {
                problem = await app.chapters.createFolder(folder, swe: app.swe)
            } else {
                problem = await app.chapters.createChapter(
                    folder: folder, file: file, include: include, swe: app.swe)
            }
            working = false
            if let problem { error = problem } else { dismiss() }
        }
    }
}

/// The Edit tab's "View AGENTS.md" button: the compiled output's state
/// as a dot, opening the centre popup.
private struct ViewCompiledButton: View {
    @Environment(AppModel.self) private var app
    @State private var showing = false

    var body: some View {
        let chapters = app.chapters
        Button {
            showing = true
        } label: {
            HStack(spacing: 6) {
                Circle().fill(stateColor)
                    .frame(width: Theme.statusDot, height: Theme.statusDot)
                Text("View AGENTS.md").font(Theme.bodySmall)
            }
        }
        .secondaryButton()
        .hoverTip(stateText, replacesLabel: false)
        .quiverSheet(isPresented: $showing) {
            CompiledOutputSheet()
        }
    }

    private var stateColor: Color {
        guard case .loaded(let check) = app.chapters.status else { return Theme.textMuted }
        if check.error != nil { return Theme.danger }
        if check.stale == true || !check.outputExists { return Theme.warning }
        return Theme.success
    }

    private var stateText: String {
        guard case .loaded(let check) = app.chapters.status else { return "Checking…" }
        if let error = check.error { return "Source problem: \(error)" }
        if check.stale == true { return "Stale: the sources changed since the last build" }
        if !check.outputExists { return "Not built yet" }
        return "Current"
    }
}

/// The compiled AGENTS.md as a centre popup: Rich by default, a Raw
/// fallback, one section per source chapter that jumps back to the editor.
private struct CompiledOutputSheet: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    @State private var mode = "rich"

    var body: some View {
        let chapters = app.chapters
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("AGENTS.md").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    if case .loaded(let check) = chapters.status {
                        Text(displayPath(check.output))
                            .font(Theme.font(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.textMuted)
                    }
                }
                stateLabel
                Spacer(minLength: 8)
                Tabs(options: [("rich", "Rich"), ("raw", "Raw")], selection: $mode,
                     style: .segmented)
                if chapters.building { ProgressView().controlSize(.small) }
                Button(chapters.building ? "Building…" : "Rebuild", systemImage: "arrow.clockwise") {
                    Task { await chapters.build(swe: app.swe) }
                }
                .secondaryButton().disabled(chapters.building)
                .hoverTip("Runs swe instructions build", replacesLabel: false)
                Button("Copy", systemImage: "doc.on.doc") {
                    if let text = chapters.output?.text { copyToPasteboard(text) }
                }
                .secondaryButton()
                Button("Close") { dismiss() }
                    .secondaryButton().keyboardShortcut(.cancelAction)
            }
            if case .loaded(let check) = chapters.status, let error = check.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let error = chapters.buildError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let note = chapters.buildNote {
                Text(note).font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            content
        }
        .padding(20)
        .frame(width: 760, height: 620)
        .sheetScaffold(width: 760, padding: 0)
    }

    @ViewBuilder private var content: some View {
        if let document = app.chapters.output {
            loadedContent(document)
        } else {
            Text("AGENTS.md hasn't been built yet.")
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
        }
    }

    @ViewBuilder private func loadedContent(
        _ document: InstructionDocument
    ) -> some View {
        switch document.state {
        case .loading:
            SkeletonLines(rows: 12, framed: false)
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle")
                .foregroundStyle(Theme.dangerText)
        case .ready:
            if mode == "raw" {
                ScrollView {
                    Text(document.text)
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.textPrimary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(4)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        ForEach(sections(of: document.text), id: \.name) { section in
                            VStack(alignment: .leading, spacing: 6) {
                                if let name = section.name {
                                    Button {
                                        app.chapters.selected = name
                                        dismiss()
                                    } label: {
                                        Text(name)
                                            .font(Theme.caption)
                                            .foregroundStyle(Theme.link)
                                    }
                                    .plainButton()
                                    .hoverTip("Open this chapter in the editor",
                                              replacesLabel: false)
                                }
                                MarkdownBody(body: section.body)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(4)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    private var stateLabel: some View {
        Group {
            if case .loaded(let check) = app.chapters.status {
                if check.error != nil {
                    Label("Source problem", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(Theme.dangerText)
                } else if check.stale == true || !check.outputExists {
                    Label(check.outputExists ? "Stale" : "Not built",
                          systemImage: "exclamationmark.circle")
                        .foregroundStyle(Theme.warningText)
                } else {
                    Label("Current", systemImage: "checkmark.circle")
                        .foregroundStyle(Theme.success)
                }
            }
        }
        .font(Theme.caption)
    }

    /// The generated text split on its `<!-- Source: AGENTS_MD/<name> -->`
    /// markers; the compiler's own comment lines are stripped. `name` is
    /// nil only for text before the first marker.
    private func sections(of text: String) -> [(name: String?, body: String)] {
        var out: [(String?, String)] = []
        var name: String?
        var body: [String] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(line)
            if line.hasPrefix("<!-- Source: AGENTS_MD/"), line.hasSuffix("-->") {
                out.append((name, body.joined(separator: "\n")))
                name = String(line.dropFirst("<!-- Source: AGENTS_MD/".count).dropLast(3))
                body = []
            } else if line.hasPrefix("<!-- Generated by swe") || line.hasPrefix("<!-- Target:") {
                continue  // the compiler's own lines, not chapter content
            } else {
                body.append(line)
            }
        }
        out.append((name, body.joined(separator: "\n")))
        return out.map { ($0.0, $0.1.trimmingCharacters(in: .whitespacesAndNewlines)) }
            .filter { !$0.1.isEmpty || $0.0 != nil }
    }
}

/// A compact bordered band above the editor: a label, then what it's about.
private struct Strip<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) { content }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: .rect(cornerRadius: Theme.blockRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

/// Where each harness's instruction file points, from `swe init --check`,
/// as one strip of dots and names so the editor keeps the full width.
private struct LinksStrip: View {
    @Environment(AppModel.self) private var app
    var report: InitCheck
    @State private var confirmingReplace = false

    var body: some View {
        let model = app.agentsMd
        Strip {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("Harnesses").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    .hoverTip("From swe init --check", replacesLabel: false)
                FlowLayout(spacing: 14, lineSpacing: 6) {
                    ForEach(report.instructionRows) { row in
                        if let link = row.instructions {
                            LinkRow(name: row.name, link: link)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if report.instructionsNeedInit || model.linking {
                    HStack(spacing: 8) {
                        if model.linking { ProgressView().controlSize(.small) }
                        Button(model.linking ? "Linking…" : "Link with swe init", systemImage: "link") {
                            Task { await model.link(swe: app.swe) }
                        }
                        .primaryButton()
                        .disabled(model.linking)
                        .hoverTip("Runs swe init, which links what is missing and never replaces a real file.", replacesLabel: false)
                    }
                    .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
                }
            }
            if report.instructionRows.contains(where: { $0.instructions?.state.isBlocked == true }) {
                HStack(spacing: 12) {
                    Text("A real file is in the way for some harnesses. swe init leaves those alone.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Replace and back up…") { confirmingReplace = true }
                        .secondaryButton()
                        .disabled(model.linking || model.projectHasUnsavedChanges)
                        .hoverTip(model.projectHasUnsavedChanges ? AgentsModel.saveFirst
                                  : "Runs swe init --force, which copies each real file in the way to ~/.swe/backups before linking over it.",
                                  replacesLabel: false)
                }
            }
            if let error = model.linkError {
                // swe init can fail with pages of output; bounded, so the
                // editor below keeps its room on this non-scrolling page.
                ScrollView {
                    Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 64)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .confirmationDialog("Replace the real files with links?", isPresented: $confirmingReplace) {
            Button("Replace and back up", role: .destructive) {
                Task { await model.link(swe: app.swe, force: true) }
            }
        } message: {
            Text("This runs swe init --force. Each real file or folder in the way is copied to ~/.swe/backups, then replaced with a link to the shared copy.")
        }
    }
}

private struct LinkRow: View {
    var name: String
    var link: InitCheck.Link

    var color: Color {
        if link.state == .linked { return Theme.success }
        if link.state.isBlocked { return Theme.danger }
        if link.state.initFixes { return Theme.warning }
        return Theme.textMuted
    }

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: Theme.statusDot, height: Theme.statusDot)
            HarnessMarkView(harness: name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            // Only what needs attention gets words; a dot says linked.
            if link.state != .linked {
                Text(link.state.title.lowercased()).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .hoverTip("\(HarnessMark(name).name), \(link.state.title.lowercased()): \(displayPath(URL(fileURLWithPath: link.path)))")
        .accessibilityElement(children: .combine)
        // The dot alone says linked, so VoiceOver gets the state in words.
        .accessibilityValue(link.state.title)
    }
}

private struct ProjectInstructions: View {
    static let folderKey = "agentsProjectFolder"

    @Environment(AppModel.self) private var app
    @State private var selectedFile = AgentsModel.projectFileNames[0]
    @State private var pendingFolder: URL?
    /// The selected file's document, opened in a task and never in body:
    /// document(for:) follows symlinks and reads the file on first use.
    @State private var opened: (file: AgentsModel.ProjectFile, document: InstructionDocument)?
    /// Bumped when a link may have moved under an unchanged file entry
    /// (the app comes back to the front, a link run ends), so the task
    /// asks the model again and follows the link to its new target.
    @State private var reopen = 0

    var body: some View {
        let model = app.agentsMd
        VStack(alignment: .leading, spacing: 12) {
            Strip {
                HStack(spacing: 12) {
                    Image(systemName: "folder").foregroundStyle(Theme.textSecondary)
                    if let folder = model.projectFolder {
                        Text(displayPath(folder)).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                            .lineLimit(1).truncationMode(.middle)
                    } else {
                        Text("Choose a project folder to edit its AGENTS.md and CLAUDE.md.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    }
                    Spacer(minLength: 8)
                    Button("Choose folder…") { choose() }
                        .secondaryButton()
                }
            }
            if model.projectFolder != nil {
                let files = model.projectFiles
                Tabs(options: files.map { ($0.name, tabTitle($0)) }, selection: $selectedFile, style: .table, onPanel: true)
                if let file = files.first(where: { $0.name == selectedFile }),
                   let opened, opened.file == file {
                    InstructionEditor(document: opened.document, title: file.name,
                                      subtitle: subtitle(file, opened.document)) {
                        model.rescanProject()
                    }
                    .id(file.name)
                }
            }
        }
        // A new folder, tab or rescan of the file opens its document; the
        // model keeps each one, so a return to a tab keeps its edits.
        .task(id: OpenKey(file: model.projectFolder == nil ? nil : model.projectFiles.first { $0.name == selectedFile },
                          reopen: reopen)) {
            guard model.projectFolder != nil,
                  let file = model.projectFiles.first(where: { $0.name == selectedFile }) else {
                opened = nil
                return
            }
            opened = (file, model.document(for: file))
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            reopen += 1
        }
        .onChange(of: model.linking) { _, linking in if !linking { reopen += 1 } }
        .task { await restore() }
        .confirmationDialog("Discard unsaved changes?", isPresented: .init(
            get: { pendingFolder != nil }, set: { if !$0 { pendingFolder = nil } })) {
            Button("Discard and switch", role: .destructive) {
                if let folder = pendingFolder { open(folder) }
                pendingFolder = nil
            }
        } message: {
            Text("The open project has edits that aren't saved.")
        }
    }

    private struct OpenKey: Equatable {
        var file: AgentsModel.ProjectFile?
        var reopen: Int
    }

    private func tabTitle(_ file: AgentsModel.ProjectFile) -> String {
        if let target = file.linksTo { return "\(file.name) → \(target)" }
        return file.exists ? file.name : "\(file.name) (new)"
    }

    private func subtitle(_ file: AgentsModel.ProjectFile, _ document: InstructionDocument) -> String {
        if let target = file.linksTo { return "Links to \(target); edits land there." }
        if !document.existsOnDisk { return "Doesn't exist yet. Saving creates it." }
        return "In the folder above"
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        panel.directoryURL = app.agentsMd.projectFolder
        guard panel.runModal() == .OK, let url = panel.url else { return }
        if app.agentsMd.projectHasUnsavedChanges {
            pendingFolder = url
        } else {
            open(url)
        }
    }

    private func open(_ folder: URL) {
        app.agentsMd.openProject(folder)
        UserDefaults.standard.set(folder.path, forKey: Self.folderKey)
    }

    /// Reopens the last folder. The check runs off the main actor: the
    /// folder may sit on a stale mount or a file provider.
    private func restore() async {
        guard app.agentsMd.projectFolder == nil,
              let path = UserDefaults.standard.string(forKey: Self.folderKey) else { return }
        let isFolder = await offMain {
            var isDirectory: ObjCBool = false
            return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory) && isDirectory.boolValue
        }
        if isFolder, app.agentsMd.projectFolder == nil {
            app.agentsMd.openProject(URL(fileURLWithPath: path))
        }
    }
}

/// A plain-text editor for one instruction file: save, revert, an unsaved
/// dot, and a stop when the file changed on disk since it was opened.
/// Raw and Rich edit the same text, so nothing is lost switching.
struct InstructionEditor: View {
    private static let modeKey = "agentsMdView"

    @Bindable var document: InstructionDocument
    var title: String
    var subtitle: String
    /// The subtitle is a path: draw it in mono.
    var monoSubtitle = false
    /// View-only: no mode tabs or save controls, just the text.
    var readOnly = false
    /// Code, not prose: always the raw editor, with no Rich tab. A hook
    /// script read as Markdown turns its docstring into paragraphs.
    var rawOnly = false
    /// A Close button in the header, for the editor inside a sheet.
    var close: (() -> Void)? = nil
    var onSaved: () -> Void = {}
    @State private var error: String?
    @FocusState private var editing: Bool
    @State private var mode = UserDefaults.standard.string(forKey: Self.modeKey) ?? "raw"
    @State private var richFocused = false
    @State private var richFocusToken = 0

    var body: some View {
        Card(title: title, subtitle: subtitle, monoSubtitle: monoSubtitle) {
            HStack(spacing: 8) {
                if !readOnly && !rawOnly {
                    Tabs(options: [("raw", "Raw"), ("rich", "Rich")], selection: $mode, style: .segmented)
                }
                if document.isDirty {
                    HStack(spacing: 5) {
                        Circle().fill(Theme.warning).frame(width: Theme.statusDot, height: Theme.statusDot)
                        Text("Unsaved").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    .accessibilityElement(children: .combine)
                }
                if !readOnly {
                    Button("Revert") { Task { await document.revert(); error = nil } }
                        .secondaryButton()
                        .disabled(!document.isDirty)
                    Button("Save") { save(overwrite: false) }
                        .primaryButton()
                        .keyboardShortcut("s", modifiers: .command)
                        .disabled(!document.isDirty || document.changedOnDisk)
                }
                if let close {
                    Button("Close") { close() }
                        .secondaryButton()
                        .keyboardShortcut(.cancelAction)
                }
            }
        } content: {
            switch document.state {
            case .loading:
                SkeletonLines(rows: 12, framed: false)
                    .padding(Theme.cardPadding)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            case .failed(let message):
                Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.dangerText)
            case .ready:
                editorContent
            }
        }
    }

    /// The ready state: the changed-on-disk banner, a save error, and the
    /// raw or rich editor filling what the card has left.
    @ViewBuilder private var editorContent: some View {
        if document.changedOnDisk {
            ChangedOnDiskBanner(
                reload: { Task { await document.load(); error = nil } },
                overwrite: { save(overwrite: true) })
        }
        if let error {
            Label(error, systemImage: "exclamationmark.triangle")
                .font(Theme.caption).foregroundStyle(Theme.dangerText)
        }
        // Fills the card, and the card fills what the page has left.
        let raw = rawOnly || mode == "raw"
        let focused = raw ? editing : richFocused
        Group {
            if readOnly {
                TextEditor(text: .constant(document.text))
                    .font(Theme.font(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
            } else if raw {
                TextEditor(text: $document.text)
                    .font(Theme.font(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .focused($editing)
            } else {
                MarkdownTextView(text: $document.text, isFocused: $richFocused,
                                 focusToken: richFocusToken, accessibilityLabel: title)
            }
        }
        .disabled(readOnly)
        .focusEffectDisabled()
        .padding(10)
        .background(raw ? Theme.code : Theme.field,
                    in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
            .strokeBorder(focused ? Theme.borderHover : Theme.border, lineWidth: 1))
        .focusRing(focused, cornerRadius: Theme.controlRadius, afterPointer: true)
        .frame(minHeight: 160, maxHeight: .infinity)
        .accessibilityLabel(title)
        .onChange(of: mode) {
            UserDefaults.standard.set(mode, forKey: Self.modeKey)
            if mode == "raw" { editing = true } else { richFocusToken += 1 }
        }
    }

    private func save(overwrite: Bool) {
        Task {
            switch await document.save(overwrite: overwrite) {
            case .saved:
                error = nil
                onSaved()
            case .changedOnDisk:
                error = nil
            case .failed(let message):
                error = message
            }
        }
    }
}

private struct ChangedOnDiskBanner: View {
    var reload: () -> Void
    var overwrite: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.warningText)
            Text("This file changed on disk since you opened it. Reload to see that version, or overwrite it with yours.")
                .font(Theme.caption).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            Button("Reload", action: reload).secondaryButton()
            Button("Overwrite", action: overwrite).secondaryButton()
        }
        .padding(12)
        .background(Theme.warning.opacity(0.08), in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.warning.opacity(0.4), lineWidth: 1))
    }
}
