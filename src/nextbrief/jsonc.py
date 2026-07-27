"""JSONC parsing: JSON plus ``//`` / ``/* */`` comments and trailing commas.

Why not YAML or TOML: the unattended run must work under any Python 3.9+ with
**stdlib only**, and the system Python on macOS ships without PyYAML. Hand-rolling
a YAML subset is a fragile maintenance surface; comment-stripped JSON is a few
dozen lines of deterministic code.

The parser is character-aware throughout. An earlier version stripped trailing
commas with ``re.sub(r",(\\s*[}\\]])", r"\\1", s)`` over the whole document, which
silently corrupted any string literal containing a comma followed by a brace --
``{"a": "foo, ]bar"}`` parsed as ``{"a": "foo]bar"}``. Since these files are the
human-authored source of truth, a silent value change is the worst possible
failure mode, so comma handling lives inside the scanner where string state is
known.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["strip_jsonc", "load_jsonc", "JSONCError"]


class JSONCError(ValueError):
    """Raised when a JSONC document cannot be parsed, with file and position."""


def strip_jsonc(text: str) -> str:
    """Return `text` as strict JSON: comments removed, trailing commas dropped.

    Whitespace and newlines are preserved so that line/column numbers in any
    downstream ``json`` error still point at the original document.
    """
    out = []
    # A comma outside a string is held back until we know what follows it: if the
    # next significant character closes a container the comma is trailing and must
    # go, otherwise it is real and gets emitted with the whitespace we buffered.
    pending_comma = False
    gap: list = []

    def flush(keep_comma: bool) -> None:
        nonlocal pending_comma, gap
        if pending_comma and keep_comma:
            out.append(",")
        out.extend(gap)
        pending_comma, gap = False, []

    def emit(ch: str) -> None:
        (gap if pending_comma else out).append(ch)

    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]

        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue

        if c == '"':
            flush(True)
            in_str = True
            out.append(c)
            i += 1
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                # Keep newlines so line numbers survive block comments.
                if text[i] == "\n":
                    emit("\n")
                i += 1
            i += 2
            continue

        if c == ",":
            # Two commas in a row is malformed JSON; emit the first so json.loads
            # reports it rather than us silently repairing it.
            flush(True)
            pending_comma = True
            i += 1
            continue

        if c.isspace():
            emit(c)
            i += 1
            continue

        flush(c not in "}]")
        out.append(c)
        i += 1

    flush(True)
    return "".join(out)


def load_jsonc(path) -> Any:
    """Parse a JSONC file. Raises JSONCError with the path on failure."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise JSONCError("cannot read %s: %s" % (p, exc)) from exc
    try:
        return json.loads(strip_jsonc(raw))
    except json.JSONDecodeError as exc:
        raise JSONCError(
            "%s: %s (line %d, column %d)" % (p, exc.msg, exc.lineno, exc.colno)
        ) from exc


def loads_jsonc(text: str) -> Any:
    """Parse a JSONC string. Convenience for tests and stdin."""
    return json.loads(strip_jsonc(text))
