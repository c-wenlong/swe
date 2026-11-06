import Foundation
import Testing
@testable import SWEApp

/// The relaunch script swaps the installed app for a fresh build after the
/// app quits. It runs `mv` and `rm -rf` on paths it is handed, so these
/// runs check it against a scratch HOME: the swap lands, every failure
/// leaves the installed app in place, and nothing outside the two bundles
/// and the one rebuild folder is ever removed. `open` is a stand-in that
/// prints what it was asked to open.
@Suite struct AppRelauncherScriptTests {
    /// Above any real pid, so `kill -0` says the app has already quit.
    private static let deadPID = "99999999"

    private struct Run {
        let status: Int32
        let opened: [String]
        let stderr: String
    }

    private struct Scratch {
        let dir: TempDir
        var home: URL { dir.url.appending(path: "home") }
        var applications: URL { dir.url.appending(path: "Applications") }
        var rebuild: URL { home.appending(path: ".swe/cache/rebuild") }

        init() throws {
            dir = try TempDir()
            let fm = FileManager.default
            try fm.createDirectory(at: home.appending(path: ".Trash"), withIntermediateDirectories: true)
            try fm.createDirectory(at: rebuild, withIntermediateDirectories: true)
            try fm.createDirectory(at: applications, withIntermediateDirectories: true)
            // `open` is a link to /bin/echo, so each path the script opens
            // is a line on its stdout. A stub script written here would be
            // a fresh executable, and macOS checks one before its first
            // run: on a loaded machine that held the stub in `_dyld_start`
            // for minutes. The script prints nothing else to stdout.
            let bin = dir.url.appending(path: "bin")
            try fm.createDirectory(at: bin, withIntermediateDirectories: true)
            try fm.createSymbolicLink(at: bin.appending(path: "open"),
                                      withDestinationURL: URL(fileURLWithPath: "/bin/echo"))
        }

        /// A stand-in app bundle whose one file says which copy it is.
        @discardableResult
        func app(at url: URL, marker: String) throws -> URL {
            try FileManager.default.createDirectory(
                at: url.appending(path: "Contents"), withIntermediateDirectories: true)
            try marker.write(to: url.appending(path: "Contents/marker"), atomically: true, encoding: .utf8)
            return url
        }

        func marker(_ url: URL) -> String? {
            try? String(contentsOf: url.appending(path: "Contents/marker"), encoding: .utf8)
        }

        func trashed() -> [String] {
            (try? FileManager.default.contentsOfDirectory(atPath: home.appending(path: ".Trash").path)) ?? []
        }

        /// Runs the script to its end, or for at most `gateTimeout`: past
        /// that the script and everything it started are killed and the
        /// test fails, rather than hang the suite on a stalled child.
        func run(src: String, dest: String) throws -> Run {
            let script = dir.url.appending(path: "relaunch-\(UUID().uuidString).sh")
            try AppRelauncher.script.write(to: script, atomically: true, encoding: .utf8)
            let task = Process()
            task.executableURL = URL(fileURLWithPath: "/bin/sh")
            task.arguments = [script.path, AppRelauncherScriptTests.deadPID, src, dest]
            task.environment = [
                "HOME": home.path,
                "PATH": dir.url.appending(path: "bin").path + ":/usr/bin:/bin",
            ]
            let out = Pipe()
            let err = Pipe()
            task.standardOutput = out
            task.standardError = err
            let ended = DispatchSemaphore(value: 0)
            task.terminationHandler = { _ in ended.signal() }
            // Both pipes drain while the script runs, so a full pipe never
            // blocks it; each read ends once every writer has gone.
            let outText = PipeText(out)
            let errText = PipeText(err)
            try task.run()

            let (seconds, attoseconds) = gateTimeout.components
            let deadline = DispatchTime.now() + Double(seconds) + Double(attoseconds) / 1e18
            if ended.wait(timeout: deadline) == .timedOut {
                killTree(task.processIdentifier)
                ended.wait()
                Issue.record("the relaunch script was still running after \(gateTimeout); killed it")
            }
            let opened = (outText.wait(until: deadline) ?? "")
                .split(separator: "\n").map(String.init)
            #expect(!FileManager.default.fileExists(atPath: script.path), "the script removes itself")
            return Run(status: task.terminationStatus, opened: opened,
                       stderr: errText.wait(until: deadline) ?? "")
        }
    }

    /// A pipe's text, read to its end on a background queue.
    private final class PipeText: @unchecked Sendable {
        private let done = DispatchSemaphore(value: 0)
        private var text = ""

        init(_ pipe: Pipe) {
            let handle = pipe.fileHandleForReading
            DispatchQueue.global().async {
                self.text = String(decoding: handle.readDataToEndOfFile(), as: UTF8.self)
                self.done.signal()
            }
        }

        /// The text, or nil if the pipe was still open at `deadline` (a
        /// child that outlived the kill still holds it). The semaphore
        /// orders the write before this read.
        func wait(until deadline: DispatchTime) -> String? {
            guard done.wait(timeout: max(deadline, .now())) == .success else { return nil }
            return text
        }
    }

    @Test func swapsTheBuildInAndTrashesTheOldCopy() throws {
        let s = try Scratch()
        let dest = try s.app(at: s.applications.appending(path: "SWE.app"), marker: "old")
        let buildDir = s.rebuild.appending(path: "b1")
        let src = try s.app(at: buildDir.appending(path: "SWE.app"), marker: "new")

        let run = try s.run(src: src.path, dest: dest.path)

        #expect(run.status == 0, "\(run.stderr)")
        #expect(s.marker(dest) == "new")
        #expect(run.opened == [dest.path])
        let trashed = s.trashed()
        #expect(trashed.count == 1)
        #expect(trashed.first.map { $0.hasPrefix("SWE ") && $0.hasSuffix(".app") } == true)
        #expect(trashed.first.flatMap { s.marker(s.home.appending(path: ".Trash/\($0)")) } == "old")
        #expect(!FileManager.default.fileExists(atPath: buildDir.path), "the per-build folder is cleaned up")
        #expect(FileManager.default.fileExists(atPath: s.rebuild.path), "the cache itself stays")
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: s.applications.path)
        #expect(leftovers == ["SWE.app"], "no .new copy is left next to the app")
    }

    /// Spaces, quotes, `$` and backticks in a path stay data: the swap
    /// still lands, and nothing in a path runs as shell.
    @Test func shellCharactersInPathsAreData() throws {
        let s = try Scratch()
        let odd = s.dir.url.appending(path: #"My "$(touch pwned)" `touch pwned2` it's Apps"#)
        let dest = try s.app(at: odd.appending(path: "SWE.app"), marker: "old")
        let src = try s.app(at: s.rebuild.appending(path: "b 2").appending(path: "SWE.app"), marker: "new")

        let run = try s.run(src: src.path, dest: dest.path)

        #expect(run.status == 0, "\(run.stderr)")
        #expect(s.marker(dest) == "new")
        #expect(run.opened == [dest.path])
        let names = try FileManager.default.contentsOfDirectory(atPath: s.dir.url.path)
            + FileManager.default.contentsOfDirectory(atPath: s.home.path)
        #expect(!names.contains { $0.hasPrefix("pwned") })
    }

    /// The installed app can live anywhere, not just /Applications; a
    /// build from outside the rebuild cache is copied but never removed.
    @Test func buildOutsideTheRebuildCacheIsLeftAlone() throws {
        let s = try Scratch()
        let dest = try s.app(at: s.dir.url.appending(path: "elsewhere/SWE.app"), marker: "old")
        let checkout = s.dir.url.appending(path: "checkout/build")
        let src = try s.app(at: checkout.appending(path: "SWE.app"), marker: "new")

        let run = try s.run(src: src.path, dest: dest.path)

        #expect(run.status == 0, "\(run.stderr)")
        #expect(s.marker(dest) == "new")
        #expect(s.marker(src) == "new", "a build outside the cache is never removed")
        #expect(!FileManager.default.fileExists(atPath: checkout.appending(path: "owner.pid").path))
    }

    /// A build path that walks up out of the cache must not make the
    /// cleanup remove the cache's parent.
    @Test func dotDotBuildFolderNeverRemovesTheCacheParent() throws {
        let s = try Scratch()
        let dest = try s.app(at: s.applications.appending(path: "SWE.app"), marker: "old")
        let cache = s.rebuild.deletingLastPathComponent()
        try s.app(at: cache.appending(path: "SWE.app"), marker: "new")
        let sentinel = cache.appending(path: "keep")
        try "x".write(to: sentinel, atomically: true, encoding: .utf8)

        let run = try s.run(src: s.rebuild.path + "/../SWE.app", dest: dest.path)

        #expect(run.status == 0, "\(run.stderr)")
        #expect(s.marker(dest) == "new")
        #expect(FileManager.default.fileExists(atPath: sentinel.path))
        #expect(FileManager.default.fileExists(atPath: s.rebuild.path))
        #expect(!FileManager.default.fileExists(atPath: s.rebuild.appending(path: "../owner.pid").path))
    }

    /// Run unbundled, Bundle.main.bundleURL is the folder holding the
    /// binary: the script refuses rather than trash that folder.
    @Test func refusesADestinationThatIsNotAnAppBundle() throws {
        let s = try Scratch()
        let folder = s.dir.url.appending(path: "debug")
        try s.app(at: folder, marker: "binary folder")
        let src = try s.app(at: s.rebuild.appending(path: "b1/SWE.app"), marker: "new")

        let run = try s.run(src: src.path, dest: folder.path)

        #expect(run.status != 0)
        #expect(s.marker(folder) == "binary folder")
        #expect(s.trashed().isEmpty)
        #expect(s.marker(src) == "new", "the build stays for another try")
        #expect(run.opened.isEmpty)
    }

    @Test func refusesASourceThatIsNotAnAppBundle() throws {
        let s = try Scratch()
        let dest = try s.app(at: s.applications.appending(path: "SWE.app"), marker: "old")
        let folder = try s.app(at: s.rebuild.appending(path: "b1/notanapp"), marker: "new")

        let run = try s.run(src: folder.path, dest: dest.path)

        #expect(run.status != 0)
        #expect(s.marker(dest) == "old")
        #expect(s.trashed().isEmpty)
        #expect(run.opened == [dest.path], "the old app comes back")
    }

    /// A copy that fails leaves the installed app exactly as it was and
    /// reopens it.
    @Test func failedCopyKeepsTheInstalledApp() throws {
        let s = try Scratch()
        let dest = try s.app(at: s.applications.appending(path: "SWE.app"), marker: "old")
        let missing = s.rebuild.appending(path: "gone/SWE.app")

        let run = try s.run(src: missing.path, dest: dest.path)

        #expect(run.status != 0)
        #expect(s.marker(dest) == "old")
        #expect(s.trashed().isEmpty)
        #expect(run.opened == [dest.path])
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: s.applications.path)
        #expect(leftovers == ["SWE.app"])
    }

    @Test func sameSourceAndDestinationJustReopens() throws {
        let s = try Scratch()
        let dest = try s.app(at: s.applications.appending(path: "SWE.app"), marker: "old")

        let run = try s.run(src: dest.path, dest: dest.path)

        #expect(run.status == 0)
        #expect(s.marker(dest) == "old")
        #expect(s.trashed().isEmpty)
        #expect(run.opened == [dest.path])
    }
}

/// SIGKILLs `pid` and every process under it, leaves first, so nothing the
/// script started reparents to launchd and keeps running. Called as soon
/// as the wait on `pid` runs out, while the script is still running.
private func killTree(_ pid: pid_t) {
    var tree: [pid_t] = []
    var queue = [pid]
    while let parent = queue.popLast() {
        tree.append(parent)
        var children = [pid_t](repeating: 0, count: 256)
        let count = proc_listchildpids(parent, &children,
                                       Int32(MemoryLayout<pid_t>.stride * children.count))
        guard count > 0 else { continue }
        queue += children.prefix(min(Int(count), children.count)).filter { $0 > 0 }
    }
    for member in tree.reversed() { kill(member, SIGKILL) }
}
