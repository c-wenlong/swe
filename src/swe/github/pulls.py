"""Pull-request reads and prompt building for the hub's ``/v1/pulls``
routes.

``fetch`` is ``swe gh pr view <n> --repo=<repo> --json`` shaped by
``cli._pr_detail`` plus the fields a phone needs before it opens a
session: ``association`` (the author's ``author_association``, which
decides whether the PR's text joins the prompt), ``worktree`` (where
``manage.pr_worktree`` puts the checkout, so the client can name the
folder before it exists) and ``suggested_prompt`` (a review/fix brief
the Open-in-session sheet prefills and the owner edits).
"""

from __future__ import annotations

from pathlib import Path

from swe.github import cli as ghcli
from swe.github import gh, manage
from swe.github import issues as gh_issues

# A PR body can be long; the prompt is capped (protocol.MAX_LAUNCH_PROMPT)
# and the harness reads the diff itself, so the body earns most of the
# room and the failing-checks line the rest.
BODY_LIMIT = 20_000

# The failing-checks line's cap: dozens of names joined could otherwise
# eat the body's room — or, worse, push the whole prompt past the
# launch cap the phone sends it back under.
CHECKS_LIMIT = 4_000

_FAILING = {"failing"}


def fetch(repo: str, number, home: Path | None = None,
          prompt_limit: int | None = None) -> gh.GhResult:
    """``gh pr view`` for ``number`` in ``repo``, shaped for the client.

    ``data`` is ``cli._pr_detail``'s row plus ``association``,
    ``worktree`` and ``suggested_prompt``; failures keep ``gh``'s
    outcome word (``not-found``, ``upstream-error``, ``unavailable``).
    ``prompt_limit`` bounds ``suggested_prompt`` — callers pass
    ``protocol.MAX_LAUNCH_PROMPT`` so the phone can send it back
    verbatim.
    """
    result = gh.run_json(
        ["pr", "view", str(number), "--repo", repo,
         "--json", ghcli._PR_DETAIL_FIELDS], timeout=60)
    if result.status != "ok":
        return result
    if not isinstance(result.data, dict):
        return gh.GhResult(
            "upstream-error", error="gh returned an invalid pull request")
    raw = {**result.data,
           "authorAssociation": author_association(repo, number)}
    return gh.GhResult("ok", data=shape(raw, repo, home, prompt_limit))


def author_association(repo: str, number) -> str:
    """The PR author's ``author_association`` (``OWNER``, ``MEMBER``,
    ``COLLABORATOR``, ``CONTRIBUTOR``, ``NONE``, ...), or "" when it
    can't be read. ``gh pr view --json`` has no such field, so this is
    the REST read; "" counts as untrusted, so a failed read only hides
    the PR's text from the prompt."""
    result = gh.run_json(
        ["api", f"repos/{repo}/pulls/{number}",
         "--jq", "{association: .author_association}"], timeout=60)
    if result.status != "ok" or not isinstance(result.data, dict):
        return ""
    return str(result.data.get("association") or "").upper()


def shape(raw: dict, repo: str, home: Path | None = None,
          prompt_limit: int | None = None) -> dict:
    """The detail payload ``/v1/pulls/<o>/<r>/<n>`` answers with:
    ``raw`` is ``gh pr view``'s reply plus ``authorAssociation``."""
    detail = ghcli._pr_detail(raw, repo)
    detail["association"] = str(raw.get("authorAssociation") or "").upper()
    detail["worktree"] = worktree_path(repo, detail.get("number"), home)
    detail["suggested_prompt"] = build_prompt(
        repo, detail, detail["worktree"], limit=prompt_limit)
    return detail


# Where ``manage.pr_worktree`` puts the checkout: its own naming, so a
# pending launch's cwd is the folder the worktree call returns.
worktree_path = manage.pr_worktree_path


def build_prompt(repo: str, pr: dict | None, worktree: str,
                 limit: int | None = None) -> str:
    """The first prompt an "Open in session" launch starts with when the
    client sent none: review the PR and fix what needs fixing.

    ``pr`` is a ``_pr_detail`` row with ``association``, or
    ``{"number": n}`` when the PR could not be read (the caller already
    refused that case unless a client prompt covers it). Anyone can open
    a PR on a public repo, and a fork's workflow names its own checks,
    so the title and body arrive quoted line by line behind a note that
    they are data, not instructions (the resolve prompt's rule, see
    ``github/issues.py``), and only when the author is the repo's
    owner, an org member or a collaborator; otherwise one line says
    they were left out. Failing check names are quoted too. ``limit``
    bounds the whole assembled prompt — ``suggested_prompt`` comes back
    as an open's ``prompt``, so it must fit the same cap the route
    validates against; the body is cut to make room.
    """
    pr = pr or {}
    number = pr.get("number")
    url = pr.get("url") or f"https://github.com/{repo}/pull/{number}"
    head = f"Review {url} ({repo}#{number})."
    author = pr.get("author") if pr.get("author") not in (
        None, "", "-") else ""
    title = (pr.get("title") or "").strip()
    read = "title" in pr or "body" in pr
    shown = read and gh_issues.trusted(pr)
    body = (pr.get("body") or "").strip() or "(no description)"
    if len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT].rstrip() + "\n…"
    failing = _failing(pr)
    if failing:
        names = "\n".join(failing)
        if len(names) > CHECKS_LIMIT:
            names = names[:CHECKS_LIMIT].rstrip() + "\n…"
        checks = "Failing checks:\n" + gh_issues.quote(names)
    else:
        checks = _STATE_LINES.get(pr.get("checks") or "", "")
    branch = pr.get("branch")
    closing = (
        f"You are in a detached checkout of the pull request's head "
        f"(refs/pull/{number}/head of {repo}) at {worktree}. The base "
        f"branch is {pr.get('base') or 'the default branch'}. Review "
        "the diff, run the tests if that helps, and fix what needs "
        "fixing. "
        f"When the head branch {f'`{branch}`' if branch else 'of the PR'} "
        "lives in this repo, commit on HEAD and push to it "
        "(git push origin HEAD:<branch>); for a fork PR keep the fix "
        "committed and say so in your reply.")

    def assemble(body_text: str) -> str:
        blocks = [head]
        if shown or failing:
            blocks.append(gh_issues.untrusted_note("pull request"))
        if shown:
            by = f" by {author}" if author else ""
            quoted = (f"Title: {title}\n\n" if title else "") + body_text
            blocks.append(f"The pull request, opened{by}:\n"
                          + gh_issues.quote(quoted))
        elif read:
            who = author or "its author"
            blocks.append(
                f"The title and description, by {who}, are not included: "
                "only the repo owner's, members' and collaborators' are.")
        if checks:
            blocks.append(checks)
        blocks.append(closing)
        return "\n\n".join(blocks)

    text = assemble(body)
    # Quoting grows the body by two characters a line, so one cut sized
    # on the raw length can still land over: cut again until it fits.
    room = len(body)
    while limit is not None and len(text) > limit and shown and room > 0:
        room = max(0, room - (len(text) - limit) - 80)
        text = assemble(body[:room].rstrip() + "\n…")
    return text


_STATE_LINES = {
    "passing": "All checks pass.",
    "running": "Checks are still running.",
    "none": "No checks are configured.",
    "unknown": "Check state is unknown.",
}


def _failing(pr: dict) -> list[str]:
    """The failing checks' names, as the repo's CI (or a fork's
    workflow) wrote them."""
    return [str(r.get("name") or "?")
            for r in pr.get("check_runs") or []
            if isinstance(r, dict) and r.get("state") in _FAILING]
