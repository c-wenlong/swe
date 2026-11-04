import Foundation
import Observation

/// Text edits on `~/.swe/AGENTS_MD/index.txt`, kept pure so the model
/// applies them through an InstructionDocument and its conflict checks.
///
/// The file is an ordered list: one relative .md path per line, blank
/// lines and `#` comments ignored. Every edit keeps the comment and blank
/// lines where they were and moves only the chapter entries.
public enum ChapterIndex {
    static func lines(_ text: String) -> [String] {
        text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
    }

    /// A chapter entry line: not blank, not a comment.
    static func isEntry(_ line: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        return !trimmed.isEmpty && !trimmed.hasPrefix("#")
    }

    /// The chapter names in index order.
    public static func entries(_ text: String) -> [String] {
        lines(text).filter(isEntry).map { $0.trimmingCharacters(in: .whitespaces) }
    }

    /// `text` with `name` moved to just before `before` — to the end when
    /// `before` is nil, which means before a final newline's empty line.
    public static func move(_ text: String, name: String, before: String?) -> String {
        var lines = lines(text)
        guard let from = lines.firstIndex(where: {
            isEntry($0) && $0.trimmingCharacters(in: .whitespaces) == name
        }) else { return text }
        let entry = lines.remove(at: from)
        let to: Int
        if let before,
           let index = lines.firstIndex(where: {
               isEntry($0) && $0.trimmingCharacters(in: .whitespaces) == before
           }) {
            to = index
        } else {
            // A trailing "" is the file's last newline; land before it.
            to = lines.last == "" ? lines.count - 1 : lines.endIndex
        }
        lines.insert(entry, at: to)
        return lines.joined(separator: "\n")
    }

    /// `text` without `name`'s line. The file it points at is not touched:
    /// an unlisted chapter is simply not compiled.
    public static func removing(_ text: String, name: String) -> String {
        var lines = lines(text)
        guard let index = lines.firstIndex(where: {
            isEntry($0) && $0.trimmingCharacters(in: .whitespaces) == name
        }) else { return text }
        lines.remove(at: index)
        return lines.joined(separator: "\n")
    }

    /// `text` with `name` appended, adding the newline a file without one
    /// needs first.
    public static func appending(_ text: String, name: String) -> String {
        var result = text
        if !result.hasSuffix("\n") { result += "\n" }
        return result + name + "\n"
    }

    /// `text` with `name` inserted after the last active entry that lives
    /// in `afterFolder` — the chapter's folder, so a new file groups with
    /// its siblings. No active entry in the folder means the end, like
    /// `appending`. Comment and blank lines never move: the insert point
    /// is after the matching entry's line, wherever comments sit around it.
    public static func inserting(_ text: String, name: String, afterFolder folder: String) -> String {
        var lines = lines(text)
        var at: Int?
        if !folder.isEmpty {
            for (index, line) in lines.enumerated() {
                guard isEntry(line) else { continue }
                let entry = line.trimmingCharacters(in: .whitespaces)
                if (entry as NSString).deletingLastPathComponent == folder {
                    at = index + 1
                }
            }
        }
        if let at {
            lines.insert(name, at: at)
            return lines.joined(separator: "\n")
        }
        return appending(text, name: name)
    }

    /// Why `folder` cannot be a chapter folder, mirroring the compiler's
    /// path rules without the .md suffix: relative, inside AGENTS_MD, one
    /// line, and no characters index.txt or the compiler would read as
    /// markup. nil means it can. `""` is the top level and also fine.
    public static func folderProblem(_ folder: String) -> String? {
        if folder.isEmpty { return nil }
        if folder.contains("\n") || folder.contains("\r") || folder.contains("\0") {
            return "A folder name is one line."
        }
        if folder.hasPrefix("/") { return "The folder must be relative, inside AGENTS_MD." }
        if folder.hasPrefix("#") { return "A # name reads as a comment in index.txt." }
        if folder.hasPrefix(".") { return "A leading . hides it from the outline." }
        if folder.contains("-->") { return "The folder cannot contain -->." }
        if folder.split(separator: "/").contains("..") {
            return "The path cannot leave AGENTS_MD."
        }
        if folder.split(separator: "/").contains(where: { $0.hasPrefix(".") }) {
            return "A leading . hides it from the outline."
        }
        return nil
    }

    /// Why `name` cannot be a chapter, mirroring the compiler's rules; nil
    /// means it can. Kept a client-side pre-check — the build still decides.
    public static func nameProblem(_ name: String) -> String? {
        if name.isEmpty { return "Name the chapter." }
        if name.contains("\n") || name.contains("\r") || name.contains("\0") {
            return "A chapter name is one line."
        }
        if name.hasPrefix("/") { return "The path must be relative, inside AGENTS_MD." }
        if name.hasPrefix("#") { return "A # name reads as a comment in index.txt." }
        if name.contains("-->") { return "The name cannot contain -->." }
        if !name.lowercased().hasSuffix(".md") { return "Chapters end in .md." }
        if name.split(separator: "/").contains("..") {
            return "The path cannot leave AGENTS_MD."
        }
        // ".md" alone has no stem; the compiler's .md suffix check rejects it.
        if (name as NSString).lastPathComponent.dropLast(3).isEmpty {
            return "The name needs letters before .md."
        }
        return nil
    }
}

/// The compiled AGENTS.md split into its chapter sections for the Rich
/// preview. `name` is nil only for text before the first Source marker;
/// the compiler's own comment lines (Generated, Target) are stripped.
public struct CompiledSection: Equatable, Sendable {
    public let name: String?
    public let body: String
    public init(name: String?, body: String) {
        self.name = name
        self.body = body
    }
}

public enum CompiledSections {
    public static func split(_ text: String) -> [CompiledSection] {
        var out: [CompiledSection] = []
        var name: String?
        var body: [String] = []
        func flush() {
            out.append(CompiledSection(
                name: name,
                body: body.joined(separator: "\n")
                    .trimmingCharacters(in: .whitespacesAndNewlines)))
            body = []
        }
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(line)
            if line.hasPrefix("<!-- Source: AGENTS_MD/"), line.hasSuffix("-->") {
                flush()
                // "x.md -->" -> "x.md": drop the marker's tail and trim.
                name = String(line.dropFirst("<!-- Source: AGENTS_MD/".count)
                    .dropLast(3))
                    .trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("<!-- Generated by swe")
                    || line.hasPrefix("<!-- Target:") {
                continue
            } else {
                body.append(line)
            }
        }
        flush()
        return out.filter { !$0.body.isEmpty || $0.name != nil }
    }
}

/// One row of the rail's outline: a folder or a `.md` file under
/// `~/.swe/AGENTS_MD`. Folders hold children; `name` is the display name
/// and `path` the AGENTS_MD-relative path ("" is never a node).
public struct ChapterNode: Identifiable, Equatable, Sendable {
    public let name: String
    public let path: String
    public let isFolder: Bool
    /// A folder's entries, folders first then files, each case-insensitively
    /// alphabetical. A file's is empty.
    public let children: [ChapterNode]
    /// .md files anywhere under a folder, for the muted count it shows.
    public let fileCount: Int

    public var id: String { path }

    public init(name: String, path: String, isFolder: Bool,
                children: [ChapterNode] = [], fileCount: Int = 0) {
        self.name = name
        self.path = path
        self.isFolder = isFolder
        self.children = children
        self.fileCount = fileCount
    }
}

/// The outline the rail draws: every folder and `.md` file under
/// AGENTS_MD, built from relative paths so it stays testable.
public enum ChapterTree {
    /// `files` and `folders` are AGENTS_MD-relative paths ("" not allowed;
    /// a folder is implied by any deeper path anyway). Result is folders
    /// first, each level case-insensitively alphabetical.
    public static func build(files: [String], folders: [String]) -> [ChapterNode] {
        var fileSet = Set(files)
        var folderSet = Set(folders)
        for file in files {
            var parent = (file as NSString).deletingLastPathComponent
            while !parent.isEmpty && parent != "." {
                folderSet.insert(parent)
                parent = (parent as NSString).deletingLastPathComponent
            }
        }
        func children(of prefix: String) -> [ChapterNode] {
            var out: [ChapterNode] = []
            for folder in folderSet where (folder as NSString).deletingLastPathComponent == prefix {
                let kids = children(of: folder)
                let count = fileSet.filter { $0.hasPrefix(folder + "/") }.count
                out.append(ChapterNode(
                    name: (folder as NSString).lastPathComponent,
                    path: folder, isFolder: true, children: kids, fileCount: count))
            }
            for file in fileSet where (file as NSString).deletingLastPathComponent == prefix {
                out.append(ChapterNode(
                    name: (file as NSString).lastPathComponent,
                    path: file, isFolder: false))
            }
            out.sort {
                ($0.isFolder && !$1.isFolder)
                    || ($0.isFolder == $1.isFolder
                        && $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending)
            }
            return out
        }
        return children(of: "")
    }
}

/// The chapter-based way of editing the shared AGENTS.md: an ordered list
/// of `~/.swe/AGENTS_MD/*.md` files compiled by `swe instructions build`.
///
/// Edits land in the chapter files through InstructionDocument; order,
/// add and remove edits land in index.txt the same way, so a watcher or
/// hand edit racing them gets the changed-on-disk stop, not a silent loss.
/// Every successful index or chapter save rebuilds through swe, so the
/// output the harnesses read never goes stale from an edit made here.
@MainActor
@Observable
public final class ChaptersModel {
    public private(set) var status: Loadable<InstructionsCheck> = .idle
    public private(set) var building = false
    /// What the last build did, or why it failed; one of the two is set.
    public private(set) var buildNote: String?
    public private(set) var buildError: String?
    public private(set) var converting = false
    public private(set) var convertError: String?
    /// The selected chapter's index.txt name.
    public var selected: String?
    /// The AGENTS_MD outline the rail draws, refreshed with `load`.
    public private(set) var tree: [ChapterNode] = []
    /// Dirty drafts whose files are gone: the rail shows a "deleted" row
    /// for each so its unsaved text stays reachable.
    public private(set) var ghosts: [String] = []
    /// Chapters index.txt lists whose files are not on disk, in index
    /// order: the rail shows a "missing" row for each.
    public private(set) var missing: [String] = []
    /// Every folder path in `tree`, top level first as "" — for the New
    /// chapter sheet's folder menu.
    public private(set) var folders: [String] = []
    /// A read-only view of the generated output, loaded for the preview.
    public private(set) var output: InstructionDocument?

    /// index.txt as a document: programmatic edits go through its save so
    /// they keep the read-compare-write conflict check.
    @ObservationIgnored private var indexDocument: InstructionDocument?
    /// Open chapter editors by index name, kept for the page's lifetime.
    @ObservationIgnored private var documents: [String: InstructionDocument] = [:]
    /// A build request that arrived while one ran, collapsed into one rerun.
    @ObservationIgnored private var buildAgain = false
    @ObservationIgnored private var generation = 0

    public init() {}

    /// Whether the shared file is chapter-managed, once `status` knows.
    public var chapterMode: Bool {
        if case .loaded(let check) = status { return check.indexExists }
        return false
    }

    /// Drafts the quit check asks about: any chapter holding unsaved text.
    public var hasUnsavedChanges: Bool {
        documents.values.contains { $0.isDirty } || (indexDocument?.isDirty ?? false)
    }

    public func load(swe: SweClient) async {
        generation += 1
        let mine = generation
        if case .loaded = status {} else { status = .loading }
        let next: Loadable<InstructionsCheck>
        do {
            next = .loaded(try await swe.instructionsCheck())
        } catch is CancellationError {
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        guard mine == generation else { return }
        status = next
        guard case .loaded(let check) = next, check.indexExists else { return }

        if indexDocument?.url.path != check.index {
            let document = InstructionDocument(url: URL(fileURLWithPath: check.index))
            indexDocument = document
            Task { await document.load() }
        }
        if output?.url.path != check.output {
            let document = InstructionDocument(url: URL(fileURLWithPath: check.output))
            output = document
            Task { await document.load() }
        }
        await refreshTree(under: check.directory)
        // The walk above is awaited: a newer load may have finished while
        // it ran. If so, this result is stale and must not move selection.
        guard mine == generation else { return }
        // Selection and editors follow the tree, not the index: a file the
        // user opened that is not listed stays selected, and its editor
        // survives. A dirty doc is never dropped either way.
        let files = treeFiles()
        documents = documents.filter { files.contains($0.key) || $0.value.isDirty }
        // A dirty draft whose file vanished stays reachable: the rail
        // draws a ghost row for it so Save/Revert keep working.
        ghosts = documents.filter { $0.value.isDirty && !files.contains($0.key) }
            .map(\.key).sorted()
        // An indexed chapter with no file keeps a row too, so it can be
        // opened and saved back into existence rather than fixed by hand.
        missing = check.chapters.map(\.name).filter {
            !files.contains($0) && !ghosts.contains($0)
                && Self.inside($0, under: check.directory) != nil
        }
        if selected.map({ !files.contains($0) && !ghosts.contains($0) && !missing.contains($0) }) ?? true {
            selected = check.chapters.first?.name
                ?? treeFilesInOrder.first
                ?? ghosts.first
        }
    }

    /// Every file path in `tree`, in outline order.
    private var treeFilesInOrder: [String] {
        func collect(_ nodes: [ChapterNode]) -> [String] {
            nodes.flatMap { $0.isFolder ? collect($0.children) : [$0.path] }
        }
        return collect(tree)
    }

    private func treeFiles() -> Set<String> { Set(treeFilesInOrder) }

    /// The whole AGENTS_MD folder as an outline: every folder and `.md`
    /// file, hidden entries, index.txt, targets.json and non-chapter files
    /// skipped, and nothing followed outside the folder. Runs off the
    /// main actor; a folder that vanished between load and walk just
    /// empties the rail.
    private func refreshTree(under directory: String) async {
        let root = directory
        let found: (files: [String], folders: [String]) = await offMain {
            var files: [String] = []
            var folders: [String] = []
            var stack: [(url: URL, rel: String)] = [(URL(fileURLWithPath: root), "")]
            while let (url, rel) = stack.popLast() {
                guard let entries = try? FileManager.default.contentsOfDirectory(
                    at: url, includingPropertiesForKeys: nil,
                    options: [.skipsHiddenFiles]) else { continue }
                for entry in entries {
                    let name = entry.lastPathComponent
                    let path = rel.isEmpty ? name : rel + "/" + name
                    var isDirectory: ObjCBool = false
                    guard FileManager.default.fileExists(
                        atPath: entry.path, isDirectory: &isDirectory) else { continue }
                    if isDirectory.boolValue {
                        // Directory links are never followed: real folders
                        // can't loop, and a link would show a folder's
                        // files under a second name or reach outside.
                        let isLink = (try? entry.resourceValues(forKeys: [.isSymbolicLinkKey]))?
                            .isSymbolicLink ?? true
                        guard !isLink, Self.inside(path, under: root) != nil else { continue }
                        folders.append(path)
                        stack.append((entry, path))
                    } else if name.lowercased().hasSuffix(".md"),
                              name != "index.txt", name != "targets.json" {
                        files.append(path)
                    }
                }
            }
            return (files, folders)
        }
        let confined = found.files.filter { Self.inside($0, under: root) != nil }
        tree = ChapterTree.build(files: confined, folders: found.folders)
        folders = [""] + found.folders.sorted {
            let byDepth = $0.split(separator: "/").count - $1.split(separator: "/").count
            return byDepth != 0 ? byDepth < 0
                : $0.localizedCaseInsensitiveCompare($1) == .orderedAscending
        }
    }

    /// The chapter's editor document, read on first use. nil when the
    /// index entry escapes AGENTS_MD (`../x.md`, a symlinked parent):
    /// `survey` still lists the broken row so it can be removed, but an
    /// editor on it would write outside the chapter folder.
    public func document(for chapter: InstructionsCheck.Chapter) -> InstructionDocument? {
        if let open = documents[chapter.name] { return open }
        guard case .loaded(let check) = status,
              let inside = Self.inside(chapter.name, under: check.directory) else { return nil }
        let document = InstructionDocument(url: inside)
        documents[chapter.name] = document
        Task { await document.load() }
        return document
    }

    /// Whether a change at `path` (relative to `~/.swe`) can change what
    /// `swe instructions check` reports: the generated AGENTS.md, a
    /// per-harness `AGENTS.<name>.md`, and the chapters behind them. The
    /// page follows these while it is on screen, so a build or an edit
    /// made in a terminal shows without a refresh.
    nonisolated public static func affectsCheck(_ path: String) -> Bool {
        (path.hasPrefix("AGENTS.") && path.hasSuffix(".md"))
            || path == "AGENTS_MD" || path.hasPrefix("AGENTS_MD/")
    }

    /// `name` resolved inside `directory` — links in every component, not
    /// just the file's own — or nil when it leaves the folder.
    nonisolated static func inside(_ name: String, under directory: String) -> URL? {
        let root = URL(fileURLWithPath: directory).resolvingSymlinksInPath().standardizedFileURL
        let resolved = URL(fileURLWithPath: directory).appending(path: name)
            .resolvingSymlinksInPath().standardizedFileURL
        guard resolved.path == root.path || resolved.path.hasPrefix(root.path + "/") else { return nil }
        return resolved
    }

    public func isDirty(_ name: String) -> Bool { documents[name]?.isDirty ?? false }

    /// Reload chapters and the output preview that changed with nothing
    /// to lose; flag the ones holding unsaved edits.
    public func checkDisk() async {
        await indexDocument?.checkDisk()
        for document in documents.values { await document.checkDisk() }
        await output?.checkDisk()
    }

    /// Drag `name` to just before `before` in the index (end when nil),
    /// then rebuild.
    public func move(chapter name: String, before: String?, swe: SweClient) async {
        guard let document = await readyIndex() else { return }
        let text = ChapterIndex.move(document.text, name: name, before: before)
        guard text != document.text else { return }
        document.text = text
        if await document.save() == .saved {
            await build(swe: swe)
        } else {
            // Drop the rejected edit and resync: index.txt has no editor of
            // its own, so a dirty flag nobody can clear must not wedge ops.
            await document.load()
            buildError = "index.txt changed on disk; drag again."
        }
    }

    /// A new chapter named for `raw` (with `.md` added when missing):
    /// the file is created with a heading, listed at the end of the index,
    /// selected, and the output rebuilt. Returns why it was refused.
    @discardableResult
    public func addChapter(named raw: String, swe: SweClient) async -> String? {
        var name = raw.trimmingCharacters(in: .whitespaces)
        if !name.isEmpty && !name.lowercased().hasSuffix(".md") { name += ".md" }
        if let problem = ChapterIndex.nameProblem(name) { return problem }
        guard case .loaded(let check) = status else { return "The chapters aren't loaded yet." }
        if check.chapters.contains(where: { $0.name == name }) {
            return "A chapter named \(name) is already listed."
        }
        guard let document = await readyIndex() else {
            return "index.txt hasn't been read, so it can't be updated."
        }
        guard let file = Self.inside(name, under: check.directory) else {
            return "The path cannot leave AGENTS_MD."
        }
        let starter = Self.starter(for: name)
        let created = await offMain { () -> Bool in
            // A folder chapter (communication/rules.md) may need parents.
            try? FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            // Exclusive create: a file that already exists is never
            // overwritten with the starter heading.
            let descriptor = open(file.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o644)
            if descriptor < 0 { return errno == EEXIST }
            let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            return (try? handle.write(contentsOf: Data(starter.utf8))) != nil
        }
        guard created else { return "Couldn't create \(name)." }
        document.text = ChapterIndex.appending(document.text, name: name)
        guard await document.save() == .saved else {
            await document.load()
            return "index.txt changed on disk; try again."
        }
        // A dirty doc kept from an unlisted chapter keeps its draft too.
        if documents[name]?.isDirty != true { documents[name] = nil }
        selected = name
        await build(swe: swe)
        return nil
    }

    /// A new chapter `file` inside `folder` ("" is the top level), made
    /// with the starter heading, listed after the folder's last active
    /// entry when `include`, and selected. Returns why it was refused.
    @discardableResult
    public func createChapter(folder: String, file rawFile: String, include: Bool,
                              swe: SweClient) async -> String? {
        if let problem = ChapterIndex.folderProblem(folder) { return problem }
        var file = rawFile.trimmingCharacters(in: .whitespaces)
        if file.contains("/") { return "The file is a name, not a path; pick its folder above." }
        if file.hasPrefix(".") { return "A leading . hides it from the outline." }
        if !file.isEmpty && !file.lowercased().hasSuffix(".md") { file += ".md" }
        let name = folder.isEmpty ? file : folder + "/" + file
        if let problem = ChapterIndex.nameProblem(name) { return problem }
        guard case .loaded(let check) = status else { return "The chapters aren't loaded yet." }
        if check.entries?.contains(where: { $0.name == name }) == true
            || check.chapters.contains(where: { $0.name == name }) {
            return "A chapter named \(name) already exists in the index."
        }
        guard let url = Self.inside(name, under: check.directory) else {
            return "The path cannot leave AGENTS_MD."
        }
        // Never overwrite: the file name is taken whether it's listed or not.
        let exists = await offMain { FileManager.default.fileExists(atPath: url.path) }
        if exists { return "\(name) already exists." }
        let starter = Self.starter(for: name)
        let created = await offMain { () -> Bool in
            try? FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let descriptor = open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o644)
            if descriptor < 0 { return false }
            let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            return (try? handle.write(contentsOf: Data(starter.utf8))) != nil
        }
        guard created else { return "Couldn't create \(name)." }
        if include {
            guard let document = await readyIndex() else {
                return "index.txt hasn't been read, so it can't be updated."
            }
            document.text = ChapterIndex.inserting(
                document.text, name: name, afterFolder: folder)
            if await document.save() != .saved {
                await document.load()
                // The file this attempt created goes too — but only while
                // it still holds exactly the starter, never anything else.
                let removed = await offMain {
                    (try? String(contentsOf: url, encoding: .utf8)) == starter
                        && (try? FileManager.default.removeItem(at: url)) != nil
                }
                return removed
                    ? "index.txt changed on disk; try again."
                    : "index.txt changed on disk. The new file was left in place, unlisted; try again."
            }
        }
        if documents[name]?.isDirty != true { documents[name] = nil }
        selected = name
        await refreshTree(under: check.directory)
        if include { await build(swe: swe) }
        return nil
    }

    /// A new folder at `path`, parents made as needed. Returns why it
    /// was refused; an existing folder is fine, not an error.
    @discardableResult
    public func createFolder(_ path: String, swe: SweClient) async -> String? {
        if let problem = ChapterIndex.folderProblem(path) { return problem }
        guard !path.isEmpty else { return "Name the folder." }
        guard case .loaded(let check) = status else { return "The chapters aren't loaded yet." }
        guard let url = Self.inside(path, under: check.directory) else {
            return "The path cannot leave AGENTS_MD."
        }
        let made = await offMain { () -> Bool in
            do {
                try FileManager.default.createDirectory(
                    at: url, withIntermediateDirectories: true)
                return true
            } catch { return false }
        }
        guard made else { return "Couldn't create \(path)." }
        await refreshTree(under: check.directory)
        return nil
    }

    /// Drops `name` from the index, then rebuilds. The chapter file stays
    /// on disk, unlisted — removing it is the user's call in Finder. A
    /// chapter holding unsaved text refuses: unlisting must not discard it.
    public func remove(chapter name: String, swe: SweClient) async {
        if documents[name]?.isDirty == true {
            buildError = "Save or revert \(name) before removing it."
            return
        }
        guard let document = await readyIndex() else { return }
        let text = ChapterIndex.removing(document.text, name: name)
        guard text != document.text else { return }
        document.text = text
        guard await document.save() == .saved else {
            await document.load()
            buildError = "index.txt changed on disk; try again."
            return
        }
        documents[name] = nil
        if selected == name {
            selected = ChapterIndex.entries(text).first
        }
        await build(swe: swe)
    }

    /// Compiles the chapters into the shared AGENTS.md and refreshes the
    /// preview and the staleness state. Called after every index save and
    /// offered as Rebuild for edits made elsewhere. A request that lands
    /// while one runs queues a single rerun: a save that goes unbuilt
    /// would leave the output stale.
    public func build(swe: SweClient) async {
        guard !building else { buildAgain = true; return }
        building = true
        buildError = nil
        do {
            buildNote = try await swe.instructionsBuild()
                ? "Rebuilt ~/.swe/AGENTS.md" : "Already current"
        } catch {
            buildNote = nil
            buildError = error.localizedDescription
        }
        await output?.load()
        // The build may have failed on a source problem the last check
        // did not know; a fresh status shows either answer.
        if case .loaded = status { await reload(swe: swe) }
        building = false
        if buildAgain {
            buildAgain = false
            await build(swe: swe)
        }
    }

    /// Turns the flat shared file into chapters (`swe instructions init`),
    /// then reloads into chapter mode.
    public func convert(swe: SweClient) async {
        guard !converting else { return }
        converting = true
        convertError = nil
        defer { converting = false }
        do {
            _ = try await swe.instructionsInit()
        } catch {
            convertError = error.localizedDescription
        }
        await load(swe: swe)
    }

    /// A chapter's first lines: a Markdown title from its file name.
    nonisolated static func starter(for name: String) -> String {
        let base = ((name as NSString).lastPathComponent as NSString).deletingPathExtension
            .replacingOccurrences(of: "[-_]+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        let title = base.isEmpty ? "Chapter" : base.prefix(1).uppercased() + base.dropFirst()
        return "# \(title)\n"
    }

    /// index.txt once it has been read. Programmatic edits only, so a
    /// reload costs nothing when it is still loading.
    private func readyIndex() async -> InstructionDocument? {
        guard let document = indexDocument else { return nil }
        if document.state != .ready { await document.load() }
        return document.state == .ready ? document : nil
    }

    /// `load` without the loading skeleton: the list refreshes in place.
    private func reload(swe: SweClient) async {
        guard let check = try? await swe.instructionsCheck() else { return }
        status = .loaded(check)
    }
}
