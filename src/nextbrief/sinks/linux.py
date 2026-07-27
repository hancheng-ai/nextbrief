"""Linux notifications via ``notify-send``, when it happens to be installed.

No D-Bus client, no optional dependency: this package has none, and a desktop
notification is not worth acquiring one for. If ``notify-send`` is absent, or
there is no session bus to talk to (a headless box, a cron job outside the
graphical session), delivery fails and the run stays a success.

Both strings arrive as separate arguments, for the same reason the macOS sink
insists on it: the body is assembled from text found in project directories and
must never be able to become part of the command.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

NAME = "linux"

_TIMEOUT = 10


def probe(cfg: Any = None) -> bool:
    return shutil.which("notify-send") is not None


def _positional(text: str) -> str:
    """Keep a value from being read as an option.

    ``notify-send`` takes the summary and body positionally, and a brief whose
    first action begins with "-- rebuild ..." would otherwise be parsed as flags
    and rejected. Not every build accepts a ``--`` terminator, so pad instead:
    one leading space costs nothing visually and cannot be mistaken for a flag.
    """
    return " " + text if text.startswith("-") else text


def send(title: str, body: str, cfg: Any = None) -> bool:
    exe = shutil.which("notify-send")
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, "--app-name", "nextbrief", _positional(title), _positional(body)],
            timeout=_TIMEOUT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
