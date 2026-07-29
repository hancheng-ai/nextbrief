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
from typing import Any, Dict, List, Sequence

from .fs import write_text
from .jsonc import JSONCError, load_jsonc
from .paths import Workspace

__all__ = [
    "ANNOTATIONS_NAME",
    "ASKED_VERSION",
    "QUESTIONS",
    "apply_annotations",
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


# Importance is asked. Urgency is not, because urgency is already known: it comes
# from the dates in `outcomes` and `deadlines`, which a human wrote and the
# renderer already turns into a boost. Asking for it again would be asking
# someone to re-derive arithmetic the engine does better.
#
# The distinction is the whole point, and getting it wrong is not academic. The
# first version of this asked "if this slipped by a month, what happens?" -- a
# delay-consequence question, which is urgency wearing importance's name. It
# scored a portfolio's centre piece at 1 out of 5, because nothing happens if a
# platform blocked on its own ecosystem slips another month. Everything
# important-but-not-urgent was systematically undervalued, which is the exact
# category a long-horizon plan is made of.
#
# So the question asks what changes on SUCCESS, not what breaks on delay. A
# project answers it the same way whether it was touched today or last spring.
QUESTIONS: Sequence[Question] = (
    Question("impact", "review.q.importance", (
        (1, "review.a.importance.itself"),
        (2, "review.a.importance.easier"),
        (4, "review.a.importance.unlocks"),
        (5, "review.a.importance.rests_on"),
    )),
)

# Bump when a question's WORDING changes enough that old answers no longer mean
# the same thing. Answers recorded under an older version are ignored and asked
# again, rather than being silently reinterpreted -- someone who answered "2" to
# "what breaks if this slips" did not say the same thing as someone answering
# "2" to "what changes if this succeeds".
ASKED_VERSION = 2

# Effort is asked by nobody and derived by nothing.
#
# It was derived from file count, described as "the axis where a human guess is
# worse than a measurement". That was true of repo SIZE, and repo size is not
# what ICE means by effort -- which is the work required to reach the impact. A
# small finished tool scores lowest on it and a large active one scores high, so
# dividing by it penalised a project for being large and rewarded one for being
# done. Asking instead is no better: "how long to a usable next milestone" is
# unanswerable for open-ended creative work.
#
# So it is neither asked nor derived, and `score_project`'s existing default of 3
# makes the base collapse to (impact x 3) / 3 == impact. Hand-written three-axis
# entries in a registry keep working exactly as they did.

def needs_annotating(snapshot: Dict[str, Any], self_ids=None) -> List[Dict[str, Any]]:
    """Projects with something to show for themselves and nothing said about them.

    Ordered by evidence recency so that the first question asked is about the
    work most recently in front of the person answering it -- they will have the
    best answer for that one, and may well stop after it.

    A project with no activity at all is excluded. Asking someone to rank a
    directory they have not touched is asking them to do filing.
    """
    self_ids = set(self_ids or ())
    # A snapshot is a cache, and invalidation has to reach it. `load_annotations`
    # drops answers given to an older wording, but the snapshot was written with
    # them already merged in -- so a workspace that does not re-sense keeps
    # scoring on withdrawn answers indefinitely. Only overlay-sourced values are
    # discounted; a value its owner typed into the registry is never retired by
    # us rewording a question.
    stale = int(((snapshot.get("run") or {}).get("asked_version")) or 1) != ASKED_VERSION
    out = []
    for p in snapshot.get("projects") or []:
        if p.get("id") in self_ids or p.get("is_self"):
            continue
        if (p.get("ice") or {}).get("impact") is not None:
            if not (stale and p.get("answered")):
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
    if not isinstance(data, dict):
        return {}
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return {}

    # Answers to a question that has since been reworded are dropped, not
    # reinterpreted: "2" against "what breaks if this slips" is not the same
    # statement as "2" against "what changes if this succeeds", and carrying it
    # over would put words in someone's mouth they never said.
    #
    # Scoped to `ice`, and only `ice`. Everything else here -- a description, say
    # -- was never an answer to a worded question, so rewording one has no
    # bearing on it. The first version of this dropped the whole file and would
    # have destroyed a sentence someone wrote by hand for a reason unrelated to
    # it.
    if int(data.get("asked_version") or 1) == ASKED_VERSION:
        return projects
    kept = {}
    for pid, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        rest = {k: v for k, v in entry.items() if k != "ice"}
        if rest:
            kept[pid] = rest
    return kept


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

    body = json.dumps({"asked_version": ASKED_VERSION, "projects": current},
                      ensure_ascii=False, indent=2, sort_keys=True)
    header = (
        "// Written by `nextbrief review`, from questions you answered.\n"
        "//\n"
        "// Kept out of registry.jsonc deliberately: that file is yours, comments\n"
        "// and ordering included, and a tool that rewrites it will eventually get\n"
        "// that wrong on the one file whose loss costs most. Anything you type\n"
        "// into the registry overrides what is here.\n"
        "//\n"
        "// Safe to delete. You will simply be asked again -- and that also happens\n"
        "// on its own if a question is ever reworded enough to change what an\n"
        "// answer meant.\n"
    )
    write_text(ws, ws.root / ANNOTATIONS_NAME, header + body + "\n")
    return len(answers)


def question_targets(snapshot, self_ids=None, limit: int = 2) -> List[Dict[str, Any]]:
    """The projects the brief should ask about today, most recent first."""
    return needs_annotating(snapshot, self_ids)[:max(0, limit)]


def pending_count(snapshot, self_ids=None) -> int:
    return len(needs_annotating(snapshot, self_ids))

