"""A deliberately small YAML-subset frontmatter parser.

Backlog entries are Markdown files with a ``---`` frontmatter block. We support
exactly the subset documented in ``schema/BACKLOG_TEMPLATE.md``: scalars, one
level of nesting, inline lists, and ``|`` block scalars. Not a YAML
implementation, and not trying to be -- see ``jsonc`` for why stdlib-only matters.

``rewrite_fields`` and ``remove_fields`` are the only writers. Both work a line
at a time inside the frontmatter block and leave the body untouched, so a
malformed value can never destroy the prose a human wrote underneath.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

__all__ = ["parse_frontmatter", "rewrite_fields", "remove_fields", "format_value"]

_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_NESTED_KEY = re.compile(r"^\s+([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_INT = re.compile(r"^-?\d+$")


def _coerce(v: str) -> Any:
    v = v.strip()
    if v in ("", "null", "~"):
        return None
    if v in ("true", "false"):
        return v == "true"
    if _INT.match(v):
        return int(v)
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_coerce(x) for x in inner.split(",")] if inner else []
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_frontmatter(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return ``(fields, body)``. ``fields`` is None when there is no frontmatter.

    Never raises: a malformed block yields ``(None, text)`` so callers can record
    a parse failure and carry on. Failing open is a project-wide contract -- one
    bad file must not take down the nightly run.
    """
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    raw = text[text.find("\n") + 1:end]
    body = text[end + 4:].lstrip("\n")

    data: Dict[str, Any] = {}
    cur_key = None
    block_key = None
    block_lines: list = []

    for line in raw.split("\n"):
        if block_key is not None:
            if line.startswith("  ") or line.strip() == "":
                block_lines.append(line[2:] if line.startswith("  ") else "")
                continue
            data[block_key] = "\n".join(block_lines).strip()
            block_key, block_lines = None, []
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  ") and cur_key is not None:
            m = _NESTED_KEY.match(line)
            if m:
                if not isinstance(data.get(cur_key), dict):
                    data[cur_key] = {}
                val = m.group(2)
                data[cur_key][m.group(1)] = "" if val.strip() == "|" else _coerce(val)
            continue
        m = _KEY.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        cur_key = key
        if val.strip() == "|":
            block_key, block_lines = key, []
        elif val.strip() == "":
            data[key] = {}
        else:
            data[key] = _coerce(val)

    if block_key is not None:
        data[block_key] = "\n".join(block_lines).strip()
    return data, body


def format_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[%s]" % ", ".join(format_value(x) for x in v)
    return str(v)


def rewrite_fields(path, fields: Dict[str, Any]) -> bool:
    """Set top-level frontmatter keys in place. Returns True if the file changed.

    Only existing keys are updated and only within the frontmatter block; keys
    that are absent are appended just before the closing ``---``. The body is
    never touched.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False

    head_start = text.find("\n") + 1
    block = text[head_start:end]
    lines = block.split("\n")
    remaining = dict(fields)

    for idx, line in enumerate(lines):
        m = _KEY.match(line)
        if not m or line.startswith("  "):
            continue
        key = m.group(1)
        if key in remaining:
            lines[idx] = "%s: %s" % (key, format_value(remaining.pop(key)))

    for key, value in remaining.items():
        lines.append("%s: %s" % (key, format_value(value)))

    new_block = "\n".join(lines)
    if new_block == block:
        return False
    p.write_text(text[:head_start] + new_block + text[end:], encoding="utf-8")
    return True


def remove_fields(path, keys) -> bool:
    """Delete top-level frontmatter keys. Returns True if the file changed.

    The counterpart to ``rewrite_fields``, and it has one caller: the
    write-permission gate, reverting a human-only field that the committed copy
    does not carry at all. There the only correct restoration is *no line*.
    Setting it back to what the baseline "had" would write ``priority: null``
    onto the item -- an illegal edit replaced by a worse one, and one the next
    run would then read as a real value.

    A key owning indented lines beneath it -- a nested block, or a ``|`` scalar
    -- is left in place. Removing its header would orphan its body into the
    previous key, which destroys more than the illegal edit did. The gate takes
    the same posture on nested values it can identify, restoring them in memory
    only, and this is the same rule for the case where there is nothing to
    restore.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False

    head_start = text.find("\n") + 1
    block = text[head_start:end]
    lines = block.split("\n")
    wanted = set(keys)

    kept = []
    for idx, line in enumerate(lines):
        m = _KEY.match(line)
        if m and not line.startswith("  ") and m.group(1) in wanted:
            following = lines[idx + 1] if idx + 1 < len(lines) else ""
            if m.group(2).strip() not in ("", "|") and not following.startswith("  "):
                continue
        kept.append(line)

    new_block = "\n".join(kept)
    if new_block == block:
        return False
    p.write_text(text[:head_start] + new_block + text[end:], encoding="utf-8")
    return True
