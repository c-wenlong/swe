import Foundation
import Testing
@testable import SWECore

@MainActor
@Suite struct ChaptersModelTests {
    // MARK: - index.txt text edits

    @Test func entriesSkipCommentsAndBlanks() {
        let text = "# header\na.md\n\n  b.md  \n# tail\n"
        #expect(ChapterIndex.entries(text) == ["a.md", "b.md"])
    }

    @Test func moveKeepsCommentsAndBlankLinesInPlace() {
        let text = "# top\na.md\nb.md\n\n# bottom\nc.md\n"
        #expect(ChapterIndex.move(text, name: "c.md", before: "a.md")
                == "# top\nc.md\na.md\nb.md\n\n# bottom\n")
    }

    @Test func moveToTheEndLandsBeforeTheFinalNewline() {
        #expect(ChapterIndex.move("a.md\nb.md\n", name: "a.md", before: nil) == "b.md\na.md\n")
        #expect(ChapterIndex.move("a.md\nb.md", name: "a.md", before: nil) == "b.md\na.md")
    }

    @Test func removingDropsOnlyItsLine() {
        #expect(ChapterIndex.removing("# c\na.md\nb.md\n", name: "a.md") == "# c\nb.md\n")
        #expect(ChapterIndex.removing("a.md\n", name: "gone.md") == "a.md\n")
    }

    @Test func appendingAddsTheMissingNewlineFirst() {
        #expect(ChapterIndex.appending("a.md\n", name: "b.md") == "a.md\nb.md\n")
        #expect(ChapterIndex.appending("a.md", name: "b.md") == "a.md\nb.md\n")
    }

    @Test func nameProblemMirrorsTheCompilersRules() {
        #expect(ChapterIndex.nameProblem("rules.md") == nil)
        #expect(ChapterIndex.nameProblem("dir/rules.md") == nil)
        #expect(ChapterIndex.nameProblem("") != nil)
        #expect(ChapterIndex.nameProblem("rules.txt") != nil)
        #expect(ChapterIndex.nameProblem("/abs/rules.md") != nil)
        #expect(ChapterIndex.nameProblem("../rules.md") != nil)
        #expect(ChapterIndex.nameProblem("a-->b.md") != nil)
        // A # line is an index comment and ".md" alone has no stem: both
        // would be listed but never compile.
        #expect(ChapterIndex.nameProblem("#notes.md") != nil)
        #expect(ChapterIndex.nameProblem(".md") != nil)
        #expect(ChapterIndex.nameProblem("dir/.md") != nil)
    }

    @Test func starterTitlesFromTheFileName() {
        #expect(ChaptersModel.starter(for: "my-rules.md") == "# My rules\n")
        #expect(ChaptersModel.starter(for: "dir/x.md") == "# X\n")
    }

    // MARK: - the model against a stubbed swe

    /// Returns the canned `instructions check --json` replies in order,
    /// then repeats the last; answers build and init by argument.
    private final class Runner: CommandRunning, @unchecked Sendable {
        var checks: [String]
        var builds = 0
        /// Bumped once per build, after its count: a test awaits
        /// `built.reach(n)` for work that lands through an unawaited task.
        let built = CountGate()
        var checkCalls = 0
        var buildStatus: Int32 = 0

        init(_ checks: [String]) { self.checks = checks }

        func run(_ arguments: [String]) async throws -> CommandResult {
            let data: String
            let status: Int32
            if arguments.contains("build") {
                builds += 1
                await built.bump()
                data = #"{"changed":true}"#
                status = buildStatus
            } else {
                checkCalls += 1
                data = checks[min(checkCalls - 1, checks.count - 1)]
                status = 0
            }
            return CommandResult(status: status,
                                 stdout: Data(#"{"schema":1,"data":"#.utf8)
                                 + Data(data.utf8) + Data("}".utf8),
                                 stderr: status == 0 ? "" : "source problem")
        }
    }

    private func check(_ dir: URL, stale: Bool = false,
                       chapters: [(String, Bool)] = [("a.md", true), ("b.md", true)]) -> String {
        let list = chapters.map { #"{"name":"\#($0.0)","path":"\#(dir.appending(path: $0.0).path)","exists":\#($0.1)}"# }
            .joined(separator: ",")
        return """
            {"directory":"\(dir.path)","index":"\(dir.appending(path: "index.txt").path)",\
            "output":"\(dir.deletingLastPathComponent().appending(path: "AGENTS.md").path)",\
            "index_exists":true,"output_exists":true,"output_generated":true,\
            "chapters":[\(list)],"error":null,"stale":\(stale)}
            """
    }

    private func model(_ dir: TempDir, runner: Runner) async throws -> ChaptersModel {
        try dir.write("a.md", "# A\n")
        try dir.write("b.md", "# B\n")
        try dir.write("index.txt", "# in order\na.md\nb.md\n")
        let model = ChaptersModel()
        await model.load(swe: SweClient(runner: runner))
        return model
    }

    @Test func loadDecodesChapterModeAndSelection() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        #expect(model.chapterMode)
        #expect(model.selected == "a.md")
        if case .loaded(let got) = model.status {
            #expect(got.chapters.map(\.name) == ["a.md", "b.md"])
        } else {
            Issue.record("status did not load")
        }
    }

    @Test func moveRewritesTheIndexAndRebuilds() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url, chapters: [("b.md", true), ("a.md", true)])])
        let model = try await model(dir, runner: runner)
        await model.move(chapter: "b.md", before: "a.md", swe: SweClient(runner: runner))
        #expect(try dir.read("index.txt") == "# in order\nb.md\na.md\n")
        #expect(runner.builds == 1)
    }

    /// A folder fixture: `index` lists `folder/one.md` between a.md and
    /// b.md so a move can show the slot and order are kept.
    private func folderModel(_ dir: TempDir, runner: Runner,
                             folder: String = "eng", index: String? = nil,
                             targets: String? = nil) async throws -> ChaptersModel {
        try dir.write("a.md", "# A\n")
        try dir.write("b.md", "# B\n")
        try FileManager.default.createDirectory(
            at: dir.path(folder), withIntermediateDirectories: true)
        try dir.write("\(folder)/one.md", "# One\n")
        try dir.write("index.txt", index ?? "# in order\na.md\n\(folder)/one.md\nb.md\n")
        if let targets { try dir.write("targets.json", targets) }
        let model = ChaptersModel()
        await model.load(swe: SweClient(runner: runner))
        return model
    }

    @Test func moveFileToTheTopLevelKeepsItsSlotAndRebuilds() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(dir, runner: runner)
        model.selected = "eng/one.md"
        let problem = await model.move(file: "eng/one.md", toFolder: "",
                                       swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("one.md") == "# One\n")
        #expect(try dir.read("index.txt") == "# in order\na.md\none.md\nb.md\n")
        #expect(model.selected == "one.md")
        #expect(runner.builds == 1)
    }

    @Test func moveFileIntoAFolderKeepsItsSlot() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(dir, runner: runner)
        let problem = await model.move(file: "a.md", toFolder: "eng",
                                       swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("index.txt") == "# in order\neng/a.md\neng/one.md\nb.md\n")
        #expect(FileManager.default.fileExists(atPath: dir.path("eng/a.md").path))
    }

    @Test func moveFileRetargetsItsHarnessRule() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(
            dir, runner: runner,
            targets: #"{"version":1,"chapters":{"eng/one.md":{"only":["claude"]}}}"#)
        let problem = await model.move(file: "eng/one.md", toFolder: "",
                                       swe: SweClient(runner: runner))
        #expect(problem == nil)
        let rules = try ChapterTargets.parse(dir.read("targets.json")).rules
        #expect(rules["one.md"] == ["only": ["claude"]])
        #expect(rules["eng/one.md"] == nil)
    }

    @Test func moveFileRefusesCollisionsAndUnsavedText() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(dir, runner: runner)
        try dir.write("eng/a.md", "# Taken\n")
        #expect(await model.move(file: "a.md", toFolder: "eng",
                                 swe: SweClient(runner: runner)) != nil)
        // The losing side is untouched.
        #expect(try dir.read("eng/a.md") == "# Taken\n")
        #expect(try dir.read("a.md") == "# A\n")
        let chapter = InstructionsCheck.Chapter(
            name: "eng/one.md", path: dir.path("eng/one.md").path, exists: true)
        let document = try #require(model.document(for: chapter))
        await document.load()
        document.text = "# Draft\n"
        #expect(await model.move(file: "eng/one.md", toFolder: "",
                                 swe: SweClient(runner: runner)) != nil)
        #expect(FileManager.default.fileExists(atPath: dir.path("eng/one.md").path))
        #expect(model.document(for: chapter)?.isDirty == true)
    }

    @Test func moveAnUnlistedFileSkipsTheIndexAndBuild() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(
            dir, runner: runner, index: "# in order\na.md\nb.md\n")
        let problem = await model.move(file: "eng/one.md", toFolder: "",
                                       swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("index.txt") == "# in order\na.md\nb.md\n")
        #expect(runner.builds == 0)
    }

    @Test func moveFileRefusesARuleAlreadyOnTheDestination() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(
            dir, runner: runner,
            targets: #"{"version":1,"chapters":{"one.md":{"only":["claude"]}}}"#)
        #expect(await model.move(file: "eng/one.md", toFolder: "",
                                 swe: SweClient(runner: runner)) != nil)
        // The stale destination rule stands; the file never moved.
        #expect(FileManager.default.fileExists(atPath: dir.path("eng/one.md").path))
        let rules = try ChapterTargets.parse(dir.read("targets.json")).rules
        #expect(rules["eng/one.md"] == nil)
        #expect(rules["one.md"] == ["only": ["claude"]])
    }

    @Test func moveFileRefusesALinkedChapter() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(dir, runner: runner)
        try dir.link("alias.md", to: dir.path("a.md").path)
        #expect(await model.move(file: "alias.md", toFolder: "eng",
                                 swe: SweClient(runner: runner)) != nil)
        // inside() would have moved the link's target; both stay put.
        #expect(dir.isLink("alias.md"))
        #expect(try dir.read("a.md") == "# A\n")
        #expect(try dir.read("index.txt").contains("a.md"))
    }

    @Test func moveAnchorsBeforeAnOffChapter() {
        #expect(ChapterIndex.move("a.md\n# off.md\nb.md\nc.md\n",
                                name: "c.md", before: "off.md")
                == "a.md\nc.md\n# off.md\nb.md\n")
    }

    @Test func moveCarriesAnOffChapterKeepingItsState() {
        #expect(ChapterIndex.move("a.md\n# off.md\nb.md\n",
                                name: "off.md", before: nil)
                == "a.md\nb.md\n# off.md\n")
    }

    @Test func treeListsFilesInIndexOrder() {
        let tree = ChapterTree.build(
            files: ["a.md", "b.md", "eng/m.md", "eng/z.md", "eng/loose.md"],
            folders: ["eng"],
            order: ["b.md", "eng/z.md", "a.md", "eng/m.md"])
        #expect(tree.map(\.path) == ["eng", "b.md", "a.md"])
        // Indexed files in index order; the unindexed one sorts after.
        #expect(tree[0].children.map(\.path)
                == ["eng/z.md", "eng/m.md", "eng/loose.md"])
    }

    @Test func moveFileIntoAGapPlacesTheIndexEntry() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(dir, runner: runner)
        let problem = await model.move(file: "b.md", toFolder: "eng",
                                       before: "eng/one.md",
                                       swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("index.txt") == "# in order\na.md\neng/b.md\neng/one.md\n")
        #expect(FileManager.default.fileExists(atPath: dir.path("eng/b.md").path))
        #expect(runner.builds == 1)
    }

    @Test func undoPutsTheFileAndIndexBack() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await folderModel(dir, runner: runner)
        let undo = UndoManager()
        model.undoManager = undo
        let problem = await model.move(file: "eng/one.md", toFolder: "",
                                       swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("index.txt") == "# in order\na.md\none.md\nb.md\n")
        #expect(undo.canUndo)
        #expect(runner.builds == 1)
        // The undo's work runs in a Task of its own and ends in a build,
        // after the file and index are back: wait for that build.
        undo.undo()
        try #require(await runner.built.reach(2), "the undo never rebuilt")
        #expect(FileManager.default.fileExists(atPath: dir.path("eng/one.md").path))
        #expect(try dir.read("index.txt") == "# in order\na.md\neng/one.md\nb.md\n")
        #expect(undo.canRedo)
        undo.redo()
        try #require(await runner.built.reach(3), "the redo never rebuilt")
        #expect(FileManager.default.fileExists(atPath: dir.path("one.md").path))
        #expect(try dir.read("index.txt") == "# in order\na.md\none.md\nb.md\n")
    }

    @Test func undoRestoresTheChapterOrder() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        let undo = UndoManager()
        model.undoManager = undo
        await model.move(chapter: "b.md", before: "a.md",
                         swe: SweClient(runner: runner))
        #expect(try dir.read("index.txt") == "# in order\nb.md\na.md\n")
        #expect(runner.builds == 1)
        // The Edit menu shows the system prefix once, not "Undo Undo ...".
        #expect(undo.undoMenuItemTitle == "Undo Move Chapter")
        undo.undo()
        try #require(await runner.built.reach(2), "the undo never rebuilt")
        #expect(try dir.read("index.txt") == "# in order\na.md\nb.md\n")
        #expect(undo.redoMenuItemTitle == "Redo Move Chapter")
    }

    @Test func removeUnlistsButKeepsTheFileOnDisk() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url, chapters: [("b.md", true)])])
        let model = try await model(dir, runner: runner)
        await model.remove(chapter: "a.md", swe: SweClient(runner: runner))
        #expect(try dir.read("index.txt") == "# in order\nb.md\n")
        // Unlisting is not deleting: the chapter file stays for Finder.
        #expect(FileManager.default.fileExists(atPath: dir.path("a.md").path))
        #expect(model.selected == "b.md")
    }

    @Test func addChapterCreatesListsSelectsAndRebuilds() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        let problem = await model.addChapter(named: "my-notes", swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("index.txt") == "# in order\na.md\nb.md\nmy-notes.md\n")
        #expect(try dir.read("my-notes.md") == "# My notes\n")
        #expect(model.selected == "my-notes.md")
        #expect(runner.builds == 1)
    }

    @Test func addChapterRefusesBadAndDuplicateNames() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        #expect(await model.addChapter(named: "../escape", swe: SweClient(runner: runner)) != nil)
        #expect(await model.addChapter(named: "a.md", swe: SweClient(runner: runner)) != nil)
        #expect(runner.builds == 0)
    }

    @Test func aFailedBuildShowsItsMessageAndKeepsState() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        runner.buildStatus = 1
        let model = try await model(dir, runner: runner)
        await model.build(swe: SweClient(runner: runner))
        #expect(model.buildError == "source problem")
        #expect(model.buildNote == nil)
    }

    @Test func removeRefusesAChapterHoldingUnsavedText() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        let chapter = InstructionsCheck.Chapter(
            name: "a.md", path: dir.path("a.md").path, exists: true)
        let document = try #require(model.document(for: chapter))
        await document.load()
        document.text = "# Draft\n"
        #expect(model.hasUnsavedChanges)
        await model.remove(chapter: "a.md", swe: SweClient(runner: runner))
        #expect(try dir.read("index.txt").contains("a.md"))
        #expect(model.buildError != nil)
        // The draft stays editable rather than being dropped with the row.
        #expect(model.document(for: chapter)?.isDirty == true)
    }

    @Test func addChapterNeverOverwritesAnExistingFile() async throws {
        let dir = try TempDir()
        try dir.write("real.md", "# Handwritten\n")
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        let problem = await model.addChapter(named: "real", swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("real.md") == "# Handwritten\n")
        #expect(try dir.read("index.txt").contains("real.md"))
    }

    // MARK: - the outline tree and create sheet paths

    @Test func treeNestsFoldersFirstAlphabeticallyWithEmptyFolders() {
        let tree = ChapterTree.build(
            files: ["engineering/review.md", "zeta.md", "alpha.md",
                    "engineering/api/limits.md"],
            folders: ["engineering", "engineering/api", "harnesses", "empty"])
        #expect(tree.map(\.path) == ["empty", "engineering", "harnesses",
                                     "alpha.md", "zeta.md"])
        let engineering = tree[1]
        #expect(engineering.isFolder && engineering.fileCount == 2)
        #expect(engineering.children.map(\.path)
                == ["engineering/api", "engineering/review.md"])
        #expect(engineering.children[0].children.map(\.path)
                == ["engineering/api/limits.md"])
        #expect(tree[0].fileCount == 0)
    }

    @Test func treeSortsCaseInsensitively() {
        let tree = ChapterTree.build(files: ["Beta.md", "alpha.md", "Gamma.md"],
                                     folders: [])
        #expect(tree.map(\.name) == ["alpha.md", "Beta.md", "Gamma.md"])
    }

    @Test func insertingGroupsUnderTheFoldersLastEntry() {
        let text = "# top\na.md\neng/one.md\n\neng/two.md\nb.md\n"
        #expect(ChapterIndex.inserting(text, name: "eng/new.md", afterFolder: "eng")
                == "# top\na.md\neng/one.md\n\neng/two.md\neng/new.md\nb.md\n")
        // No entry in the folder falls back to the end.
        #expect(ChapterIndex.inserting(text, name: "x/new.md", afterFolder: "x")
                == text + "x/new.md\n")
        // Top level is the end too, newline added when missing.
        #expect(ChapterIndex.inserting("a.md", name: "b.md", afterFolder: "")
                == "a.md\nb.md\n")
    }

    @Test func folderProblemMirrorsThePathRules() {
        #expect(ChapterIndex.folderProblem("") == nil)
        #expect(ChapterIndex.folderProblem("eng/review") == nil)
        #expect(ChapterIndex.folderProblem("../x") != nil)
        #expect(ChapterIndex.folderProblem("/abs") != nil)
        #expect(ChapterIndex.folderProblem("#c") != nil)
        #expect(ChapterIndex.folderProblem(".hidden") != nil)
        #expect(ChapterIndex.folderProblem("a-->b") != nil)
    }

    @Test func createChapterIntoANestedFolderCreatesParentsAndInserts() async throws {
        let dir = try TempDir()
        try FileManager.default.createDirectory(
            at: dir.url.appending(path: "eng"), withIntermediateDirectories: true)
        try dir.write("eng/one.md", "# One\n")
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        let problem = await model.createChapter(
            folder: "eng/api", file: "limits", include: true,
            swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("eng/api/limits.md") == "# Limits\n")
        #expect(try dir.read("index.txt")
                == "# in order\na.md\nb.md\neng/api/limits.md\n")
        #expect(model.selected == "eng/api/limits.md")
        #expect(model.folders.contains("eng/api"))
    }

    @Test func createChapterUnlistedCreatesTheFileOnly() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        let problem = await model.createChapter(
            folder: "", file: "scratch", include: false,
            swe: SweClient(runner: runner))
        #expect(problem == nil)
        #expect(try dir.read("index.txt") == "# in order\na.md\nb.md\n")
        #expect(runner.builds == 0)
    }

    @Test func createChapterRefusesAnExistingFile() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        try dir.write("real.md", "# Handwritten\n")
        #expect(await model.createChapter(
            folder: "", file: "real", include: true,
            swe: SweClient(runner: runner)) != nil)
        #expect(try dir.read("real.md") == "# Handwritten\n")
    }

    @Test func createFolderMakesParentsAndRefreshesTheTree() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        #expect(await model.createFolder("eng/api", swe: SweClient(runner: runner)) == nil)
        #expect(model.folders.contains("eng/api"))
        #expect(await model.createFolder("../x", swe: SweClient(runner: runner)) != nil)
    }

    // MARK: - compile toggles

    @Test func disablingAndEnablingEditOnlyTheirLine() {
        let text = "# top\na.md\n# b.md\n\n# tail\n"
        #expect(ChapterIndex.disabling(text, name: "a.md")
                == "# top\n# a.md\n# b.md\n\n# tail\n")
        #expect(ChapterIndex.disabling(text, name: "gone.md") == text)
        #expect(ChapterIndex.enabling(text, name: "b.md")
                == "# top\na.md\nb.md\n\n# tail\n")
        #expect(ChapterIndex.enabling(text, name: "gone.md") == text)
    }

    @Test func renamingRetargetsTheEntryAndItsOffComment() {
        let text = "# top\na.md\neng/one.md\n# eng/off.md\nb.md\n"
        #expect(ChapterIndex.renaming(text, from: "eng/one.md", to: "x/one.md")
                == "# top\na.md\nx/one.md\n# eng/off.md\nb.md\n")
        #expect(ChapterIndex.renaming(text, from: "eng/off.md", to: "x/off.md")
                == "# top\na.md\neng/one.md\n# x/off.md\nb.md\n")
        // A comment that only mentions the path is not the chapter's slot.
        #expect(ChapterIndex.renaming("# see eng/one.md\n", from: "eng/one.md", to: "x.md")
                == "# see eng/one.md\n")
        #expect(ChapterIndex.renaming("a.md\n", from: "gone.md", to: "x.md") == "a.md\n")
    }

    @Test func targetsParseAndDeterministicEncode() throws {
        let parsed = try ChapterTargets.parse("""
            {"version":1,"chapters":{"b.md":{"except":["z","a"]},
            "a.md":{"only":["claude"]}}}
            """)
        let text = parsed.encoded()
        #expect(text.hasPrefix("{\n  \"version\": 1,\n  \"chapters\": {\n"))
        #expect(text.hasSuffix("}\n"))
        let a = text.range(of: "\"a.md\"")!.lowerBound
        let b = text.range(of: "\"b.md\"")!.lowerBound
        #expect(a < b)  // chapters sorted
        #expect(text.contains("\"except\": [\"a\", \"z\"]"))  // names sorted
        #expect(ChapterTargets().encoded()
                == "{\n  \"version\": 1,\n  \"chapters\": {}\n}\n")
        #expect(throws: (any Error).self) {
            try ChapterTargets.parse("not json")
        }
        #expect(throws: (any Error).self) {
            try ChapterTargets.parse(#"{"version":2,"chapters":{}}"#)
        }
        #expect(throws: (any Error).self) {
            try ChapterTargets.parse(
                #"{"version":1,"chapters":{"a.md":{"only":["x"],"except":["y"]}}}"#)
        }
    }

    @Test func allToggleDisablesEnablesAndInserts() {
        let text = "a.md\n"
        if case .index(let next) = CompileToggle.all(
            index: text, state: .on, name: "a.md", folder: "").first {
            #expect(next == "# a.md\n")
        } else { Issue.record("expected an index write") }
        if case .index(let next) = CompileToggle.all(
            index: "# a.md\n", state: .off, name: "a.md", folder: "").first {
            #expect(next == "a.md\n")
        } else { Issue.record("expected an index write") }
        if case .index(let next) = CompileToggle.all(
            index: text, state: .unlisted, name: "eng/x.md", folder: "eng").first {
            #expect(next == "a.md\neng/x.md\n")
        } else { Issue.record("expected an index write") }
    }

    @Test func harnessToggleAddsAndRemovesReceipt() throws {
        // on with no rule -> except [h] narrows (rule write only)
        var writes = try CompileToggle.harness(
            "claude", want: false, index: "a.md\n", rules: [:],
            state: .on, name: "a.md", folder: "")
        if case .targets(let t) = writes.first {
            #expect(t.contains(#""a.md": { "except": ["claude"] }"#))
        } else { Issue.record("expected a targets write") }
        // off + want -> only [h] written first, then the enable
        writes = try CompileToggle.harness(
            "claude", want: true, index: "# a.md\n", rules: [:],
            state: .off, name: "a.md", folder: "")
        #expect(writes.count == 2)
        if case .targets(let t) = writes[0] {
            #expect(t.contains(#""a.md": { "only": ["claude"] }"#))
        } else { Issue.record("rule write must come first") }
        if case .index(let t) = writes[1] { #expect(t == "a.md\n") }
        else { Issue.record("enable after the rule") }
        // unlisted + want -> only [h] then an insert
        writes = try CompileToggle.harness(
            "codex", want: true, index: "a.md\n", rules: [:],
            state: .unlisted, name: "x.md", folder: "")
        if case .index(let t) = writes[1] { #expect(t == "a.md\nx.md\n") }
        else { Issue.record("insert after the rule") }
        // on + only -> removing the last name disables the line first
        writes = try CompileToggle.harness(
            "claude", want: false, index: "a.md\n",
            rules: ["a.md": ["only": ["claude"]]],
            state: .on, name: "a.md", folder: "")
        #expect(writes.count == 2)
        if case .index(let t) = writes[0] { #expect(t == "# a.md\n") }
        else { Issue.record("disable precedes the rule drop") }
        if case .targets(let t) = writes[1] {
            #expect(!t.contains("\"a.md\""))
        } else { Issue.record("rule dropped after") }
        // on + only -> adding a name widens the rule
        writes = try CompileToggle.harness(
            "codex", want: true, index: "a.md\n",
            rules: ["a.md": ["only": ["claude"]]],
            state: .on, name: "a.md", folder: "")
        if case .targets(let t) = writes[0] {
            #expect(t.contains(#""only": ["claude", "codex"]"#))
        } else { Issue.record("only widens") }
        // on + except -> removing the harness drops an emptied rule
        writes = try CompileToggle.harness(
            "codex", want: true, index: "a.md\n",
            rules: ["a.md": ["except": ["codex"]]],
            state: .on, name: "a.md", folder: "")
        if case .targets(let t) = writes[0] {
            #expect(!t.contains("\"a.md\""))
        } else { Issue.record("emptied except is dropped") }
        // already-received want and already-skipped exclusion are no-ops
        #expect(try CompileToggle.harness(
            "claude", want: true, index: "a.md\n", rules: [:],
            state: .on, name: "a.md", folder: "") == [])
        #expect(try CompileToggle.harness(
            "claude", want: false, index: "a.md\n",
            rules: ["a.md": ["except": ["claude"]]],
            state: .on, name: "a.md", folder: "") == [])
    }

    @Test func folderToggleBatchesEveryFile() throws {
        // Any off -> all on; all on -> all off.
        let members: [(String, CompileState, [String: [String]]?)] = [
            ("a.md", .off, nil),
            ("b.md", .on, nil),
            ("c.md", .unlisted, nil),
        ]
        let onWrites = try CompileToggle.folder(
            on: true, harness: nil, index: "# a.md\nb.md\n",
            rules: [:], members: members)
        if case .index(let t) = onWrites.first {
            #expect(t == "a.md\nb.md\nc.md\n")
        } else { Issue.record("folder on writes the index once") }
        let offWrites = try CompileToggle.folder(
            on: false, harness: nil, index: "a.md\nb.md\nc.md\n",
            rules: [:], members: [
                ("a.md", .on, nil), ("b.md", .on, nil), ("c.md", .on, nil)])
        if case .index(let t) = offWrites.first {
            #expect(t == "# a.md\n# b.md\n# c.md\n")
        } else { Issue.record("folder off writes the index once") }
        // Harness filter: one index write plus one targets write.
        let hWrites = try CompileToggle.folder(
            on: false, harness: "claude", index: "a.md\n", rules: [:],
            members: [("a.md", .on, nil)])
        #expect(hWrites.count == 1)
        if case .targets(let t) = hWrites[0] {
            #expect(t.contains(#""a.md": { "except": ["claude"] }"#))
        } else { Issue.record("folder harness write") }
    }

    @Test func folderHarnessWriteOrderNarrowsFirst() throws {
        // Narrowing batch: index disables land before the rule writes.
        let off = try CompileToggle.folder(
            on: false, harness: "claude", index: "a.md\n",
            rules: ["a.md": ["only": ["claude"]]],
            members: [("a.md", .on, ["only": ["claude"]])])
        #expect(off.count == 2)
        if case .index = off[0], case .targets = off[1] {} else {
            Issue.record("narrowing must write index before targets")
        }
        // Widening batch: the rule lands before the enables.
        let on = try CompileToggle.folder(
            on: true, harness: "claude", index: "# a.md\n", rules: [:],
            members: [("a.md", .off, nil)])
        #expect(on.count == 2)
        if case .targets = on[0], case .index = on[1] {} else {
            Issue.record("widening must write targets before index")
        }
    }

    @Test func aToggleNeverWritesOverAnUnreadIndex() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        // Right after load, the index doc may still be loading: a toggle
        // must wait for it rather than write over unread text.
        let model = try await model(dir, runner: runner)
        let problem = await model.setIncluded(
            name: "a.md", on: false, swe: SweClient(runner: runner))
        let text = try String(contentsOf: dir.path("index.txt"), encoding: .utf8)
        // The toggle read the index first: a.md is off and the rest of
        // the file survived. One that wrote over the unread document
        // would have lost the comment and b.md.
        #expect(problem == nil)
        #expect(text == "# in order\n# a.md\nb.md\n")
    }

    @Test func targetsParseNormalizesChapterKeys() throws {
        let parsed = try ChapterTargets.parse("""
            {"version":1,"chapters":{"./a.md":{"only":["claude"]},
            "eng//x.md":{"except":["codex"]}}}
            """)
        #expect(parsed.rules["a.md"] == ["only": ["claude"]])
        #expect(parsed.rules["eng/x.md"] == ["except": ["codex"]])
        #expect(throws: (any Error).self) {
            try ChapterTargets.parse("""
                {"version":1,"chapters":{"a.md":{"only":["x"]},
                "./a.md":{"only":["y"]}}}
                """)
        }
    }

    @Test func concurrentReceiptTogglesKeepBothRules() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url), check(dir.url)])
        let model = try await model(dir, runner: runner)
        async let a = model.setReceipt(
            name: "a.md",
            harness: "claude", want: false, swe: SweClient(runner: runner))
        async let b = model.setReceipt(
            name: "b.md",
            harness: "codex", want: false, swe: SweClient(runner: runner))
        let (pa, pb) = await (a, b)
        #expect(pa == nil && pb == nil)
        let text = try String(contentsOf: dir.path("targets.json"), encoding: .utf8)
        #expect(text.contains(#""a.md": { "except": ["claude"] }"#))
        #expect(text.contains(#""b.md": { "except": ["codex"] }"#))
    }

    @Test func aFixedTargetsFileUnblocksToggles() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url)])
        // Broken at load: the problem is reported and toggles refuse.
        try "not json".write(to: dir.path("targets.json"), atomically: true,
                            encoding: .utf8)
        let model = try await model(dir, runner: runner)
        // A toggle awaits the doc first, so the refusal is deterministic.
        #expect(await model.setReceipt(
            name: "a.md",
            harness: "claude", want: false,
            swe: SweClient(runner: runner)) != nil)
        #expect(model.targetsParseProblem != nil)
        // Fixed on disk: checkDisk picks it up and toggles write again.
        try """
            {"version":1,"chapters":{}}
            """.write(to: dir.path("targets.json"), atomically: true,
                      encoding: .utf8)
        await model.checkDisk()
        #expect(model.targetsParseProblem == nil)
        let problem = await model.setReceipt(
            name: "a.md",
            harness: "claude", want: false, swe: SweClient(runner: runner))
        #expect(problem == nil)
    }

    @Test func normalizeMatchesPurePosixPath() {
        #expect(ChapterIndex.normalize("./a.md") == "a.md")
        #expect(ChapterIndex.normalize("a//b.md") == "a/b.md")
        #expect(ChapterIndex.normalize("a/./b.md") == "a/b.md")
        #expect(ChapterIndex.normalize("/a.md") == "/a.md")
        #expect(ChapterIndex.normalize("//a.md") == "//a.md")
        #expect(ChapterIndex.normalize("../a.md") == "../a.md")
        #expect(ChapterIndex.disabling("a.md\n./b.md\n", name: "b.md")
                == "a.md\n# b.md\n")
    }

    @Test func queuedIncludesForOneFileListItOnce() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url), check(dir.url)])
        try dir.write("new.md", "# New\n")
        let model = try await model(dir, runner: runner)
        // Two queued includes for the same unlisted file — a folder batch
        // and a single toggle — leave exactly one index line.
        async let folder = model.setFolder(
            "", on: true, harness: nil, swe: SweClient(runner: runner))
        async let single = model.setIncluded(
            name: "new.md", on: true, swe: SweClient(runner: runner))
        _ = await (folder, single)
        let text = try String(contentsOf: dir.path("index.txt"), encoding: .utf8)
        #expect(text.components(separatedBy: "new.md").count - 1 == 1)
    }

    @Test func theLastChapterIsNeverSwitchedOff() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        try dir.write("a.md", "# A\n")
        try dir.write("index.txt", "a.md\n")
        let model = ChaptersModel()
        await model.load(swe: SweClient(runner: runner))
        let problem = await model.setIncluded(
            name: "a.md", on: false, swe: SweClient(runner: runner))
        #expect(problem == "AGENTS.md needs at least one chapter; include another first.")
        #expect(try String(contentsOf: dir.path("index.txt"),
                         encoding: .utf8) == "a.md\n")
    }

    @Test func anActiveLineBeatsACommentForTheSameChapter() async throws {
        // "# a.md" above an active "a.md" reads as .on; unchecking
        // comments only the active line and no duplicates appear.
        #expect(ChapterIndex.disabling("# a.md\na.md\n", name: "a.md")
                == "# a.md\n# a.md\n")
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url)])
        try dir.write("a.md", "# A\n")
        try dir.write("b.md", "# B\n")
        try dir.write("index.txt", "# a.md\na.md\nb.md\n")
        let model = ChaptersModel()
        await model.load(swe: SweClient(runner: runner))
        let problem = await model.setIncluded(
            name: "a.md", on: false, swe: SweClient(runner: runner))
        #expect(problem == nil)
        let text = try String(contentsOf: dir.path("index.txt"), encoding: .utf8)
        #expect(text == "# a.md\n# a.md\nb.md\n")
    }

    @Test func setIncludedOnAnAlreadyOnChapterWritesNothing() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        let problem = await model.setIncluded(
            name: "a.md", on: true, swe: SweClient(runner: runner))
        #expect(problem == nil)
        let text = try String(contentsOf: dir.path("index.txt"), encoding: .utf8)
        #expect(text == "# in order\na.md\nb.md\n")
    }

    @Test func setIncludedOffOnUnlistedOrOffIsANoOp() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        // Unlisted: turning it "off" writes nothing.
        #expect(await model.setIncluded(
            name: "gone.md", on: false, swe: SweClient(runner: runner)) == nil)
        // Off: same — the index must not get a second `# off.md` line.
        try "# in order\na.md\n# off.md\n".write(
            to: dir.path("index.txt"), atomically: true, encoding: .utf8)
        try dir.write("off.md", "# Off\n")
        await model.checkDisk()
        #expect(await model.setIncluded(
            name: "off.md", on: false, swe: SweClient(runner: runner)) == nil)
        let text = try String(contentsOf: dir.path("index.txt"), encoding: .utf8)
        #expect(text == "# in order\na.md\n# off.md\n")
    }

    @Test func enablingANameTheCompilerRejectsIsRefused() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        // "-->" reads as a comment marker to the compiler: enabling it
        // must refuse before index.txt changes at all.
        try dir.write("bad-->name.md", "# Bad\n")
        let problem = await model.setIncluded(
            name: "bad-->name.md", on: true, swe: SweClient(runner: runner))
        #expect(problem != nil)
        #expect(try String(contentsOf: dir.path("index.txt"),
                         encoding: .utf8) == "# in order\na.md\nb.md\n")
    }

    @Test func aFolderIncludeSkipsUnusableNamesOnly() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url)])
        try dir.write("bad-->name.md", "# Bad\n")
        let model = try await model(dir, runner: runner)
        let problem = await model.setFolder(
            "", on: true, harness: nil, swe: SweClient(runner: runner))
        // The good names were enabled; the bad one was skipped and named.
        #expect(problem?.contains("bad-->name.md") == true)
        let text = try String(contentsOf: dir.path("index.txt"), encoding: .utf8)
        #expect(!text.contains("bad-->name.md"))
        #expect(text.contains("a.md"))
    }

    // MARK: - New chapter sheet suggestions

    @Test func emptyFolderQueryListsTopLevelAndEveryFolder() {
        let rows = ChapterSuggest.folders(
            "", in: ["", "eng/review", "eng", "harnesses"], files: [])
        #expect(rows == [.folder(""), .folder("eng"), .folder("eng/review"),
                         .folder("harnesses")])
    }

    @Test func emptyFileQueryListsTheFoldersDirectChildren() {
        let files = ["a.md", "eng/x.md", "eng/deep/y.md", "other.md"]
        #expect(ChapterSuggest.files("", folder: "eng", in: files, folders: [])
                == [.file("eng/x.md")])
        #expect(ChapterSuggest.files("", folder: "", in: files, folders: [])
                == [.file("a.md"), .file("other.md")])
    }

    @Test func folderRankingIsExactPrefixComponentThenContains() {
        let folders = ["xeng", "eng/review", "eng", "review/eng"]
        let rows = ChapterSuggest.folders("eng", in: folders, files: [])
        #expect(rows.prefix(4).map {
            if case .folder(let p) = $0 { return p }; return "?"
        } == ["eng", "eng/review", "review/eng", "xeng"])
        // Exact match is occupied: no create row.
        #expect(!rows.contains {
            if case .create = $0 { return true }; return false
        })
    }

    @Test func folderSuggestionsIgnoreCase() {
        #expect(ChapterSuggest.folders("ENG", in: ["eng/review"], files: []) ==
                [.folder("eng/review"), .create("ENG")])
        // Existing folder under a different case blocks the create row.
        let rows = ChapterSuggest.folders("ENG", in: ["Eng"], files: [])
        #expect(rows == [.folder("Eng")])
    }

    @Test func fileQueriesIgnoreATrailingMdSuffix() {
        let files = ["eng/x.md"]
        #expect(ChapterSuggest.files("x", folder: "eng", in: files, folders: []) ==
                [.file("eng/x.md")])
        // "x.md" matches the same file and offers no duplicate create.
        #expect(ChapterSuggest.files("x.md", folder: "eng", in: files, folders: [])
                == [.file("eng/x.md")])
    }

    @Test func createRowsVanishWhenTheNameIsTakenOrInvalid() {
        // Path exists, case-insensitively.
        #expect(ChapterSuggest.files("NEW", folder: "eng", in: ["eng/new.md"], folders: []) ==
                [.file("eng/new.md")])
        // Invalid names never get a create row.
        let rows = ChapterSuggest.files("../x", folder: "", in: [], folders: [])
        #expect(rows == [])
        // A hidden component is rejected the same way.
        #expect(ChapterSuggest.folders(".secret", in: [], files: []).filter {
            if case .create = $0 { return true }; return false
        } == [])
    }

    @Test func aNewNestedFolderGetsACreateRow() {
        #expect(ChapterSuggest.folders("a/b", in: [], files: []) ==
                [.create("a/b")])
    }

    @Test func suggestionsCapAtEight() {
        let folders = ["eng"] + (0..<20).map { "eng\($0)" }
        let rows = ChapterSuggest.folders("eng", in: folders, files: [])
        #expect(rows.count == 8)
        #expect(rows.allSatisfy {
            if case .folder = $0 { return true }; return false
        })
    }

    @Test func fileRowsRankTheCurrentFoldersChildrenFirst() {
        let files = ["other/eng.md", "eng/eng.md", "eng/a.md"]
        let rows = ChapterSuggest.files("eng", folder: "eng", in: files, folders: [])
        #expect(rows.prefix(2).map {
            if case .file(let p) = $0 { return p }; return "?"
        } == ["eng/eng.md", "other/eng.md"])
    }

    @Test func fileRankingUsesTheBasenameNotThePath() {
        let files = ["x/style-guide.md", "x/style.md", "y/style.md",
                     "y/style-guide.md"]
        // In the same folder an exact basename beats a longer prefix.
        #expect(ChapterSuggest.files("style", folder: "x", in: files, folders: [])
            .prefix(2).map {
                if case .file(let p) = $0 { return p }; return "?"
            } == ["x/style.md", "x/style-guide.md"])
        // The folder's children still rank first across folders.
        #expect(ChapterSuggest.files("style", folder: "x", in: files, folders: [])
            .prefix(3).map {
                if case .file(let p) = $0 { return p }; return "?"
            } == ["x/style.md", "x/style-guide.md", "y/style.md"])
    }

    @Test func createRowsRequireCreatableNames() {
        // A leading dot hides a name from the outline: no create row.
        #expect(ChapterSuggest.files(".secret", folder: "", in: [], folders: [])
                .filter { if case .create = $0 { return true }; return false } == [])
        #expect(ChapterSuggest.folders(".hidden", in: [], files: [])
                .filter { if case .create = $0 { return true }; return false } == [])
        // An invalid folder blocks the file create row too.
        #expect(ChapterSuggest.files("x", folder: ".bad", in: [], folders: [])
                .filter { if case .create = $0 { return true }; return false } == [])
        // A taken path never gets a "New" row, whichever list owns it.
        #expect(ChapterSuggest.folders("rules.md", in: [], files: ["rules.md"])
                .filter { if case .create = $0 { return true }; return false } == [])
        #expect(ChapterSuggest.files("notes.md", folder: "", in: [],
                                     folders: ["", "notes.md"])
                .filter { if case .create = $0 { return true }; return false } == [])
        // The shared validator and createChapter reject the same names.
        #expect(ChapterIndex.newChapterProblem(folder: "", file: ".hidden") != nil)
        #expect(ChapterIndex.newChapterProblem(folder: "eng", file: "ok") == nil)
    }

    /// `load` returns with the documents it opened already read, so an
    /// edit made straight after compares against the disk as it was at
    /// load time. A read left in a detached task loses that race now
    /// and then (the toggle test below flaked on it); many loads in a
    /// row make losing it near certain.
    @Test func loadReturnsWithItsDocumentsRead() async throws {
        for _ in 0..<40 {
            let dir = try TempDir()
            let model = try await model(dir, runner: Runner([check(dir.url)]))
            #expect(model.output?.state == .ready)
        }
    }

    @Test func aToggleHitByADiskChangeReloadsAndReports() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        // index.txt changes on disk after the toggle read state from the
        // loaded text but before the save lands: the save's own compare
        // must refuse it rather than overwriting.
        try "outside.md\na.md\n".write(to: dir.path("index.txt"),
                                      atomically: true, encoding: .utf8)
        let problem = await model.setIncluded(
            name: "a.md", on: false, swe: SweClient(runner: runner))
        #expect(problem == "The chapter files changed on disk; try again.")
        // The refused edit is dropped, not left dirty on the document.
        let text = try String(contentsOf: dir.path("index.txt"), encoding: .utf8)
        #expect(text == "outside.md\na.md\n")
    }

    @Test func receivesCoversStateAndRules() {
        #expect(CompileToggle.receives(state: .on, rule: nil, harness: nil))
        #expect(!CompileToggle.receives(state: .off, rule: nil, harness: nil))
        #expect(!CompileToggle.receives(state: .unlisted, rule: nil, harness: nil))
        #expect(CompileToggle.receives(
            state: .on, rule: ["only": ["claude"]], harness: "claude"))
        #expect(!CompileToggle.receives(
            state: .on, rule: ["only": ["claude"]], harness: "codex"))
        #expect(!CompileToggle.receives(
            state: .on, rule: ["except": ["claude"]], harness: "claude"))
        #expect(CompileToggle.receives(
            state: .on, rule: ["except": ["claude"]], harness: "codex"))
        #expect(CompileToggle.triState([true, true]) == .all)
        #expect(CompileToggle.triState([true, false]) == .mixed)
        #expect(CompileToggle.triState([false, false]) == .none)
    }

    @Test func aReloadKeepsAnUnlistedFileSelected() async throws {
        let dir = try TempDir()
        try dir.write("README.md", "# Notes\n")
        let runner = Runner([check(dir.url), check(dir.url)])
        let model = try await model(dir, runner: runner)
        model.selected = "README.md"
        await model.load(swe: SweClient(runner: runner))
        // README.md is a tree file, so a reload does not bounce the user
        // back to chapter 1.
        #expect(model.selected == "README.md")
        // A file that vanished does fall back to the first chapter.
        model.selected = "gone.md"
        await model.load(swe: SweClient(runner: runner))
        #expect(model.selected == "a.md")
    }

    @Test func checkDecodesEntriesAndOutputsWhenPresent() throws {
        let json = #"""
            {"directory":"/x","index":"/x/index.txt","output":"/x/AGENTS.md",
            "index_exists":true,"output_exists":true,"output_generated":true,
            "chapters":[{"name":"one.md","path":"/x/one.md","exists":true,
            "only":["claude"],"except":null}],
            "targets":"/x/targets.json","targets_exists":true,
            "entries":[{"name":"one.md","enabled":true},
            {"name":"off.md","enabled":false}],
            "outputs":[{"harness":null,"path":"/x/AGENTS.md","exists":true,
            "chapters":["one.md"]},{"harness":"claude",
            "path":"/x/AGENTS.claude.md","exists":true,"chapters":["one.md"]}],
            "error":null,"stale":false}
            """#.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let check = try decoder.decode(InstructionsCheck.self, from: json)
        #expect(check.chapters[0].only == ["claude"])
        #expect(check.compiledNames == ["one.md"])
        #expect(check.outputs?.map(\.harness) == [nil, "claude"])
        // An older swe without the new keys still decodes and falls back.
        let old = #"""
            {"directory":"/x","index":"/x/index.txt","output":"/x/AGENTS.md",
            "index_exists":true,"output_exists":true,"output_generated":true,
            "chapters":[{"name":"one.md","path":"/x/one.md","exists":true}],
            "error":null,"stale":false}
            """#.data(using: .utf8)!
        let older = try decoder.decode(InstructionsCheck.self, from: old)
        #expect(older.compiledNames == ["one.md"])
        #expect(older.outputs == nil && older.entries == nil)
    }

    @Test func compiledSectionsSplitTrimsMarkerAndStripsCompilerLines() {
        let text = """
            <!-- Generated by swe instructions build. Edit the chapters. -->
            <!-- Target: claude -->
            <!-- Source: AGENTS_MD/one.md -->
            # One
            <!-- Source: AGENTS_MD/dir/two.md -->
            # Two
            """
        let sections = CompiledSections.split(text)
        #expect(sections.compactMap(\.name) == ["one.md", "dir/two.md"])
        #expect(sections.allSatisfy { $0.name != nil })
        #expect(sections[0].body.contains("# One"))
        #expect(!sections.contains { $0.body.contains("Generated by") })
        #expect(sections.compactMap(\.name).allSatisfy { !$0.hasSuffix(" ") })
    }

    @Test func refreshSkipsOutsideAndLoopedDirectorySymlinks() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url)])
        let model = try await model(dir, runner: runner)
        // A symlink out of AGENTS_MD: never descended into.
        try FileManager.default.createSymbolicLink(
            at: dir.url.appending(path: "outside-link"),
            withDestinationURL: URL(fileURLWithPath: "/tmp"))
        // A loop back to the root: never followed.
        try FileManager.default.createSymbolicLink(
            at: dir.url.appending(path: "loop"),
            withDestinationURL: dir.url)
        // An alias of a real folder: the real path keeps its files.
        let real = dir.url.appending(path: "real")
        try FileManager.default.createDirectory(at: real, withIntermediateDirectories: true)
        try "# R\n".write(to: real.appending(path: "r.md"), atomically: true, encoding: .utf8)
        try FileManager.default.createSymbolicLink(
            at: dir.url.appending(path: "alias"), withDestinationURL: real)
        await model.load(swe: SweClient(runner: runner))
        let paths = model.folders + allFilePaths(model.tree)
        #expect(!paths.contains { $0.hasPrefix("outside-link") })
        #expect(!paths.contains { $0 == "loop" || $0.hasPrefix("loop/") })
        #expect(paths.contains("real/r.md"))
        #expect(!paths.contains { $0.hasPrefix("alias") })
    }

    private func allFilePaths(_ nodes: [ChapterNode]) -> [String] {
        nodes.flatMap { $0.isFolder ? [$0.path] + allFilePaths($0.children) : [$0.path] }
    }

    @Test func aDirtyDraftOfADeletedFileStaysSelectedAsAGhost() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url), check(dir.url)])
        let model = try await model(dir, runner: runner)
        model.selected = "a.md"
        let chapter = InstructionsCheck.Chapter(
            name: "a.md", path: dir.path("a.md").path, exists: true)
        let document = try #require(model.document(for: chapter))
        await document.load()
        document.text = "# Unsaved draft\n"
        try FileManager.default.removeItem(at: dir.path("a.md"))
        await model.load(swe: SweClient(runner: runner))
        #expect(model.selected == "a.md")
        #expect(model.ghosts == ["a.md"])
        #expect(model.document(for: chapter)?.isDirty == true)
    }

    /// The async open the page uses: the same cached document as the
    /// sync one, and nil for a path that leaves the folder.
    @Test func openingAChapterSharesTheDocumentAndRefusesAnEscape() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        let chapter = InstructionsCheck.Chapter(
            name: "a.md", path: dir.path("a.md").path, exists: true)
        #expect(model.openDocument(named: "a.md") == nil)
        let opened = try #require(await model.opening(chapter))
        #expect(model.openDocument(named: "a.md") === opened)
        #expect(model.document(for: chapter) === opened)
        let escape = InstructionsCheck.Chapter(name: "../x.md", path: dir.path("../x.md").path, exists: true)
        #expect(await model.opening(escape) == nil)
    }

    @Test func createChapterCleansItsFileWhenTheIndexSaveFails() async throws {
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        // Break index.txt's save by turning it into a directory.
        try FileManager.default.removeItem(at: dir.path("index.txt"))
        try FileManager.default.createDirectory(
            at: dir.path("index.txt"), withIntermediateDirectories: false)
        let problem = await model.createChapter(
            folder: "", file: "retry-me", include: true,
            swe: SweClient(runner: runner))
        #expect(problem != nil)
        #expect(!FileManager.default.fileExists(atPath: dir.path("retry-me.md").path))
    }

    @Test func leadingDotsAreRejectedEverywhere() async throws {
        #expect(ChapterIndex.folderProblem(".hidden") != nil)
        #expect(ChapterIndex.folderProblem("eng/.secret") != nil)
        let dir = try TempDir()
        let runner = Runner([check(dir.url)])
        let model = try await model(dir, runner: runner)
        #expect(await model.createChapter(
            folder: "", file: ".hidden", include: true,
            swe: SweClient(runner: runner)) != nil)
    }

    @Test func uniqueChaptersDedupsADoubleListing() {
        let chapters = [
            InstructionsCheck.Chapter(name: "a.md", path: "/x/a.md", exists: true),
            InstructionsCheck.Chapter(name: "a.md", path: "/x/a.md", exists: true),
            InstructionsCheck.Chapter(name: "b.md", path: "/x/b.md", exists: true),
        ]
        let state = InstructionsCheck(
            directory: "/x", index: "/x/index.txt", output: "/AGENTS.md",
            indexExists: true, outputExists: true, outputGenerated: true,
            chapters: chapters, error: "duplicated", stale: false)
        #expect(state.uniqueChapters.map(\.name) == ["a.md", "b.md"])
    }
}
