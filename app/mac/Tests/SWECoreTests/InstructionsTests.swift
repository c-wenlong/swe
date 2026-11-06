import Foundation
import Testing
@testable import SWECore

/// `swe instructions check|build|init --json`: the replies' decode with the
/// fields an older or half-set-up swe leaves out, and how the client reads a
/// failing run. The chapter rail's own behaviour is in `ChaptersModelTests`.
@Suite struct InstructionsTests {
    private func reply(_ body: String, status: Int32 = 0, stderr: String = "") -> FakeRunner {
        FakeRunner(.init(status: status, stdout: Data(#"{"schema": 1, "data": \#(body)}"#.utf8), stderr: stderr))
    }

    @Test func aFlatFileSetupDecodesWithNoStaleVerdictAndNoChapters() async throws {
        let runner = reply(#"""
        {"directory": "/h/.swe/AGENTS_MD", "index": "/h/.swe/AGENTS_MD/index.txt",
         "output": "/h/.swe/AGENTS.md", "index_exists": false, "output_exists": true,
         "output_generated": false, "chapters": [], "error": null, "stale": null}
        """#)
        let check = try await SweClient(runner: runner).instructionsCheck()
        #expect(runner.calls == [["instructions", "check", "--json"]])
        #expect(!check.indexExists && check.outputExists && !check.outputGenerated)
        #expect(check.stale == nil)
        #expect(check.error == nil)
        #expect(check.chapters.isEmpty && check.compiledNames.isEmpty)
    }

    @Test func aStaleOutputThatExitsOneStillReturnsItsDocument() async throws {
        let runner = reply(#"""
        {"directory": "/h/.swe/AGENTS_MD", "index": "/h/.swe/AGENTS_MD/index.txt",
         "output": "/h/.swe/AGENTS.md", "index_exists": true, "output_exists": true,
         "output_generated": true, "chapters": [], "error": "missing chapter: gone.md", "stale": true}
        """#, status: 1)
        let check = try await SweClient(runner: runner).instructionsCheck()
        #expect(check.stale == true)
        #expect(check.error == "missing chapter: gone.md")
    }

    @Test func aCheckThatFailsWithoutADocumentThrows() async {
        let runner = FakeRunner(.init(status: 1, stdout: Data(), stderr: "boom\n"))
        await #expect(throws: SweError.commandFailed(status: 1, message: "boom")) {
            try await SweClient(runner: runner).instructionsCheck()
        }
    }

    @Test func anOutputWithNoHarnessIsTheSharedFileAndChapterPinsMayBeMissing() throws {
        let json = #"""
        {"directory": "/x", "index": "/x/index.txt", "output": "/x/AGENTS.md",
         "index_exists": true, "output_exists": true, "output_generated": true,
         "chapters": [{"name": "a.md", "path": "/x/a.md", "exists": false},
                      {"name": "b.md", "path": "/x/b.md", "exists": true,
                       "only": ["claude"], "except": ["codex"]}],
         "entries": [{"name": "a.md", "enabled": true}, {"name": "off.md", "enabled": false}],
         "outputs": [{"harness": null, "path": "/x/AGENTS.md", "exists": true, "chapters": ["a.md"]},
                     {"path": "/x/AGENTS.shared.md", "exists": false, "chapters": []},
                     {"harness": "claude", "path": "/x/AGENTS.claude.md", "exists": true,
                      "chapters": ["a.md", "b.md"]}],
         "error": null, "stale": false}
        """#
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let check = try decoder.decode(InstructionsCheck.self, from: Data(json.utf8))
        // A chapter listed but absent still gets a row, without pins.
        #expect(check.chapters[0].exists == false)
        #expect(check.chapters[0].only == nil && check.chapters[0].except == nil)
        #expect(check.chapters[1].only == ["claude"] && check.chapters[1].except == ["codex"])
        // Only enabled entries compile; the off one holds its slot.
        #expect(check.compiledNames == ["a.md"])
        let outputs = try #require(check.outputs)
        #expect(outputs.map(\.harness) == [nil, nil, "claude"])
        #expect(outputs[1].exists == false && outputs[1].chapters.isEmpty)
        #expect(outputs[2].chapters == ["a.md", "b.md"])
    }

    @Test func buildReturnsWhetherTheOutputChanged() async throws {
        let changed = reply(#"{"changed": true}"#)
        #expect(try await SweClient(runner: changed).instructionsBuild() == true)
        #expect(changed.calls == [["instructions", "build", "--json"]])
        #expect(try await SweClient(runner: reply(#"{"changed": false}"#)).instructionsBuild() == false)
    }

    @Test func aRefusedBuildThrowsTheCompilersMessage() async {
        let runner = FakeRunner(.init(status: 1, stdout: Data(), stderr: "index.txt: ../escape.md leaves AGENTS_MD\n"))
        await #expect(throws: SweError.commandFailed(status: 1, message: "index.txt: ../escape.md leaves AGENTS_MD")) {
            try await SweClient(runner: runner).instructionsBuild()
        }
    }

    @Test func aBuildReplyMissingItsFlagIsMalformedNotSilentlyFalse() async {
        let runner = reply(#"{}"#)
        await #expect {
            try await SweClient(runner: runner).instructionsBuild()
        } throws: { error in
            guard case SweError.malformedOutput = error else { return false }
            return true
        }
    }

    @Test func initDecodesTheChapterItFoldedAndWhatItCameFrom() async throws {
        let runner = reply(#"""
        {"created": true, "chapter": "01-main.md", "source": "/h/.swe/AGENTS.md", "built": false}
        """#)
        let made = try await SweClient(runner: runner).instructionsInit()
        #expect(runner.calls == [["instructions", "init", "--json"]])
        #expect(made == InstructionsInitReply(
            created: true, chapter: "01-main.md", source: "/h/.swe/AGENTS.md", built: false))
    }

    @Test func initRefusesWhenChaptersExistAlready() async {
        let runner = FakeRunner(.init(status: 1, stdout: Data(), stderr: "chapters already exist\n"))
        await #expect(throws: SweError.commandFailed(status: 1, message: "chapters already exist")) {
            try await SweClient(runner: runner).instructionsInit()
        }
    }
}
