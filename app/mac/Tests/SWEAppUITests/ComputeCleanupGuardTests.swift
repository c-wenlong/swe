import Darwin
import Foundation
import SWECore
import Testing

/// The Compute page's Quit buttons and the clean-up sheet all end in
/// `ComputeModel.quit` / `quitHarness`. Whatever the process table looks
/// like, they must never signal SWE itself, the chain of processes it runs
/// under, launchd, or pid 0. These drive the model the way the page does,
/// with a fake process table and a signaller that only records.
@Suite @MainActor struct ComputeCleanupGuardTests {
    private static let selfPID: Int32 = 99
    private static let old = Date.now.addingTimeInterval(-30 * 3600)

    private final class Fake: ProcessSampling, ProcessSignaller, @unchecked Sendable {
        private let lock = NSLock()
        private var _table: [ProcessSample]
        private var _signalled: [Int32] = []
        init(_ table: [ProcessSample]) { _table = table }
        var signalled: [Int32] { lock.withLock { _signalled } }
        func sample() -> [ProcessSample] { lock.withLock { _table } }
        func signal(_ pid: Int32, _ sig: Int32) -> Bool {
            lock.withLock { _signalled.append(pid) }
            return true
        }
    }

    private static func proc(_ pid: Int32, _ ppid: Int32, _ path: String, _ args: [String] = [],
                             cwd: String? = nil) -> ProcessSample {
        ProcessSample(pid: pid, ppid: ppid, path: path, arguments: args.isEmpty ? [path] : args,
                      cwd: cwd, startedAt: old, threads: 1, memoryBytes: 1 << 20)
    }

    private nonisolated static let claude = HarnessMatcher(harness: "claude", commands: ["claude"],
                                               homeFolder: NSHomeDirectory() + "/.claude")
    private static let swePath = "/Applications/SWE.app/Contents/MacOS/SWE"

    private func model(_ fake: Fake) async -> ComputeModel {
        let model = ComputeModel(sampler: fake, matchers: { [Self.claude] }, signaller: fake,
                                 gpu: NoGPU(), temperature: NoTemperature(),
                                 defaults: TestDefaults("compute-guard"), selfPID: Self.selfPID)
        await model.loadMatchers()
        await model.refresh()
        return model
    }

    /// Never SWE, any ancestor, launchd or pid 0.
    private func expectSpared(_ fake: Fake, ancestors: Set<Int32>,
                              sourceLocation: SourceLocation = #_sourceLocation) {
        let forbidden = ancestors.union([Self.selfPID, 0, 1])
        let hit = Set(fake.signalled).intersection(forbidden)
        #expect(hit.isEmpty, "signalled \(hit.sorted())", sourceLocation: sourceLocation)
        #expect(!fake.signalled.contains { $0 <= 1 }, sourceLocation: sourceLocation)
    }

    /// A dev run of SWE from a terminal inside a Claude session: SWE hangs
    /// under the harness, so "Quit all" for Claude must stop short of it
    /// and of the shell and harness process it runs under.
    @Test func quitAllForTheHarnessSWERunsUnderSparesItsChain() async {
        let fake = Fake([
            Self.proc(0, 0, "kernel_task"),
            Self.proc(1, 0, "/sbin/launchd"),
            Self.proc(10, 1, "/opt/homebrew/bin/claude", ["claude"]),
            Self.proc(20, 10, "/bin/zsh", ["-zsh"]),
            Self.proc(99, 20, Self.swePath),
            Self.proc(30, 10, "/opt/homebrew/bin/node", ["node", "mcp-server.js"]),
        ])
        let model = await model(fake)
        await model.quitHarness("claude")
        await model.quitHarness("claude", force: true)
        expectSpared(fake, ancestors: [20, 10])
        #expect(fake.signalled.contains(30), "the harness's other helpers still quit")
    }

    /// Everything the clean-up sheet offers, all ticked: an orphaned
    /// script runner SWE was launched from stays.
    @Test func cleanupOfEveryItemSparesAnOrphanedLauncherOfSWE() async {
        let fake = Fake([
            Self.proc(1, 0, "/sbin/launchd"),
            Self.proc(50, 1, "/opt/homebrew/bin/npm", ["npm", "run", "app"], cwd: "/Users/test/proj"),
            Self.proc(51, 50, "/bin/sh", ["sh", "-c", "open-swe"]),
            Self.proc(99, 51, Self.swePath),
            Self.proc(60, 1, "/opt/homebrew/bin/npm", ["npm", "run", "dev"], cwd: "/Users/test/other"),
            Self.proc(61, 60, "/opt/homebrew/bin/node", ["node", "server.js"]),
        ])
        let model = await model(fake)
        await model.quit(model.cleanup)
        await model.quit(model.cleanup, force: true)
        for item in model.cleanup { await model.quit(item, force: true) }
        expectSpared(fake, ancestors: [51, 50])
        #expect(Set(fake.signalled).isSuperset(of: [60, 61]), "the unrelated orphan tree still quits")
    }

    /// A process table where SWE's pid itself shows up as the root of an
    /// orphan tree (started by launchd, an npm-style runner name).
    @Test func SWEAsAnOrphanRootIsNeverSignalled() async {
        let fake = Fake([
            Self.proc(1, 0, "/sbin/launchd"),
            Self.proc(99, 1, "/opt/homebrew/bin/node", ["node", "swe-dev.js"], cwd: "/Users/test/swe"),
            Self.proc(100, 99, "/opt/homebrew/bin/node", ["node", "worker.js"]),
        ])
        let model = await model(fake)
        #expect(!model.cleanup.isEmpty, "the tree is offered, so the guard is what holds")
        await model.quit(model.cleanup, force: true)
        expectSpared(fake, ancestors: [])
    }

    /// A parent loop in the table (pid reuse caught mid-sample) can't make
    /// the ancestor walk hang or wrongly clear anything.
    @Test func aParentLoopStillSparesSWE() async {
        let fake = Fake([
            Self.proc(1, 0, "/sbin/launchd"),
            Self.proc(10, 99, "/opt/homebrew/bin/claude", ["claude"]),
            Self.proc(99, 10, Self.swePath),
        ])
        let model = await model(fake)
        await model.quitHarness("claude", force: true)
        await model.quit(model.cleanup, force: true)
        expectSpared(fake, ancestors: [10])
    }
}

private struct NoGPU: GPUSampling {
    func times() -> [Int32: UInt64] { [:] }
}

private struct NoTemperature: TemperatureSampling {
    func read() -> TemperatureReading? { nil }
}
