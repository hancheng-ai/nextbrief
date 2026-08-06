"""What a backlog item's *state* means.

Neither :mod:`nextbrief.frontmatter` (which only reads lines) nor
:mod:`nextbrief.render` (which judges projects) owns this, and both need it. Two
things live here, and they exist for the same reason:

**A closed item is a lost item.** The moment something stops being open is the
moment it carries the most information -- what was actually done, how it differed
from what the entry said, and what it uncovered on the way -- and it is the last
moment anyone will ever be in a position to say so. A single boolean consumed all
of that.

* **defer.** The most common real state change is neither "finished" nor
  "abandoned": it is *still true, just not now*. With only ``done`` and ``drop``
  available, that state had to be recorded as one of two lies. A deferred item is
  hidden from the brief until its date arrives and then comes back on its own --
  see ``is_live``, which is the entire mechanism. **A defer that cannot return is
  a silent drop**, so every path here fails towards the item reappearing.

* **the closing record.** ``summary`` (what actually happened) and
  ``future_work`` (what this uncovered that does not belong to it), written into
  the item's own file. No new store: a done entry stays in ``backlog/`` forever
  and is already in git, so the only thing that was missing was somewhere to put
  the words.

The closing block is parsed from the body rather than the frontmatter because
both fields are prose -- a multi-paragraph summary and a list of sentences -- and
the frontmatter subset holds neither without mangling them.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

__all__ = [
    "OPEN_STATUSES", "TERMINAL_STATUSES", "DEFERRED", "HUMAN_ONLY_STATUSES",
    "status_of", "defer_due", "is_live", "is_parked", "days_until_due",
    "Closing", "FutureWork", "CLOSING_BEGIN", "CLOSING_END",
    "parse_closing", "render_closing", "upsert_closing", "record_promotion",
    "next_item_id", "slug", "new_item_text",
]

OPEN_STATUSES = ("open", "in_progress", "waiting")
TERMINAL_STATUSES = ("done", "dropped")
DEFERRED = "deferred"

# Statuses only a person may move an item INTO. `deferred` belongs here for the
# same reason the terminal two do: it takes an item off the page. An agent that
# could park something would be able to hide work it did not want to be asked
# about, which is the false-completion failure wearing a different word.
HUMAN_ONLY_STATUSES = TERMINAL_STATUSES + (DEFERRED,)


def status_of(fm: Dict[str, Any]) -> str:
    return str(fm.get("status") or "open").strip().lower()


def defer_due(fm: Dict[str, Any]) -> Optional[dt.date]:
    """The date a deferred item returns, or ``None`` when it does not parse."""
    raw = fm.get("deferred_until")
    if raw is None:
        return None
    try:
        return dt.date.fromisoformat(str(raw).strip()[:10])
    except (TypeError, ValueError):
        return None


def is_live(fm: Dict[str, Any], today: dt.date) -> bool:
    """Whether this item belongs on today's page.

    Nothing is written to bring a deferred item back. The status stays
    ``deferred`` and this function decides, from the date, whether it counts as
    open today -- so the return is a property of the file rather than of some run
    having happened on the right morning. A workspace nobody senses for a week
    still shows every item that came due during it.

    A deferred item whose date is missing or unreadable is **live**. That is the
    fail-safe direction and the only one available: the alternative is an item
    parked forever by a typo, which is exactly the silent abandonment ``defer``
    exists to prevent.
    """
    status = status_of(fm)
    if status in OPEN_STATUSES:
        return True
    if status != DEFERRED:
        return False
    due = defer_due(fm)
    return due is None or due <= today


def is_parked(fm: Dict[str, Any], today: dt.date) -> bool:
    """Deferred, and not due yet -- the only state in which an item is hidden."""
    return status_of(fm) == DEFERRED and not is_live(fm, today)


def days_until_due(fm: Dict[str, Any], today: dt.date) -> Optional[int]:
    due = defer_due(fm)
    return None if due is None else (due - today).days


# ---------------------------------------------------------------------------
# the closing record
# ---------------------------------------------------------------------------

CLOSING_BEGIN = "<!-- SECTION:CLOSING:BEGIN -->"
CLOSING_END = "<!-- SECTION:CLOSING:END -->"

# `- some follow-up -> NA-0023`. Anchored at the end of the line so a sentence
# containing an arrow is not mistaken for a promotion.
_PROMOTED = re.compile(r"\s+->\s+([A-Za-z][A-Za-z0-9]*-\d+)\s*$")
_ID = re.compile(r"^([A-Za-z][A-Za-z0-9]*)-(\d+)$")


class FutureWork(NamedTuple):
    text: str
    promoted_to: Optional[str]


class Closing(NamedTuple):
    closed_on: str
    summary: str
    future_work: List[FutureWork]

    @property
    def empty(self) -> bool:
        return not self.summary.strip() and not self.future_work


def _block(text: str) -> Optional[Tuple[int, int, str]]:
    """``(start, end, inner)`` of the closing block, or None."""
    start = text.find(CLOSING_BEGIN)
    if start == -1:
        return None
    end = text.find(CLOSING_END, start)
    if end == -1:
        return None
    return start, end + len(CLOSING_END), text[start + len(CLOSING_BEGIN):end]


def parse_closing(text: str) -> Optional[Closing]:
    """Read the closing record out of a whole item file. ``None`` when absent.

    Never raises. A record somebody hand-edited into something this cannot read
    yields empty fields rather than an exception -- the file is prose a person
    owns, and the summary view is not worth a crashed command.
    """
    found = _block(text)
    if found is None:
        return None
    _s, _e, inner = found

    closed_on = ""
    summary_lines: List[str] = []
    future: List[FutureWork] = []
    mode = None
    for line in inner.split("\n"):
        stripped = line.strip()
        if mode == "summary":
            # Any unindented, non-blank line ends the block scalar.
            if not stripped or line.startswith("  "):
                summary_lines.append(line[2:] if line.startswith("  ") else "")
                continue
            mode = None
        if stripped.startswith("closed_on:"):
            closed_on = stripped[len("closed_on:"):].strip()
            continue
        if stripped == "summary: |":
            mode, summary_lines = "summary", []
            continue
        if stripped.startswith("summary:"):
            summary_lines = [stripped[len("summary:"):].strip()]
            continue
        if stripped == "future_work:":
            mode = "future"
            continue
        if mode == "future" and stripped.startswith("- "):
            body = stripped[2:].strip()
            m = _PROMOTED.search(body)
            future.append(FutureWork(_PROMOTED.sub("", body).strip(),
                                     m.group(1) if m else None))
    return Closing(closed_on, "\n".join(summary_lines).strip(), future)


def render_closing(closing: Closing) -> str:
    """The block, markers included, ready to drop into a file.

    Keys are English and structural, like the frontmatter's. They are read by
    ``nextbrief closed`` in every locale, so a translated heading here would be a
    view that works in one language and silently finds nothing in the other.
    """
    lines = [CLOSING_BEGIN, "closed_on: %s" % (closing.closed_on or "")]
    summary = closing.summary.strip()
    if summary:
        lines.append("")
        lines.append("summary: |")
        lines.extend(("  " + ln).rstrip() for ln in summary.split("\n"))
    if closing.future_work:
        lines.append("")
        lines.append("future_work:")
        for entry in closing.future_work:
            tail = " -> %s" % entry.promoted_to if entry.promoted_to else ""
            lines.append("- %s%s" % (entry.text.strip(), tail))
    lines.append(CLOSING_END)
    return "\n".join(lines)


def upsert_closing(text: str, closing: Closing) -> str:
    """Put ``closing`` into an item file, replacing any block already there.

    Appended at the end rather than woven into the body: everything above it was
    written while the item was open, and a record of how it ended reads as a
    postscript because that is what it is.
    """
    rendered = render_closing(closing)
    found = _block(text)
    if found is not None:
        start, end, _inner = found
        return text[:start] + rendered + text[end:]
    body = text.rstrip("\n")
    return "%s\n\n%s\n" % (body, rendered) if body else rendered + "\n"


def record_promotion(text: str, index: int, new_id: str) -> str:
    """Note in the file that future-work entry ``index`` became ``new_id``.

    The edge is written on both sides -- ``discovered_from`` on the new item, the
    id here -- because each answers a question the other cannot. From the new
    item: where did this come from. From here: was this follow-up ever picked up,
    or is it still a sentence nobody acted on.
    """
    closing = parse_closing(text)
    if closing is None or not (0 <= index < len(closing.future_work)):
        return text
    entries = list(closing.future_work)
    entries[index] = FutureWork(entries[index].text, new_id)
    return upsert_closing(text, closing._replace(future_work=entries))


# ---------------------------------------------------------------------------
# minting a new item
# ---------------------------------------------------------------------------


def next_item_id(existing: Sequence[str], like: str) -> str:
    """The next free id sharing ``like``'s prefix and zero padding.

    Derived from an id that exists rather than from a constant: the prefix is a
    workspace's own convention (``NA-``, ``P-``, anything), and hard-coding one
    would mint follow-ups into a namespace the rest of the backlog does not use.
    """
    m = _ID.match(str(like).strip())
    prefix, width = (m.group(1), len(m.group(2))) if m else ("NA", 4)
    highest = 0
    for item_id in existing:
        got = _ID.match(str(item_id).strip())
        if got and got.group(1) == prefix:
            highest = max(highest, int(got.group(2)))
    return "%s-%0*d" % (prefix, width, highest + 1)


def slug(title: str, limit: int = 48) -> str:
    """A filename fragment. Keeps letters and digits in any script -- a CJK
    backlog would otherwise produce files named after nothing but their id."""
    out = []
    for ch in str(title).strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:limit].strip("-") or "item"


def new_item_text(item_id: str, title: str, project: str, discovered_from: str,
                  today: str, source_note: str = "") -> str:
    """A backlog file for a follow-up lifted out of a closing record.

    ``human_confirmed: true`` and ``created_by: human``, and both are literally
    true: a person typed this sentence while closing the item it came from, and
    typed the command that promoted it. Automatic decay only ever withdraws the
    agent's own unconfirmed guesses, and this is neither.
    """
    lines = [
        "---",
        "id: %s" % item_id,
        "title: %s" % title,
        "project: %s" % project,
        "type: task",
        "status: open",
        "priority: 2",
        "blocked_by: none",
        "is_next_action: false",
        "automation:",
        "  tier: explore",
        "  what_agent_can_do: not assessed yet",
        "  what_needs_human: not assessed yet",
        "  next_probe: not assessed yet",
        "  assessed_on: %s" % today,
        "  human_confirmed: false",
        "source:",
        "  doc: backlog/%s" % (source_note or discovered_from),
        "  anchor: closing record of %s" % discovered_from,
        "  seen_on: %s" % today,
        "estimate_min: 30",
        "dependencies: []",
        "discovered_from: %s" % discovered_from,
        "created_date: %s" % today,
        "updated_date: %s" % today,
        "created_by: human",
        "human_confirmed: true",
        "---",
        "",
        "<!-- SECTION:NEXT_ACTION:BEGIN -->",
        title,
        "<!-- SECTION:NEXT_ACTION:END -->",
        "",
        "<!-- AC:BEGIN -->",
        "- [ ] #1 %s" % title,
        "<!-- AC:END -->",
        "",
        "<!-- SECTION:NOTES:BEGIN -->",
        "Lifted out of the closing record of %s on %s. Nobody has sized it, "
        "scoped it, or decided it is worth doing -- it is here so that it stopped "
        "being something only one person remembered." % (discovered_from, today),
        "<!-- SECTION:NOTES:END -->",
        "",
    ]
    return "\n".join(lines)
