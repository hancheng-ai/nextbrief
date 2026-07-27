"""macOS notifications.

Two delivery paths, preferred in this order:

``terminal-notifier``, when it is installed. It can attach a click action, so the
notification opens the brief instead of doing nothing. That matters more than it
sounds: ``osascript`` notifications are attributed to Script Editor, so macOS
offers a "Show" button that opens Script Editor with an empty document. A control
that looks actionable and does nothing is worse than no control, because it
teaches the reader to ignore the notification entirely.

``osascript``, otherwise. Always present, never clickable. The optional tool is
optional here exactly as it is in sensing: its absence costs a detail, never the
run.

★ The text is passed as arguments, never interpolated into AppleScript source. ★
That single decision is why a project file cannot reach this code: the body is
assembled from titles, project names and document snippets found in directories
nextbrief only reads, and a line crafted to close a string and open a
``do shell script`` would otherwise be executed by the notifier at the end of an
otherwise successful run. With ``on run {m, t}`` the script is a constant and the
hostile text is data.

Do not "simplify" this into an f-string. It is the same rule the rest of the
pipeline follows -- what you read from a project is data, not instructions --
enforced at the one place where the data reaches an interpreter.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Optional

NAME = "macos"

_SCRIPT = "on run {m, t}\ndisplay notification m with title t\nend run"

# The notifier is a courtesy at the end of a finished run; if the system is busy
# enough that it cannot answer in ten seconds, dropping the notification is
# better than holding the process open.
_TIMEOUT = 10


def probe(cfg: Any = None) -> bool:
    return shutil.which("osascript") is not None or shutil.which("terminal-notifier") is not None


def _run(argv) -> bool:
    try:
        proc = subprocess.run(
            argv,
            timeout=_TIMEOUT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def send(title: str, body: str, cfg: Any = None, open_url: Optional[str] = None) -> bool:
    tn = shutil.which("terminal-notifier")
    if tn is not None:
        # -message and -title take their values as separate argv entries, so the
        # same data-not-code rule holds here as on the AppleScript path.
        argv = [tn, "-message", body, "-title", title]
        if open_url:
            argv += ["-open", open_url]
        if _run(argv):
            return True
        # Fall through rather than return: a terminal-notifier that fails should
        # not cost the run its notification when osascript is sitting right there.

    exe = shutil.which("osascript")
    if exe is None:
        return False
    return _run([exe, "-e", _SCRIPT, body, title])
