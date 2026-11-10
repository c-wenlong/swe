import Foundation

/// A stand-in for `swe wt …` that answers the pinned contract from memory.
/// Tests and the debug build's `SWE_WT_FIXTURE` mode use it, so the page
/// can be exercised before the real commands exist on a machine. It keeps
/// its worktrees between calls: add, remove, lock and move change what the
/// next list says, and a removal refuses what swe would refuse.
public final class WorktreesFixture: CommandRunning, @unchecked Sendable {
    public enum Scenario: String, Sendable {
        /// A mix of every state across three repositories.
        case standard
        /// A swe that knows no worktrees.
        case empty
        /// A swe whose list fails.
        case error
    }

    private let lock = NSLock()
    private var trees: [Worktree]
    private var log: [[String]] = []
    private let scenario: Scenario

    public init(_ scenario: Scenario = .standard, now: Date = Date()) {
        self.scenario = scenario
        trees = scenario == .standard ? Self.standard(now: now) : []
    }

    /// Every call so far, in order.
    public var calls: [[String]] { lock.withLock { log } }
    public var worktrees: [Worktree] { lock.withLock { trees } }

    // MARK: Runner

    public func run(_ arguments: [String]) async throws -> CommandResult {
        // A little latency, so a spinner and a busy state are visible.
        try? await Task.sleep(for: .milliseconds(120))
        return lock.withLock { handle(arguments) }
    }

    private func handle(_ args: [String]) -> CommandResult {
        log.append(args)
        guard args.count >= 2, args[0] == "wt" else { return fail("bad_args", "Unknown command") }
        let rest = Array(args.dropFirst(2)).filter { $0 != "--json" }
        // `--name=value`, the repo's own form, or the spaced one.
        func flag(_ name: String) -> String? {
            if let joined = rest.first(where: { $0.hasPrefix(name + "=") }) {
                return String(joined.dropFirst(name.count + 1))
            }
            return rest.firstIndex(of: name).flatMap { rest.indices.contains($0 + 1) ? rest[$0 + 1] : nil }
        }
        let positional = positionals(rest)
        switch args[1] {
        case "list": return list(rest, repo: flag("--repo"))
        case "show":
            guard let path = positional.first, let tree = tree(at: path) else { return missing(positional.first) }
            return reply(["worktree": tree])
        case "add": return add(positional.first, rest: rest, flag: flag)
        case "remove": return remove(positional.first, force: rest.contains("--force"),
                                     deleteBranch: rest.contains("--delete-branch"))
        case "lock":
            if tree(at: positional.first ?? "")?.state == .main {
                return fail("not_safe", "The main checkout can't be locked.")
            }
            return update(positional.first) { var t = $0; t.locked = true; t.lockReason = flag("--reason"); return t }
        case "unlock":
            return update(positional.first) { var t = $0; t.locked = false; t.lockReason = nil; return t }
        case "move":
            guard positional.count >= 2 else { return fail("bad_args", "move needs a path and a new path") }
            return move(positional[0], to: positional[1])
        case "prune": return prune(rest, repo: flag("--repo"))
        case "slim": return slim(positional.first, confirmed: rest.contains("--yes"))
        default: return fail("bad_args", "Unknown command: wt \(args[1])")
        }
    }

    /// Arguments that aren't flags or a flag's value.
    private func positionals(_ rest: [String]) -> [String] {
        let valued: Set<String> = ["--repo", "--branch", "--base", "--pr", "--path", "--reason", "--include"]
        var out: [String] = []
        var skip = false
        for item in rest {
            if skip { skip = false; continue }
            if valued.contains(item) { skip = true; continue }
            if item.hasPrefix("--") { continue }
            out.append(item)
        }
        return out
    }

    private func tree(at path: String) -> Worktree? { trees.first { $0.path == path } }

    private func list(_ rest: [String], repo: String?) -> CommandResult {
        if scenario == .error {
            return fail("git_failed", "git isn't on this Mac's PATH, so swe can't list worktrees.")
        }
        let all = rest.contains("--all")
        let shown = repo.map { path in trees.filter { $0.mainCheckout == path } } ?? trees
        return reply(["scope": all || repo == nil ? "all" : "repo",
                      "generated_at": iso(Date()), "worktrees": shown])
    }

    private func add(_ repo: String?, rest: [String], flag: (String) -> String?) -> CommandResult {
        guard let repo, let main = trees.first(where: { $0.mainCheckout == repo && $0.state == .main })
        else { return fail("not_a_repo", "\(repo ?? "That folder") isn't a repository swe knows.") }
        let pr = flag("--pr").flatMap(Int.init)
        let branch = flag("--branch") ?? "pr-\(pr ?? 0)"
        if trees.contains(where: { $0.mainCheckout == repo && $0.branch == branch }) {
            return fail("exists", "\(branch) is already checked out in another worktree.")
        }
        let slug = branch.replacingOccurrences(of: "/", with: "-")
        let path = flag("--path") ?? "\(repo)/.claude/worktrees/\(slug)"
        let tree = Worktree(
            path: path, repo: main.repo, mainCheckout: repo, branch: branch,
            head: "a1b2c3d4e5f6", state: pr == nil ? .noPr : .openPr,
            pr: pr.map { WorktreePR(number: $0, url: "https://github.com/\(main.repo ?? "o/r")/pull/\($0)", state: "OPEN") },
            lastCommitAt: Date(), sizeBytes: 12_000_000, rebuildableBytes: 0, harness: "other")
        trees.append(tree)
        return reply(["worktree": tree])
    }

    private func remove(_ path: String?, force: Bool, deleteBranch: Bool) -> CommandResult {
        guard let path, let tree = tree(at: path) else { return missing(path) }
        if !tree.state.isRemovable {
            return fail("not_safe", "\(tree.title) is \(tree.state.rawValue) and is never removed.")
        }
        if tree.state.removalNeedsForce, !force {
            return fail(WorktreeFailure.notSafe,
                        "\(tree.title) is \(tree.state.rawValue). Pass --force to remove it anyway.")
        }
        if tree.locked, !force {
            return fail(WorktreeFailure.notSafe, "\(tree.title) is locked. Pass --force to remove it anyway.")
        }
        trees.removeAll { $0.path == path }
        return reply(["removed": path, "freed_bytes": tree.sizeBytes ?? 0,
                      "branch_deleted": deleteBranch ? (tree.state == .mergedClean) as Any : NSNull()])
    }

    private func update(_ path: String?, _ change: (Worktree) -> Worktree) -> CommandResult {
        guard let path, let index = trees.firstIndex(where: { $0.path == path }) else { return missing(path) }
        trees[index] = change(trees[index])
        return reply(["worktree": trees[index]])
    }

    private func move(_ path: String, to newPath: String) -> CommandResult {
        guard let index = trees.firstIndex(where: { $0.path == path }) else { return missing(path) }
        if trees[index].state == .main { return fail("not_safe", "The main checkout can't be moved.") }
        trees[index].path = newPath
        return reply(["worktree": trees[index]])
    }

    private func prune(_ rest: [String], repo: String?) -> CommandResult {
        let includeDirty = rest.contains("--include=merged-dirty")
            || (rest.firstIndex(of: "--include").map { rest.indices.contains($0 + 1) && rest[$0 + 1] == "merged-dirty" } ?? false)
        let confirmed = rest.contains("--yes")
        let candidates = trees.filter { ($0.state == .mergedClean || (includeDirty && $0.state == .mergedDirty)) && !$0.locked && (repo == nil || $0.mainCheckout == repo) }
        let freed = candidates.reduce(0) { $0 + ($1.sizeBytes ?? 0) }
        if confirmed { trees.removeAll { tree in candidates.contains { $0.path == tree.path } } }
        return reply(["dry_run": !confirmed, "candidates": candidates,
                      "removed": confirmed ? candidates.map(\.path) : [],
                      "skipped": trees.filter { $0.state == .mergedClean && $0.locked }.map {
                          ["path": $0.path, "reason": "locked"]
                      },
                      "freed_bytes": freed])
    }

    private func slim(_ path: String?, confirmed: Bool) -> CommandResult {
        guard let path, let index = trees.firstIndex(where: { $0.path == path }) else { return missing(path) }
        let tree = trees[index]
        let weights: [(String, Double)] = [("node_modules", 0.6), (".venv", 0.25), ("build", 0.15)]
        let rebuildable = tree.rebuildableBytes ?? 0
        let dirs = rebuildable == 0 ? [] : weights.map {
            ["path": "\(path)/\($0.0)", "bytes": Int(Double(rebuildable) * $0.1)] as [String: Any]
        }
        if confirmed { trees[index].sizeBytes = tree.sizeBytes.map { max(0, $0 - rebuildable) }; trees[index].rebuildableBytes = 0 }
        return reply(["dry_run": !confirmed, "dirs": dirs, "freed_bytes": rebuildable])
    }

    // MARK: Replies

    private func reply(_ data: [String: Any]) -> CommandResult {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        // Encodable members travel through JSONSerialization as dictionaries.
        let object = Self.plain(data, encoder: encoder)
        let body = (try? JSONSerialization.data(withJSONObject: ["schema": 1, "data": object])) ?? Data()
        return CommandResult(status: 0, stdout: body, stderr: "")
    }

    private func fail(_ code: String, _ message: String) -> CommandResult {
        // The contract: a failure prints the envelope with `error` to stdout.
        let body = (try? JSONSerialization.data(withJSONObject: ["schema": 1, "error": ["code": code, "message": message]])) ?? Data()
        return CommandResult(status: 1, stdout: body, stderr: "")
    }

    private func missing(_ path: String?) -> CommandResult {
        fail("not_found", "No worktree at \(path ?? "that path").")
    }

    private static func plain(_ value: Any, encoder: JSONEncoder) -> Any {
        switch value {
        case let tree as Worktree: return encoded(tree, encoder)
        case let trees as [Worktree]: return trees.map { encoded($0, encoder) }
        case let dict as [String: Any]: return dict.mapValues { plain($0, encoder: encoder) }
        case let array as [[String: String]]: return array
        case let array as [[String: Any]]: return array
        default: return value
        }
    }

    private static func encoded(_ tree: Worktree, _ encoder: JSONEncoder) -> Any {
        guard let data = try? encoder.encode(tree),
              let object = try? JSONSerialization.jsonObject(with: data) else { return [:] }
        return object
    }

    private func iso(_ date: Date) -> String { Date.ISO8601FormatStyle().format(date) }

    // MARK: Data

    static func standard(now: Date) -> [Worktree] {
        func ago(_ days: Double) -> Date { now.addingTimeInterval(-days * 86_400) }
        func mb(_ n: Int) -> Int { n * 1_000_000 }
        let swe = "/Users/demo/Projects/swe", dots = "/Users/demo/Projects/dotfiles"
        let site = "/Users/demo/Projects/agent-site"
        func pr(_ repo: String, _ n: Int, _ state: String) -> WorktreePR {
            WorktreePR(number: n, url: "https://github.com/\(repo)/pull/\(n)", state: state)
        }
        return [
            Worktree(path: swe, repo: "c-wenlong/swe", mainCheckout: swe, branch: "main", head: "7156ada8b2c1",
                     state: .main, lastCommitAt: ago(0.1), sizeBytes: mb(2_400), rebuildableBytes: mb(1_100),
                     harness: "other"),
            Worktree(path: swe + "/.claude/worktrees/mac-worktrees-page", repo: "c-wenlong/swe", mainCheckout: swe,
                     branch: "mac/worktrees-page", head: "9f3e1c0a77d2", state: .openPr,
                     pr: pr("c-wenlong/swe", 1912, "OPEN"), lastCommitAt: ago(0.04), sizeBytes: mb(640),
                     rebuildableBytes: mb(520), dirty: true, untracked: 2, harness: "claude",
                     reason: "Pull request #1912 is open", hold: "You are inside it"),
            Worktree(path: swe + "/.claude/worktrees/cli-wt-commands", repo: "c-wenlong/swe", mainCheckout: swe,
                     branch: "cli/wt-commands", head: "41c0de99ab10", state: .openPr,
                     pr: pr("c-wenlong/swe", 1911, "OPEN"), lastCommitAt: ago(0.3), sizeBytes: mb(580),
                     rebuildableBytes: mb(470), harness: "codex"),
            Worktree(path: swe + "/.claude/worktrees/fix-hooks-race", repo: "c-wenlong/swe", mainCheckout: swe,
                     branch: "fix/hooks-race", head: "c5d6e7f80912", state: .mergedClean,
                     pr: pr("c-wenlong/swe", 1890, "MERGED"), lastCommitAt: ago(3), sizeBytes: mb(810),
                     rebuildableBytes: mb(700), harness: "claude",
                     reason: "Pull request #1890 merged and the folder is clean"),
            Worktree(path: swe + "/.claude/worktrees/docs-roadmap-refresh", repo: "c-wenlong/swe", mainCheckout: swe,
                     branch: "docs/roadmap-refresh", head: "0a1b2c3d4e5f", state: .mergedClean,
                     pr: pr("c-wenlong/swe", 1884, "MERGED"), lastCommitAt: ago(9), sizeBytes: mb(450),
                     rebuildableBytes: mb(380), harness: "devin"),
            Worktree(path: swe + "/.claude/worktrees/pr-1877", repo: "c-wenlong/swe", mainCheckout: swe,
                     branch: "pr-1877", head: "7788aabbccdd", state: .mergedDirty,
                     pr: pr("c-wenlong/swe", 1877, "MERGED"), lastCommitAt: ago(14), sizeBytes: mb(390),
                     rebuildableBytes: mb(300), dirty: true, untracked: 4, harness: "swe-pr"),
            Worktree(path: swe + "/.claude/worktrees/spike-vector-index", repo: "c-wenlong/swe", mainCheckout: swe,
                     branch: "spike/vector-index", head: "deadbeef0042", state: .noPr, lastCommitAt: ago(5),
                     sizeBytes: mb(520), rebuildableBytes: mb(410), harness: "codex"),
            Worktree(path: swe + "/.claude/worktrees/old-experiment", repo: "c-wenlong/swe", mainCheckout: swe,
                     branch: nil, head: "123456789abc", state: .orphaned, lastCommitAt: ago(41),
                     sizeBytes: nil, rebuildableBytes: nil, harness: "other",
                     reason: "git no longer lists this folder as a worktree", detached: true),
            Worktree(path: "/Users/demo/Projects/release-1.4", repo: "c-wenlong/swe", mainCheckout: swe,
                     branch: "release/1.4", head: "feedface0001", state: .protected, lastCommitAt: ago(20),
                     sizeBytes: mb(900), rebuildableBytes: mb(750), locked: true,
                     lockReason: "Release branch, do not touch", harness: "other",
                     reason: "Release checkout swe never removes", hold: "Locked"),
            Worktree(path: dots, repo: "kaichen/dotfiles", mainCheckout: dots, branch: "main", head: "b00b1e500000",
                     state: .main, lastCommitAt: ago(1), sizeBytes: mb(180), rebuildableBytes: 0, harness: "other"),
            Worktree(path: dots + "/.claude/worktrees/nix-bump-inputs", repo: "kaichen/dotfiles", mainCheckout: dots,
                     branch: "nix/bump-inputs", head: "cafe00112233", state: .mergedClean,
                     pr: pr("kaichen/dotfiles", 212, "MERGED"), lastCommitAt: ago(6), sizeBytes: mb(95),
                     rebuildableBytes: mb(20), harness: "claude"),
            Worktree(path: dots + "/.claude/worktrees/tmux-popup", repo: "kaichen/dotfiles", mainCheckout: dots,
                     branch: "tmux/popup", head: "99aa88bb77cc", state: .unknown, lastCommitAt: ago(27),
                     sizeBytes: mb(60), rebuildableBytes: 0, harness: "other"),
            Worktree(path: site, repo: "kai/agent-site", mainCheckout: site, branch: "main", head: "11223344aabb",
                     state: .main, lastCommitAt: ago(2), sizeBytes: mb(1_300), rebuildableBytes: mb(1_000),
                     harness: "other"),
            Worktree(path: site + "/.claude/worktrees/redesign-hero", repo: "kai/agent-site", mainCheckout: site,
                     branch: "redesign/hero", head: "5566778899aa", state: .openPr,
                     pr: pr("kai/agent-site", 58, "OPEN"), lastCommitAt: ago(1.5), sizeBytes: mb(1_100),
                     rebuildableBytes: mb(950), locked: true, lockReason: "Waiting on design review",
                     harness: "claude", reason: "Pull request #58 is open", hold: "Locked"),
        ]
    }
}

