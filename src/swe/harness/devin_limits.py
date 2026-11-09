"""Observed free-model limits from Devin's own CLI errors.

The seat-management API reports plan quota, not free-model allowance.
Logs provide evidence of a reached limit and its UTC reset, never a
remaining percentage or proof that an account is unrestricted.
"""

from __future__ import annotations

import datetime as dt
import gzip
import os
import re
import time
import zlib
from pathlib import Path


_ERROR = re.compile(
    r"^(\d{4}-\d\d-\d\dT[\d:.]+Z)\s+(?:WARN|ERROR)\s+"
    r"affogato::[^\n]*?Reached free model rate limit\. "
    r"Please switch to a different model\. Your limit will reset in "
    r"[^\n]*?\(at (\d\d):(\d\d) UTC\)"
)
_MAX_BYTES = 2 * 1024 * 1024
_MAX_GZIP_BYTES = 16 * 1024 * 1024


def free_model_reset(credentials_path: str, *, now: float | None = None) -> float:
    """Last observed reset for this account directory, or 0 (unknown).

    Inspect at most 32 recent logs, with bounded reads including rotated
    gzip logs. Errors in one log do not hide evidence in the others. The
    UTC clock in an error belongs to its logged date, rolling into the
    next day at midnight. An expired observation is no longer a limit.
    """
    now = time.time() if now is None else now
    credentials = Path(os.path.expanduser(credentials_path))
    root = credentials.parent / "cli" / "logs"
    recent = now - 7 * 86400
    # A new sign-in can reuse a directory. Its predecessor's block must
    # not be attributed to the new credentials. A same-account credential
    # rewrite may conservatively make the allowance unknown again.
    try:
        recent = max(recent, credentials.stat().st_mtime)
    except OSError:
        pass
    files = []
    try:
        for path in root.iterdir():
            if not (path.name.startswith("devin_")
                    and (path.name.endswith(".log") or path.name.endswith(".log.gz"))):
                continue
            try:
                stamp = path.stat().st_mtime
            except OSError:
                continue
            if stamp >= recent:
                files.append((stamp, path))
    except OSError:
        return 0.0

    latest = 0.0
    reset = 0.0
    for _, path in sorted(files, key=lambda item: item[0], reverse=True)[:32]:
        try:
            if path.suffix == ".gz":
                # Rotation bounds ordinary logs; cap decompression as well.
                with gzip.open(path, "rb") as stream:
                    raw = stream.read(_MAX_GZIP_BYTES + 1)
                if len(raw) > _MAX_GZIP_BYTES:
                    continue
                raw = raw[-_MAX_BYTES:]
            else:
                with path.open("rb") as stream:
                    stream.seek(0, os.SEEK_END)
                    stream.seek(max(0, stream.tell() - _MAX_BYTES))
                    raw = stream.read(_MAX_BYTES)
            for line in raw.decode("utf-8", errors="replace").splitlines():
                match = _ERROR.search(line)
                if not match:
                    continue
                try:
                    seen = dt.datetime.fromisoformat(match[1].replace("Z", "+00:00"))
                    until = seen.replace(hour=int(match[2]), minute=int(match[3]),
                                         second=0, microsecond=0)
                except ValueError:
                    continue
                if until < seen - dt.timedelta(minutes=1):
                    until += dt.timedelta(days=1)
                stamp = seen.timestamp()
                if recent <= stamp <= now and stamp > latest:
                    latest, reset = stamp, until.timestamp()
        except (OSError, EOFError, zlib.error):
            continue
    return reset if reset > now else 0.0
