"""`swe worktree` (alias `swe wt`): find, classify, prune and slim the git
worktrees coding agents leave behind, across every repo and harness.

``discover`` finds checkouts, ``classify`` gives each one state,
``sizing`` measures them, ``actions`` removes or slims them, and
``commands`` is the controller. ``models`` holds the one ``--json`` row.
"""

from swe.worktrees.api import (WorktreeError, add, list_worktrees, lock,  # noqa: E402
                               move, prune, remove, show, slim, unlock)

__all__ = ["WorktreeError", "add", "list_worktrees", "lock", "move", "prune",
           "remove", "show", "slim", "unlock"]
