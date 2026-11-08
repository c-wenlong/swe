import AppKit
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

    /// Whether a `# name` off-comment names the chapter — the disabled
    /// form of the slot a rail row still occupies.
    static func offNames(_ line: String, _ name: String) -> Bool {
        let stripped = line.trimmingCharacters(in: .whitespaces)
        guard stripped.hasPrefix("#") else { return false }
        let rest = stripped.dropFirst().trimmingCharacters(in: .whitespaces)
        return normalize(rest) == normalize(name)
    }

    /// The chapter a slot line names — an entry's own text or an
    /// off-comment's `rest` — so a move's undo anchor can name whatever
    /// followed the moved line, active or off.
    static func slotName(_ line: String) -> String? {
        let stripped = line.trimmingCharacters(in: .whitespaces)
        if isEntry(stripped) { return stripped }
        guard stripped.hasPrefix("#") else { return nil }
        let rest = stripped.dropFirst().trimmingCharacters(in: .whitespaces)
        return rest.isEmpty ? nil : String(rest)
    }

    /// `text` with `name` moved to just before `before` — to the end when
    /// `before` is nil, which means before a final newline's empty line.
    /// The moved line and the anchor are entries first, then `# name`
    /// off-comments, so a rail gap before a muted chapter still lands
    /// there and a muted chapter keeps its off state at the new slot.
    public static func move(_ text: String, name: String, before: String?) -> String {
        var lines = lines(text)
        guard let from = lines.firstIndex(where: {
            isEntry($0) && names($0, name)
        }) ?? lines.firstIndex(where: { offNames($0, name) }) else { return text }
        let entry = lines.remove(at: from)
        let to: Int
        if let before,
           let index = lines.firstIndex(where: {
               isEntry($0) && names($0, before)
           }) ?? lines.firstIndex(where: { offNames($0, before) }) {
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

    /// `text` without every slot naming a member of `names` — an active
    /// entry or its `# name` off-comment alike. Deleting the files leaves
    /// no slot to keep, unlike unlisting one, which keeps the comment.
    public static func removingSlots(_ text: String, names: Set<String>) -> String {
        let wanted = Set(names.map(normalize))
        var lines = lines(text)
        lines.removeAll { line in
            guard let slot = slotName(line) else { return false }
            return wanted.contains(normalize(slot))
        }
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

    /// `text` with every line naming `from` retargeted to `to`: an active
    /// entry becomes `to`, a `# from` off-comment becomes `# to`, so a
    /// moved chapter keeps its slot and its on/off state. Comments that
    /// merely mention the name are not the slot and stay untouched.
    public static func renaming(_ text: String, from: String, to: String) -> String {
        var lines = lines(text)
        for (index, line) in lines.enumerated() {
            if isEntry(line) {
                if names(line, from) { lines[index] = to }
            } else {
                let stripped = line.trimmingCharacters(in: .whitespaces)
                guard stripped.hasPrefix("#") else { continue }
                let rest = stripped.dropFirst().trimmingCharacters(in: .whitespaces)
                if normalize(rest) == normalize(from) { lines[index] = "# \(to)" }
            }
        }
        return lines.joined(separator: "\n")
    }

    /// Every chapter slot's name in file order: active entries, then
    /// `# path.md` off-comments that are valid chapter names — what a
    /// folder move rewrites, so also what its checks must look at.
    public static func slots(_ text: String) -> [String] {
        lines(text).compactMap { line in
            let stripped = line.trimmingCharacters(in: .whitespaces)
            if isEntry(stripped) { return stripped }
            guard stripped.hasPrefix("#") else { return nil }
            let rest = stripped.dropFirst().trimmingCharacters(in: .whitespaces)
            return nameProblem(rest) == nil ? rest : nil
        }
    }

    /// Whether `name` climbs with a `..` component: its text prefix then
    /// says nothing about which folder the file is really in.
    public static func climbs(_ name: String) -> Bool {
        normalize(name).split(separator: "/").contains("..")
    }

    /// `name` with `.` and `..` resolved the way a path parser reads
    /// them, so `a/../b.md` reads as `b.md`. Purely lexical: a link in a
    /// component would send `..` somewhere else, which is why paths with
    /// `..` are refused rather than trusted where links can hide.
    public static func collapse(_ name: String) -> String {
        var parts: [String] = []
        for part in normalize(name).split(separator: "/") {
            if part == ".." { _ = parts.popLast() }
            else if part != "." { parts.append(String(part)) }
        }
        return parts.joined(separator: "/")
    }

    /// Whether `name` sits somewhere under `folder` (not the folder
    /// itself), compared through normalize.
    public static func isUnder(_ name: String, folder: String) -> Bool {
        normalize(name).hasPrefix(normalize(folder) + "/")
    }

    /// `name` with its leading `from` folder swapped for `to`. Only for a
    /// name `isUnder(name, folder: from)`.
    public static func rehome(_ name: String, from: String, to: String) -> String {
        normalize(to) + "/" + normalize(name).dropFirst(normalize(from).count + 1)
    }

    /// `text` with every slot under folder `from` retargeted under `to` —
    /// the folder-move form of `renaming`. An active entry is rewritten,
    /// and so is a `# path.md` off-comment that is a valid chapter name,
    /// so each chapter keeps its slot and its on/off state. Other comments
    /// that happen to start with the folder's name stay untouched.
    public static func renamingFolder(_ text: String, from: String, to: String) -> String {
        var lines = lines(text)
        for (index, line) in lines.enumerated() {
            let stripped = line.trimmingCharacters(in: .whitespaces)
            if isEntry(line) {
                if isUnder(stripped, folder: from) {
                    lines[index] = rehome(stripped, from: from, to: to)
                }
            } else if stripped.hasPrefix("#") {
                let rest = stripped.dropFirst().trimmingCharacters(in: .whitespaces)
                guard nameProblem(rest) == nil, isUnder(rest, folder: from) else { continue }
                lines[index] = "# " + rehome(rest, from: from, to: to)
            }
        }
        return lines.joined(separator: "\n")
    }

    /// Why folder `folder` cannot move into `parent` ("" is the top level):
    /// not into itself or one of its own subfolders, and both the parent
    /// and the folder's new path must pass `folderProblem`. nil means the
    /// move can go ahead (or is a no-op, when `parent` already holds it).
    public static func folderMoveProblem(_ folder: String, into parent: String) -> String? {
        let folder = folder.isEmpty ? "" : normalize(folder)
        let parent = parent.isEmpty ? "" : normalize(parent)
        if folder.isEmpty { return "Pick a folder to move." }
        if parent == folder { return "A folder can't move into itself." }
        if parent.hasPrefix(folder + "/") {
            return "\(folder) can't move into its own subfolder \(parent)."
        }
        let base = (folder as NSString).lastPathComponent
        return folderProblem(folder) ?? folderProblem(parent)
            ?? folderProblem(parent.isEmpty ? base : parent + "/" + base)
    }

    /// The full check createChapter runs: the folder must pass
    /// `folderProblem`, then the file's name must pass `nameProblem`, be
    /// a name rather than a path, and not start with "." — the two file
    /// rules `nameProblem` does not cover. nil means it can be created.
    public static func newChapterProblem(folder: String,
                                         file rawFile: String) -> String? {
        if let problem = folderProblem(folder) { return problem }
        var file = rawFile.trimmingCharacters(in: .whitespaces)
        if file.contains("/") { return "The file is a name, not a path; pick its folder above." }
        if file.hasPrefix(".") { return "A leading . hides it from the outline." }
        if !file.isEmpty && !file.lowercased().hasSuffix(".md") { file += ".md" }
        return nameProblem(folder.isEmpty ? file : folder + "/" + file)
    }

    /// The same for a folder alone: a path `folderProblem` accepts and
    /// non-empty.
    public static func newFolderProblem(_ path: String) -> String? {
        if path.isEmpty { return "Name the folder." }
        return folderProblem(path)
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

/// Each folder's open/closed state in the AGENTS.md outlines, kept in
/// UserDefaults under its AGENTS_MD path: one key family for the Edit
/// rail, one for the Compile tab. Unset means open.
public enum ChapterFolderState {
    public static let outlinePrefix = "agentsMd.outline."
    public static let compilePrefix = "agentsMd.compile."

    /// The folder's own key in both families, then every subfolder's.
    static func keys(under folder: String, in defaults: UserDefaults,
                     prefix: String) -> [String] {
        let own = prefix + folder
        return defaults.dictionaryRepresentation().keys
            .filter { $0 == own || $0.hasPrefix(own + "/") }
    }

    /// Moves the stored state of `from` and every folder under it to the
    /// same place under `to`, in both families. Stale keys already under
    /// `to` go first, so a subfolder with no state of its own opens by
    /// default instead of inheriting an old folder's.
    public static func carry(in defaults: UserDefaults, from: String, to: String) {
        guard from != to else { return }
        for prefix in [outlinePrefix, compilePrefix] {
            let moving = keys(under: from, in: defaults, prefix: prefix)
                .map { ($0, defaults.object(forKey: $0)) }
            for stale in keys(under: to, in: defaults, prefix: prefix) {
                defaults.removeObject(forKey: stale)
            }
            for (key, _) in moving { defaults.removeObject(forKey: key) }
            for (key, value) in moving {
                let rest = key.dropFirst((prefix + from).count)
                defaults.set(value, forKey: prefix + to + rest)
            }
        }
    }
}

/// The outline the rail draws: every folder and `.md` file under
/// AGENTS_MD, built from relative paths so it stays testable.
public enum ChapterTree {
    /// `files` and `folders` are AGENTS_MD-relative paths ("" not allowed;
    /// a folder is implied by any deeper path anyway). `order` is the
    /// index's chapter order: each level lists its indexed files in that
    /// order — so a drag up or down the rail means an index reorder —
    /// then its unindexed files, case-insensitively alphabetical like the
    /// folders, which always come first.
    public static func build(files: [String], folders: [String],
                             order: [String] = []) -> [ChapterNode] {
        var fileSet = Set(files)
        var folderSet = Set(folders)
        for file in files {
            var parent = (file as NSString).deletingLastPathComponent
            while !parent.isEmpty && parent != "." {
                folderSet.insert(parent)
                parent = (parent as NSString).deletingLastPathComponent
            }
        }
        var rank: [String: Int] = [:]
        for (index, name) in order.enumerated() where rank[name] == nil {
            rank[name] = index
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
                if $0.isFolder != $1.isFolder { return $0.isFolder }
                let first = rank[$0.path] ?? .max, second = rank[$1.path] ?? .max
                if first != second { return first < second }
                return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
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
    /// Every `.md` path under AGENTS_MD from the same scan as `folders`,
    /// for the New chapter sheet's file suggestions. Ghosts excluded.
    public private(set) var files: [String] = []
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

        // The documents opened here are read before load returns: an edit
        // made right after must compare against what was on disk at load
        // time, not whatever a detached read happened to find later.
        var opened: [InstructionDocument] = []
        if indexDocument?.url.path != check.index {
            let document = InstructionDocument(url: URL(fileURLWithPath: check.index))
            indexDocument = document
            opened.append(document)
        }
        if output?.url.path != check.output {
            let document = InstructionDocument(url: URL(fileURLWithPath: check.output))
            output = document
            opened.append(document)
        }
        let targetsPath = check.directory + "/targets.json"
        if targetsDocument?.url.path != targetsPath {
            let document = InstructionDocument(url: URL(fileURLWithPath: targetsPath))
            targetsDocument = document
            opened.append(document)
        }
        await withDiscardingTaskGroup { group in
            for document in opened { group.addTask { await document.load() } }
        }
        // A newer load may have started and finished during the reads or
        // the walk: this one's chapter order is then stale and must not
        // be published over the newer tree.
        guard mine == generation else { return }
        await refreshTree(under: check.directory,
                          order: check.entries?.map(\.name)
                              ?? check.uniqueChapters.map(\.name),
                          current: { mine == self.generation })
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
    /// `current` is asked once the walk is back: false drops the result,
    /// for a caller a newer one may have overtaken.
    private func refreshTree(under directory: String, order: [String],
                             current: () -> Bool = { true }) async {
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
        guard current() else { return }
        let confined = found.files.filter { Self.inside($0, under: root) != nil }
        tree = ChapterTree.build(files: confined, folders: found.folders,
                                 order: order)
        files = confined.sorted {
            $0.localizedCaseInsensitiveCompare($1) == .orderedAscending
        }
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
        return adopt(chapter.name, at: inside)
    }

    /// `document(for:)` with the folder check off the main actor: it
    /// resolves links in every path component, and `~/.swe` can sit on a
    /// synced folder that stalls. For a view opening chapters as they
    /// are picked; an already open document comes back at once.
    public func opening(_ chapter: InstructionsCheck.Chapter) async -> InstructionDocument? {
        if let open = documents[chapter.name] { return open }
        guard case .loaded(let check) = status else { return nil }
        let name = chapter.name, directory = check.directory
        let inside = await offMain { Self.inside(name, under: directory) }
        // Opened by another caller while this one resolved.
        if let open = documents[name] { return open }
        guard let inside else { return nil }
        return adopt(name, at: inside)
    }

    /// The chapter's document if one is open, without touching the disk.
    public func openDocument(named name: String) -> InstructionDocument? { documents[name] }

    private func adopt(_ name: String, at url: URL) -> InstructionDocument {
        let document = InstructionDocument(url: url)
        documents[name] = document
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

    /// The window's UndoManager chapter moves register on, so ⌘Z walks
    /// them back alongside text edits. The page hands over its
    /// `\.undoManager`; the key window's manager is the fallback.
    public weak var undoManager: UndoManager?

    /// Where the rail and Compile outlines keep each folder's open/closed
    /// state (`ChapterFolderState` keys); a folder move carries it along.
    @ObservationIgnored public var folderStateDefaults: UserDefaults = .standard
    private var movesUndoManager: UndoManager? {
        if let undoManager { return undoManager }
        // No NSApplication under `swift test`: NSApp is nil there.
        guard NSApp != nil else { return nil }
        return NSApp.keyWindow?.undoManager ?? NSApp.mainWindow?.undoManager
    }

    /// Why the last undo/redo move failed, shown under the rail's header
    /// alongside the drag refusals the page collects itself.
    public var moveError: String?

    /// Puts a finished move on the window's UndoManager. `backward` runs
    /// on ⌘Z; `forward` is parked on the redo stack first, while the undo
    /// is still executing — registering it inside the async work would
    /// land on the undo stack again, so ⌘Z would ping-pong instead of
    /// walking back. The work closures run the raw op and never register.
    private func registerMoveUndo(
        _ actionName: String = "Move Chapter",
        forward: @escaping @MainActor () async -> String?,
        backward: @escaping @MainActor () async -> String?
    ) {
        guard let manager = movesUndoManager else { return }
        moveError = nil
        // macOS adds "Undo " / "Redo " in front of the action name itself.
        manager.registerUndo(withTarget: self) { _ in
            if let manager = self.movesUndoManager {
                manager.registerUndo(withTarget: self) { _ in
                    self.registerMoveUndo(actionName, forward: forward, backward: backward)
                    Task { await self.reportMove(forward) }
                }
                manager.setActionName(actionName)
            }
            Task { await self.reportMove(backward) }
        }
        manager.setActionName(actionName)
    }

    /// An undo or redo move: a refusal goes to `moveError` rather than
    /// vanishing with the consumed undo entry.
    private func reportMove(_ work: @escaping @MainActor () async -> String?) async {
        if let problem = await work() { moveError = problem }
    }

    /// Drag `name` to just before `before` in the index (end when nil),
    /// then rebuild. Registers undo so ⌘Z puts it back — between the save
    /// and the build, so a second reorder's save can't overtake this
    /// one's undo entry on the stack.
    public func move(chapter name: String, before: String?, swe: SweClient) async {
        let (moved, after) = await applyChapterMove(name: name, before: before)
        guard moved else { return }
        registerMoveUndo(
            forward: { await self.rerunChapterMove(name: name, before: before, swe: swe) },
            backward: { await self.rerunChapterMove(name: name, before: after, swe: swe) })
        await build(swe: swe)
    }

    /// apply + build for undo/redo work; the refusal when it didn't move.
    private func rerunChapterMove(name: String, before: String?,
                                  swe: SweClient) async -> String? {
        let (moved, _) = await applyChapterMove(name: name, before: before)
        if !moved { return "index.txt changed; the move could not be restored." }
        await build(swe: swe)
        return nil
    }

    /// The reorder itself: `name` to just before `before` (end when nil).
    /// Queued like every index write, so a second drag's baseline is the
    /// first's save, not the same text it started from. Returns whether
    /// the index changed and which slot followed `name` beforehand —
    /// the undo's landing spot.
    private func applyChapterMove(name: String, before: String?)
                                  async -> (moved: Bool, after: String?) {
        var moved = false
        var after: String?
        let problem = await queuedCompile { () -> String? in
            guard let document = await self.readyIndex() else { return nil }
            let raw = ChapterIndex.lines(document.text)
            let at = raw.firstIndex(where: {
                ChapterIndex.isEntry($0) && ChapterIndex.names($0, name)
            }) ?? raw.firstIndex(where: { ChapterIndex.offNames($0, name) })
            let text = ChapterIndex.move(document.text, name: name, before: before)
            guard text != document.text else { return nil }
            document.text = text
            guard await document.save() == .saved else {
                // Drop the rejected edit and resync: index.txt has no editor of
                // its own, so a dirty flag nobody can clear must not wedge ops.
                await document.load()
                return "index.txt changed on disk; drag again."
            }
            moved = true
            after = at.flatMap {
                raw.dropFirst($0 + 1).first(where: { ChapterIndex.slotName($0) != nil })
            }.flatMap(ChapterIndex.slotName)
            return nil
        }
        if let problem { buildError = problem }
        return (moved, after)
    }

    /// Where a file move drops the chapter's index entry.
    private enum MovePlacement {
        /// Keep the index slot it had — a drop on a folder or the top level.
        case keep
        /// Move it before `anchor` (the end when nil): a drop in a row gap.
        case before(String?)
    }

    /// How a completed file move is walked back: the file and its entry
    /// go to `folder`, and its index slot before `after` (the end when
    /// nil — `keep` when it was never indexed).
    private typealias MoveInverse = (name: String, folder: String, placement: MovePlacement)

    /// Drop `name` into `folder` ("" is the top level): the file moves on
    /// disk and its index line and targets rule follow, so the chapter
    /// keeps its slot and its harness scope. Registers undo so ⌘Z puts
    /// the file back. Returns why it was refused.
    @discardableResult
    public func move(file name: String, toFolder folder: String,
                     swe: SweClient) async -> String? {
        await move(file: name, toFolder: folder, placement: .keep, swe: swe)
    }

    /// The same, with the moved chapter's index entry landing before
    /// `anchor` (at the end when nil): the rail's drop-into-a-gap move.
    @discardableResult
    public func move(file name: String, toFolder folder: String, before anchor: String?,
                     swe: SweClient) async -> String? {
        await move(file: name, toFolder: folder, placement: .before(anchor), swe: swe)
    }

    private func move(file name: String, toFolder folder: String,
                      placement: MovePlacement, swe: SweClient) async -> String? {
        await applyFileMove(name: name, toFolder: folder, placement: placement,
                            swe: swe) { inverse in
            self.registerMoveUndo(
                forward: {
                    await self.applyFileMove(name: name, toFolder: folder,
                                             placement: placement, swe: swe)
                },
                backward: {
                    await self.applyFileMove(name: inverse.name,
                                             toFolder: inverse.folder,
                                             placement: inverse.placement, swe: swe)
                })
        }
    }

    /// The move itself: file, targets rule, index slot. `onMoved` fires
    /// inside the compile queue the moment the save lands — registration
    /// there keeps undo order equal to save order; undo and redo passes
    /// call this directly (onMoved nil) so they never register mid-undo.
    private func applyFileMove(name: String, toFolder folder: String,
                               placement: MovePlacement, swe: SweClient,
                               onMoved: (@MainActor (MoveInverse) -> Void)? = nil)
        async -> String?
    {
        guard case .loaded(let check) = status else {
            return "The chapters aren't loaded yet."
        }
        let base = (name as NSString).lastPathComponent
        let newName = folder.isEmpty ? base : folder + "/" + base
        guard newName != name else {
            // Same folder: only the index gap can still matter.
            guard case .before(let anchor) = placement else { return nil }
            let (moved, after) = await applyChapterMove(name: name, before: anchor)
            guard moved else {
                // A no-op drop stays silent; a failed undo does not.
                return onMoved == nil
                    ? "index.txt changed; the move could not be restored." : nil
            }
            // Registered before the build, like move(chapter:), so a
            // second move's save can't overtake this one's undo entry.
            onMoved?((name, folder, .before(after)))
            await build(swe: swe)
            return nil
        }
        if documents[name]?.isDirty == true {
            return "Save or revert \(name) before moving it."
        }
        if let problem = ChapterIndex.folderProblem(folder)
            ?? ChapterIndex.nameProblem(newName) { return problem }
        guard let from = Self.inside(name, under: check.directory),
              let to = Self.inside(newName, under: check.directory) else {
            return "The path cannot leave AGENTS_MD."
        }
        // The undo's landing spot, filled inside the queue where the
        // index is current: the slot — entry or off comment — that
        // followed the chapter, or `keep` when it was never indexed.
        var indexed = false
        var successor: String?
        let problem = await queuedCompile { () -> String? in
            // Checked again inside the queue: the draft may have gone
            // dirty while earlier work ran, and dropping it loses edits.
            if self.documents[name]?.isDirty == true {
                return "Save or revert \(name) before moving it."
            }
            guard let index = await self.readyIndex(),
                  let targetsDocument = await self.readyTargets() else {
                return "The chapter files haven't been read."
            }
            let rawLines = ChapterIndex.lines(index.text)
            let at = rawLines.firstIndex(where: {
                ChapterIndex.isEntry($0) && ChapterIndex.names($0, name)
            }) ?? rawLines.firstIndex(where: { ChapterIndex.offNames($0, name) })
            if let at {
                indexed = true
                successor = rawLines.dropFirst(at + 1)
                    .first(where: { ChapterIndex.slotName($0) != nil })
                    .flatMap(ChapterIndex.slotName)
            }
            let oldEntries = ChapterIndex.entries(index.text)
            let rules: [String: [String: [String]]]
            if targetsDocument.existsOnDisk {
                guard let parsed = try? ChapterTargets.parse(targetsDocument.text) else {
                    return self.targetsParseProblem ?? "targets.json can't be edited."
                }
                rules = parsed.rules
            } else {
                rules = [:]
            }
            guard !oldEntries.contains(where: {
                ChapterIndex.names($0, newName)
            }) else { return "\(newName) is already in the index." }
            // A stale rule under the destination name would silently
            // scope the moved chapter; which rule wins is a hand edit.
            guard rules[ChapterIndex.normalize(newName)] == nil else {
                return "\(newName) already has a harness rule; fix targets.json first."
            }
            // inside() resolves links: moving what it returns relocates
            // the link's target out from under the link.
            let linked = await offMain {
                (try? FileManager.default.destinationOfSymbolicLink(
                    atPath: URL(fileURLWithPath: check.directory)
                        .appending(path: name).path)) != nil
            }
            if linked { return "\(name) is a link; move the file it points at." }
            // The file moves first: until index.txt catches up, the old
            // entry is missing, which a racing build reports rather than
            // compiling the chapter without its rule.
            let clash = await offMain {
                FileManager.default.fileExists(atPath: to.path)
            }
            if clash { return "\(newName) already exists." }
            let moved = await offMain {
                (try? FileManager.default.moveItem(at: from, to: to)) != nil
            }
            guard moved else { return "Couldn't move \(name) to \(newName)." }
            let undo = { await offMain {
                try? FileManager.default.moveItem(at: to, to: from)
            } }
            // The rule moves under the new key ahead of the index: while
            // index.txt still names the old path the chapter is absent to
            // a build, never unruled.
            var targetsUndo: String?
            if let rule = rules[ChapterIndex.normalize(name)] ?? rules[name] {
                var renamed = rules
                renamed.removeValue(forKey: ChapterIndex.normalize(name))
                renamed.removeValue(forKey: name)
                renamed[ChapterIndex.normalize(newName)] = rule
                targetsUndo = targetsDocument.text
                targetsDocument.text = ChapterTargets(rules: renamed).encoded()
                if await targetsDocument.save() != .saved {
                    await targetsDocument.load()
                    _ = await undo()
                    return "targets.json changed on disk; try again."
                }
            }
            var renamed = ChapterIndex.renaming(index.text, from: name, to: newName)
            if case .before(let anchor) = placement {
                renamed = ChapterIndex.move(renamed, name: newName, before: anchor)
            }
            let indexChanged = renamed != index.text
            if indexChanged {
                index.text = renamed
                if await index.save() != .saved {
                    await index.load()
                    // The rule goes back before the file does: while the
                    // file is still at its new path the old index entry
                    // is missing to a build, never unruled.
                    var restored = targetsUndo == nil
                    if let targetsUndo {
                        targetsDocument.text = targetsUndo
                        restored = await targetsDocument.save() == .saved
                        if !restored { await targetsDocument.load() }
                    }
                    guard restored else {
                        return "index.txt changed on disk and the move couldn't be undone: \(name) is at \(newName) with its rule under the new name; fix index.txt and targets.json by hand."
                    }
                    _ = await undo()
                    return "index.txt changed on disk; try again."
                }
            }
            // A doc that went dirty mid-move keeps its draft, re-homed
            // onto a fresh document at the new path.
            if let document = self.documents[name] {
                self.documents[name] = nil
                if document.isDirty {
                    let rehomed = InstructionDocument(url: to)
                    self.documents[newName] = rehomed
                    await rehomed.load()
                    rehomed.text = document.text
                }
            }
            if self.selected == name { self.selected = newName }
            await self.refreshTree(under: check.directory,
                                   order: ChapterIndex.entries(index.text))
            if indexChanged { await self.build(swe: swe) }
            // Registered inside the queue turn: the undo stack then
            // matches the order the moves actually landed.
            let sourceFolder = name.contains("/")
                ? (name as NSString).deletingLastPathComponent : ""
            onMoved?((newName, sourceFolder,
                      indexed ? .before(successor) : .keep))
            return nil
        }
        return problem
    }

    /// Drop folder `path` into `parent` ("" is the top level): the
    /// directory moves on disk and every chapter under it follows — each
    /// index.txt slot (active or off) and each targets.json rule is
    /// retargeted in place, so order and harness scope are kept. Refuses a
    /// move into itself or its own subfolder, a name already taken on disk,
    /// in the index or in targets.json, a linked folder, and unsaved drafts
    /// inside. Registers undo so ⌘Z puts the folder back. Returns why it
    /// was refused.
    @discardableResult
    public func move(folder path: String, toFolder parent: String,
                     swe: SweClient) async -> String? {
        await applyFolderMove(path: path, toFolder: parent, swe: swe) { newPath, oldParent in
            self.registerMoveUndo(
                "Move Folder",
                forward: {
                    await self.applyFolderMove(path: path, toFolder: parent, swe: swe)
                },
                backward: {
                    await self.applyFolderMove(path: newPath, toFolder: oldParent, swe: swe)
                })
        }
    }

    /// The first relative symlink under `folder` (its path below the
    /// folder) whose target lies outside `folder`, or nil. Absolute links
    /// and links within the folder survive the folder moving.
    nonisolated static func escapingLink(in folder: URL) -> String? {
        let root = folder.standardizedFileURL.path
        guard let walker = FileManager.default.enumerator(
            at: folder, includingPropertiesForKeys: [.isSymbolicLinkKey]) else { return nil }
        for case let entry as URL in walker {
            guard let destination = try? FileManager.default
                .destinationOfSymbolicLink(atPath: entry.path) else { continue }
            if destination.hasPrefix("/") { continue }
            let target = entry.deletingLastPathComponent()
                .appending(path: destination).standardizedFileURL.path
            if target == root || target.hasPrefix(root + "/") { continue }
            let below = entry.standardizedFileURL.path.dropFirst(root.count + 1)
            return String(below)
        }
        return nil
    }

    /// The folder move itself, in the same order as a file move: the
    /// directory, then the rules, then the index, each step walked back if
    /// a later one is refused. `onMoved` gets the new path and the old
    /// parent — the undo — inside the compile queue, like applyFileMove.
    private func applyFolderMove(path: String, toFolder parent: String, swe: SweClient,
                                 onMoved: (@MainActor (String, String) -> Void)? = nil)
        async -> String?
    {
        guard case .loaded(let check) = status else {
            return "The chapters aren't loaded yet."
        }
        if let problem = ChapterIndex.folderMoveProblem(path, into: parent) { return problem }
        let path = ChapterIndex.normalize(path)
        let parent = parent.isEmpty ? "" : ChapterIndex.normalize(parent)
        let oldParent = path.contains("/") ? (path as NSString).deletingLastPathComponent : ""
        // Already there: a drop back onto its own parent is silent.
        guard parent != oldParent else { return nil }
        let base = (path as NSString).lastPathComponent
        let newPath = parent.isEmpty ? base : parent + "/" + base
        func dirtyDraft() -> String? {
            self.documents.first {
                ChapterIndex.isUnder($0.key, folder: path) && $0.value.isDirty
            }?.key
        }
        if let draft = dirtyDraft() {
            return "Save or revert \(draft) before moving its folder."
        }
        guard let from = Self.inside(path, under: check.directory),
              let to = Self.inside(newPath, under: check.directory) else {
            return "The path cannot leave AGENTS_MD."
        }
        return await queuedCompile { () -> String? in
            // Checked again inside the queue: a draft may have gone dirty
            // while earlier work ran, and moving its file loses the edit.
            if let draft = dirtyDraft() {
                return "Save or revert \(draft) before moving its folder."
            }
            guard let index = await self.readyIndex(),
                  let targetsDocument = await self.readyTargets() else {
                return "The chapter files haven't been read."
            }
            let rules: [String: [String: [String]]]
            if targetsDocument.existsOnDisk {
                guard let parsed = try? ChapterTargets.parse(targetsDocument.text) else {
                    return self.targetsParseProblem ?? "targets.json can't be edited."
                }
                rules = parsed.rules
            } else {
                rules = [:]
            }
            // An index line or rule already under the destination would
            // silently claim a moved chapter; which one wins is a hand edit.
            // Off slots count too: the undo would rewrite them as well.
            let slots = ChapterIndex.slots(index.text)
            if let taken = slots.first(where: { ChapterIndex.isUnder($0, folder: newPath) }) {
                return "\(taken) is already in the index."
            }
            // "devin/../shared.md" names a file outside devin/ that does
            // not move; rewriting its prefix would point it somewhere else.
            if let climbing = (slots + rules.keys.sorted()).first(where: {
                ChapterIndex.isUnder($0, folder: path) && ChapterIndex.climbs($0)
            }) {
                return "\(climbing) climbs out with ..; list it by its own path first."
            }
            if let ruled = rules.keys.sorted()
                .first(where: { ChapterIndex.isUnder($0, folder: newPath) }) {
                return "\(ruled) already has a harness rule; fix targets.json first."
            }
            let root = URL(fileURLWithPath: check.directory)
            let (linked, clash) = await offMain {
                ((try? FileManager.default.destinationOfSymbolicLink(
                    atPath: root.appending(path: path).path)) != nil,
                 FileManager.default.fileExists(atPath: to.path))
            }
            // inside() resolves links: moving what it returns relocates the
            // link's target out from under the link.
            if linked { return "\(path) is a link; move the folder it points at." }
            // A relative link inside that reaches outside the folder would
            // point somewhere else once the folder moves.
            if let link = await offMain({ Self.escapingLink(in: from) }) {
                let shown = path + "/" + link
                return "\(shown) is a relative link out of \(path); moving it would change what it points at."
            }
            if clash { return "\(newPath) already exists." }
            let moved = await offMain {
                (try? FileManager.default.moveItem(at: from, to: to)) != nil
            }
            guard moved else { return "Couldn't move \(path) to \(newPath)." }
            let undo = { await offMain {
                try? FileManager.default.moveItem(at: to, to: from)
            } }
            // Rules ahead of the index, as for a file: while index.txt
            // still names the old paths those chapters are missing to a
            // build, never compiled without their rule.
            var targetsUndo: String?
            if rules.keys.contains(where: { ChapterIndex.isUnder($0, folder: path) }) {
                var renamed: [String: [String: [String]]] = [:]
                for (key, rule) in rules {
                    let target = ChapterIndex.isUnder(key, folder: path)
                        ? ChapterIndex.rehome(key, from: path, to: newPath) : key
                    renamed[target] = rule
                }
                targetsUndo = targetsDocument.text
                targetsDocument.text = ChapterTargets(rules: renamed).encoded()
                if await targetsDocument.save() != .saved {
                    await targetsDocument.load()
                    _ = await undo()
                    return "targets.json changed on disk; try again."
                }
            }
            let renamed = ChapterIndex.renamingFolder(index.text, from: path, to: newPath)
            let indexChanged = renamed != index.text
            if indexChanged {
                index.text = renamed
                if await index.save() != .saved {
                    await index.load()
                    var restored = targetsUndo == nil
                    if let targetsUndo {
                        targetsDocument.text = targetsUndo
                        restored = await targetsDocument.save() == .saved
                        if !restored { await targetsDocument.load() }
                    }
                    guard restored else {
                        return "index.txt changed on disk and the move couldn't be undone: \(path) is at \(newPath) with its rules under the new names; fix index.txt and targets.json by hand."
                    }
                    _ = await undo()
                    return "index.txt changed on disk; try again."
                }
            }
            // Open editors point at the old paths: clean ones are dropped
            // and reopen at the new path; one that went dirty mid-move
            // keeps its draft on a fresh document there.
            for (name, document) in self.documents where ChapterIndex.isUnder(name, folder: path) {
                self.documents[name] = nil
                guard document.isDirty else { continue }
                let newName = ChapterIndex.rehome(name, from: path, to: newPath)
                let rehomed = InstructionDocument(url: to.appending(
                    path: String(newName.dropFirst(newPath.count + 1))))
                self.documents[newName] = rehomed
                await rehomed.load()
                rehomed.text = document.text
            }
            if let selected = self.selected, ChapterIndex.isUnder(selected, folder: path) {
                self.selected = ChapterIndex.rehome(selected, from: path, to: newPath)
            }
            // Before the tree refresh, so the moved rows read their state.
            ChapterFolderState.carry(in: self.folderStateDefaults, from: path, to: newPath)
            await self.refreshTree(under: check.directory,
                                   order: ChapterIndex.entries(index.text))
            if indexChanged { await self.build(swe: swe) }
            onMoved?(newPath, oldParent)
            return nil
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
        var file = rawFile.trimmingCharacters(in: .whitespaces)
        if !file.isEmpty && !file.lowercased().hasSuffix(".md") { file += ".md" }
        let name = folder.isEmpty ? file : folder + "/" + file
        if let problem = ChapterIndex.newChapterProblem(folder: folder, file: rawFile) {
            return problem
        }
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
        var order = check.entries?.map(\.name) ?? check.uniqueChapters.map(\.name)
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
            order = ChapterIndex.entries(document.text)
        }
        if documents[name]?.isDirty != true { documents[name] = nil }
        selected = name
        await refreshTree(under: check.directory, order: order)
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
        await refreshTree(under: check.directory,
                          order: check.entries?.map(\.name)
                              ?? check.uniqueChapters.map(\.name))
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

    /// Move file `name` to the Trash: its index slot — active or off —
    /// and targets rule drop first so a build between the writes never
    /// compiles a chapter whose file is already gone; the file lands in
    /// the Trash last, recoverable from Finder like any trashed file.
    /// `remove(chapter:)` is the unlist — this is the file itself.
    /// Refuses a path climbing out of AGENTS_MD and a draft holding
    /// unsaved text, which deleting the file would discard.
    @discardableResult
    public func delete(file name: String, swe: SweClient) async -> String? {
        await applyDelete(name, isFolder: false, swe: swe)
    }

    /// Move folder `path` and everything under it to the Trash, in the
    /// same order as a file delete. Refuses the same, plus a slot or rule
    /// under it that climbs (`devin/../shared.md` names a file the folder
    /// does not own).
    @discardableResult
    public func delete(folder path: String, swe: SweClient) async -> String? {
        await applyDelete(path, isFolder: true, swe: swe)
    }

    /// The delete both context menus run: the narrowing writes through
    /// `applyCompile` first — a slot or rule gone is never a broader
    /// intermediate state — then the entry into the Trash. A refused
    /// Trash after the saves leaves the file on disk as an unlisted row.
    private func applyDelete(_ rawPath: String, isFolder: Bool, swe: SweClient) async -> String? {
        guard case .loaded(let check) = status else {
            return "The chapters aren't loaded yet."
        }
        let path = ChapterIndex.normalize(rawPath)
        func covered(_ name: String) -> Bool {
            isFolder ? ChapterIndex.isUnder(name, folder: path)
                     : ChapterIndex.normalize(name) == path
        }
        func dirtyDraft() -> String? {
            self.documents.keys.filter(covered)
                .first { self.documents[$0]?.isDirty == true }
        }
        if let draft = dirtyDraft() {
            return "Save or revert \(draft) before deleting it."
        }
        // The entry itself is never resolved: a linked chapter deletes
        // the link, not its target. Its parent folder is resolved through
        // inside() — `alias/x.md` through a link out of AGENTS_MD would
        // otherwise trash the outside file under its index name.
        let root = URL(fileURLWithPath: check.directory).standardizedFileURL
        let url = root.appending(path: path)
        let parentInside = await offMain {
            Self.inside((path as NSString).deletingLastPathComponent,
                        under: check.directory) != nil
        }
        guard !ChapterIndex.climbs(path), parentInside else {
            return "The path cannot leave AGENTS_MD."
        }
        return await queuedCompile { () -> String? in
            // Checked again inside the queue, like the moves: a draft may
            // have gone dirty while earlier work ran.
            if let draft = dirtyDraft() {
                return "Save or revert \(draft) before deleting it."
            }
            guard let index = await self.readyIndex(),
                  let targetsDocument = await self.readyTargets() else {
                return "The chapter files haven't been read."
            }
            let rules: [String: [String: [String]]]
            if targetsDocument.existsOnDisk {
                guard let parsed = try? ChapterTargets.parse(targetsDocument.text) else {
                    return self.targetsParseProblem ?? "targets.json can't be edited."
                }
                rules = parsed.rules
            } else {
                rules = [:]
            }
            let slots = ChapterIndex.slots(index.text)
            if isFolder,
               let climbing = (slots + rules.keys.sorted())
                   .first(where: { ChapterIndex.isUnder($0, folder: path)
                                   && ChapterIndex.climbs($0) }) {
                return "\(climbing) climbs out with ..; fix its path by hand first."
            }
            // A slot or rule that reaches the entry through ".." never
            // matches the text compare, but it names the same file:
            // dropping the file would leave it pointing at a gap. Which
            // file it really names depends on links the index can't see.
            if let alias = (slots + rules.keys.sorted()).first(where: {
                let collapsed = ChapterIndex.collapse($0)
                let reached = isFolder
                    ? ChapterIndex.isUnder(collapsed, folder: path)
                    : collapsed == path
                return reached && !covered($0)
            }) {
                return "\(alias) reaches \(path) through ..; fix its path by hand first."
            }
            let nextIndex = ChapterIndex.removingSlots(
                index.text, names: Set(slots.filter(covered)))
            var nextRules = rules
            var droppedRule = false
            for key in rules.keys where covered(key) {
                nextRules[key] = nil
                droppedRule = true
            }
            var writes: [CompileWrite] = []
            if nextIndex != index.text { writes.append(.index(nextIndex)) }
            // targets.json is only written when a rule actually went:
            // encoding an absent file's empty rules would create it.
            if droppedRule {
                writes.append(.targets(ChapterTargets(rules: nextRules).encoded()))
            }
            if !writes.isEmpty,
               let problem = await self.applyCompile(writes: writes, swe: swe) {
                return problem
            }
            // An entry already gone from disk just gets its slot and rule
            // dropped. fileExists follows the last link, so a broken link
            // counts as here through destinationOfSymbolicLink instead.
            let entryExists = await offMain {
                FileManager.default.fileExists(atPath: url.path)
                    || (try? FileManager.default.destinationOfSymbolicLink(
                        atPath: url.path)) != nil
            }
            if entryExists {
                let trashed = await offMain {
                    (try? FileManager.default.trashItem(at: url,
                                                        resultingItemURL: nil)) != nil
                }
                guard trashed else { return "Couldn't move \(path) to the Trash." }
            }
            // A draft that went dirty during the saves keeps its document:
            // its file is gone, so the rail shows it as a ghost whose text
            // stays reachable instead of being dropped unsaved.
            for key in self.documents.keys.filter(covered)
                where self.documents[key]?.isDirty != true {
                self.documents[key] = nil
            }
            if let selected = self.selected, covered(selected),
               self.documents[selected] == nil {
                self.selected = ChapterIndex.entries(nextIndex).first
            }
            await self.refreshTree(under: check.directory,
                                   order: ChapterIndex.entries(index.text))
            return nil
        }
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
                if problem == nil, !skipped.isEmpty {
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

/// Suggestion rows for the New chapter/folder sheet's folder and file
/// fields. `.create` is the "+ enter to add" row, carrying the query (for
/// a folder) or the full index path (for a file); it is appended only
/// when nothing already occupies that name.
public enum ChapterSuggest {
    public enum Row: Equatable, Hashable {
        case folder(String)
        case file(String)
        case create(String)
    }

    /// Exact, then path prefix, then last-component prefix, then
    /// contains. Lower is better.
    private static func rank(_ path: String, _ query: String) -> Int {
        let p = path.lowercased(), q = query.lowercased()
        if p == q { return 0 }
        if p.hasPrefix(q) { return 1 }
        if (path as NSString).lastPathComponent.lowercased().hasPrefix(q) { return 2 }
        return 3
    }

    /// Folder rows for the Folder field: everything on an empty query,
    /// ranked matches otherwise, capped at 8 plus the create row.
    public static func folders(_ query: String,
                               in folders: [String], files: [String]) -> [Row] {
        let known = folders.filter { !$0.isEmpty }
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else {
            return [.folder("")] + known.sorted().map(Row.folder)
        }
        let ranked = known
            .filter { $0.lowercased().contains(q.lowercased()) }
            .sorted {
                rank($0, q) != rank($1, q)
                    ? rank($0, q) < rank($1, q)
                    : $0.localizedCaseInsensitiveCompare($1) == .orderedAscending
            }
        var rows = ranked.prefix(8).map(Row.folder)
        if !taken(q, in: folders, files: files),
           ChapterIndex.newFolderProblem(q) == nil {
            rows.append(.create(q))
        }
        return rows
    }

    /// `path` names something that already exists — a folder or a file —
    /// so a "New ..." row would collide with it.
    private static func taken(_ path: String, in folders: [String],
                              files: [String]) -> Bool {
        let lower = path.lowercased()
        return folders.contains { $0.lowercased() == lower }
            || files.contains { $0.lowercased() == lower }
    }

    /// File rows for the File field: the folder's direct children on an
    /// empty query; on a query the folder's children rank first, then the
    /// folder-rank order across every file. A trailing ".md" on the query
    /// is optional. `.create` carries `folder/name.md`.
    public static func files(_ query: String, folder: String,
                             in files: [String], folders: [String]) -> [Row] {
        var q = query.trimmingCharacters(in: .whitespaces)
        if q.lowercased().hasSuffix(".md") { q = String(q.dropLast(3)) }
        let folderPrefix = folder.isEmpty ? "" : folder + "/"
        func inFolder(_ path: String) -> Bool {
            (path as NSString).deletingLastPathComponent == folder
        }
        guard !q.isEmpty else {
            return files.filter(inFolder).sorted().map(Row.file)
        }
        // Files rank on their basename minus ".md": an exact file name
        // beats a prefix, which beats a contains. The folder's own
        // children stay first either way.
        func fileRank(_ path: String) -> Int {
            var base = (path as NSString).lastPathComponent.lowercased()
            if base.hasSuffix(".md") { base = String(base.dropLast(3)) }
            if base == q.lowercased() { return 0 }
            if base.hasPrefix(q.lowercased()) { return 1 }
            return 2
        }
        let ranked = files
            .filter {
                ($0 as NSString).lastPathComponent.lowercased()
                    .contains(q.lowercased())
            }
            .sorted {
                let a = (inFolder($0) ? 0 : 1, fileRank($0))
                let b = (inFolder($1) ? 0 : 1, fileRank($1))
                return a != b ? a < b
                    : $0.localizedCaseInsensitiveCompare($1) == .orderedAscending
            }
        var rows = ranked.prefix(8).map(Row.file)
        let name = q + ".md"
        let path = folderPrefix + name
        // No create row for a taken path or a name createChapter would
        // refuse — including a bad Folder, since it is part of the path.
        if !taken(path, in: folders, files: files),
           ChapterIndex.newChapterProblem(folder: folder, file: q) == nil {
            rows.append(.create(path))
        }
        return rows
    }
}
