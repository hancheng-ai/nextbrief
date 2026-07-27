"""The null sink: no notification, ever.

Selected on platforms with no dependency-free notifier, and by anyone who wants
the brief written without being interrupted for it. Returning ``False`` rather
than ``True`` is deliberate -- the caller logs whether a notification actually
reached the desktop, and a silent sink claiming delivery would make the log
useless for diagnosing the case that matters ("it ran, so why didn't I see it").
"""

from __future__ import annotations

from typing import Any

NAME = "none"


def probe(cfg: Any = None) -> bool:
    return True


def send(title: str, body: str, cfg: Any = None) -> bool:
    return False
