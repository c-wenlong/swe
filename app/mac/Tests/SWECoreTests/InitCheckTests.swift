import Foundation
import Testing
@testable import SWECore

/// `swe init --check --json` and the plain `swe init` it previews: the
/// reply's decode (with the keys a harness may leave out) and the verdict
/// on whether a plain `init` has anything to link. All values are made up.
@Suite struct InitCheckTests {
    private func reply(_ body: String) -> FakeRunner {
        FakeRunner(.init(status: 0, stdout: Data(#"{"schema": 1, "data": \#(body)}"#.utf8), stderr: ""))
    }

    private func link(_ state: LinkState) -> InitCheck.Link { .init(path: "/h/.x", state: state) }

    private func check(instructions states: [LinkState?], skills: LinkState? = nil) -> InitCheck {
        InitCheck(
            agentsFile: "/h/.swe/AGENTS.md", agentsFileExists: true,
            harnesses: states.enumerated().map { index, state in
                .init(name: "h\(index)", instructions: state.map(link), skills: skills.map(link))
            })
    }

    @Test func aHarnessMayLeaveOutEitherLinkAndAnUnknownStateStillDecodes() async throws {
        let runner = reply(#"""
        {"agents_file": "/h/.swe/AGENTS.md", "agents_file_exists": false,
         "harnesses": [
          {"name": "alpha", "instructions": {"path": "/h/.alpha/AGENTS.md", "state": "create"}},
          {"name": "beta", "skills": {"path": "/h/.beta/skills", "state": "linked"}},
          {"name": "gamma"},
          {"name": "delta", "instructions": {"path": "/h/.delta/AGENTS.md", "state": "brand-new"}}
         ]}
        """#)
        let check = try await SweClient(runner: runner).initCheck()
        #expect(runner.calls == [["init", "--check", "--json"]])
        #expect(check.agentsFileExists == false)
        #expect(check.harnesses.map(\.id) == ["alpha", "beta", "gamma", "delta"])
        #expect(check.harnesses[0].skills == nil)
        #expect(check.harnesses[1].instructions == nil)
        #expect(check.harnesses[2].instructions == nil && check.harnesses[2].skills == nil)
        // A state a newer swe adds is kept as written, and titled from it.
        let unknown = try #require(check.harnesses[3].instructions?.state)
        #expect(unknown.rawValue == "brand-new")
        #expect(unknown.title == "Brand-new")
        #expect(!unknown.initFixes && !unknown.isBlocked)
    }

    @Test func instructionRowsAreTheHarnessesThatTakeTheSharedFile() {
        let state = check(instructions: [.linked, nil, .create, nil])
        #expect(state.instructionRows.map(\.name) == ["h0", "h2"])
    }

    @Test func aPlainInitHasWorkOnlyForStatesItResolvesItself() {
        for fixable in [LinkState.create, .relink, .absorb] {
            #expect(check(instructions: [.linked, fixable]).instructionsNeedInit, "\(fixable.rawValue)")
        }
        // Linked, blocked, skipped and archived rows need no plain `init`
        // (a blocked one waits for `--force`, which asks first).
        for settled in [LinkState.linked, .keep, .conflict, .skipped, .ignored] {
            #expect(!check(instructions: [.linked, settled]).instructionsNeedInit, "\(settled.rawValue)")
        }
        #expect(!check(instructions: []).instructionsNeedInit)
    }

    @Test func onlyTheInstructionLinkDecidesTheVerdict() {
        // A skills link `init` would create does not make instructions need init...
        #expect(!check(instructions: [.linked, nil], skills: .create).instructionsNeedInit)
        // ...and a harness with no instruction link is not counted as one that does.
        #expect(!check(instructions: [nil], skills: .relink).instructionsNeedInit)
    }

    @Test func blockedStatesAreTheOnesNeedingForce() {
        #expect(LinkState.conflict.isBlocked && LinkState.keep.isBlocked)
        for other in [LinkState.linked, .relink, .create, .absorb, .skipped, .ignored] {
            #expect(!other.isBlocked, "\(other.rawValue)")
        }
    }

    @Test func everyKnownStateHasItsPageWording() {
        let titles: [(LinkState, String)] = [
            (.linked, "Linked"), (.relink, "Points elsewhere"), (.create, "Not linked"),
            (.absorb, "Copy to absorb"), (.keep, "Only copy, kept"), (.conflict, "File in the way"),
            (.skipped, "Not installed"), (.ignored, "Archived"),
        ]
        for (state, title) in titles { #expect(state.title == title) }
    }

    @Test func aLinkStateRoundTripsAsAPlainString() throws {
        let data = try JSONEncoder().encode([LinkState.relink])
        #expect(String(decoding: data, as: UTF8.self) == #"["relink"]"#)
        #expect(try JSONDecoder().decode([LinkState].self, from: data) == [.relink])
    }

    @Test func runInitSendsForceOnlyWhenAsked() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: Data(), stderr: ""))
        let swe = SweClient(runner: runner)
        try await swe.runInit()
        try await swe.runInit(force: true)
        #expect(runner.calls == [["init"], ["init", "--force"]])
    }

    @Test func aFailedInitThrowsWithTheClearestMessage() async {
        let withStderr = FakeRunner(.init(status: 2, stdout: Data("partial".utf8), stderr: "\u{1B}[31mno write access\u{1B}[0m\n"))
        await #expect(throws: SweError.commandFailed(status: 2, message: "no write access")) {
            try await SweClient(runner: withStderr).runInit()
        }
        // With nothing on stderr, the output is the message.
        let stdoutOnly = FakeRunner(.init(status: 1, stdout: Data("refused: AGENTS.md missing\n".utf8), stderr: ""))
        await #expect(throws: SweError.commandFailed(status: 1, message: "refused: AGENTS.md missing")) {
            try await SweClient(runner: stdoutOnly).runInit(force: true)
        }
    }

    @Test func aMissingSweIsNotInstalledRatherThanAFailure() async {
        let runner = FakeRunner(.init(status: 127, stdout: Data(), stderr: "env: swe: No such file or directory\n"))
        await #expect(throws: SweError.notInstalled("swe")) {
            try await SweClient(runner: runner).runInit()
        }
        // A 127 that is not env's message stays an ordinary failure.
        let other = FakeRunner(.init(status: 127, stdout: Data(), stderr: "command not found"))
        await #expect(throws: SweError.commandFailed(status: 127, message: "command not found")) {
            try await SweClient(runner: other).runInit()
        }
    }
}
