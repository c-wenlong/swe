import Foundation
import Observation

/// The AGENTS.md page: the shared instruction file, where it is linked, and
/// the instruction files of one project folder.
///
/// Lives as long as the app, so leaving the page keeps unsaved edits.
@MainActor
@Observable
public final class AgentsModel {
    /// The files a project folder can hold, in the order the page lists them.
    public nonisolated static let projectFileNames = ["AGENTS.md", "CLAUDE.md"]

    public struct ProjectFile: Equatable, Sendable, Identifiable {
        public var id: String { name }
        public let name: String
        public let url: URL
        public let exists: Bool
        /// When this file is a symlink to another file in the list, its name.
        public let linksTo: String?
        /// Where the file's link chain ends (the file itself when it is
        /// not a link), as the last scan found it: the path its document
        /// opens at and is cached under.
        public let target: URL

        public init(name: String, url: URL, exists: Bool, linksTo: String?, target: URL? = nil) {
            self.name = name
            self.url = url
            self.exists = exists
            self.linksTo = linksTo
            self.target = target ?? url.standardizedFileURL
        }
    }

    public private(set) var check: Loadable<InitCheck> = .idle
    public private(set) var linking = false
    public private(set) var linkError: String?
    /// `~/.swe/AGENTS.md`, once `swe init --check` has said where it is.
    public private(set) var global: InstructionDocument?
    public private(set) var projectFolder: URL?
    public private(set) var projectFiles: [ProjectFile] = []
    /// Open project documents by the path they write to, so CLAUDE.md
    /// linked to AGENTS.md opens the same document rather than a second copy.
    /// Not observed: `document(for:)` fills it while a view reads it.
    @ObservationIgnored private var projectDocuments: [URL: InstructionDocument] = [:]
    @ObservationIgnored private var generation = 0
    /// Lists a folder's instruction files. It stats and reads links, and
    /// the folder may sit on a stalled mount or file provider, so it
    /// always runs off the main actor.
    @ObservationIgnored private let scan: @Sendable (URL) -> [ProjectFile]
    /// Bumped by each rescan, so an older scan finishing late can't
    /// overwrite a newer one.
    @ObservationIgnored private var scans = 0

    public init(scan: @escaping @Sendable (URL) -> [ProjectFile] = AgentsModel.scan) {
        self.scan = scan
    }

    public func load(swe: SweClient) async {
        generation += 1
        let mine = generation
        if case .loaded = check {} else { check = .loading }
        let next: Loadable<InitCheck>
        do {
            next = .loaded(try await swe.initCheck())
        } catch is CancellationError {
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        guard mine == generation else { return }
        check = next
        if case .loaded(let report) = next {
            let url = URL(fileURLWithPath: report.agentsFile)
            if global?.url != url {
                let document = InstructionDocument(url: url)
                await document.load()
                guard mine == generation else { return }
                global = document
            }
        }
    }

    /// Runs `swe init` (`--force` when `force`, which backs up and replaces
    /// real files in the way), then checks again.
    public func link(swe: SweClient, force: Bool = false) async {
        guard !linking else { return }
        // A project file being edited can be one of the real files --force
        // replaces; its edits would then be cut off from the editor, and the
        // backup holds only what was saved.
        if force && projectHasUnsavedChanges {
            linkError = Self.saveFirst
            return
        }
        linking = true
        linkError = nil
        defer { linking = false }
        do {
            try await swe.runInit(force: force)
        } catch {
            linkError = error.localizedDescription
        }
        await load(swe: swe)
        // swe init seeds a missing AGENTS.md; show it unless there are edits.
        await checkDisk()
    }

    /// Why Replace and back up waits.
    public static let saveFirst = "Save or revert your project edits first: Replace and back up can replace the file you're editing."

    public var projectHasUnsavedChanges: Bool {
        projectDocuments.values.contains { $0.isDirty }
    }

    public var hasUnsavedChanges: Bool {
        (global?.isDirty ?? false) || projectHasUnsavedChanges
    }

    /// Switches to `folder`, dropping the documents of the previous one.
    /// The page asks before calling this while edits are unsaved.
    public func openProject(_ folder: URL) async {
        projectFolder = folder
        projectDocuments = [:]
        projectFiles = []
        await rescanProject()
    }

    /// Lists the folder's instruction files again, after one was created.
    public func rescanProject() async {
        scans += 1
        let mine = scans
        guard let folder = projectFolder else {
            projectFiles = []
            return
        }
        let scan = self.scan
        let files = await offMain { scan(folder) }
        // Another folder opened, or a newer rescan ran, while this read.
        guard mine == scans, projectFolder == folder else { return }
        projectFiles = files
    }

    /// The instruction files `folder` can hold, whether each exists, and
    /// which one links to another: the disk work behind `rescanProject`.
    public nonisolated static func scan(_ folder: URL) -> [ProjectFile] {
        let fm = FileManager.default
        let urls: [URL] = projectFileNames.map { folder.appending(path: $0) }
        return urls.map { url in
            // CLAUDE.md -> AGENTS.md: this file's link ends at another listed file.
            let target = InstructionDocument.resolveLinks(url).standardizedFileURL
            let other = urls.first { $0 != url && $0.standardizedFileURL == target }
            return ProjectFile(name: url.lastPathComponent, url: url,
                               exists: fm.fileExists(atPath: url.path),
                               linksTo: other?.lastPathComponent, target: target)
        }
    }

    /// Looks at the disk again: rereads open files that changed with nothing
    /// to lose, flags ones with edits, and notices files created or relinked.
    public func checkDisk() async {
        await global?.checkDisk()
        await rescanProject()
        for document in projectDocuments.values { await document.checkDisk() }
    }

    /// The document for one of `projectFiles`, read on first use.
    ///
    /// Opened at the link's target, the same path it is cached under, so it
    /// always reads and writes that one file even if the link moves later.
    public func document(for file: ProjectFile) -> InstructionDocument {
        let key = file.target
        if let open = projectDocuments[key] { return open }
        let document = InstructionDocument(url: key)
        projectDocuments[key] = document
        // Read off the main actor; the editor shows .loading until then.
        let id = UUID()
        loads[key] = (id, Task { [weak self] in
            await document.load()
            // A reopened project's newer read owns the entry now.
            if self?.loads[key]?.id == id { self?.loads[key] = nil }
        })
        return document
    }

    /// Reads `document(for:)` started and hasn't finished.
    private var loads: [URL: (id: UUID, task: Task<Void, Never>)] = [:]

    /// Waits for every read `document(for:)` started.
    public func settled() async {
        while let next = loads.values.first { await next.task.value }
    }
}
