import Foundation
import Testing
@testable import SWECore

/// The Accounts page's "Shared content" switches against
/// `swe init --accounts --json`, captured from a real swe run in a
/// throwaway HOME (skills and instructions on, one account's CLAUDE.md
/// already its own).
@MainActor
@Suite struct AccountSyncTests {
    private func model() throws -> (AccountSyncModel, FakeRunner) {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("swe-init-accounts"), stderr: ""))
        return (AccountSyncModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func reportDecodesSwitchesRowsAndConflicts() async throws {
        let (sync, runner) = try model()
        await sync.load()
        #expect(runner.calls == [["init", "--accounts", "--check", "--json"]])
        #expect(sync.isOn(.skills))
        #expect(sync.isOn(.instructions))
        #expect(!sync.isOn(.hooks))
        let report = try #require(sync.report)
        #expect(report.accounts.count == 2)
        #expect(report.conflicts.map(\.primitive) == ["instructions"])
        #expect(sync.error == nil)
    }

    @Test func flippingASwitchSavesItThenRunsThePass() async throws {
        let (sync, runner) = try model()
        await sync.set(.hooks, on: true)
        #expect(runner.calls == [
            ["config", "set", "accounts.sync.hooks", "true"],
            ["init", "--accounts", "--json"],
        ])
        #expect(!sync.busy)
    }

    @Test func aNewAccountIsSyncedOnlyWhenASwitchIsOn() async throws {
        let (sync, runner) = try model()
        await sync.syncIfOn()
        #expect(runner.calls == [
            ["init", "--accounts", "--check", "--json"],
            ["init", "--accounts", "--json"],
        ])

        let off = FakeRunner(.init(status: 0, stdout: Data(
            #"{"schema": 1, "data": {"account_sync": {"instructions": false, "skills": false, "hooks": false}, "accounts": [], "notes": []}}"#
                .utf8), stderr: ""))
        let idle = AccountSyncModel(swe: SweClient(runner: off))
        await idle.syncIfOn()
        #expect(off.calls == [["init", "--accounts", "--check", "--json"]])
    }

    @Test func aFailedSwitchKeepsItsReasonAfterTheReload() async throws {
        let runner = AccountSyncRunner(fixture: try fixture("swe-init-accounts"), holdConfig: false,
                                    configFails: true)
        let sync = AccountSyncModel(swe: SweClient(runner: runner))
        await sync.set(.hooks, on: true)
        #expect(sync.error?.contains("config.json is read-only") == true)
        #expect(sync.report != nil)
    }

    @Test func anAccountAddedDuringAPassGetsAPassOfItsOwn() async throws {
        let runner = AccountSyncRunner(fixture: try fixture("swe-init-accounts"), holdConfig: true,
                                    configFails: false)
        let sync = AccountSyncModel(swe: SweClient(runner: runner))
        await sync.load()
        let flip = Task { await sync.set(.hooks, on: true) }
        try #require(await runner.configGate.arrived(), "the switch never ran config set")
        #expect(sync.busy)
        await sync.syncIfOn()  // busy: queued, not dropped
        runner.configGate.open()
        await flip.value
        #expect(runner.calls.filter { $0 == ["init", "--accounts", "--json"] }.count == 2)
        #expect(!sync.busy)
    }

    @Test func mcpRowsNameTheirServerAndBlockedFilesShowApart() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: Data(#"""
            {"schema": 1, "data": {"account_sync": {"instructions": false, "skills": false, "hooks": false, "mcp": true},
             "accounts": [
              {"account": "claude/claude-work", "primitive": "mcp", "path": "/h/.claude.json", "state": "conflict",
               "detail": "server github: the account's own server, left alone", "name": "github"},
              {"account": "codex/codex-alt", "primitive": "mcp", "path": "/h/config.toml", "state": "blocked",
               "detail": "invalid TOML, not changed", "name": null},
              {"account": "claude/claude-work", "primitive": "skills", "path": "/h/skills", "state": "linked",
               "detail": ""}],
             "notes": []}}
            """#.utf8), stderr: ""))
        let sync = AccountSyncModel(swe: SweClient(runner: runner))
        await sync.load()
        #expect(sync.isOn(.mcp))
        let report = try #require(sync.report)
        #expect(report.conflicts.map(\.target) == ["github"])
        #expect(report.blocked.map(\.target) == ["config.toml"])
        #expect(report.accounts.last?.target == "skills")
    }

    @Test func aPassThatCouldNotRunShowsWhy() async throws {
        let runner = FakeRunner(.init(status: 1, stdout: Data(
            #"{"schema": 1, "data": {"account_sync": null, "accounts": [], "notes": ["account sync skipped: cannot read config.json"]}}"#
                .utf8), stderr: ""))
        let sync = AccountSyncModel(swe: SweClient(runner: runner))
        await sync.load()
        #expect(sync.error == "account sync skipped: cannot read config.json")
        #expect(sync.report?.accountSync == nil)
    }
}

/// The failure branches: `swe` refusing a read, a pass or a switch. The
/// runner answers by argv prefix, so each call can succeed or fail on its own.
@MainActor
@Suite struct AccountSyncFailureTests {
    private static let switchedOn = #"""
        {"account_sync": {"instructions": true, "skills": false, "hooks": false, "mcp": false},
         "accounts": [], "notes": []}
        """#

    private func failure(_ stderr: String, status: Int32 = 2) -> CommandResult {
        CommandResult(status: status, stdout: Data(), stderr: stderr)
    }

    private func model(_ rules: [([String], CommandResult)]) -> (AccountSyncModel, ScriptedRunner) {
        let runner = ScriptedRunner(rules)
        return (AccountSyncModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func aFailedFirstReadLeavesNoReportAndSaysWhy() async {
        let (sync, _) = model([(["init", "--accounts"], failure("swe could not open its config\n"))])
        await sync.load()
        #expect(sync.error == "swe could not open its config")
        #expect(sync.report == nil)
        #expect(!sync.anyOn && !sync.isOn(.skills))
    }

    @Test func aFailedReloadKeepsTheReportAlreadyShown() async {
        let (sync, runner) = model([(["init", "--accounts", "--check"], ok(Self.switchedOn))])
        await sync.load()
        #expect(sync.isOn(.instructions) && sync.error == nil)

        runner.set(["init", "--accounts", "--check"], failure("disk is full", status: 1))
        await sync.load()
        #expect(sync.error == "disk is full")
        #expect(sync.isOn(.instructions), "the switches from the last good read stay")
    }

    @Test func aMissingSweReadsAsNotInstalled() async {
        let (sync, _) = model([(["init", "--accounts"], failure("env: swe: No such file or directory\n", status: 127))])
        await sync.load()
        #expect(sync.error?.contains("Couldn't find swe") == true)
    }

    @Test func aNewAccountsFailedPassIsReportedAndLeavesTheModelFree() async {
        let (sync, runner) = model([
            (["init", "--accounts", "--check"], ok(Self.switchedOn)),
            (["init", "--accounts"], failure("cannot link CLAUDE.md\n")),
        ])
        await sync.syncIfOn()
        #expect(runner.calls == [["init", "--accounts", "--check", "--json"], ["init", "--accounts", "--json"]])
        #expect(sync.error == "cannot link CLAUDE.md")
        #expect(sync.isOn(.instructions), "the checked report is still what the switches show")
        #expect(!sync.busy)
    }

    @Test func aFailedReadMeansNoPassForTheNewAccount() async {
        let (sync, runner) = model([(["init", "--accounts"], failure("unreadable\n"))])
        await sync.syncIfOn()
        // Nothing is known to be on, so the pass does not run.
        #expect(runner.calls == [["init", "--accounts", "--check", "--json"]])
        #expect(sync.error == "unreadable")
        #expect(!sync.busy)
    }

    @Test func aSwitchWhoseSavedPassFailsRereadsButKeepsThePassesReason() async {
        let (sync, runner) = model([
            (["config", "set"], CommandResult(status: 0, stdout: Data(), stderr: "")),
            (["init", "--accounts", "--check"], ok(Self.switchedOn)),
            (["init", "--accounts"], failure("hooks dir is a file\n")),
        ])
        await sync.set(.hooks, on: true)
        #expect(runner.calls == [
            ["config", "set", "accounts.sync.hooks", "true"],
            ["init", "--accounts", "--json"],
            ["init", "--accounts", "--check", "--json"],
        ])
        #expect(sync.error == "hooks dir is a file")
        // The reload landed: the report is the fresh read, not nil.
        #expect(sync.isOn(.instructions))
        #expect(!sync.busy)
    }

    @Test func aRefusedConfigWriteStopsBeforeAnyPass() async {
        let (sync, runner) = model([
            (["config", "set"], failure("config.json is read-only\n", status: 1)),
            (["init", "--accounts", "--check"], ok(Self.switchedOn)),
        ])
        await sync.set(.skills, on: false)
        #expect(!runner.calls.contains(["init", "--accounts", "--json"]))
        #expect(runner.calls.first == ["config", "set", "accounts.sync.skills", "false"])
        #expect(sync.error == "config.json is read-only")
    }
}
