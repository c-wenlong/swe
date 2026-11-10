import Foundation
import Testing
@testable import SWECore

/// The Edit tab's targets strip and the Compile tab read a chapter's
/// harnesses through `ChapterReceipts`; these pin that read to the
/// compiler's rules and to what a chip's write does.
@Suite struct ChapterReceiptsTests {
    private static let harnesses = ["claude", "codex", "gemini"]

    /// A survey the way `swe instructions check --json` reports one:
    /// active lines become chapters carrying their rule, `#` lines that
    /// name a chapter become off entries.
    private static func check(index: String,
                              rules: [String: [String: [String]]] = [:]) -> InstructionsCheck {
        var chapters: [InstructionsCheck.Chapter] = []
        var entries: [InstructionsCheck.Entry] = []
        for line in index.split(separator: "\n").map(String.init) {
            let stripped = line.trimmingCharacters(in: .whitespaces)
            if stripped.isEmpty { continue }
            if stripped.hasPrefix("#") {
                let name = stripped.dropFirst().trimmingCharacters(in: .whitespaces)
                if name.hasSuffix(".md") { entries.append(.init(name: name, enabled: false)) }
                continue
            }
            let rule = rules[stripped]
            chapters.append(.init(name: stripped, path: "/x/" + stripped, exists: true,
                                  only: rule?["only"], except: rule?["except"]))
            entries.append(.init(name: stripped, enabled: true))
        }
        return InstructionsCheck(directory: "/x", index: "/x/index.txt", output: "/x/AGENTS.md",
                                 indexExists: true, outputExists: true, outputGenerated: true,
                                 chapters: chapters, entries: entries, error: nil, stale: false)
    }

    private static func tree(_ files: [String]) -> [ChapterNode] {
        ChapterTree.build(files: files, folders: [])
    }

    private static func receipts(_ name: String, index: String,
                                 rules: [String: [String: [String]]] = [:],
                                 files: [String]) -> ChapterReceipts {
        ChapterReceipts(name: name, check: check(index: index, rules: rules),
                        harnesses: harnesses, tree: tree(files))
    }

    private static func gets(_ r: ChapterReceipts) -> [String: Bool] {
        Dictionary(uniqueKeysWithValues: r.receipts.map { ($0.harness, $0.gets) })
    }

    @Test func noRuleInheritsEveryHarness() {
        let r = Self.receipts("a.md", index: "a.md\n", files: ["a.md"])
        #expect(r.state == .on)
        #expect(r.source == .everyHarness)
        #expect(r.receivers == Self.harnesses)
        #expect(r.folder == nil)
        #expect(r.summary.hasPrefix("Every harness gets it"))
    }

    @Test func onlyRuleLimitsToItsHarnesses() {
        let r = Self.receipts("a.md", index: "a.md\n", rules: ["a.md": ["only": ["codex", "claude"]]],
                              files: ["a.md"])
        #expect(r.source == .only(["claude", "codex"]))
        #expect(Self.gets(r) == ["claude": true, "codex": true, "gemini": false])
        #expect(r.summary == "Only Claude Code and Codex get it, by its targets.json rule.")
    }

    @Test func emptyOnlyReachesNoHarness() {
        let r = Self.receipts("a.md", index: "a.md\n", rules: ["a.md": ["only": []]],
                              files: ["a.md"])
        #expect(r.receivers.isEmpty)
        #expect(r.summary.hasPrefix("No harness gets it"))
    }

    @Test func exceptRuleDropsItsHarnesses() {
        let r = Self.receipts("a.md", index: "a.md\n", rules: ["a.md": ["except": ["codex"]]],
                              files: ["a.md"])
        #expect(r.source == .except(["codex"]))
        #expect(Self.gets(r) == ["claude": true, "codex": false, "gemini": true])
    }

    @Test func offAndUnlistedChaptersReachNoHarnessWhateverTheRule() {
        // The compiler ignores a rule for a chapter index.txt doesn't
        // activate, so the survey sends none and nobody receives it.
        let off = Self.receipts("a.md", index: "# a.md\nb.md\n",
                                rules: ["a.md": ["only": ["claude"]]], files: ["a.md", "b.md"])
        #expect(off.state == .off)
        #expect(off.source == .off)
        #expect(off.receivers.isEmpty)
        let unlisted = Self.receipts("c.md", index: "b.md\n", files: ["b.md", "c.md"])
        #expect(unlisted.state == .unlisted)
        #expect(unlisted.source == .unlisted)
        #expect(unlisted.receivers.isEmpty)
    }

    @Test func aRuleNamingAnUnlinkedHarnessStillGetsAChip() {
        let r = Self.receipts("a.md", index: "a.md\n", rules: ["a.md": ["only": ["zed", "claude"]]],
                              files: ["a.md"])
        #expect(r.receipts.map(\.harness) == Self.harnesses + ["zed"])
        #expect(Self.gets(r)["zed"] == true)
    }

    @Test func folderLineShowsWhenEveryMemberSharesTheRule() {
        let files = ["h/a.md", "h/b.md", "top.md"]
        let index = "top.md\nh/a.md\nh/b.md\n"
        // As a folder checkbox in Compile writes it: the same rule on each.
        let shared = Self.receipts("h/a.md", index: index,
                                   rules: ["h/a.md": ["only": ["claude"]],
                                           "h/b.md": ["only": ["claude"]]], files: files)
        #expect(shared.folder == ChapterReceipts.Folder(path: "h", count: 2))
        #expect(shared.folderSummary?.contains("all 2 chapters in h/") == true)
        // Name order inside a rule doesn't make it a different rule.
        let reordered = Self.receipts("h/a.md", index: index,
                                      rules: ["h/a.md": ["except": ["codex", "gemini"]],
                                              "h/b.md": ["except": ["gemini", "codex"]]],
                                      files: files)
        #expect(reordered.folder != nil)
        // One member differs: the rule is the chapter's own.
        let own = Self.receipts("h/a.md", index: index,
                                rules: ["h/a.md": ["only": ["claude"]],
                                        "h/b.md": ["only": ["codex"]]], files: files)
        #expect(own.folder == nil)
        // A whole folder on with no rule is just the default.
        let plain = Self.receipts("h/a.md", index: index, files: files)
        #expect(plain.folder == nil)
        // A whole folder switched off reads as set together.
        let off = Self.receipts("h/a.md", index: "top.md\n# h/a.md\n# h/b.md\n", files: files)
        #expect(off.folder?.path == "h")
        // A lone file and a top-level file have no folder line.
        #expect(Self.receipts("top.md", index: "# top.md\nh/a.md\n", files: files).folder == nil)
        #expect(Self.receipts("s/x.md", index: "# s/x.md\ntop.md\n",
                              files: ["s/x.md", "top.md"]).folder == nil)
    }

    @Test func compileLookupsMatchTheSurvey() {
        let c = Self.check(index: "a.md\n# b.md\n", rules: ["a.md": ["except": ["codex"]]])
        #expect(c.compileState(of: "a.md") == .on)
        #expect(c.compileState(of: "b.md") == .off)
        #expect(c.compileState(of: "c.md") == .unlisted)
        #expect(c.rule(for: "a.md") == ["except": ["codex"]])
        #expect(c.rule(for: "b.md") == nil)
    }

    @Test func instructionHarnessNamesKeepFirstSeenOrderOnce() {
        let link = InitCheck.Link(path: "/p", state: .linked)
        let report = InitCheck(agentsFile: "/x", agentsFileExists: true, harnesses: [
            .init(name: "codex", instructions: link, skills: nil),
            .init(name: "claude", instructions: link, skills: nil),
            .init(name: "skills-only", instructions: nil, skills: link),
            .init(name: "codex", instructions: link, skills: nil),
        ])
        #expect(report.instructionHarnessNames == ["codex", "claude"])
    }

    @Test func filesUnderAFolderWalkTheTree() {
        let tree = Self.tree(["a.md", "h/b.md", "h/deep/c.md", "other/d.md"])
        #expect(Set(ChapterTree.files(under: "h", in: tree)) == ["h/b.md", "h/deep/c.md"])
        #expect(ChapterTree.files(under: "", in: tree).count == 4)
        #expect(ChapterTree.files(under: "missing", in: tree).isEmpty)
    }

    /// A chip writes `CompileToggle.harness(want: !gets)`, the same call
    /// Compile's checkbox makes. Applying those writes and surveying again
    /// must flip exactly that harness, for every starting rule and state.
    @Test func chipWritesFlipOnlyTheirHarness() throws {
        let starts: [(index: String, rules: [String: [String: [String]]])] = [
            ("a.md\nb.md\n", [:]),
            ("a.md\nb.md\n", ["a.md": ["only": ["claude"]]]),
            ("a.md\nb.md\n", ["a.md": ["only": ["claude", "codex"]]]),
            ("a.md\nb.md\n", ["a.md": ["except": ["codex"]]]),
            ("# a.md\nb.md\n", [:]),
            ("b.md\n", [:]),
        ]
        for start in starts {
            for harness in Self.harnesses {
                let before = Self.receipts("a.md", index: start.index, rules: start.rules,
                                           files: ["a.md", "b.md"])
                let want = !Self.gets(before)[harness]!
                // Turning the last `only` harness off disables the line;
                // a chapter off everywhere has no receipt to drop.
                let writes = try CompileToggle.harness(
                    harness, want: want, index: start.index, rules: start.rules,
                    state: before.state, name: "a.md", folder: "")
                var index = start.index
                var rules = start.rules
                for write in writes {
                    switch write {
                    case .index(let text): index = text
                    case .targets(let text): rules = try ChapterTargets.parse(text).rules
                    }
                }
                let after = Self.receipts("a.md", index: index, rules: rules,
                                          files: ["a.md", "b.md"])
                var expected = Self.gets(before)
                expected[harness] = want
                #expect(Self.gets(after) == expected,
                        "\(start.index) \(start.rules) toggling \(harness)")
            }
        }
    }
}
