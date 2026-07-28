"""Asking a person the one thing only a person knows, as cheaply as possible.

The registry's `ice` block wants three integers per project. Nobody fills it in,
and the reason is not laziness -- it is that the question is harder than the
judgement it is trying to capture. "Impact 4" is an absolute, uncalibrated
number about a scale nobody defined, and a month later its author cannot
reconstruct what they meant by it. People do not think that way. Asked what
matters more, they answer instantly and correctly; asked to score it out of
five, they stall.

So nothing here asks for a number.

**Effort is never asked.** It is observable -- how much there is, how much of it
moves -- and on this axis a human guess is worse than a measurement, not better.

**Impact and confidence are asked as consequences**, with three or four fixed
answers each. "If this slipped by a month, what happens?" is answerable in a
second and stays comparable between projects and across time, which "4" does
not. The stored integer is an implementation detail of the scoring formula; the
question is what the user actually sees.

**Answers land in `annotations.jsonc`, never in `registry.jsonc`.** The registry
is the human's file. A tool that rewrites it has to preserve their comments and
their ordering, will eventually fail at it, and will fail on the one file whose
loss costs the most. An overlay keeps authorship unambiguous: everything in the
registry was typed by its owner, and everything here was answered by them
through a question this module asked.

The line this module must not cross is the one discovery crossed and had to be
walked back: **a proposal awaiting an answer is not an assertion.** Suggested
answers may be pre-selected as aggressively as is useful, but an unanswered
question stays visibly unanswered and never quietly becomes data.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence, Tuple

from .fs import write_text
from .jsonc import JSONCError, load_jsonc
from .paths import Workspace

__all__ = [
    "ANNOTATIONS_NAME",
    "EFFORT_BANDS",
    "QUESTIONS",
    "apply_annotations",
    "derive_effort",
    "load_annotations",
    "needs_annotating",
    "pending_count",
    "question_targets",
    "record_answers",
]

ANNOTATIONS_NAME = "annotations.jsonc"


class Question:
    """One multiple-choice question and what each answer stores."""

    def __init__(self, field, key, choices):
        self.field = field          # the `ice` axis this answer sets
        self.key = key              # locale key for the question text
        self.choices = choices      # ((stored_value, locale_key), ...)


# Both questions are about consequences, not magnitudes. The numbers exist only
# because `score_project` multiplies them; they are never shown to anyone.
QUESTIONS: Sequence[Question] = (
    Question("impact", "review.q.impact", (
        (1, "review.a.impact.nothing"),
        (2, "review.a.impact.annoyed"),
        (4, "review.a.impact.blocks"),
        (5, "review.a.impact.protect"),
    )),
    Question("confidence", "review.q.confidence", (
        (1, "review.a.confidence.unknown"),
        (3, "review.a.confidence.roughly"),
        (5, "review.a.confidence.exactly"),
    )),
)

# Effort, derived. Deliberately coarse: this is a size band, and pretending to
# more precision than "how much is there" supports would be false comfort. The
# thresholds are documented rather than tuned, so that a project moving band is
# a fact about the project and not about someone's calibration drifting.
EFFORT_BANDS: Sequence[Tuple[int, int]] = (
    (50, 1),
    (200, 2),
    (1000, 3),
    (5000, 4),
)


def derive_effort(project: Dict[str, Any]) -> int:
    """Effort band from what is actually on disk.

    Uses the file count the sensing stage already computed, which has the
    project's own ignore globs applied -- so vendored and generated trees do not
    inflate it, provided they are declared. That caveat is real: an undeclared
    build directory makes a project look enormous, and the fix is a glob, not a
    different formula here.
    """
    total = ((project.get("fs") or {}).get("total_files")) or 0
    for ceiling, band in EFFORT_BANDS:
        if total < ceiling:
            return band
    return 5


def needs_annotating(snapshot: Dict[str, Any], self_ids=None) -> List[Dict[str, Any]]:
    """Projects with something to show for themselves and nothing said about them.

    Ordered by evidence recency so that the first question asked is about the
    work most recently in front of the person answering it -- they will have the
    best answer for that one, and may well stop after it.

    A project with no activity at all is excluded. Asking someone to rank a
    directory they have not touched is asking them to do filing.
    """
    self_ids = set(self_ids or ())
    out = []
    for p in snapshot.get("projects") or []:
        if p.get("id") in self_ids or p.get("is_self"):
            continue
        ice = p.get("ice") or {}
        if ice.get("impact") is not None and ice.get("confidence") is not None:
            continue
        ev = p.get("evidence") or {}
        if ev.get("days_since") is None:
            continue
        out.append(p)
    out.sort(key=lambda p: ((p.get("evidence") or {}).get("days_since", 10**6),
                            str(p.get("id"))))
    return out


# ---------------------------------------------------------------------------
# the overlay
# ---------------------------------------------------------------------------


def load_annotations(ws: Workspace) -> Dict[str, Any]:
    """Answers recorded so far. A missing or unreadable file is simply no answers.

    Fail-open on purpose: a corrupt overlay must cost the ranking its extra
    precision, never cost the run its brief.
    """
    path = ws.root / ANNOTATIONS_NAME
    if not path.is_file():
        return {}
    try:
        data = load_jsonc(str(path))
    except (JSONCError, OSError, ValueError):
        return {}
    projects = data.get("projects") if isinstance(data, dict) else None
    return projects if isinstance(projects, dict) else {}


def apply_annotations(reg: Dict[str, Any], annotations: Dict[str, Any]) -> Dict[str, Any]:
    """Lay recorded answers over the registry, without overwriting a hand edit.

    The registry always wins. Someone who opened their own file and typed a value
    has said something more deliberate than an answer they clicked through, and
    the overlay must never quietly undo it -- otherwise the fix for a wrong
    answer would be to find and delete it in a file they did not write.
    """
    if not annotations:
        return reg
    projects = []
    for pr in reg.get("projects") or []:
        ann = annotations.get(str(pr.get("id")))
        if not isinstance(ann, dict):
            projects.append(pr)
            continue
        merged = dict(pr)
        for key, value in ann.items():
            if key == "ice":
                # Shapes are checked here rather than trusted. This file invites
                # hand-editing -- its own header says it is safe to delete -- and
                # `check_shapes` never sees it, so `"ice": "high"` reaches this
                # line unvalidated. It used to raise straight out of `build`,
                # which on the unattended path is a stack trace and no brief at
                # all: the exact opposite of the fail-open contract
                # `load_annotations` states one function above.
                if not isinstance(value, dict):
                    continue
                own = pr.get("ice")
                ice = dict(value)
                if isinstance(own, dict):
                    ice.update({k: v for k, v in own.items() if v is not None})
                merged["ice"] = ice
            elif pr.get(key) is None:
                merged[key] = value
        projects.append(merged)
    out = dict(reg)
    out["projects"] = projects
    return out


def record_answers(ws: Workspace, answers: Dict[str, Dict[str, Any]]) -> int:
    """Merge answers into the overlay and write it. Returns projects touched.

    Written as JSONC with a header explaining where the file came from, because
    a file nobody remembers agreeing to is a file they will delete in confusion.
    """
    if not answers:
        return 0
    current = load_annotations(ws)
    for pid, fields in answers.items():
        entry = dict(current.get(pid) or {})
        for key, value in fields.items():
            if key == "ice":
                ice = dict(entry.get("ice") or {})
                ice.update(value or {})
                entry["ice"] = ice
            else:
                entry[key] = value
        current[pid] = entry

    body = json.dumps({"projects": current}, ensure_ascii=False, indent=2, sort_keys=True)
    header = (
        "// Written by `nextbrief review`, from questions you answered.\n"
        "//\n"
        "// Kept out of registry.jsonc deliberately: that file is yours, comments\n"
        "// and ordering included, and a tool that rewrites it will eventually get\n"
        "// that wrong on the one file whose loss costs most. Anything you type\n"
        "// into the registry overrides what is here.\n"
        "//\n"
        "// Safe to delete. You will simply be asked again.\n"
    )
    write_text(ws, ws.root / ANNOTATIONS_NAME, header + body + "\n")
    return len(answers)


def question_targets(snapshot, self_ids=None, limit: int = 2) -> List[Dict[str, Any]]:
    """The projects the brief should ask about today, most recent first."""
    return needs_annotating(snapshot, self_ids)[:max(0, limit)]


def pending_count(snapshot, self_ids=None) -> int:
    return len(needs_annotating(snapshot, self_ids))

