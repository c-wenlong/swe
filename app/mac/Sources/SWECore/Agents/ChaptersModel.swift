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

    /// A chapter name as the compiler reads it — PurePosixPath: "." and
    /// doubled inner slashes collapse, ".." stays, and leading slashes
    /// survive ("/a.md", "//a.md"), so "./a.md" is the same chapter as
    /// "a.md" while a leading slash is a different, unused name.
    public static func normalize(_ name: String) -> String {
        let slash = name.hasPrefix("//") ? "//"
            : name.hasPrefix("/") ? "/" : ""
        let parts = name.split(separator: "/", omittingEmptySubsequences: true)
            .filter { $0 != "." }
        if parts.isEmpty { return slash.isEmpty ? "." : slash }
        return slash + parts.joined(separator: "/")
    }

    /// Whether a raw index line (entry or `# off` comment) names the
    /// chapter, compared through normalize.
    static func names(_ line: String, _ name: String) -> Bool {
        normalize(line.trimmingCharacters(in: .whitespaces)) == normalize(name)
    }

    /// `text` with `name` moved to just before `before` — to the end when
    /// `before` is nil, which means before a final newline's empty line.
    public static func move(_ text: String, name: String, before: String?) -> String {
        var lines = lines(text)
        guard let from = lines.firstIndex(where: {
            isEntry($0) && names($0, name)
        }) else { return text }
        let entry = lines.remove(at: from)
        let to: Int
        if let before,
           let index = lines.firstIndex(where: {
               isEntry($0) && names($0, before)
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
            isEntry($0) && names($0, name)
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
        var last: Int?
        if !folder.isEmpty {
            for (index, line) in lines.enumerated() {
                guard isEntry(line) else { continue }
                let entry = line.trimmingCharacters(in: .whitespaces)
                let at = (entry as NSString).deletingLastPathComponent
                if normalize(at) == normalize(folder) {
                    last = index + 1
                }
            }
        }
        if let last {
            lines.insert(name, at: last)
            return lines.joined(separator: "\n")
        }
        return appending(text, name: name)
    }

    /// `text` with `name`'s active line commented out in place — the off
    /// state, which keeps the chapter's slot for re-enabling. No-op when
    /// `name` is not an active line; other comments and blanks untouched.
    public static func disabling(_ text: String, name: String) -> String {
        var lines = lines(text)
        for (index, line) in lines.enumerated() where isEntry(line) {
            if names(line, name) {
                lines[index] = "# \(name)"
                break
            }
        }
        return lines.joined(separator: "\n")
    }

    /// `text` with `name`'s `# name` comment line reactivated in place.
    /// No-op when no such comment exists.
    public static func enabling(_ text: String, name: String) -> String {
        var lines = lines(text)
        for (index, line) in lines.enumerated() {
            let stripped = line.trimmingCharacters(in: .whitespaces)
            guard stripped.hasPrefix("#") else { continue }
            let rest = stripped.dropFirst().trimmingCharacters(in: .whitespaces)
            if normalize(rest) == normalize(name) {
                lines[index] = name
                break
            }
        }
        return lines.joined(separator: "\n")
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

/// `~/.swe/AGENTS_MD/targets.json`: the per-harness rules, one of
/// `only`/`except` per chapter. The model writes it through an
/// InstructionDocument like index.txt; a file that does not parse is
/// shown as an error and never overwritten.
public struct ChapterTargets: Equatable, Sendable {
    /// name -> either "only" or "except" mapped to harness names.
    public var rules: [String: [String: [String]]]

    public init(rules: [String: [String: [String]]] = [:]) {
        self.rules = rules
    }

    public var isEmpty: Bool { rules.isEmpty }

    /// Parse a targets.json body. Throws a one-line problem naming
    /// targets.json for anything that is not a version-1 shape.
    public static func parse(_ text: String) throws -> ChapterTargets {
        let data = Data(text.utf8)
        let object: Any
        do {
            object = try JSONSerialization.jsonObject(with: data)
        } catch {
            throw NSError(domain: "ChapterTargets", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "targets.json is not valid JSON"])
        }
        guard let root = object as? [String: Any],
              root["version"] as? Int == 1,
              let chapters = root["chapters"] as? [String: Any] else {
            throw NSError(domain: "ChapterTargets", code: 1, userInfo: [
                NSLocalizedDescriptionKey:
                    "targets.json is not a version-1 rules file"])
        }
        var rules: [String: [String: [String]]] = [:]
        for (chapter, value) in chapters {
            guard let rule = value as? [String: Any], rule.count == 1,
                  let (kind, names) = rule.first,
                  kind == "only" || kind == "except",
                  let list = names as? [Any],
                  list.allSatisfy({ $0 is String }) else {
                throw NSError(domain: "ChapterTargets", code: 1, userInfo: [
                    NSLocalizedDescriptionKey:
                        "targets.json has a rule the app cannot edit: \(chapter)"])
            }
            // Chapter names normalize like the compiler's (PurePosixPath:
            // "." and doubled inner slashes collapse, leading slashes and
            // ".." stay), so "./a.md" is "a.md". Two spellings of one
            // chapter is a collision, never merged.
            let name = ChapterIndex.normalize(chapter)
            if rules[name] != nil {
                throw NSError(domain: "ChapterTargets", code: 1, userInfo: [
                    NSLocalizedDescriptionKey:
                        "targets.json lists \(name) twice"])
            }
            rules[name] = [kind: list.map { $0 as! String }]
        }
        return ChapterTargets(rules: rules)
    }

    /// Deterministic JSON: keys sorted, 2-space indent, harness names
    /// sorted, trailing newline, always the version-1 shape.
    public func encoded() -> String {
        var lines = ["{", "  \"version\": 1,", "  \"chapters\": {"]
        let chapters = rules.keys.sorted()
        for (index, chapter) in chapters.enumerated() {
            let (kind, names) = rules[chapter]!.first!
            let comma = index == chapters.count - 1 ? "" : ","
            let list = names.sorted().map { "\"\($0)\"" }.joined(separator: ", ")
            lines.append(
                "    \(Self.jsonString(chapter)): { \"\(kind)\": [\(list)] }\(comma)")
        }
        if chapters.isEmpty {
            lines[2] = "  \"chapters\": {}"
        } else {
            lines.append("  }")
        }
        lines.append("}")
        return lines.joined(separator: "\n") + "\n"
    }

    private static func jsonString(_ string: String) -> String {
        guard let data = try? JSONEncoder().encode(string),
              let text = String(data: data, encoding: .utf8) else { return "\"\\\"\"" }
        return text
    }
}

/// What a chapter's toggle does, in the order the writes must run so a
/// build between them never sees a broader set than either endpoint:
/// narrowing edits first, then the rule (or vice versa for widening).
public enum CompileWrite: Equatable, Sendable {
    case index(String)
    case targets(String)
}

/// A chapter's compile state for one filter: on the index (`on`), holding
/// a commented slot (`off`), or not in the index at all (`unlisted`).
public enum CompileState: Equatable, Sendable { case on, off, unlisted }

public enum CompileToggle {
    /// Whether `harness` receives a chapter that is `on` with `rule`
    /// (`["only": [..]]` or `["except": [..]]`, nil for no rule).
    public static func receives(rule: [String: [String]]?, harness: String) -> Bool {
        guard let rule, let (kind, names) = rule.first else { return true }
        if kind == "only" { return names.contains(harness) }
        return !names.contains(harness)
    }

    /// All-harnesses view: a chapter is included iff its index line is on.
    /// Harness view: included iff on and `receives`.
    public static func receives(state: CompileState,
                                rule: [String: [String]]?,
                                harness: String?) -> Bool {
        guard state == .on else { return false }
        guard let harness else { return true }
        return receives(rule: rule, harness: harness)
    }

    /// Every member on (receipts all true), none, or a mix.
    public enum TriState: Equatable, Sendable { case all, none, mixed }
    public static func triState(_ receipts: [Bool]) -> TriState {
        if receipts.allSatisfy({ $0 }) { return .all }
        if receipts.contains(true) { return .mixed }
        return .none
    }

    /// The ordered writes for the All-harnesses checkbox: on -> commented
    /// (the rule stays so re-enabling restores it), off -> on in place,
    /// unlisted -> inserted after its folder's last entry.
    public static func all(index text: String, state: CompileState,
                           name: String, folder: String) -> [CompileWrite] {
        switch state {
        case .on: return [.index(ChapterIndex.disabling(text, name: name))]
        case .off: return [.index(ChapterIndex.enabling(text, name: name))]
        case .unlisted:
            return [.index(ChapterIndex.inserting(text, name: name, afterFolder: folder))]
        }
    }

    /// The ordered writes for one harness's checkbox. `rules`/`indexText`
    /// are the current file contents; `state`/`rule` describe the chapter.
    /// Narrowing writes (disabling a line, adding an `except`, removing an
    /// `only`) go first so any build between them sees the narrower set;
    /// a rule write precedes the enable it pairs with because a rule for
    /// an off chapter is ignored anyway.
    public static func harness(
        _ harness: String, want: Bool,
        index text: String, rules: [String: [String: [String]]],
        state: CompileState, name: String, folder: String
    ) throws -> [CompileWrite] {
        func encoded(_ rules: [String: [String: [String]]]) -> String {
            ChapterTargets(rules: rules).encoded()
        }
        let rule = rules[name]
        var writes: [CompileWrite] = []
        if want {
            switch state {
            case .unlisted:
                // Rule first: it is ignored while the line is off/absent.
                var next = rules
                next[name] = ["only": [harness]]
                writes = [.targets(encoded(next)),
                          .index(ChapterIndex.inserting(
                              text, name: name, afterFolder: folder))]
            case .off:
                var next = rules
                next[name] = ["only": [harness]]
                writes = [.targets(encoded(next)),
                          .index(ChapterIndex.enabling(text, name: name))]
            case .on:
                guard let rule, let (kind, names) = rule.first,
                      !receives(rule: rule, harness: harness) else { return [] }
                var next = rules
                if kind == "only" {
                    next[name] = ["only": (names + [harness]).sorted()]
                } else {
                    let left = names.filter { $0 != harness }
                    next[name] = left.isEmpty ? nil : ["except": left]
                }
                writes = [.targets(encoded(next))]
            }
        } else {
            guard state == .on else { return [] }
            guard receives(rule: rule, harness: harness) else { return [] }
            if let rule, let (kind, names) = rule.first {
                var next = rules
                if kind == "except" {
                    next[name] = ["except": (names + [harness]).sorted()]
                    writes = [.targets(encoded(next))]
                } else {
                    let left = names.filter { $0 != harness }
                    if left.isEmpty {
                        // Narrow first: disable the line, then drop the
                        // rule. A disable that changes nothing means the
                        // line was never active — dropping the rule would
                        // lose the record, so refuse instead.
                        let disabled = ChapterIndex.disabling(text, name: name)
                        guard disabled != text else {
                            throw NSError(domain: "CompileToggle", code: 1, userInfo: [
                                NSLocalizedDescriptionKey:
                                    "\(name) has no active index line; edit index.txt by hand"])
                        }
                        writes = [.index(disabled)]
                        next[name] = nil
                        writes.append(.targets(encoded(next)))
                    } else {
                        next[name] = ["only": left]
                        writes = [.targets(encoded(next))]
                    }
                }
            } else {
                writes = [.targets(encoded(rules.merging(
                    [name: ["except": [harness]]]) { _, new in new }))]
            }
        }
        return writes
    }

    /// One batch of writes for a folder checkbox: apply `all` or
    /// `harness` per member, folded into evolving index/targets texts so
    /// the batch is a single write per file. `members` is (name, state,
    /// rule) for every file under the folder; `on` is whether the click
    /// means include (any off) or exclude (all on).
    public static func folder(
        on: Bool, harness: String?,
        index text: String, rules: [String: [String: [String]]],
        members: [(name: String, state: CompileState, rule: [String: [String]]?)]
    ) throws -> [CompileWrite] {
        var indexText = text
        var ruleMap = rules
        var changed = false
        for member in members {
            if let harness {
                let writes = try self.harness(
                    harness, want: on, index: indexText, rules: ruleMap,
                    state: member.state, name: member.name,
                    folder: (member.name as NSString).deletingLastPathComponent)
                guard !writes.isEmpty else { continue }
                changed = true
                for write in writes {
                    switch write {
                    case .index(let t): indexText = t
                    case .targets(let t): ruleMap = (try? ChapterTargets.parse(t))?.rules ?? ruleMap
                    }
                }
            } else {
                // All view: `on` means include, so enabling only touches
                // members not already on; excluding only touches on ones.
                let needsMove = on ? member.state != .on : member.state == .on
                guard needsMove else { continue }
                let write = all(index: indexText, state: member.state,
                                name: member.name,
                                folder: (member.name as NSString).deletingLastPathComponent)
                if case .index(let t) = write.first, t != indexText {
                    changed = true
                    indexText = t
                }
            }
        }
        guard changed else { return [] }
        // Widening in a harness view writes the rules first (ignored while
        // the lines are off); narrowing writes the disables first. Every
        // intermediate state is never broader than either end.
        let indexWrite: CompileWrite? = indexText != text ? .index(indexText) : nil
        let targetWrite: CompileWrite? = ruleMap != rules
            ? .targets(ChapterTargets(rules: ruleMap).encoded()) : nil
        if on && harness != nil {
            return [targetWrite, indexWrite].compactMap { $0 }
        }
        return [indexWrite, targetWrite].compactMap { $0 }
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
    /// Indexed chapters the outline has no row for, in index order: the
    /// rail shows a row for each, labelled "missing" when in `absent`.
    public private(set) var missing: [String] = []
    /// The `missing` names whose file is not on disk at all.
    public private(set) var absent: Set<String> = []
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
        let targetsPath = check.directory + "/targets.json"
        if targetsDocument?.url.path != targetsPath {
            let document = InstructionDocument(url: URL(fileURLWithPath: targetsPath))
            targetsDocument = document
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
        // So does one the outline skips (a hidden name, a linked folder).
        let unshown = check.uniqueChapters.filter {
            !files.contains($0.name) && !ghosts.contains($0.name)
                && Self.inside($0.name, under: check.directory) != nil
        }
        missing = unshown.map(\.name)
        absent = Set(unshown.filter { !$0.exists }.map(\.name))
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
        await targetsDocument?.checkDisk()
        for document in documents.values { await document.checkDisk() }
        await output?.checkDisk()
        for document in outputDocuments.values { await document.checkDisk() }
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
            // The file this attempt created goes back out when the index
            // can't be updated — but only while it still holds exactly
            // the starter, never anything else.
            let removeIfStarter = { await offMain {
                (try? String(contentsOf: url, encoding: .utf8)) == starter
                    && (try? FileManager.default.removeItem(at: url)) != nil
            } }
            guard let document = await readyIndex() else {
                _ = await removeIfStarter()
                return "index.txt hasn't been read, so it can't be updated."
            }
            document.text = ChapterIndex.inserting(
                document.text, name: name, afterFolder: folder)
            if await document.save() != .saved {
                await document.load()
                return await removeIfStarter()
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
        for document in outputDocuments.values { await document.load() }
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

    /// targets.json beside index.txt, edited through a document like the
    /// index so a disk change still wins. No file means no rules and the
    /// document is created on the first save.
    private var targetsDocument: InstructionDocument?

    /// The parsed rules, nil only while a targets.json the app cannot
    /// read is on disk — `targetsParseProblem` says why, and toggles are
    /// refused rather than overwriting a file they cannot edit.
    public var targets: ChapterTargets? {
        guard let doc = targetsDocument, doc.state == .ready else {
            return targetsDocument == nil ? ChapterTargets() : nil
        }
        if !doc.existsOnDisk { return ChapterTargets() }
        return try? ChapterTargets.parse(doc.text)
    }

    /// Why the targets on disk cannot be edited, nil when it parses or
    /// does not exist.
    public var targetsParseProblem: String? {
        guard let doc = targetsDocument, doc.existsOnDisk,
              doc.state == .ready else { return nil }
        do {
            _ = try ChapterTargets.parse(doc.text)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    private func readyTargets() async -> InstructionDocument? {
        guard let document = targetsDocument else { return nil }
        if document.state != .ready { await document.load() }
        return document.state == .ready ? document : nil
    }

    /// Write a toggle's ordered edits — each to its document, in order —
    /// then rebuild. Returns a problem line, nil on success. The ordered
    /// writes keep every intermediate file state narrower than the
    /// result, so a build landing between them is never wrong.
    public func applyCompile(writes: [CompileWrite], swe: SweClient) async -> String? {
        // The compiler refuses an index with no active chapters, which
        // would leave every output stale: refuse before writing.
        for write in writes {
            if case .index(let text) = write,
               ChapterIndex.entries(text).isEmpty {
                return "AGENTS.md needs at least one chapter; include another first."
            }
        }
        var savedAny = false
        for write in writes {
            let document: InstructionDocument?
            let text: String
            switch write {
            case .index(let new):
                document = await readyIndex()
                text = new
            case .targets(let new):
                document = await readyTargets()
                text = new
            }
            guard let document else { return "The chapter files haven't been read." }
            document.text = text
            var failure: String?
            switch await document.save() {
            case .saved: savedAny = true
            case .changedOnDisk: failure = "The chapter files changed on disk; try again."
            case .failed(let message): failure = message
            }
            if let failure {
                // Reload so the refused text doesn't linger as a dirty
                // draft; a save that already landed still gets its build.
                await document.load()
                if savedAny { await build(swe: swe) }
                return failure
            }
        }
        await build(swe: swe)
        return nil
    }

    /// Two quick toggles must not compute from the same baseline: every
    /// compile op awaits the one before it, so each reads the previous
    /// op's saved text instead of overwriting it.
    private var compileTail: Task<String?, Never>?
    private func queuedCompile(_ work: @escaping () async -> String?) async -> String? {
        let prior = compileTail
        let task = Task { () -> String? in
            if let prior { _ = await prior.value }
            return await work()
        }
        compileTail = task
        return await task.value
    }

    /// Both documents, ready, or the refusal. `needsTargets` is true when
    /// a rule could be part of the change (any harness-scoped write).
    private func readyCompile(needsTargets: Bool)
        async -> (index: InstructionDocument, targets: InstructionDocument)?
    {
        guard let index = await readyIndex() else { return nil }
        guard let targets = await readyTargets() else { return nil }
        if needsTargets && targets.existsOnDisk
            && (try? ChapterTargets.parse(targets.text)) == nil { return nil }
        return (index, targets)
    }

    /// A chapter's state read from the index text itself — the line is
    /// active (`.on`), a `# name` comment (`.off`), or absent entirely
    /// (`.unlisted`). Derived inside the queued op so a stale view state
    /// can't pick the wrong branch.
    private static func compileState(of name: String, in index: String)
        -> CompileState
    {
        // Scan every line: an active entry later in the file beats a `#`
        // comment for the same chapter — the line, not the comment,
        // is what compiles.
        var off = false
        for line in ChapterIndex.lines(index) {
            let stripped = line.trimmingCharacters(in: .whitespaces)
            if ChapterIndex.isEntry(line) {
                if ChapterIndex.normalize(stripped) == ChapterIndex.normalize(name) {
                    return .on
                }
            } else if stripped.hasPrefix("#") {
                let rest = stripped.dropFirst().trimmingCharacters(in: .whitespaces)
                if ChapterIndex.normalize(rest) == ChapterIndex.normalize(name) {
                    off = true
                }
            }
        }
        return off ? .off : .unlisted
    }

    /// The All-harnesses checkbox on one chapter.
    public func setIncluded(name: String, on: Bool,
                            swe: SweClient) async -> String? {
        await queuedCompile {
            guard let docs = await self.readyCompile(needsTargets: false) else {
                return "The chapter files haven't been read."
            }
            // The read state is what matters: when it already matches the
            // request — an earlier queued op got there first — write
            // nothing rather than flipping it back.
            let state = Self.compileState(of: name, in: docs.index.text)
            if on == (state == .on) { return nil }
            // Enabling writes the name into index.txt; a name the
            // compiler can't use would just break the next build.
            if on, let problem = ChapterIndex.nameProblem(name) { return problem }
            return await self.applyCompile(writes: CompileToggle.all(
                index: docs.index.text,
                state: state,
                name: name,
                folder: (name as NSString).deletingLastPathComponent), swe: swe)
        }
    }

    /// One harness's checkbox on one chapter.
    public func setReceipt(name: String, harness: String,
                           want: Bool, swe: SweClient) async -> String? {
        await queuedCompile {
            guard let docs = await self.readyCompile(needsTargets: true) else {
                return self.targetsParseProblem ?? "The chapter files haven't been read."
            }
            let rules = docs.targets.existsOnDisk
                ? (try? ChapterTargets.parse(docs.targets.text))?.rules ?? [:]
                : [:]
            let state = Self.compileState(of: name, in: docs.index.text)
            // Enabling an off or unlisted chapter writes its name into
            // index.txt; refuse names the compiler can't use.
            if want, state != .on,
               let problem = ChapterIndex.nameProblem(name) { return problem }
            do {
                return try await self.applyCompile(writes: CompileToggle.harness(
                    harness, want: want, index: docs.index.text,
                    rules: rules, state: state,
                    name: name,
                    folder: (name as NSString).deletingLastPathComponent), swe: swe)
            } catch {
                return error.localizedDescription
            }
        }
    }

    /// A folder checkbox: one batch over every file under it, members and
    /// their states derived inside the queue like a single-file toggle.
    public func setFolder(
        _ folder: String, on: Bool, harness: String?,
        swe: SweClient
    ) async -> String? {
        await queuedCompile {
            guard let docs = await self.readyCompile(needsTargets: harness != nil) else {
                return self.targetsParseProblem ?? "The chapter files haven't been read."
            }
            let rules = docs.targets.existsOnDisk
                ? (try? ChapterTargets.parse(docs.targets.text))?.rules ?? [:]
                : [:]
            var members = self.files(under: folder).map {
                (name: $0,
                 state: Self.compileState(of: $0, in: docs.index.text),
                 rule: rules[ChapterIndex.normalize($0)] ?? rules[$0])
            }
            // Enabling writes each name into index.txt: files the
            // compiler can't use are skipped, not enabled, and reported.
            var skipped: [String] = []
            if on {
                members = members.filter { member in
                    if let problem = ChapterIndex.nameProblem(member.name) {
                        skipped.append("\(member.name): \(problem)")
                        return false
                    }
                    return true
                }
            }
            do {
                let problem = try await self.applyCompile(writes: CompileToggle.folder(
                    on: on, harness: harness, index: docs.index.text,
                    rules: rules, members: members), swe: swe)
                if !skipped.isEmpty {
                    return "Skipped \(skipped.joined(separator: "; "))"
                }
                return problem
            } catch {
                return error.localizedDescription
            }
        }
    }

    /// Every .md path under `folder` in the current tree.
    private func files(under folder: String) -> [String] {
        func collect(_ node: ChapterNode) -> [String] {
            node.isFolder ? node.children.flatMap(collect) : [node.path]
        }
        func find(_ nodes: [ChapterNode]) -> ChapterNode? {
            for node in nodes {
                if node.isFolder && node.path == folder { return node }
                if let deep = find(node.children) { return deep }
            }
            return nil
        }
        return folder.isEmpty
            ? tree.flatMap(collect)
            : find(tree).map(collect) ?? []
    }

    /// The read-only document for one generated output, kept per path so
    /// the Compile tab's popup shows the harness's own file when it has one.
    private var outputDocuments: [String: InstructionDocument] = [:]
    public func outputDocument(for path: String) -> InstructionDocument {
        if let doc = outputDocuments[path] { return doc }
        let doc = InstructionDocument(url: URL(fileURLWithPath: path))
        outputDocuments[path] = doc
        Task { await doc.load() }
        return doc
    }

    /// `load` without the loading skeleton: the list refreshes in place.
    private func reload(swe: SweClient) async {
        guard let check = try? await swe.instructionsCheck() else { return }
        status = .loaded(check)
    }
}
