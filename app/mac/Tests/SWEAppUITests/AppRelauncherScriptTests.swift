import Foundation
import Testing
@testable import SWEApp

/// The relaunch script swaps the installed app for a fresh build after the
/// app quits. It runs `mv` and `rm -rf` on paths it is handed, so these
/// runs check it against a scratch HOME: the swap lands, every failure
/// leaves the installed app in place, and nothing outside the two bundles
/// and the one rebuild folder is ever removed. `open` is a stub that
/// records what it was asked to open.
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
            let bin = dir.url.appending(path: "bin")
            try fm.createDirectory(at: bin, withIntermediateDirectories: true)
            let open = bin.appending(path: "open")
            try "#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$HOME/opened\"\n"
                .write(to: open, atomically: true, encoding: .utf8)
            try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: open.path)
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
            let err = Pipe()
            task.standardError = err
            task.standardOutput = FileHandle.nullDevice
            try task.run()
            let errText = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            task.waitUntilExit()
            let opened = (try? String(contentsOf: home.appending(path: "opened"), encoding: .utf8))?
                .split(separator: "\n").map(String.init) ?? []
            #expect(!FileManager.default.fileExists(atPath: script.path), "the script removes itself")
            return Run(status: task.terminationStatus, opened: opened, stderr: errText)
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
