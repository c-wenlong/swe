"""The one place `swe wt` spawns `git`.

Every read passes ``--no-optional-locks``: `git status` would otherwise
refresh the index of the worktree it looks at, which both writes during a
dry run and bumps the mtime the recent-use check reads.
"""

from __future__ import annotations

import os
import subprocess

GIT_TIMEOUT = 120


def git(args: list[str], cwd: str | None = None, *, stdin: str | None = None,
        timeout: int = GIT_TIMEOUT) -> tuple[int, str, str]:
    """``(returncode, stdout, stderr)``; never raises. A missing git or a
    timeout is returncode 127 / 124 with the reason in stderr."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    try:
        done = subprocess.run(
            ["git", "--no-optional-locks", *args], cwd=cwd, input=stdin,
            capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        return 127, "", "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr.strip()


def out(args: list[str], cwd: str | None = None) -> str | None:
    """Stripped stdout, or None when git failed."""
    rc, stdout, _ = git(args, cwd)
    return stdout.strip() if rc == 0 else None


def is_ancestor(git_dir: str, rev: str, ref: str) -> bool:
    rc, _, _ = git(["--git-dir", git_dir, "merge-base", "--is-ancestor",
                    rev, ref])
    return rc == 0
