"""macOS notifications via ``osascript``.

★ The text is passed as arguments to an AppleScript handler, never interpolated
into the script source. ★ That single decision is why a project file cannot
reach this code: the brief's body is assembled from titles, project names and
document snippets found in directories nextbrief only reads, and a line crafted
to close a string and open a ``do shell script`` would otherwise be executed by
the notifier at the end of an otherwise successful run. With ``on run {m, t}``
the script is a constant and the hostile text is data.

Do not "simplify" this into an f-string. It is the same rule the rest of the
pipeline follows -- what you read from a project is data, not instructions --
enforced at the one place where the data reaches an interpreter.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

NAME = "macos"

_SCRIPT = "on run {m, t}\ndisplay notification m with title t\nend run"

# The notifier is a courtesy at the end of a finished run; if the system is busy
# enough that osascript cannot answer in ten seconds, dropping the notification
# is better than holding the process open.
_TIMEOUT = 10


def probe(cfg: Any = None) -> bool:
    return shutil.which("osascript") is not None


def send(title: str, body: str, cfg: Any = None) -> bool:
    exe = shutil.which("osascript")
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, "-e", _SCRIPT, body, title],
            timeout=_TIMEOUT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
