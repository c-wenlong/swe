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
        var checkCalls = 0
        var buildStatus: Int32 = 0

        init(_ checks: [String]) { self.checks = checks }

        func run(_ arguments: [String]) async throws -> CommandResult {
            let data: String
            let status: Int32
            if arguments.contains("build") {
                builds += 1
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
