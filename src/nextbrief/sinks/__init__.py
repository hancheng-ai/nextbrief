"""Desktop notification sinks.

The brief is a file; the notification is only a nudge towards it. That ordering
decides everything here: a missing or broken notifier returns ``False`` and the
run is still a success, because the artifact -- the thing you actually read --
was written either way. Nothing in this package raises.

The other half of the contract lives upstream, in the *silence rule*: a system
that tells you at the same time every day that nothing has changed gets muted in
week three, and after that it can never tell you anything. Deciding whether
tonight is worth interrupting for is the caller's job. By the time a sink is
called, that judgement has already been made.

Dispatch is by platform, with an explicit override:

    "notify": { "enabled": true, "backend": "auto", "title": "..." }

``auto`` picks macOS on Darwin, ``notify-send`` on Linux, and nothing anywhere
else -- Windows has no dependency-free equivalent, and a sink that returns False
is more honest than one that pretends.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional

from . import linux as _linux
from . import macos as _macos
from . import none as _none

__all__ = ["notify", "SINKS", "resolve_backend", "MAX_LEN"]

SINKS = {
    "macos": _macos,
    "linux": _linux,
    "none": _none,
}

ALIASES = {
    "darwin": "macos",
    "osascript": "macos",
    "notify-send": "linux",
    "libnotify": "linux",
    "off": "none",
    "false": "none",
}

# Both notification systems truncate long text themselves, at lengths they do not
# document and do not agree on. Cutting here keeps the argv small and makes the
# result the same shape everywhere.
MAX_LEN = 200


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _section(cfg: Any) -> Dict[str, Any]:
    section = _as_dict(_as_dict(cfg).get("notify"))
    return section if section else _as_dict(cfg)


def clip(text: Any, limit: int = MAX_LEN) -> str:
    """One line, bounded. Newlines are collapsed because a notification body is
    a single line in practice and an embedded newline just truncates it early."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def resolve_backend(cfg: Any = None) -> str:
    """Which sink to use: config first, then the platform."""
    requested = str(_section(cfg).get("backend") or "auto").strip().lower()
    requested = ALIASES.get(requested, requested)
    if requested in SINKS:
        return requested
    if requested not in ("auto", ""):
        return "none"  # a name we do not know; stay quiet rather than guess
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "none"


def notify(title: str, body: str, cfg: Any = None, open_url: Optional[str] = None) -> bool:
    """Post one desktop notification. Returns True only if it was delivered.

    ``open_url`` is where the notification should take the reader when clicked --
    in practice the rendered BRIEF.html. Not every backend can attach an action,
    so it is a hint rather than a promise; a backend that cannot honour it still
    delivers the text.

    Never raises: see the module docstring.
    """
    section = _section(cfg)
    if section.get("enabled") is False:
        return False

    title = clip(title or section.get("title") or "nextbrief", 80)
    body = clip(body)
    if not body:
        return False

    module: Optional[Any] = SINKS.get(resolve_backend(cfg))
    if module is None:
        return False
    try:
        try:
            return bool(module.send(title, body, cfg, open_url=open_url))
        except TypeError:
            # A third-party or older sink may only accept the three-argument form.
            return bool(module.send(title, body, cfg))
    except Exception:
        # A notifier that fails is an annoyance; a notifier that propagates is a
        # lost brief, since this is called at the very end of a successful run.
        return False
