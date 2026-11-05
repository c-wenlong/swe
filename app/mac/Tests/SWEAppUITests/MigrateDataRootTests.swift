import Foundation
import Testing
@testable import SWEApp

/// The app's own `~/.quiver` → `~/.swe` move, run against a scratch home.
@Suite @MainActor struct MigrateDataRootTests {
    private let fm = FileManager.default

    private func link(_ url: URL) -> String? { try? fm.destinationOfSymbolicLink(atPath: url.path) }

    @Test func movesARealOldRootAndLeavesALinkBehind() throws {
        let home = try TempDir()
        let old = home.url.appending(path: ".quiver")
        try fm.createDirectory(at: old, withIntermediateDirectories: true)
        try "x".write(to: old.appending(path: "harness.json"), atomically: true, encoding: .utf8)

        AppModel.migrateDataRoot(home: home.url)

        #expect(fm.fileExists(atPath: home.url.appending(path: ".swe/harness.json").path))
        #expect(link(old) == ".swe")
    }

    @Test func anExistingNewRootIsLeftAlone() throws {
        let home = try TempDir()
        try fm.createDirectory(at: home.url.appending(path: ".quiver"), withIntermediateDirectories: true)
        try fm.createDirectory(at: home.url.appending(path: ".swe"), withIntermediateDirectories: true)

        AppModel.migrateDataRoot(home: home.url)

        #expect(link(home.url.appending(path: ".quiver")) == nil, "the old root stays a directory")
    }

    @Test func aLinkedOldRootIsMirrored() throws {
        let home = try TempDir()
        let elsewhere = home.url.appending(path: "Dropbox/quiver-data")
        try fm.createDirectory(at: elsewhere, withIntermediateDirectories: true)
        try fm.createSymbolicLink(atPath: home.url.appending(path: ".quiver").path,
                                  withDestinationPath: elsewhere.path)

        AppModel.migrateDataRoot(home: home.url)

        #expect(link(home.url.appending(path: ".swe")) == elsewhere.path)
        #expect(fm.fileExists(atPath: elsewhere.path))
    }

    /// After a move, `~/.quiver` → `.swe`. Deleting `~/.swe` to start
    /// over must not turn it into a link to itself.
    @Test func theLeftoverLinkAfterDeletingTheNewRootIsNotMirrored() throws {
        let home = try TempDir()
        let new = home.url.appending(path: ".swe")
        try fm.createSymbolicLink(atPath: home.url.appending(path: ".quiver").path, withDestinationPath: ".swe")

        AppModel.migrateDataRoot(home: home.url)

        #expect(link(new) == nil, "~/.swe is not a link to itself")
        #expect((try? fm.attributesOfItem(atPath: new.path)) == nil)
        try fm.createDirectory(at: new.appending(path: "cache"), withIntermediateDirectories: true)
        #expect(fm.fileExists(atPath: new.appending(path: "cache").path), "a fresh ~/.swe can be made")
    }

    /// ~/link → <elsewhere>/child and ~/.quiver → link/../.swe: the
    /// target is <elsewhere>/.swe, not ~/.swe, so it is mirrored.
    @Test func aLinkThroughALinkedFolderIsStillMirrored() throws {
        let home = try TempDir()
        let elsewhere = try TempDir()
        try fm.createDirectory(at: elsewhere.url.appending(path: "child"), withIntermediateDirectories: true)
        try fm.createDirectory(at: elsewhere.url.appending(path: ".swe"), withIntermediateDirectories: true)
        try fm.createSymbolicLink(atPath: home.url.appending(path: "link").path,
                                  withDestinationPath: elsewhere.url.appending(path: "child").path)
        try fm.createSymbolicLink(atPath: home.url.appending(path: ".quiver").path,
                                  withDestinationPath: "link/../.swe")

        AppModel.migrateDataRoot(home: home.url)

        #expect(link(home.url.appending(path: ".swe")) == "link/../.swe")
    }

    @Test func anAbsoluteLinkToTheNewRootIsNotMirroredEither() throws {
        let home = try TempDir()
        let new = home.url.appending(path: ".swe")
        try fm.createSymbolicLink(atPath: home.url.appending(path: ".quiver").path,
                                  withDestinationPath: new.path + "/")

        AppModel.migrateDataRoot(home: home.url)

        #expect(link(new) == nil)
    }
}
