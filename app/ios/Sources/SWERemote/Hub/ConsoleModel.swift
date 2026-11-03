import Foundation
import Observation

/// Where the terminal view's bytes go: SwiftTerm's view, or a test sink.
@MainActor protocol ConsoleScreen: AnyObject {
    func feed(_ bytes: [UInt8])
}

/// One console: set up through the hub when it answers, then an SSH
/// shell straight to the machine's sshd, which needs no hub at all.
@MainActor @Observable
final class ConsoleModel {
    enum Phase: Equatable {
        case preparing
        /// The hub answered, but the machine isn't set up for the console.
        case needsSetup(SSHReadiness)
        case connecting
        case open
        /// The shell ended; nil is a plain logout.
        case closed(String?)
        case failed(String)
    }

    private(set) var phase: Phase = .preparing
    private(set) var target: SSHTarget?
    /// True when the hub didn't answer and the console runs on a saved target.
    private(set) var offlineTarget = false

    /// The terminal view, once it exists. Output that arrived before it
    /// (a fast login's banner and prompt) is fed to it then, in order.
    @ObservationIgnored weak var screen: ConsoleScreen? {
        didSet {
            guard let screen, !unshown.isEmpty else { return }
            screen.feed(unshown)
            unshown = []
        }
    }
    @ObservationIgnored private var unshown: [UInt8] = []
    @ObservationIgnored private var shell: SSHShell?
    /// One per attempt (start, reconnect, stop): anything an older attempt
    /// finishes later, a shell or its events, is dropped.
    @ObservationIgnored private var generation = 0
    /// The shell of this generation ended before `open` handed it over.
    @ObservationIgnored private var endedEarly: (generation: Int, error: SSHConsoleError?)?
    @ObservationIgnored private var size = (cols: 80, rows: 24)
    @ObservationIgnored private let store: SSHTargetStore
    @ObservationIgnored private let loadIdentity: () throws -> SSHIdentity

    init(store: SSHTargetStore = SSHTargetStore(),
         loadIdentity: @escaping () throws -> SSHIdentity = { try SSHIdentity.loadOrCreate() }) {
        self.store = store
        self.loadIdentity = loadIdentity
    }

    var title: String { target?.address ?? "Console" }

    /// Registers this phone's key with the hub and opens the shell.
    func start(hub: PairedHub, client: HubClient) async {
        let gen = begin()
        phase = .preparing
        let identity: SSHIdentity
        do {
            identity = try loadIdentity()
        } catch {
            phase = .failed(error.localizedDescription)
            return
        }
        do {
            var info = try await client.ssh()
            if info.device.publicKey != identity.publicKey {
                info = try await client.registerSSHKey(identity.publicKey)
            }
            guard gen == generation else { return }
            let readiness = SSHReadiness.of(info)
            guard readiness == .ready else {
                phase = .needsSetup(readiness)
                return
            }
            let fresh = SSHTarget(host: hub.baseURL.host ?? "", port: info.port, user: info.user, hostKeys: info.hostKeys)
            store.save(fresh, for: hub.id)
            target = fresh
            offlineTarget = false
        } catch let error as HubError where Self.hubIsDown(error) {
            guard gen == generation else { return }
            // The hub is down: exactly when the console matters, so dial
            // the machine with what the hub said last time.
            guard let saved = store.target(for: hub.id) else {
                phase = .failed("The hub isn't answering, and this phone hasn't opened a console on \(hub.address) before, so it doesn't know which host keys to trust. Open it once while the hub is up.")
                return
            }
            target = saved
            offlineTarget = true
        } catch {
            guard gen == generation else { return }
            phase = .failed(error.localizedDescription)
            return
        }
        await connect(identity: identity, generation: gen)
    }

    /// No answer at all, or a proxy in front of the hub (`tailscale
    /// serve`) answering for it that the hub is down or not answering.
    static func hubIsDown(_ error: HubError) -> Bool {
        [0, 502, 503, 504].contains(error.status)
    }

    /// Dials the target again after a logout or a dropped connection.
    func reconnect() async {
        guard target != nil else { return }
        let gen = begin()
        do {
            await connect(identity: try loadIdentity(), generation: gen)
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    /// Ends the shell, and any attempt still on its way to one.
    func stop() {
        _ = begin()
    }

    /// A new attempt: whatever an older one was doing is dropped.
    private func begin() -> Int {
        generation += 1
        shell?.close()
        shell = nil
        endedEarly = nil
        return generation
    }

    private func connect(identity: SSHIdentity, generation gen: Int) async {
        guard let target, gen == generation else { return }
        phase = .connecting
        do {
            let batches = ConsoleEventBatcher { [weak self] events in
                for event in events { self?.handle(event, generation: gen) }
            }
            let opened = try await SSHShell.open(target: target, key: identity.privateKey,
                                                 cols: size.cols, rows: size.rows) { event in
                batches.push(event)
            }
            guard gen == generation else {
                opened.close()
                return
            }
            if let early = endedEarly, early.generation == gen {
                // It ended while open() was returning: never call it open.
                endedEarly = nil
                opened.close()
                phase = .closed(early.error?.localizedDescription)
                return
            }
            shell = opened
            phase = .open
            // The view may have settled on a size while the shell opened.
            opened.resize(cols: size.cols, rows: size.rows)
        } catch {
            guard gen == generation else { return }
            phase = .failed(error.localizedDescription)
        }
    }

    private func handle(_ event: SSHShell.Event, generation gen: Int) {
        guard gen == generation else { return }
        switch event {
        case .output(let bytes):
            if let screen {
                screen.feed(bytes)
            } else if unshown.count < 1 << 20 {
                unshown += bytes
            }
        case .closed(let error):
            guard phase == .open else {
                endedEarly = (gen, error)
                return
            }
            shell = nil
            phase = .closed(error?.localizedDescription)
        }
    }

    /// Feeds an event as the current shell would (tests).
    func receiveForTesting(_ event: SSHShell.Event) {
        handle(event, generation: generation)
    }

    func send(_ bytes: [UInt8]) {
        shell?.write(bytes)
    }

    func resize(cols: Int, rows: Int) {
        guard cols > 0, rows > 0, (cols, rows) != size else { return }
        size = (cols, rows)
        shell?.resize(cols: cols, rows: rows)
    }
}

/// Hands the shell's events to the main queue in batches. SSH reads come
/// in small chunks on NIO's thread, often hundreds a second while a
/// command prints; one main-queue hop per chunk woke the main thread for
/// each. Events wait here until the main queue runs the one drain the
/// first of them scheduled, and back-to-back output joins into one feed.
/// Order is kept: a close is delivered after the output before it.
final class ConsoleEventBatcher: @unchecked Sendable {
    private let lock = NSLock()
    private var pending: [SSHShell.Event] = []
    private let schedule: (@escaping @Sendable () -> Void) -> Void
    private let deliver: @MainActor ([SSHShell.Event]) -> Void

    init(schedule: @escaping (@escaping @Sendable () -> Void) -> Void = { DispatchQueue.main.async(execute: $0) },
         deliver: @escaping @MainActor ([SSHShell.Event]) -> Void) {
        self.schedule = schedule
        self.deliver = deliver
    }

    /// From any thread.
    func push(_ event: SSHShell.Event) {
        let first: Bool = lock.withLock {
            let wasEmpty = pending.isEmpty
            if case .output(let more) = event, case .output(var bytes)? = pending.last {
                // Off the array first, so the append grows it in place.
                pending.removeLast()
                bytes += more
                pending.append(.output(bytes))
            } else {
                pending.append(event)
            }
            return wasEmpty
        }
        if first { schedule { [self] in drain() } }
    }

    /// On the main queue: everything that arrived since the last drain.
    private func drain() {
        let events: [SSHShell.Event] = lock.withLock {
            defer { pending = [] }
            return pending
        }
        guard !events.isEmpty else { return }
        MainActor.assumeIsolated { deliver(events) }
    }
}
