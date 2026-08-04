"""Deliver through cc-notify, so the brief posts under its own identity.

macOS draws a banner's icon, and its Notification Center grouping, from the
*sending* app. Shelling out to a bare ``terminal-notifier`` means sending as
``fr.julienxx.oss.terminal-notifier`` -- the identity every other tool doing the
same thing also sends as -- so a nightly brief lands in one undifferentiated pile
with everything else on the machine that posts notifications.

`cc-notify <https://github.com/hancheng-ai/cc-notify>`_ solved that for itself by
building a re-badged private copy of the notifier carrying its own bundle id, and
grew a ``--send`` mode so other local tools can do the same. ``--badge nextbrief``
mints ``ai.hancheng.cc-notify.nextbrief``, which groups on its own and says
"nextbrief" rather than the notifier's name.

This is the shape ``CONTRIBUTING.md`` asks a sink to take: it shells out to a CLI
the user already has, adds no dependency, and returns ``False`` rather than
raising when that CLI is absent -- which is most machines, so the fallback is the
normal path rather than the exceptional one.

**Its exit code is the contract.** cc-notify documents ``0`` only when a banner
was actually delivered, checking each rung of its own fallback rather than firing
and forgetting, because an unauthorized bundle id fails *silently* on macOS. That
is precisely the failure a caller must not be told is success -- so this module
can trust the code, and `sinks.notify` can fall through to `macos` on anything
else.

Click handling is cc-notify's too, and better than what it replaces: it resolves
the target when the banner is clicked rather than freezing a URL at post time, so
a brief regenerated overnight opens as it is *now*, and one since deleted does
nothing rather than raising an error at somebody who only clicked a notification.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, List, Optional

__all__ = ["available", "send"]

# Long enough for a re-badged copy to be built on the first call -- cc-notify
# caches ~1.2 MB per caller and re-signs it -- short enough that an unattended
# run is never held up by a notifier. A notification is the least important thing
# in the pipeline and must never be able to stall it.
TIMEOUT = 45

# `[a-z0-9][a-z0-9-]{0,31}`, which cc-notify validates before the name reaches a
# bundle id or a filesystem path. Hard-coded rather than configurable: this names
# *this tool*, and a per-workspace badge would scatter one program's banners
# across several identities, which is the problem rather than a feature.
BADGE = "nextbrief"

# Where cc-notify actually lives, most specific first. The README documents the
# hook path, and a plugin install lands somewhere else entirely -- on the machine
# this was written against, only the marketplace path existed. Searching rather
# than assuming is the difference between a working sink and one that silently
# never fires.
CANDIDATES = (
    "~/.claude/hooks/notify.py",
    "~/.claude/plugins/marketplaces/hancheng-ai/notify.py",
    "~/.claude/plugins/cc-notify/notify.py",
)


def _script(cfg: Any = None) -> Optional[str]:
    """Path to cc-notify's ``notify.py``, or None.

    An explicit ``notify.cc_notify_path`` wins, so a machine that keeps it
    somewhere else says so once instead of going without.
    """
    section = (cfg or {}).get("notify") if isinstance(cfg, dict) else None
    declared = (section or {}).get("cc_notify_path") if isinstance(section, dict) else None
    for candidate in ([declared] if declared else []) + list(CANDIDATES):
        try:
            path = os.path.expanduser(str(candidate))
        except (TypeError, ValueError):
            continue
        if os.path.isfile(path):
            return path
    return None


def _python() -> Optional[str]:
    """An interpreter to run it with.

    `sys.executable` is deliberately not used. The nightly run is launched by a
    GUI scheduler with a minimal PATH, and may itself be a pipx-managed
    interpreter inside a virtualenv; cc-notify is standard-library-only and wants
    a plain python3. Falling back to `sys.executable` would still work, and is
    the last rung rather than the first.
    """
    import sys

    return shutil.which("python3") or shutil.which("python") or sys.executable


def available(cfg: Any = None) -> bool:
    return _script(cfg) is not None and _python() is not None


def send(title: str, body: str, cfg: Any = None, open_url: Optional[str] = None) -> bool:
    script, python = _script(cfg), _python()
    if script is None or python is None:
        return False

    # Every value its own argv entry. The body is assembled from project files
    # this engine only ever reads -- prose somebody else wrote, in a document it
    # does not control -- so it is hostile input by construction, and there is no
    # shell for it to be hostile at.
    argv: List[str] = [python, script, "--send",
                       "--title", title, "--message", body, "--badge", BADGE]
    if open_url:
        argv += ["--open", open_url]

    try:
        proc = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        # Includes TimeoutExpired. A notifier that hangs has already cost the run
        # more than it is worth; the caller falls through to the next sink.
        return False
    return proc.returncode == 0
