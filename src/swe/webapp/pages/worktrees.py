"""The Worktrees page: ``swe wt`` built in process from ``swe.worktrees``.

``GET /v1/app/worktrees`` is ``swe wt list``: ``scope=all`` (the default)
lists every worktree of every repo the hub knows, ``scope=repo&repo=<path>``
one repo's (``repo=`` of the API). The reply is the list's own JSON (``scope``, ``generated_at``,
``worktrees``) plus the hub's ``machine`` name.

``GET /v1/app/worktrees?worktree=<path>`` is ``swe wt show``: ``{"worktree": W}``.

``POST /v1/app/worktrees/<action>`` runs one write and answers with that
command's JSON, unchanged:

| action | body | command |
|---|---|---|
| ``add`` | ``repo``, ``branch`` (+ ``new``, ``base``) or ``pr``, optional ``path`` | ``swe wt add`` |
| ``remove`` | ``path``, ``force``, ``delete_branch`` | ``swe wt remove`` |
| ``lock`` | ``path``, optional ``reason`` | ``swe wt lock`` |
| ``unlock`` | ``path`` | ``swe wt unlock`` |
| ``move`` | ``path``, ``new_path`` | ``swe wt move`` |
| ``prune`` | ``all`` or ``repo``; ``yes`` with ``paths``; ``include``, ``branch`` | ``swe wt prune`` |
| ``slim`` | ``path``, or ``all`` or ``repo``; ``yes``, ``days`` | ``swe wt slim`` |

Nothing destructive runs unless the body says so: ``force``, ``yes`` and
``delete_branch`` count only when they are the JSON value ``true``, so a
string like ``"false"`` or a stray ``1`` never turns one on. ``prune`` and
``slim`` are dry runs until ``yes`` is true, and a ``prune`` with ``yes`` must
name the previewed ``paths``: it refuses (409 ``changed``) when more would go. A ``WorktreeError`` becomes a
``PageError`` with the same code and message.

``swe.worktrees`` is imported when a request needs it, so the page loads
(and answers 503 ``unavailable``) on a build that does not ship it.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from typing import Any

from swe.webapp.pages import PageError

# ``WorktreeError.code`` to an HTTP status: the closed set swe.worktrees
# raises (docs/json-contracts.md, "swe wt shapes").
_STATUS = {
    "bad_args": 400,
    "not_a_repo": 404,
    "not_found": 404,
    "not_safe": 409,
    "exists": 409,
    "busy": 409,
    "gh_unavailable": 503,
    "git_failed": 502,
    "bad_config": 500,
    "io_error": 500,
}

# One write at a time: each one runs git against the same repos, and a
# prune walking the list must not race an add or a remove.
_WRITE_LOCK = threading.Lock()


def _api():
    """The ``swe.worktrees`` module, imported on demand."""
    try:
        return importlib.import_module("swe.worktrees")
    except ImportError as exc:
        raise PageError(503, "unavailable",
                        f"This swe build has no worktree support ({exc}).") from exc


def _raise(wt, exc: Exception) -> None:
    """Re-raise ``exc`` as a PageError when it is a WorktreeError."""
    code = str(getattr(exc, "code", "") or "worktree_error")
    message = str(getattr(exc, "message", "") or exc)
    if code == "bad_config":
        message = f"swe's worktree settings are invalid: {message}"
    raise PageError(_STATUS.get(code, 500), code, message) from exc


def _call(ctx, fn_name: str, *args, **kwargs) -> dict:
    """``swe.worktrees.<fn_name>(..., home=<the hub's home>)`` with a
    WorktreeError as PageError."""
    wt = _api()
    try:
        return getattr(wt, fn_name)(*args, home=getattr(ctx, "home", None), **kwargs)
    except wt.WorktreeError as exc:
        _raise(wt, exc)
    raise AssertionError("unreachable")  # pragma: no cover


def _text(body: dict, key: str, *, required: bool = False) -> str | None:
    """``body[key]`` as a trimmed string, or None when absent or blank."""
    value = body.get(key)
    if value is None or value == "":
        if required:
            raise PageError(400, "bad_request", f"{key} is required.")
        return None
    if not isinstance(value, str):
        raise PageError(400, "bad_request", f"{key} must be a string.")
    value = value.strip()
    if not value and required:
        raise PageError(400, "bad_request", f"{key} is required.")
    return value or None


def _path(body: dict, key: str = "path", *, required: bool = True) -> str | None:
    """An absolute path from the body. Never a relative one: the hub's own
    working directory means nothing to the person who sent it."""
    value = _text(body, key, required=required)
    if value is not None and not Path(value).is_absolute():
        raise PageError(400, "bad_request", f"{key} must be an absolute path.")
    return value


def _flag(body: dict, key: str) -> bool:
    """True only for the JSON value ``true``."""
    value = body.get(key)
    if value is None or isinstance(value, bool):
        return value is True
    raise PageError(400, "bad_request", f"{key} must be true or false.")


# ---------------------------------------------------------------- reads

def get(ctx, args: dict) -> dict:
    worktree = args.get("worktree")
    if worktree:
        if not Path(worktree).is_absolute():
            raise PageError(400, "bad_request", "worktree must be an absolute path.")
        return _call(ctx, "show", worktree)
    scope = args.get("scope") or "all"
    if scope not in ("all", "repo"):
        raise PageError(400, "bad_request", "scope must be all or repo.")
    repo = args.get("repo") or None
    if scope == "repo" and not repo:
        raise PageError(400, "bad_request", "scope=repo needs repo=<path>.")
    if repo and not Path(repo).is_absolute():
        raise PageError(400, "bad_request", "repo must be an absolute path.")
    if scope == "repo":
        data = _call(ctx, "list_worktrees", repo=repo)
    else:
        data = _call(ctx, "list_worktrees", all=True)
    out = dict(data)
    out["machine"] = (getattr(ctx, "machine", None) or {}).get("name")
    return out


# --------------------------------------------------------------- writes

def post(ctx, action: str, body: dict) -> dict:
    handler = _ACTIONS.get(action)
    if handler is None:
        raise PageError(404, "not_found", "unknown worktrees action")
    if not isinstance(body, dict):
        raise PageError(400, "bad_request", "the body must be a JSON object.")
    if not _WRITE_LOCK.acquire(blocking=False):
        raise PageError(409, "busy", "Another worktree operation is running. Try again in a moment.")
    try:
        return handler(ctx, body)
    finally:
        _WRITE_LOCK.release()


def _add(ctx, body: dict) -> dict:
    repo = _path(body, "repo")
    branch = _text(body, "branch")
    pr = body.get("pr")
    if pr is not None:
        if isinstance(pr, bool) or not isinstance(pr, int) or pr < 1:
            raise PageError(400, "bad_request", "pr must be a positive number.")
    if (branch is None) == (pr is None):
        raise PageError(400, "bad_request", "Give a branch or a pull request number, not both and not neither.")
    new = _flag(body, "new")
    base = _text(body, "base")
    if pr is not None and (new or base):
        raise PageError(400, "bad_request", "A pull request checks out its own branch: new and base don't apply.")
    if base and not new:
        raise PageError(400, "bad_request", "base only applies to a new branch.")
    path = _path(body, "path", required=False)
    if pr is not None:
        return _call(ctx, "add", repo, pr=pr, path=path)
    return _call(ctx, "add", repo, branch=branch, new=new, base=base, path=path)


def _remove(ctx, body: dict) -> dict:
    path = _path(body)
    return _call(ctx, "remove", path, force=_flag(body, "force"),
                 delete_branch=_flag(body, "delete_branch"))


def _lock(ctx, body: dict) -> dict:
    return _call(ctx, "lock", _path(body), reason=_text(body, "reason"))


def _unlock(ctx, body: dict) -> dict:
    return _call(ctx, "unlock", _path(body))


def _move(ctx, body: dict) -> dict:
    return _call(ctx, "move", _path(body), _path(body, "new_path"))


def _scope(body: dict) -> dict:
    """``all`` (every repo) or ``repo`` (one), never neither: with neither
    the API would scan the hub process's own working directory."""
    every = _flag(body, "all")
    repo = _path(body, "repo", required=False)
    if every == (repo is not None):
        raise PageError(400, "bad_request", "Give all, or one repo path, not both and not neither.")
    return {"repo": repo} if repo else {"all": True}


def _prune(ctx, body: dict) -> dict:
    include = body.get("include")
    if include is not None and include != "merged-dirty":
        raise PageError(400, "bad_request", "include can only be merged-dirty.")
    args = dict(_scope(body), include=include, branch=_text(body, "branch"))
    if not _flag(body, "yes"):
        return _call(ctx, "prune", yes=False, **args)
    # Deleting takes the paths the person saw in the preview, and only
    # those can go. merged-clean worktrees have no per-item gate in the
    # API, so a changed list is refused here and previewed again.
    paths = body.get("paths")
    if (not isinstance(paths, list) or not paths
            or not all(isinstance(p, str) and Path(p).is_absolute() for p in paths)):
        raise PageError(400, "bad_request", "yes needs paths: the worktrees the preview listed.")
    previewed = set(paths)
    dry = _call(ctx, "prune", yes=False, **args)
    extra = [c.get("path") for c in dry.get("candidates", []) if c.get("path") not in previewed]
    if extra:
        raise PageError(409, "changed", "The list changed since the preview "
                        f"({len(extra)} more would be deleted). Preview again.")
    return _call(ctx, "prune", yes=True, confirm=lambda wt: wt.path in previewed, **args)


def _slim(ctx, body: dict) -> dict:
    path = _path(body, "path", required=False)
    days = body.get("days")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 0):
        raise PageError(400, "bad_request", "days must be a whole number of days.")
    extra = {} if days is None else {"days": days}
    if path is not None:
        if _flag(body, "all") or body.get("repo"):
            raise PageError(400, "bad_request", "Give one worktree path, or a scope, not both.")
        return _call(ctx, "slim", path, yes=_flag(body, "yes"), **extra)
    return _call(ctx, "slim", yes=_flag(body, "yes"), **_scope(body), **extra)


_ACTIONS: dict[str, Any] = {
    "add": _add, "remove": _remove, "lock": _lock, "unlock": _unlock,
    "move": _move, "prune": _prune, "slim": _slim,
}
