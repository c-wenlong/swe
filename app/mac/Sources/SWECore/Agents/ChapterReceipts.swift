import Foundation

// The read side of the Compile rules, shared by the Compile tab and the
// Edit tab's targets strip so both show the same answer for a chapter.
// Nothing here writes: toggles go through `ChaptersModel.setIncluded` and
// `setReceipt`, the one save queue both tabs use.

extension InstructionsCheck {
    /// A file's compile state by its AGENTS_MD-relative path: an active
    /// index line (`on`), a commented slot (`off`), or neither.
    public func compileState(of name: String) -> CompileState {
        if chapters.contains(where: { $0.name == name }) { return .on }
        if let entries {
            return entries.contains { $0.name == name && !$0.enabled } ? .off : .unlisted
        }
        return .unlisted
    }

    /// The targets.json rule the compiler applies to `name`: only active
    /// chapters carry one, since a rule for an off chapter is ignored.
    public func rule(for name: String) -> [String: [String]]? {
        guard let chapter = chapters.first(where: { $0.name == name }) else { return nil }
        if let only = chapter.only { return ["only": only] }
        if let except = chapter.except { return ["except": except] }
        return nil
    }
}

extension InitCheck {
    /// Every harness with an instruction link, first-seen order: the
    /// harnesses the Compile filter and the targets strip offer.
    public var instructionHarnessNames: [String] {
        var seen: [String] = []
        for row in instructionRows where !seen.contains(row.name) {
            seen.append(row.name)
        }
        return seen
    }
}

extension ChapterTree {
    /// Every file path under `folder` in `tree` ("" is the whole tree).
    public static func files(under folder: String, in tree: [ChapterNode]) -> [String] {
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
}

/// Which harnesses receive one chapter, and why, by the same rules the
/// Compile tab reads: index.txt decides whether it compiles at all, then
/// its targets.json `only`/`except` rule decides per harness.
public struct ChapterReceipts: Equatable, Sendable {
    /// Where the chapter's harness set comes from.
    public enum Source: Equatable, Sendable {
        /// Active with no rule: it inherits every harness.
        case everyHarness
        case only([String])
        case except([String])
        /// A `# name` comment in index.txt: no harness gets it.
        case off
        /// Not in index.txt at all: no harness gets it.
        case unlisted
    }

    public struct Receipt: Equatable, Sendable, Identifiable {
        public let harness: String
        public let gets: Bool
        public var id: String { harness }
    }

    /// The chapter's folder, when it shares its targets with every other
    /// chapter there. targets.json holds per-chapter rules only; a folder
    /// checkbox in Compile writes the same rule into each member, so a
    /// folder whose members all match is where these targets came from.
    public struct Folder: Equatable, Sendable {
        public let path: String
        /// Files under the folder, this chapter included.
        public let count: Int
    }

    public let name: String
    public let state: CompileState
    public let rule: [String: [String]]?
    public let source: Source
    /// One per harness: the given harnesses in order, then any harness the
    /// rule names that the link report does not list (so it can be cleared).
    public let receipts: [Receipt]
    public let folder: Folder?

    public init(name: String, check: InstructionsCheck, harnesses: [String],
                tree: [ChapterNode]) {
        let state = check.compileState(of: name)
        let rule = check.rule(for: name)
        self.name = name
        self.state = state
        self.rule = rule
        switch state {
        case .off: source = .off
        case .unlisted: source = .unlisted
        case .on:
            if let rule, let (kind, names) = rule.first {
                source = kind == "only" ? .only(names.sorted()) : .except(names.sorted())
            } else {
                source = .everyHarness
            }
        }
        var names = harnesses
        for named in (rule?.first?.value ?? []).sorted() where !names.contains(named) {
            names.append(named)
        }
        receipts = names.map {
            Receipt(harness: $0, gets: CompileToggle.receives(state: state, rule: rule,
                                                              harness: $0))
        }
        folder = Self.sharedFolder(name: name, state: state, rule: rule,
                                   check: check, tree: tree)
    }

    /// The folder whose every chapter has this one's state and rule, when
    /// that says something: two or more files, and not merely the default
    /// (every chapter on with no rule).
    private static func sharedFolder(name: String, state: CompileState,
                                     rule: [String: [String]]?,
                                     check: InstructionsCheck,
                                     tree: [ChapterNode]) -> Folder? {
        let path = (name as NSString).deletingLastPathComponent
        guard !path.isEmpty, state != .on || rule != nil else { return nil }
        let members = ChapterTree.files(under: path, in: tree)
        guard members.count > 1, members.contains(name) else { return nil }
        let normalized = rule.map(Self.sortedRule)
        let shared = members.allSatisfy {
            check.compileState(of: $0) == state
                && check.rule(for: $0).map(Self.sortedRule) == normalized
        }
        return shared ? Folder(path: path, count: members.count) : nil
    }

    private static func sortedRule(_ rule: [String: [String]]) -> [String: [String]] {
        rule.mapValues { $0.sorted() }
    }

    /// The harnesses that receive it.
    public var receivers: [String] { receipts.filter(\.gets).map(\.harness) }

    /// One line saying where the targets come from, harness names spelled
    /// as `HarnessMark` spells them.
    public var summary: String {
        func list(_ names: [String]) -> String {
            let spelled = names.map { HarnessMark($0).name }
            switch spelled.count {
            case 0: return ""
            case 1: return spelled[0]
            default:
                return spelled.dropLast().joined(separator: ", ") + " and " + spelled.last!
            }
        }
        switch source {
        case .everyHarness:
            return "Every harness gets it: targets.json has no rule for it."
        case .only(let names) where names.isEmpty:
            return "No harness gets it: its targets.json rule lists none."
        case .only(let names):
            return "Only \(list(names)) \(names.count == 1 ? "gets" : "get") it, by its targets.json rule."
        case .except(let names) where names.isEmpty:
            return "Every harness gets it: its targets.json rule excludes none."
        case .except(let names):
            return "Every harness except \(list(names)) gets it, by its targets.json rule."
        case .off:
            return "No harness gets it: it is switched off in index.txt."
        case .unlisted:
            return "No harness gets it: index.txt does not list it."
        }
    }

    /// The folder line, nil when the targets are the chapter's own.
    public var folderSummary: String? {
        guard let folder else { return nil }
        return "Same targets as all \(folder.count) chapters in \(folder.path)/, as a folder checkbox in Compile sets them."
    }
}

/// The Compile tab's harness filter as stored under
/// `agentsMd.compile.filter`: a comma-joined list of harness names, ""
/// for none. A single name (the only form older builds wrote) is a
/// one-item list, so old values keep working.
public enum CompileFilter {
    /// The stored names in order, trimmed, without empties or repeats.
    public static func parse(_ stored: String) -> [String] {
        normalise(stored.split(separator: ",").map(String.init))
    }

    /// The value to store for `names`; `parse` reads it back unchanged.
    public static func serialise(_ names: [String]) -> String {
        normalise(names).joined(separator: ",")
    }

    /// The one harness the tab shows, or nil for the All view: the stored
    /// names that are still linked (`known`, or all of them before the
    /// link report loads) must come down to exactly one.
    public static func harness(stored: String, known: [String]) -> String? {
        let names = parse(stored).filter { known.isEmpty || known.contains($0) }
        return names.count == 1 ? names[0] : nil
    }

    /// The harnesses the dropdown shows ticked: the stored names that are
    /// still linked, or none when nothing is stored or every linked
    /// harness is (both read as All harnesses).
    public static func picked(stored: String, known: [String]) -> [String] {
        let names = parse(stored).filter { known.isEmpty || known.contains($0) }
        if !known.isEmpty && Set(names) == Set(known) { return [] }
        return names
    }

    /// The value to store for a dropdown selection: names in `known`
    /// order, "" for none or all of them.
    public static func stored(picking selection: Set<String>, known: [String]) -> String {
        let ordered = known.filter(selection.contains)
        let names = known.isEmpty ? Array(selection).sorted() : ordered
        if !known.isEmpty && Set(names) == Set(known) { return "" }
        return serialise(names)
    }

    /// The dropdown's face: "All harnesses", the one harness's name, or
    /// "N harnesses".
    public static func label(stored: String, known: [String]) -> String {
        let names = picked(stored: stored, known: known)
        switch names.count {
        case 0: return "All harnesses"
        case 1: return HarnessMark(names[0]).name
        default: return "\(names.count) harnesses"
        }
    }

    /// The harness columns the Order matrix draws: the picked ones in
    /// `known` order, or every linked harness for the All view.
    public static func columns(stored: String, known: [String]) -> [String] {
        let names = picked(stored: stored, known: known)
        return names.isEmpty ? known : known.filter(names.contains)
    }

    private static func normalise(_ names: [String]) -> [String] {
        var out: [String] = []
        for name in names.map({ $0.trimmingCharacters(in: .whitespaces) })
        where !name.isEmpty && !out.contains(name) {
            out.append(name)
        }
        return out
    }
}
