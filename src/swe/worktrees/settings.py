"""The ``worktree`` block of ``~/.swe/config/config.json``.

    "worktree": {
      "roots":   ["~/code/work-monorepo", "~/code"],   # repos, or folders of repos
      "protect": ["~/code/keep-this-worktree"],
      "repos": {
        "owner/name": {"ignore": [".env.local"], "auto_prune": true}
      }
    }

A ``repos`` key is the GitHub ``owner/name`` or the main checkout's path.
Everything is optional; a missing or unreadable file means no settings.
"""

from __future__ import annotations

import os
from pathlib import Path

from swe import configuration, paths
from swe.worktrees.models import Repo


def _expand(value: str, home: Path | None) -> str:
    base = str(home or Path.home())
    if value == "~" or value.startswith("~/"):
        value = base + value[1:]
    return os.path.realpath(value)


class Settings:
    def __init__(self, data: dict | None = None, home: Path | None = None):
        block = (data or {}).get("worktree")
        self.block = block if isinstance(block, dict) else {}
        self.home = home

    @classmethod
    def load(cls, home: Path | None = None) -> "Settings":
        try:
            data = configuration.load_config(paths.config_file_for(home))
        except configuration.ConfigurationError:
            data = {}
        return cls(data, home)

    def _paths(self, key: str) -> list[str]:
        raw = self.block.get(key)
        if not isinstance(raw, list):
            return []
        return [_expand(v, self.home) for v in raw if isinstance(v, str) and v]

    def roots(self) -> list[str]:
        return self._paths("roots")

    def protect(self) -> list[str]:
        builtin = str(paths.cache_dir_for(self.home) / "install" / "src")
        return [os.path.realpath(builtin), *self._paths("protect")]

    def repo(self, repo: Repo | None) -> dict:
        repos = self.block.get("repos")
        if repo is None or not isinstance(repos, dict):
            return {}
        for key in (repo.slug, repo.root):
            entry = repos.get(key) if key else None
            if isinstance(entry, dict):
                return entry
        for key, entry in repos.items():
            if (isinstance(entry, dict) and isinstance(key, str)
                    and ("/" in key and key.startswith(("~", "/")))
                    and _expand(key, self.home) == repo.root):
                return entry
        return {}

    def ignore(self, repo: Repo | None) -> list[str]:
        raw = self.repo(repo).get("ignore")
        return [p for p in raw if isinstance(p, str) and p] if isinstance(raw, list) else []

    def auto_prune(self, repo: Repo | None) -> bool:
        return self.repo(repo).get("auto_prune") is True
