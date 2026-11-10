#!/usr/bin/env bash
#
# reaper.sh: reap stale agent worktrees in this repo (issue #747).
#
# A thin wrapper over `swe wt` (issue #1909), which now owns every rule
# this script used to carry, for every repo and harness folder rather than
# only .claude/worktrees/ here. Keeping the rules in one place stops the
# two from drifting apart. Safe by default: nothing changes without
# --apply (or --yes).
#
#   scripts/reaper.sh [--dry-run | --apply | --yes] [--days=N] [REPO_PATH]
#
#     --dry-run   print what would be removed (default)
#     --apply     actually remove (--yes is an alias)
#     --days=N    staleness threshold for rebuildable dirs (default: 14)
#     REPO_PATH   any path inside the repo (default: this script's repo)
#
# It runs, from REPO_PATH:
#   swe wt prune [--yes] --delete-branches   merged-clean worktrees, then the
#                                            merged local branches they leave
#   swe wt slim  [--yes] --days=N            ignored build/.venv/node_modules
#                                            dirs in unmerged worktrees
#
# The swe code comes from this checkout's src/, so the rules match the
# script's own revision. From cron:
#   15 4 * * *  /path/to/repo/scripts/reaper.sh --yes >> ~/.swe/logs/reaper.log 2>&1

set -euo pipefail

DAYS=14
APPLY=0
REPO_ARG=""

usage() { sed -n '2,27p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//'; }
die() { printf 'reaper: ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		--dry-run) APPLY=0 ;;
		--apply | --yes) APPLY=1 ;;
		--days=*) DAYS="${1#*=}" ;;
		--days) die "write it as --days=N" ;;
		-h | --help) usage; exit 0 ;;
		-*) die "unknown flag: $1" ;;
		*)
			[[ -z "$REPO_ARG" ]] || die "unexpected extra argument: $1"
			REPO_ARG="$1"
			;;
	esac
	shift
done

[[ "$DAYS" =~ ^[0-9]+$ ]] || die "--days must be a non-negative integer, got: $DAYS"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SRC="$(cd "$SCRIPT_DIR/../src" && pwd -P)"
TARGET="${REPO_ARG:-$SCRIPT_DIR}"
[[ -d "$TARGET" ]] || die "no such directory: $TARGET"
git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: $TARGET"
cd "$TARGET"

swe() { PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}" python3 -m swe.cli "$@"; }

yes=()
[[ $APPLY -eq 1 ]] && yes=(--yes)

swe wt prune "${yes[@]+"${yes[@]}"}" --delete-branches
swe wt slim "${yes[@]+"${yes[@]}"}" "--days=$DAYS"
