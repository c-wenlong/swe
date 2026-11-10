import Foundation
import Observation

// The Worktrees page's data: `swe wt …` as pinned in the swe-wt contract.
// Every reply rides the usual `{"schema": 1, "data": …}` envelope that
// `SweClient.call` unwraps, and keys arrive snake_case, which the client's
// decoder turns into the camelCase names used here.

/// Where a worktree stands against its pull request and the working tree,
/// as `swe wt` classifies it. A state a newer swe adds reads as `unknown`,
/// which never offers a plain delete.
public enum WorktreeState: String, Codable, Sendable, CaseIterable {
    case mergedClean = "merged-clean"
    case mergedDirty = "merged-dirty"
    case openPr = "open-pr"
    case noPr = "no-pr"
    case orphaned
    case unknown
    case protected
    case main

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = WorktreeState(rawValue: raw) ?? .unknown
    }

    public var title: String {
        switch self {
        case .mergedClean: "Merged"
        case .mergedDirty: "Merged, changes left"
        case .openPr: "Open PR"
        case .noPr: "No PR"
        case .orphaned: "Orphaned"
        case .unknown: "Unknown"
        case .protected: "Protected"
        case .main: "Main checkout"
        }
    }

    /// One sentence for the detail pane.
    public var explanation: String {
        switch self {
        case .mergedClean: "The pull request merged and nothing is left to lose. Safe to remove."
        case .mergedDirty: "The pull request merged, but this folder still has uncommitted or untracked files."
        case .openPr: "Its pull request is still open."
        case .noPr: "The branch has no pull request yet."
        case .orphaned: "Its branch is gone or its folder no longer matches the repository."
        case .unknown: "swe couldn't tell where this worktree stands."
        case .protected: "swe never removes this worktree."
        case .main: "The repository's own checkout. It cannot be removed."
        }
    }

    /// How a state reads at a glance, for the badge colour.
    public enum Tone: Sendable { case good, caution, attention, neutral }

    public var tone: Tone {
        switch self {
        case .mergedClean: .good
        case .mergedDirty, .orphaned: .attention
        case .openPr, .noPr: .caution
        case .unknown, .protected, .main: .neutral
        }
    }

    /// `main` and `protected` rows have no delete at all, and swe refuses
    /// `orphaned` ones even with `--force`: git has to forget them first.
    public var isRemovable: Bool { self != .main && self != .protected && self != .orphaned }

    /// Move and slim apply to ordinary worktrees only.
    public var supportsMoveAndSlim: Bool { self != .main && self != .protected && self != .orphaned }

    /// swe refuses Lock on the main checkout, a protected worktree and an
    /// orphan, so the page never offers it there.
    public var supportsLock: Bool { self != .main && self != .orphaned && self != .protected }

    /// Anything but `merged-clean` needs `--force` to remove.
    public var removalNeedsForce: Bool { self != .mergedClean }

    /// The order the filter chips and the page's counts list states in.
    public static var chipOrder: [WorktreeState] {
        [.mergedClean, .mergedDirty, .openPr, .noPr, .orphaned, .unknown, .protected, .main]
    }
}

/// The pull request a worktree's branch belongs to.
public struct WorktreePR: Codable, Equatable, Hashable, Sendable {
    public let number: Int
    public let url: String
    /// `OPEN`, `MERGED` or `CLOSED`, as `gh` says it.
    public let state: String

    public init(number: Int, url: String, state: String) {
        self.number = number
        self.url = url
        self.state = state
    }

    public var isOpen: Bool { state.uppercased() == "OPEN" }
    public var isMerged: Bool { state.uppercased() == "MERGED" }
}

/// One entry of `swe wt list`: a checked-out worktree and what swe knows
/// about it.
public struct Worktree: Codable, Equatable, Sendable, Identifiable, Hashable {
    public var id: String { path }

    public internal(set) var path: String
    /// `owner/name`, nil for a repository with no GitHub remote.
    public let repo: String?
    public let mainCheckout: String
    /// nil on a detached HEAD.
    public let branch: String?
    public let head: String
    public let state: WorktreeState
    public let pr: WorktreePR?
    public let lastCommitAt: Date?
    /// `du` estimates.
    /// nil when sizes were skipped or the folder is gone.
    public internal(set) var sizeBytes: Int?
    public internal(set) var rebuildableBytes: Int?
    public let dirty: Bool
    public let untracked: Int
    public internal(set) var locked: Bool
    public internal(set) var lockReason: String?
    /// `claude`, `codex`, `devin`, `swe-pr` or `other`.
    public let harness: String
    /// Why it has its state, when swe says.
    public let reason: String?
    /// Why clean up keeps it whatever its state: locked, you are inside
    /// it, a detached HEAD used in the last day. nil when nothing does.
    public let hold: String?
    /// Tracked files with changes.
    public let changes: Int?
    public let detached: Bool?
    /// The folders `slim` would remove, when swe lists them.
    public let rebuildable: [RebuildableDir]?

    public struct RebuildableDir: Codable, Equatable, Hashable, Sendable {
        public let path: String
        public let bytes: Int?
        public init(path: String, bytes: Int?) {
            self.path = path
            self.bytes = bytes
        }
    }

    public init(path: String, repo: String? = nil, mainCheckout: String, branch: String?,
                head: String = "", state: WorktreeState, pr: WorktreePR? = nil,
                lastCommitAt: Date? = nil, sizeBytes: Int? = 0, rebuildableBytes: Int? = 0,
                dirty: Bool = false, untracked: Int = 0, locked: Bool = false,
                lockReason: String? = nil, harness: String = "other",
                reason: String? = nil, hold: String? = nil, changes: Int? = nil,
                detached: Bool? = nil, rebuildable: [RebuildableDir]? = nil) {
        self.path = path
        self.repo = repo
        self.mainCheckout = mainCheckout
        self.branch = branch
        self.head = head
        self.state = state
        self.pr = pr
        self.lastCommitAt = lastCommitAt
        self.sizeBytes = sizeBytes
        self.rebuildableBytes = rebuildableBytes
        self.dirty = dirty
        self.untracked = untracked
        self.locked = locked
        self.lockReason = lockReason
        self.harness = harness
        self.reason = reason
        self.hold = hold
        self.changes = changes
        self.detached = detached
        self.rebuildable = rebuildable
    }

    enum CodingKeys: String, CodingKey {
        case path, repo, mainCheckout, branch, head, state, pr, lastCommitAt
        case sizeBytes, rebuildableBytes, dirty, untracked, locked, lockReason, harness
        case reason, hold, changes, detached, rebuildable
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        repo = try c.decodeIfPresent(String.self, forKey: .repo)
        mainCheckout = try c.decodeIfPresent(String.self, forKey: .mainCheckout) ?? path
        branch = try c.decodeIfPresent(String.self, forKey: .branch)
        head = try c.decodeIfPresent(String.self, forKey: .head) ?? ""
        state = try c.decodeIfPresent(WorktreeState.self, forKey: .state) ?? .unknown
        pr = try c.decodeIfPresent(WorktreePR.self, forKey: .pr)
        // ISO 8601 text; an unreadable stamp is "no date", not a failed list.
        lastCommitAt = (try? c.decodeIfPresent(String.self, forKey: .lastCommitAt))
            .flatMap { $0 }.flatMap(Timestamp.parse)
        sizeBytes = try c.decodeIfPresent(Int.self, forKey: .sizeBytes)
        rebuildableBytes = try c.decodeIfPresent(Int.self, forKey: .rebuildableBytes)
        dirty = try c.decodeIfPresent(Bool.self, forKey: .dirty) ?? false
        untracked = try c.decodeIfPresent(Int.self, forKey: .untracked) ?? 0
        locked = try c.decodeIfPresent(Bool.self, forKey: .locked) ?? false
        lockReason = try c.decodeIfPresent(String.self, forKey: .lockReason)
        harness = try c.decodeIfPresent(String.self, forKey: .harness) ?? "other"
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
        hold = try? c.decodeIfPresent(String.self, forKey: .hold)
        changes = try? c.decodeIfPresent(Int.self, forKey: .changes)
        detached = try? c.decodeIfPresent(Bool.self, forKey: .detached)
        rebuildable = try? c.decodeIfPresent([RebuildableDir].self, forKey: .rebuildable)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(path, forKey: .path)
        try c.encode(repo, forKey: .repo)
        try c.encode(mainCheckout, forKey: .mainCheckout)
        try c.encode(branch, forKey: .branch)
        try c.encode(head, forKey: .head)
        try c.encode(state, forKey: .state)
        try c.encode(pr, forKey: .pr)
        try c.encode(lastCommitAt.map { Date.ISO8601FormatStyle().format($0) }, forKey: .lastCommitAt)
        try c.encode(sizeBytes, forKey: .sizeBytes)
        try c.encode(rebuildableBytes, forKey: .rebuildableBytes)
        try c.encode(dirty, forKey: .dirty)
        try c.encode(untracked, forKey: .untracked)
        try c.encode(locked, forKey: .locked)
        try c.encode(lockReason, forKey: .lockReason)
        try c.encode(harness, forKey: .harness)
        try c.encode(reason, forKey: .reason)
        try c.encode(hold, forKey: .hold)
        try c.encode(changes, forKey: .changes)
        try c.encode(detached, forKey: .detached)
        try c.encode(rebuildable, forKey: .rebuildable)
    }

    // MARK: Derived

    /// The branch, or the short head on a detached checkout.
    public var title: String {
        if let branch, !branch.isEmpty { return branch }
        return head.isEmpty ? (path as NSString).lastPathComponent : "detached \(shortHead)"
    }

    public var shortHead: String { String(head.prefix(7)) }

    /// The repository's name for grouping: `owner/name`, else the main
    /// checkout's folder.
    public var repoName: String {
        if let repo, !repo.isEmpty { return repo }
        return (mainCheckout as NSString).lastPathComponent
    }

    /// Why Remove is unavailable, nil when it is on offer.
    public var removalBlock: String? {
        switch state {
        case .main: "The main checkout can't be removed."
        case .protected: "swe never removes a protected worktree."
        case .orphaned: "swe can't remove an orphaned worktree. Run git worktree prune in the main checkout."
        default: locked ? "Unlock it before removing." : nil
        }
    }

    public var canRemove: Bool { removalBlock == nil }

    /// Slim has something to remove: unmeasured counts as "maybe".
    public var canSlim: Bool { state.supportsMoveAndSlim && (rebuildableBytes ?? 1) > 0 }

    /// Remove needs `--force` and a second explicit confirmation.
    public var removalNeedsForce: Bool { state.removalNeedsForce }

    /// The main checkout has no lock or move; everything else does.
    public var canLock: Bool { state.supportsLock }
    /// Unlock stays on offer for anything git still lists that is locked,
    /// even a row Lock is never offered on.
    public var canUnlock: Bool { locked && state != .orphaned }
    public var canMove: Bool { state.supportsMoveAndSlim && !locked }

    /// What clears an orphan: git forgets the folder it no longer lists.
    public var pruneCommand: String {
        "git -C \(Self.shellQuote(mainCheckout)) worktree prune"
    }

    private static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: #"'\''"#) + "'"
    }

    /// What deleting it would throw away beyond the folder itself.
    public var removalLosses: [String] {
        var lines: [String] = []
        if dirty { lines.append("Uncommitted changes in tracked files") }
        if untracked > 0 { lines.append("\(untracked) untracked \(untracked == 1 ? "file" : "files")") }
        switch state {
        case .openPr: lines.append("The branch's pull request is still open")
        case .noPr: lines.append("The branch has no pull request; unpushed work may be lost")
        case .orphaned: lines.append("The branch no longer matches the repository")
        case .unknown: lines.append("swe couldn't tell whether anything is left to lose")
        default: break
        }
        return lines
    }
}

/// `swe wt list --json`.
public struct WorktreeList: Decodable, Equatable, Sendable {
    /// `repo` or `all`: what the list covered.
    public let scope: String
    public let generatedAt: Date?
    public let worktrees: [Worktree]

    public init(scope: String, generatedAt: Date? = nil, worktrees: [Worktree]) {
        self.scope = scope
        self.generatedAt = generatedAt
        self.worktrees = worktrees
    }

    enum CodingKeys: String, CodingKey { case scope, generatedAt, worktrees }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        scope = try c.decodeIfPresent(String.self, forKey: .scope) ?? "all"
        generatedAt = (try? c.decodeIfPresent(String.self, forKey: .generatedAt))
            .flatMap { $0 }.flatMap(Timestamp.parse)
        // One bad row degrades instead of failing the whole list.
        worktrees = try c.decode([LossyRow<Worktree>].self, forKey: .worktrees).compactMap(\.value)
    }
}

/// `{"worktree": W}`: the reply of show, add, lock, unlock and move.
public struct WorktreeReply: Decodable, Equatable, Sendable {
    public let worktree: Worktree
}

/// `swe wt remove --json`.
public struct WorktreeRemoval: Decodable, Equatable, Sendable {
    public let removed: String
    /// nil when swe could not measure it (another volume, say).
    public let freedBytes: Int?
    /// true or false after `--delete-branch`, nil when it was not asked.
    public let branchDeleted: Bool?

    public init(removed: String, freedBytes: Int?, branchDeleted: Bool? = nil) {
        self.removed = removed
        self.freedBytes = freedBytes
        self.branchDeleted = branchDeleted
    }
}

/// `swe wt prune --json`: a dry run lists `candidates`; with `--yes` the
/// same shape reports what was `removed` and what was `skipped`.
public struct PruneReport: Decodable, Equatable, Sendable {
    public struct Skipped: Decodable, Equatable, Sendable, Identifiable {
        public var id: String { path }
        public let path: String
        public let reason: String

        public init(path: String, reason: String) {
            self.path = path
            self.reason = reason
        }
    }

    public let dryRun: Bool
    public let candidates: [Worktree]
    public let removed: [String]
    public let skipped: [Skipped]
    public let freedBytes: Int

    public init(dryRun: Bool, candidates: [Worktree], removed: [String] = [],
                skipped: [Skipped] = [], freedBytes: Int) {
        self.dryRun = dryRun
        self.candidates = candidates
        self.removed = removed
        self.skipped = skipped
        self.freedBytes = freedBytes
    }

    enum CodingKeys: String, CodingKey { case dryRun, candidates, removed, skipped, freedBytes }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dryRun = try c.decodeIfPresent(Bool.self, forKey: .dryRun) ?? true
        candidates = try c.decodeIfPresent([LossyRow<Worktree>].self, forKey: .candidates)?
            .compactMap(\.value) ?? []
        removed = try c.decodeIfPresent([String].self, forKey: .removed) ?? []
        skipped = try c.decodeIfPresent([Skipped].self, forKey: .skipped) ?? []
        freedBytes = try c.decodeIfPresent(Int.self, forKey: .freedBytes) ?? 0
    }
}

/// `swe wt slim --json`: the rebuildable folders a worktree can shed.
/// What a confirmed clean up did, worktree by worktree.
public struct CleanupResult: Equatable, Sendable {
    public let removed: [String]
    public let skipped: [PruneReport.Skipped]
    /// nil when no removal reported a figure.
    public let freedBytes: Int?

    public init(removed: [String], skipped: [PruneReport.Skipped], freedBytes: Int?) {
        self.removed = removed
        self.skipped = skipped
        self.freedBytes = freedBytes
    }
}

public struct SlimReport: Decodable, Equatable, Sendable {
    public struct Dir: Decodable, Equatable, Sendable, Identifiable {
        public var id: String { path }
        public let path: String
        public let bytes: Int?

        public init(path: String, bytes: Int?) {
            self.path = path
            self.bytes = bytes
        }
    }

    public let dryRun: Bool
    public let dirs: [Dir]
    /// nil when swe has no figure, such as when nothing was left to delete.
    public let freedBytes: Int?

    public init(dryRun: Bool, dirs: [Dir], freedBytes: Int?) {
        self.dryRun = dryRun
        self.dirs = dirs
        self.freedBytes = freedBytes
    }
}

// MARK: - Failures

/// `{"error": {"code", "message"}}`, as swe prints a refusal under `--json`.
/// `SweClient` surfaces that text as the failure's message, so the page
/// reads the code and message back out instead of showing raw JSON.
public struct WorktreeFailure: Equatable, Sendable {
    public let code: String?
    public let message: String

    /// A code swe uses when a removal needs `--force`.
    public static let notSafe = "not_safe"

    public init(code: String?, message: String) {
        self.code = code
        self.message = message
    }

    /// Reads the error object out of `text`, whole or buried in noise.
    public static func parse(_ text: String) -> WorktreeFailure? {
        guard let start = text.firstIndex(of: "{"),
              let data = String(text[start...]).data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        let object = (root["error"] as? [String: Any])
            ?? ((root["data"] as? [String: Any])?["error"] as? [String: Any])
        guard let object, let message = object["message"] as? String else { return nil }
        return WorktreeFailure(code: object["code"] as? String, message: message)
    }

    /// What the page shows for `error`: swe's own words under a plain
    /// sentence for its code.
    public static func describe(_ error: any Error) -> WorktreeFailure {
        let raw: WorktreeFailure
        if case SweError.commandFailed(_, let message) = error {
            raw = parse(message) ?? WorktreeFailure(code: nil, message: message)
        } else {
            let text = error.localizedDescription
            raw = parse(text) ?? WorktreeFailure(code: nil, message: text)
        }
        return WorktreeFailure(code: raw.code, message: friendly(code: raw.code, message: raw.message))
    }

    /// One sentence per documented code, then swe's detail. An unknown
    /// code, or none, shows swe's message alone.
    public static func friendly(code: String?, message: String) -> String {
        let lead: String? = switch code {
        case "bad_args": "swe didn't understand that request."
        case "bad_config": "swe's worktree settings are invalid."
        case "io_error": "swe couldn't read or write a file."
        case "not_a_repo": "That folder isn't a git repository."
        case "not_found": "swe couldn't find that worktree. It may already be gone."
        case "not_safe": "swe won't remove this without force, because it could lose work."
        case "exists": "Something already exists there."
        case "git_failed": "git reported an error."
        case "gh_unavailable": "The GitHub CLI isn't available or isn't signed in, so swe couldn't check pull requests."
        case "busy": "Another swe worktree command is running. Try again in a moment."
        default: nil
        }
        guard let lead else { return message }
        return message.isEmpty ? lead : "\(lead) \(message)"
    }
}

// MARK: - Commands

/// Where a list looks.
public enum WorktreeScope: Hashable, Sendable {
    /// Every repository swe knows (`--all`).
    case all
    /// One repository, by its main checkout (`--repo PATH`).
    case repo(String)
}

/// The argument lists each action sends, kept apart so tests can pin them.
public enum WorktreeCommand {
    public static func list(_ scope: WorktreeScope) -> [String] {
        switch scope {
        case .all: ["wt", "list", "--all", "--json"]
        case .repo(let path): ["wt", "list", "--repo=\(path)", "--json"]
        }
    }

    public static func show(_ path: String) -> [String] { ["wt", "show", path, "--json"] }

    public static func add(_ draft: NewWorktreeDraft) -> [String] {
        var args = ["wt", "add", draft.repoPath]
        switch draft.mode {
        case .existingBranch:
            args += ["--branch=\(draft.trimmedBranch)"]
        case .newBranch:
            args += ["--branch=\(draft.trimmedBranch)", "--new"]
            if !draft.trimmedBase.isEmpty { args.append("--base=\(draft.trimmedBase)") }
        case .pullRequest:
            args.append("--pr=\(draft.trimmedPR)")
        }
        if !draft.trimmedPath.isEmpty { args.append("--path=\(draft.trimmedPath)") }
        return args + ["--json"]
    }

    public static func remove(_ path: String, force: Bool, deleteBranch: Bool) -> [String] {
        var args = ["wt", "remove", path]
        if force { args.append("--force") }
        if deleteBranch { args.append("--delete-branch") }
        return args + ["--json"]
    }

    public static func lock(_ path: String, reason: String) -> [String] {
        let reason = reason.trimmingCharacters(in: .whitespacesAndNewlines)
        return ["wt", "lock", path] + (reason.isEmpty ? [] : ["--reason=\(reason)"]) + ["--json"]
    }

    public static func unlock(_ path: String) -> [String] { ["wt", "unlock", path, "--json"] }

    public static func move(_ path: String, to newPath: String) -> [String] {
        ["wt", "move", path, newPath, "--json"]
    }

    /// `--include merged-dirty` also offers worktrees with files left in
    /// them, so it is never on unless the person asked for it.
    public static func prune(scope: WorktreeScope, includeDirty: Bool, confirmed: Bool) -> [String] {
        var args = ["wt", "prune"]
        switch scope {
        case .all: args.append("--all")
        case .repo(let path): args.append("--repo=\(path)")
        }
        if includeDirty { args.append("--include=merged-dirty") }
        if confirmed { args.append("--yes") }
        return args + ["--json"]
    }

    public static func slim(_ path: String, confirmed: Bool) -> [String] {
        ["wt", "slim", path] + (confirmed ? ["--yes"] : []) + ["--json"]
    }
}

/// The New worktree sheet's form, with the rules `swe wt add` enforces
/// checked before it runs.
public struct NewWorktreeDraft: Equatable, Sendable {
    public enum Mode: String, CaseIterable, Sendable {
        case existingBranch, newBranch, pullRequest

        public var title: String {
            switch self {
            case .existingBranch: "Existing branch"
            case .newBranch: "New branch"
            case .pullRequest: "Pull request"
            }
        }
    }

    public var repoPath: String
    public var mode: Mode = .newBranch
    public var branch = ""
    public var base = ""
    public var pullRequest = ""
    public var path = ""

    public init(repoPath: String = "", mode: Mode = .newBranch, branch: String = "",
                base: String = "", pullRequest: String = "", path: String = "") {
        self.repoPath = repoPath
        self.mode = mode
        self.branch = branch
        self.base = base
        self.pullRequest = pullRequest
        self.path = path
    }

    public var trimmedBranch: String { branch.trimmingCharacters(in: .whitespacesAndNewlines) }
    public var trimmedBase: String { base.trimmingCharacters(in: .whitespacesAndNewlines) }
    public var trimmedPR: String {
        pullRequest.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "#"))
    }
    public var trimmedPath: String { path.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// What blocks Create, nil when the form is complete.
    public var problem: String? {
        if repoPath.isEmpty { return "Pick a repository." }
        switch mode {
        case .existingBranch, .newBranch:
            if let reason = Self.refProblem(trimmedBranch, noun: "branch") { return reason }
            // The base goes to git as a starting point, so any revision
            // expression (HEAD~1, origin/main^) is fine.
        case .pullRequest:
            guard let number = Int(trimmedPR), number > 0 else { return "Enter the pull request number." }
        }
        if !trimmedPath.isEmpty, !trimmedPath.hasPrefix("/"), !trimmedPath.hasPrefix("~") {
            return "Use a full path, starting with / or ~."
        }
        return nil
    }

    public var isComplete: Bool { problem == nil }

    /// Git's ref rules, trimmed to the ones that catch typos.
    static func refProblem(_ name: String, noun: String) -> String? {
        if name.isEmpty { return "Enter the \(noun) name." }
        if name.hasPrefix("-") { return "A \(noun) name can't start with a dash." }
        if name.contains("..") || name.hasSuffix("/") || name.hasSuffix(".") || name.hasSuffix(".lock") {
            return "That isn't a valid \(noun) name."
        }
        if name.rangeOfCharacter(from: CharacterSet(charactersIn: " ~^:?*[\\").union(.controlCharacters)) != nil {
            return "A \(noun) name can't hold spaces or ~ ^ : ? * [ \\."
        }
        return nil
    }
}

// MARK: - Client

extension SweClient {
    /// Disk estimates walk every worktree, so a list gets more than the
    /// runner's default bound.
    private static let listTimeout: Duration = .seconds(120)

    public func worktrees(_ scope: WorktreeScope) async throws -> WorktreeList {
        try await call(WorktreeCommand.list(scope), timeout: Self.listTimeout)
    }

    public func worktree(at path: String) async throws -> Worktree {
        let reply: WorktreeReply = try await call(WorktreeCommand.show(path))
        return reply.worktree
    }

    public func addWorktree(_ draft: NewWorktreeDraft) async throws -> Worktree {
        let reply: WorktreeReply = try await call(WorktreeCommand.add(draft), timeout: .seconds(300))
        return reply.worktree
    }

    public func removeWorktree(_ path: String, force: Bool, deleteBranch: Bool) async throws -> WorktreeRemoval {
        try await call(WorktreeCommand.remove(path, force: force, deleteBranch: deleteBranch),
                       timeout: .seconds(300))
    }

    public func lockWorktree(_ path: String, reason: String) async throws -> Worktree {
        let reply: WorktreeReply = try await call(WorktreeCommand.lock(path, reason: reason))
        return reply.worktree
    }

    public func unlockWorktree(_ path: String) async throws -> Worktree {
        let reply: WorktreeReply = try await call(WorktreeCommand.unlock(path))
        return reply.worktree
    }

    public func moveWorktree(_ path: String, to newPath: String) async throws -> Worktree {
        let reply: WorktreeReply = try await call(WorktreeCommand.move(path, to: newPath))
        return reply.worktree
    }

    public func pruneWorktrees(scope: WorktreeScope, includeDirty: Bool,
                               confirmed: Bool) async throws -> PruneReport {
        try await call(WorktreeCommand.prune(scope: scope, includeDirty: includeDirty, confirmed: confirmed),
                       timeout: .seconds(600))
    }

    public func slimWorktree(_ path: String, confirmed: Bool) async throws -> SlimReport {
        try await call(WorktreeCommand.slim(path, confirmed: confirmed), timeout: .seconds(300))
    }
}

// MARK: - Formatting

public enum WorktreeFormat {
    /// "1.2 GB", "340 MB": Finder's decimal sizes, since `du` estimates.
    public static func bytes(_ count: Int?) -> String {
        guard let count else { return "Not measured" }
        if count <= 0 { return "0 KB" }
        let formatter = ByteCountFormatter()
        formatter.countStyle = .file
        return formatter.string(fromByteCount: Int64(count))
    }

    /// "Freed space unknown" when swe has no figure.
    public static func freed(_ count: Int?) -> String {
        count.map { bytes($0) } ?? "Freed space unknown"
    }

    /// "3d ago", or "no commits" without a date.
    public static func lastCommit(_ date: Date?, now: Date = Date()) -> String {
        guard let date else { return "No date" }
        return UsageFormat.ago(date, now: now)
    }

    /// The harness as the page names it.
    public static func harness(_ id: String) -> String {
        switch id {
        case "swe-pr": "swe PR checkout"
        case "other": "Other"
        default: HarnessMark(id).name
        }
    }

    /// "1 worktree", "12 worktrees".
    public static func count(_ n: Int) -> String { "\(n) \(n == 1 ? "worktree" : "worktrees")" }
}

// MARK: - Grouping and filtering

/// One repository's worktrees on the page.
public struct WorktreeGroup: Identifiable, Equatable, Sendable {
    /// The main checkout, which names the repository uniquely.
    public let id: String
    public let title: String
    public let worktrees: [Worktree]
    public var totalBytes: Int { worktrees.reduce(0) { $0 + ($1.sizeBytes ?? 0) } }
}

/// The page's headline numbers, over the whole loaded list.
public struct WorktreeSummary: Equatable, Sendable {
    public let count: Int
    public let totalBytes: Int
    public let rebuildableBytes: Int
    /// What removing every unlocked, unheld `merged-clean` worktree would free.
    public let reclaimableBytes: Int
    public let reclaimableCount: Int
}

/// A main checkout the New worktree sheet can add to.
public struct WorktreeRepo: Identifiable, Equatable, Hashable, Sendable {
    public var id: String { path }
    public let path: String
    public let name: String
}

/// A repository card's fold: the first `limit` rows, plus the open row
/// when it sits beyond them, so it never hides behind the toggle.
public enum WorktreeFold {
    public static func visible(_ rows: [Worktree], limit: Int, expanded: Bool,
                               pinned: String?) -> (shown: [Worktree], hidden: Int) {
        let head = RowFold.visible(rows, limit: limit, expanded: expanded)
        let extra = rows.dropFirst(head.count).filter { $0.path == pinned }
        let shown = head + extra
        return (shown, rows.count - shown.count)
    }

    /// Folders whose contents change with every build or install; nothing
    /// in them says a worktree came or went.
    static let rebuildable: Set<String> = [
        "node_modules", ".venv", "venv", ".next", "build", "dist", "target",
        "DerivedData", "__pycache__", ".gradle", "Pods",
    ]

    /// Whether a path under `~/.swe` (relative to it) is a worktree being
    /// registered or removed: `.git/worktrees/<name>` or a worktree folder
    /// added or dropped directly under a `worktrees` folder. Edits inside a
    /// worktree, build output, caches and `.git/index` are not.
    public static func affectsList(_ relative: String) -> Bool {
        let parts = relative.split(separator: "/").map(String.init)
        if parts.contains(where: { rebuildable.contains($0) }) { return false }
        if let first = parts.first, ["cache", "data", "logs"].contains(first) { return false }
        if relative == "config.json" { return true }
        for (index, part) in parts.enumerated() where part == "worktrees" {
            let after = parts.count - index - 1
            let inGit = index > 0 && parts[index - 1] == ".git"
            if after <= 1 { return !(parts.last?.hasSuffix(".lock") ?? false) || inGit }
        }
        return false
    }
}

public enum WorktreeFilter {
    /// Rows matching a state chip and the search text. `query` matches the
    /// branch, repository, path, harness and PR number, ignoring case.
    public static func apply(_ worktrees: [Worktree], state: WorktreeState?, query: String) -> [Worktree] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return worktrees.filter { tree in
            if let state, tree.state != state { return false }
            guard !needle.isEmpty else { return true }
            let hay = [tree.branch ?? "", tree.repoName, tree.path, tree.harness, tree.state.title,
                       tree.pr.map { "#\($0.number) \($0.number)" } ?? ""]
            return hay.contains { $0.lowercased().contains(needle) }
        }
    }

    /// Repositories alphabetically; inside one the main checkout first,
    /// then the most recently committed to.
    public static func group(_ worktrees: [Worktree]) -> [WorktreeGroup] {
        let byRepo = Dictionary(grouping: worktrees, by: \.mainCheckout)
        return byRepo.map { checkout, trees in
            WorktreeGroup(id: checkout, title: trees[0].repoName, worktrees: trees.sorted { a, b in
                if (a.state == .main) != (b.state == .main) { return a.state == .main }
                let (x, y) = (a.lastCommitAt ?? .distantPast, b.lastCommitAt ?? .distantPast)
                return x == y ? a.path < b.path : x > y
            })
        }
        .sorted { ($0.title.lowercased(), $0.id) < ($1.title.lowercased(), $1.id) }
    }

    public static func summary(_ worktrees: [Worktree]) -> WorktreeSummary {
        let reclaim = worktrees.filter { $0.state == .mergedClean && !$0.locked && ($0.hold ?? "").isEmpty }
        return WorktreeSummary(
            count: worktrees.count,
            totalBytes: worktrees.reduce(0) { $0 + ($1.sizeBytes ?? 0) },
            rebuildableBytes: worktrees.reduce(0) { $0 + ($1.rebuildableBytes ?? 0) },
            reclaimableBytes: reclaim.reduce(0) { $0 + ($1.sizeBytes ?? 0) },
            reclaimableCount: reclaim.count)
    }

    public static func repos(in worktrees: [Worktree]) -> [WorktreeRepo] {
        var seen: Set<String> = []
        return worktrees.compactMap { tree in
            seen.insert(tree.mainCheckout).inserted
                ? WorktreeRepo(path: tree.mainCheckout, name: tree.repoName) : nil
        }
        .sorted { $0.name.lowercased() < $1.name.lowercased() }
    }
}

// MARK: - Model

/// The Worktrees page's model. A change goes through `swe wt` and the list
/// reloads after it; nothing is edited in place, so what shows is always
/// what swe last said.
@MainActor
@Observable
public final class WorktreesModel {
    public private(set) var state: Loadable<WorktreeList> = .idle
    /// Where the list looks. Changing it reloads through the page.
    public var scope: WorktreeScope = .all
    public var stateFilter: WorktreeState?
    public var query = ""
    /// Every main checkout seen so far, kept across scope changes so the
    /// scope picker and the New worktree sheet always have the full set.
    public private(set) var repos: [WorktreeRepo] = []
    /// Paths with a change in flight.
    public private(set) var working: Set<String> = []
    /// The last page-level action failure, in swe's words.
    public var actionError: String?
    /// A one-line result ("Removed fix/hooks-race, freed 810 MB").
    public var notice: String?
    /// The last list failure, so the page can leave a saved scope that no
    /// longer exists.
    public private(set) var failure: WorktreeFailure?

    /// Replaces the `swe` the page would use, for the debug fixture.
    private let clientOverride: SweClient?
    private let maxAge: TimeInterval
    private let now: @Sendable () -> Date
    private let gate = SerialGate()
    private var generation = 0
    private var loadedAt: Date?
    private var loadedScope: WorktreeScope?
    private var loading = 0
    /// A list is being read, so a watcher event never queues a second one.
    public var isLoading: Bool { loading > 0 }
    /// The swe that produced the list on show, as Settings names it.
    private var loadedSource: String?
    private var requestedSource = ""

    /// How long a list counts as fresh, for the page's own re-check.
    public var freshness: TimeInterval { maxAge }

    public init(client: SweClient? = nil, maxAge: TimeInterval = 120,
                now: @escaping @Sendable () -> Date = Date.init) {
        clientOverride = client
        self.maxAge = maxAge
        self.now = now
    }

    private func client(_ swe: SweClient) -> SweClient { clientOverride ?? swe }

    // MARK: Reading

    public var worktrees: [Worktree] {
        guard case .loaded(let list) = state else { return [] }
        return list.worktrees
    }

    /// The rows the chips and search leave.
    public var visible: [Worktree] {
        WorktreeFilter.apply(worktrees, state: stateFilter, query: query)
    }

    public var groups: [WorktreeGroup] { WorktreeFilter.group(visible) }
    public var summary: WorktreeSummary { WorktreeFilter.summary(worktrees) }

    /// How many loaded worktrees each state has, for the chips.
    public var stateCounts: [WorktreeState: Int] {
        Dictionary(grouping: WorktreeFilter.apply(worktrees, state: nil, query: query), by: \.state)
            .mapValues(\.count)
    }

    public func worktree(at path: String) -> Worktree? { worktrees.first { $0.path == path } }

    // MARK: Loading

    /// Lists unless this scope's list, from this `source` (the swe path as
    /// Settings has it), is under `maxAge` old.
    public func loadIfStale(swe: SweClient, source: String = "") async {
        if isLoading { return }
        if case .loaded = state, loadedScope == scope, loadedSource == source, let loadedAt,
           now().timeIntervalSince(loadedAt) < maxAge { return }
        await load(swe: swe, source: source)
    }

    /// A reload a folder change asked for: only once the freshness window
    /// has passed and no list is already being read. Events inside the
    /// window are dropped; the page's own re-check picks the change up
    /// when the window ends. A manual Refresh calls `load` and always runs.
    public func reloadFromWatcher(swe: SweClient, source: String) async {
        guard !isLoading, let loadedAt, now().timeIntervalSince(loadedAt) >= maxAge else { return }
        await load(swe: swe, source: source)
    }

    /// Always lists, for the scope on show.
    public func load(swe: SweClient, source: String? = nil) async {
        if let source { requestedSource = source }
        let source = requestedSource
        generation += 1
        loading += 1
        defer { loading -= 1 }
        let mine = generation
        let asked = scope
        if case .loaded = state {} else { state = .loading }
        do {
            let list = try await client(swe).worktrees(asked)
            guard mine == generation else { return }
            state = .loaded(list)
            failure = nil
            loadedAt = now()
            loadedScope = asked
            loadedSource = source
            if case .all = asked {
                repos = WorktreeFilter.repos(in: list.worktrees)
            } else {
                let merged = Set(repos).union(WorktreeFilter.repos(in: list.worktrees))
                repos = merged.sorted { $0.name.lowercased() < $1.name.lowercased() }
            }
        } catch is CancellationError {
            return
        } catch {
            guard mine == generation else { return }
            let described = WorktreeFailure.describe(error)
            failure = described
            state = .failed(described.message)
        }
    }

    // MARK: Changing

    /// Runs one change at a time (swe rewrites the repository's worktree
    /// registry on each), then lists again whether it worked or not: a
    /// failed change may still have moved something.
    private func perform<T>(_ paths: [String] = [], swe: SweClient,
                            _ work: (SweClient) async throws -> T) async throws -> T {
        await gate.acquire()
        defer { gate.release() }
        working.formUnion(paths)
        actionError = nil
        defer { working.subtract(paths) }
        do {
            let value = try await work(client(swe))
            await load(swe: swe)
            return value
        } catch {
            await load(swe: swe)
            throw error
        }
    }

    @discardableResult
    public func add(_ draft: NewWorktreeDraft, swe: SweClient) async throws -> Worktree {
        try await perform(swe: swe) { try await $0.addWorktree(draft) }
    }

    @discardableResult
    public func remove(_ tree: Worktree, force: Bool, deleteBranch: Bool,
                       swe: SweClient) async throws -> WorktreeRemoval {
        // The model refuses what the page never offers, so a stale view
        // can't send a delete swe would only have to refuse.
        guard tree.canRemove else {
            throw WorktreeFailure(code: "protected", message: tree.removalBlock ?? "Can't be removed.")
                .asError
        }
        let result = try await perform([tree.path], swe: swe) {
            try await $0.removeWorktree(tree.path, force: force && tree.removalNeedsForce,
                                        deleteBranch: deleteBranch)
        }
        var line = "Removed \(tree.title). \(WorktreeFormat.freed(result.freedBytes))"
        if deleteBranch, result.branchDeleted == false, let branch = tree.branch {
            line += ". The folder is gone but git kept the branch \(branch), usually because it has commits that aren't merged."
        }
        notice = line
        return result
    }

    @discardableResult
    public func lock(_ tree: Worktree, reason: String, swe: SweClient) async throws -> Worktree {
        try await perform([tree.path], swe: swe) { try await $0.lockWorktree(tree.path, reason: reason) }
    }

    @discardableResult
    public func unlock(_ tree: Worktree, swe: SweClient) async throws -> Worktree {
        try await perform([tree.path], swe: swe) { try await $0.unlockWorktree(tree.path) }
    }

    @discardableResult
    public func move(_ tree: Worktree, to newPath: String, swe: SweClient) async throws -> Worktree {
        try await perform([tree.path], swe: swe) {
            try await $0.moveWorktree(tree.path, to: newPath.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    /// The dry run: what `prune` would remove. Changes nothing, so it
    /// skips the gate and the reload.
    public func prunePreview(swe: SweClient) async throws -> PruneReport {
        try await client(swe).pruneWorktrees(scope: scope, includeDirty: false, confirmed: false)
    }

    /// Removes the worktrees the preview showed, one unforced
    /// `swe wt remove` each, so swe itself refuses any that stopped being
    /// merged and clean, or got locked, since. `prune --yes` would rescan
    /// and could take worktrees the person never saw, and `--force` would
    /// skip swe's lock and hold checks, so bulk clean up never uses either.
    @discardableResult
    public func cleanUp(_ candidates: [Worktree], swe: SweClient) async throws -> CleanupResult {
        let result = try await perform(candidates.map(\.path), swe: swe) { client in
            var removed: [String] = []
            var skipped: [PruneReport.Skipped] = []
            var freed: Int?
            for tree in candidates {
                try Task.checkCancellation()
                do {
                    let done = try await client.removeWorktree(tree.path, force: false, deleteBranch: false)
                    removed.append(tree.path)
                    if let bytes = done.freedBytes { freed = (freed ?? 0) + bytes }
                } catch is CancellationError {
                    throw CancellationError()
                } catch {
                    skipped.append(.init(path: tree.path, reason: WorktreeFailure.describe(error).message))
                }
            }
            return CleanupResult(removed: removed, skipped: skipped, freedBytes: freed)
        }
        notice = "Removed \(WorktreeFormat.count(result.removed.count)). \(WorktreeFormat.freed(result.freedBytes))"
        return result
    }

    public func slimPreview(_ tree: Worktree, swe: SweClient) async throws -> SlimReport {
        try await client(swe).slimWorktree(tree.path, confirmed: false)
    }

    @discardableResult
    public func slim(_ tree: Worktree, swe: SweClient) async throws -> SlimReport {
        let report = try await perform([tree.path], swe: swe) {
            try await $0.slimWorktree(tree.path, confirmed: true)
        }
        notice = "Slimmed \(tree.title). \(WorktreeFormat.freed(report.freedBytes))"
        return report
    }
}

extension WorktreeFailure {
    /// A failure thrown from the model itself, readable like swe's.
    var asError: any Error { SweError.commandFailed(status: 1, message: message) }
}
