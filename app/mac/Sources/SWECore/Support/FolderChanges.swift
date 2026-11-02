import CoreServices
import Foundation

/// Changes to files under a folder, as an async stream: one element for
/// each burst of file system events that touches a path `matches` accepts.
/// `matches` gets the path relative to `root` (`AGENTS_MD/01-working.md`).
/// A burst is everything that landed within `latency` seconds, so a build
/// that rewrites ten files wakes the consumer once.
///
/// Watching runs on a queue of its own and stops when the stream ends: a
/// SwiftUI `.task` that loops over it watches only while its view is on
/// screen.
///
/// Pages that show what a terminal can change under `~/.swe` (the generated
/// AGENTS.md's state, the hub's git status) follow it this way instead of
/// waiting for the app to come to the front. Keep `matches` to the paths
/// that decide what the page shows: `data/`, `cache/` and `.git/index` are
/// written by the app's own reads, and a filter that lets them through
/// reloads in a loop.
public enum FolderChanges {
    public static func stream(root: URL, latency: TimeInterval = 0.5,
                              matches: @escaping @Sendable (String) -> Bool) -> AsyncStream<Void> {
        AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            let watch = Watch(root: root, matches: matches, continuation: continuation)
            guard watch.start(latency: latency) else {
                continuation.finish()
                return
            }
            continuation.onTermination = { _ in watch.stop() }
        }
    }

    /// The event stream and what its callback needs, kept alive by the
    /// stream's termination handler until watching stops.
    private final class Watch: @unchecked Sendable {
        let prefix: String
        let matches: @Sendable (String) -> Bool
        let continuation: AsyncStream<Void>.Continuation
        private let queue = DispatchQueue(label: "swe.folder-changes")
        private var stream: FSEventStreamRef?

        init(root: URL, matches: @escaping @Sendable (String) -> Bool,
             continuation: AsyncStream<Void>.Continuation) {
            // Events name real paths (/private/var/..., a linked ~/.swe's
            // target), so compare against the root as realpath(3) gives it.
            // URL.resolvingSymlinksInPath() won't do: it drops /private.
            prefix = Watch.realPath(root.path) + "/"
            self.matches = matches
            self.continuation = continuation
        }

        static func realPath(_ path: String) -> String {
            guard let resolved = realpath(path, nil) else { return path }
            defer { free(resolved) }
            return String(cString: resolved)
        }

        func start(latency: TimeInterval) -> Bool {
            var context = FSEventStreamContext(
                version: 0, info: Unmanaged.passUnretained(self).toOpaque(),
                retain: nil, release: nil, copyDescription: nil)
            let flags = UInt32(kFSEventStreamCreateFlagUseCFTypes | kFSEventStreamCreateFlagFileEvents)
            guard let stream = FSEventStreamCreate(
                nil, Watch.callback, &context, [String(prefix.dropLast())] as CFArray,
                FSEventStreamEventId(kFSEventStreamEventIdSinceNow), latency, flags) else { return false }
            self.stream = stream
            FSEventStreamSetDispatchQueue(stream, queue)
            return FSEventStreamStart(stream)
        }

        func stop() {
            queue.sync {
                guard let stream else { return }
                FSEventStreamStop(stream)
                FSEventStreamInvalidate(stream)
                FSEventStreamRelease(stream)
                self.stream = nil
            }
        }

        func handle(_ paths: [String]) {
            let relative = paths.compactMap { path in
                path.hasPrefix(prefix) ? String(path.dropFirst(prefix.count)) : nil
            }
            if relative.contains(where: matches) { continuation.yield() }
        }

        private static let callback: FSEventStreamCallback = { _, info, _, paths, _, _ in
            guard let info else { return }
            let watch = Unmanaged<Watch>.fromOpaque(info).takeUnretainedValue()
            let list = Unmanaged<CFArray>.fromOpaque(paths).takeUnretainedValue() as? [String] ?? []
            watch.handle(list)
        }
    }
}
