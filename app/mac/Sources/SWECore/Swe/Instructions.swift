import Foundation

/// `swe instructions check --json`: whether the shared AGENTS.md is built
/// from chapters under `~/.swe/AGENTS_MD`, the chapter list in index
/// order, and whether the output still matches the sources.
public struct InstructionsCheck: Decodable, Sendable, Equatable {
    /// `~/.swe/AGENTS_MD`.
    public let directory: String
    /// `index.txt`, the ordered chapter list.
    public let index: String
    /// The generated `~/.swe/AGENTS.md`.
    public let output: String
    /// Chapters are set up at all; without them the flat file is edited.
    public let indexExists: Bool
    public let outputExists: Bool
    /// The output carries the compiler's generated marker.
    public let outputGenerated: Bool
    public let chapters: [Chapter]
    /// Every index line and off chapter in order; nil on a swe before
    /// per-harness targets, where `chapters` is the whole list.
    public let entries: [Entry]?
    /// What the build writes: the shared output first, then each harness
    /// with its own chapter set; nil on a swe before per-harness targets.
    public let outputs: [Output]?
    /// A source problem the build would refuse (missing file, bad entry);
    /// nil means the sources compile.
    public let error: String?
    /// nil when `indexExists` is false: flat-file mode has nothing to go stale.
    public let stale: Bool?

    /// One row per chapter name, as the rail shows them. A duplicated
    /// index entry is a source problem `error` already reports; a
    /// second row would share the first's identity while dragged.
    public var uniqueChapters: [Chapter] {
        var seen = Set<String>()
        return chapters.filter { seen.insert($0.name).inserted }
    }

    public init(directory: String, index: String, output: String, indexExists: Bool,
                outputExists: Bool, outputGenerated: Bool, chapters: [Chapter],
                entries: [Entry]? = nil, outputs: [Output]? = nil,
                error: String?, stale: Bool?) {
        self.directory = directory
        self.index = index
        self.output = output
        self.indexExists = indexExists
        self.outputExists = outputExists
        self.outputGenerated = outputGenerated
        self.chapters = chapters
        self.entries = entries
        self.outputs = outputs
        self.error = error
        self.stale = stale
    }

    /// The chapter names that compile into the shared AGENTS.md: `entries`
    /// where `enabled` is true, falling back to `chapters` when the swe is
    /// too old to send `entries` (every listed chapter is compiled there).
    public var compiledNames: Set<String> {
        if let entries {
            return Set(entries.filter(\.enabled).map(\.name))
        }
        return Set(chapters.map(\.name))
    }

    public struct Chapter: Decodable, Sendable, Equatable, Identifiable {
        public var id: String { name }
        /// The index.txt line: a relative .md path, possibly in a folder.
        public let name: String
        /// The chapter's absolute path.
        public let path: String
        /// Listed but absent files still get a row, so the entry can be fixed.
        public let exists: Bool
        /// Harnesses the chapter is pinned to, when targets.json says so.
        public let only: [String]?
        /// Harnesses the chapter is kept from, when targets.json says so.
        public let except: [String]?

        public init(name: String, path: String, exists: Bool,
                    only: [String]? = nil, except: [String]? = nil) {
            self.name = name
            self.path = path
            self.exists = exists
            self.only = only
            self.except = except
        }
    }

    /// One index.txt line as the survey reports it: an active chapter or
    /// a commented-out one holding its slot.
    public struct Entry: Decodable, Sendable, Equatable {
        public let name: String
        public let enabled: Bool

        public init(name: String, enabled: Bool) {
            self.name = name
            self.enabled = enabled
        }
    }

    /// One generated file: `harness` nil is the shared AGENTS.md.
    public struct Output: Decodable, Sendable, Equatable {
        public let harness: String?
        public let path: String
        public let exists: Bool
        public let chapters: [String]

        public init(harness: String?, path: String, exists: Bool, chapters: [String]) {
            self.harness = harness
            self.path = path
            self.exists = exists
            self.chapters = chapters
        }
    }
}

/// `swe instructions build --json`.
public struct InstructionsBuildReply: Decodable, Sendable, Equatable {
    public let changed: Bool
    public init(changed: Bool) { self.changed = changed }
}

/// `swe instructions init --json`.
public struct InstructionsInitReply: Decodable, Sendable, Equatable {
    public let created: Bool
    public let chapter: String
    /// What the chapter was folded from: the old AGENTS.md path or a starter.
    public let source: String
    public let built: Bool
    public init(created: Bool, chapter: String, source: String, built: Bool) {
        self.created = created
        self.chapter = chapter
        self.source = source
        self.built = built
    }
}

extension SweClient {
    /// The chapter state. `check` exits 1 when the output is stale or the
    /// sources are broken, and still prints the document — so the run is
    /// allowed to fail and the state inside is what matters.
    public func instructionsCheck() async throws -> InstructionsCheck {
        try await call(["instructions", "check", "--json"], allowFailure: true)
    }

    /// Compiles the chapters into `~/.swe/AGENTS.md`. A refusal (bad
    /// index entry, escaping source) throws with the compiler's message.
    public func instructionsBuild() async throws -> Bool {
        let reply: InstructionsBuildReply = try await call(["instructions", "build", "--json"])
        return reply.changed
    }

    /// Folds the current flat AGENTS.md into a first chapter. Refuses when
    /// chapters exist already.
    public func instructionsInit() async throws -> InstructionsInitReply {
        try await call(["instructions", "init", "--json"])
    }
}
