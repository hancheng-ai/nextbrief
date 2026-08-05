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

import datetime as dt
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
    "RESTATE_AFTER_DAYS",
    "coerce_answer",
    "current_answer",
    "store_answer",
    "needs_annotating",
    "pending_count",
    "question_targets",
    "record_answers",
    "render_review_form",
    "parse_review_form",
]

ANNOTATIONS_NAME = "annotations.jsonc"


class Question:
    """One question and where its answer is stored.

    ``target`` is "ice" for an axis of the ICE triple and "project" for a
    top-level registry field. ``kind`` is "choice" or "date"; a date is free text
    because there is no useful multiple choice over calendars.
    """

    def __init__(self, field, key, choices=(), kind="choice", target="ice"):
        self.field = field          # the field this answer sets
        self.key = key              # locale key for the question text
        self.choices = choices      # ((stored_value, locale_key), ...)
        self.kind = kind
        self.target = target


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
    # 1. Importance, as consequence. What changes if this succeeds.
    Question("impact", "review.q.importance", (
        (1, "review.a.importance.itself"),
        (2, "review.a.importance.easier"),
        (4, "review.a.importance.unlocks"),
        (5, "review.a.importance.rests_on"),
    )),

    # 2. Positioning: the same question pointed forwards. Impact asks what this
    # changes now; positioning asks what it is meant to become. They come apart
    # exactly where it matters -- something can be small today and be the thing
    # everything else is planned around, and a portfolio that cannot say that
    # cannot tell an early flagship from a side project.
    Question("positioning", "review.q.positioning", (
        ("experiment", "review.a.positioning.experiment"),
        ("supporting", "review.a.positioning.supporting"),
        ("platform", "review.a.positioning.platform"),
        ("flagship", "review.a.positioning.flagship"),
    ), target="project"),

    # 3. Phase. Orthogonal to both of the above and to observed activity: a busy
    # project can be one that has finished evolving, and only its owner knows
    # which. This is the field the *neglected* and *stalled* verdicts read.
    Question("status", "review.q.status", (
        ("active", "review.a.status.active"),
        ("maintenance", "review.a.status.maintenance"),
        ("frozen", "review.a.status.frozen"),
        ("done", "review.a.status.done"),
    ), target="project"),

    # 4. Urgency, as a date rather than a feeling. A stored "urgency: 4" is wrong
    # within a week; a date stays true and recomputes its own urgency every
    # morning. Blank is a real answer -- most projects have no date, and that is
    # not the same as not mattering.
    Question("deadline", "review.q.deadline", kind="date", target="project"),
)

# Bump when a question's WORDING changes enough that old answers no longer mean
# the same thing. Answers recorded under an older version are ignored and asked
# again, rather than being silently reinterpreted -- someone who answered "2" to
# "what breaks if this slips" did not say the same thing as someone answering
# "2" to "what changes if this succeeds".
ASKED_VERSION = 3

# How long an answer is taken at face value before `review` asks again.
#
# Importance drifts. A project that mattered most six months ago may be finished,
# abandoned, or overtaken, and nothing in the engine can observe that -- the one
# thing it is not allowed to do is guess. The alternative to re-asking is a
# command for correcting an answer, which assumes the reader remembers a number
# they set half a year ago and thinks to revisit it. Making the revisit periodic
# rather than manual is what turns a stale judgement back into a live one.
#
# An answer with no date is treated as stale rather than fresh: undated means
# unknown age, and pretending otherwise is the same defaulting mistake as reading
# an absent impact as 3.
RESTATE_AFTER_DAYS = 180

# One timer for four questions was wrong in both directions at once, so this
# splits it rather than shortening it. The stamp keys are the ones
# `record_answers` writes -- impact lands in `ice`, the rest under their own name.
#
#   ice / positioning   180 days. Budget-class judgements, genuinely re-decided
#                       about twice a year. These are also exactly the fields
#                       inline correction is forbidden to touch, so nothing else
#                       refreshes them and a timer is the only thing that can.
#
#   status              NO timer. A calendar cannot know this. Re-asking about a
#                       maintenance project answered correctly 181 days ago is a
#                       warning that fires for a harmless reason, and saying
#                       nothing about one abandoned last week is the miss that
#                       actually costs something. It is re-asked when the
#                       evidence contradicts it, not when the date rolls over.
#
#   deadline            NO timer. It expires at its own date, which is the only
#                       honest expiry a date has.
RESTATE_AFTER_BY_FIELD = {
    "ice": RESTATE_AFTER_DAYS,
    "positioning": RESTATE_AFTER_DAYS,
    "status": None,
    "deadline": None,
}

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
# So it is neither asked nor derived, and the score does not read it. Nor does it
# read `confidence`. Both still parse from a registry, so no existing file breaks
# -- they simply stop changing the answer.
#
# That last part was once the opposite: the two were defaulted to 3 so that a
# hand-written three-axis entry kept scoring exactly as before. It was meant as
# courtesy to existing files and it produced two rules in one ordered list, where
# a project rated 5 and divided by effort 5 ranked below one rated 4 and divided
# by 2 -- the large-project penalty this very comment exists to condemn, still
# operating, one layer down from where it was removed.

def current_answer(project: Dict[str, Any], q: Question):
    """What this project already says for `q`, or None.

    One function rather than a conditional at every call site: the three targets
    read from three different shapes, and a caller that forgets which is which
    re-asks a question that has an answer, or skips one that does not.
    """
    if q.target == "ice":
        return (project.get("ice") or {}).get(q.field)
    if q.kind == "date":
        dated = project.get("deadlines") or []
        return (dated[0] or {}).get("date") if dated else None
    return project.get(q.field)


def store_answer(into: Dict[str, Any], q: Question, value, cat=None) -> None:
    """Put `value` where `q` says it belongs, in an overlay-shaped dict."""
    if q.target == "ice":
        into.setdefault("ice", {})[q.field] = value
    elif q.kind == "date":
        label = "from review"
        if cat is not None:
            try:
                label = cat.t("review.deadline.label")
            except Exception:
                pass
        # A list, because that is the shape the registry uses and the renderer
        # reads. One entry: `review` asks for the date that matters, not for a
        # schedule.
        into["deadlines"] = [{"label": label, "date": value}]
    else:
        into[q.field] = value


def _answer_expired(p, as_of=None, restate_after=None) -> bool:
    """Is this project's answer old enough to be worth restating?

    Only overlay answers expire. A value its owner typed into `registry.jsonc` is
    a standing declaration and is never retired by us -- the same rule that keeps
    a reworded question from discarding hand-written entries.
    """
    if not p.get("answered"):
        return False
    days = RESTATE_AFTER_DAYS if restate_after is None else restate_after
    if days is None:
        return False         # expiry switched off entirely
    if days <= 0:
        return True          # restate everything -- what `review --all` passes
    stamps = _stamps_of(p)
    if not stamps:
        return True          # undated is unknown age, not fresh
    today = as_of or dt.date.today()

    # Only the TIMED fields can bring a project back. A phase answered last week
    # and a phase answered last year are equally current as far as the calendar
    # is concerned, because the calendar is not what makes a phase wrong.
    #
    # An explicit `restate_after` overrides every field -- that is `review --all`
    # and `--restate-after`, where the person is asking rather than the timer.
    ages = []
    for field, value in stamps.items():
        limit = days if restate_after is not None else RESTATE_AFTER_BY_FIELD.get(
            field, RESTATE_AFTER_DAYS)
        if limit is None:
            continue
        try:
            age = (today - dt.date.fromisoformat(str(value))).days
        except (TypeError, ValueError):
            return True      # unparseable is unknown age
        ages.append(age >= limit)
    return any(ages)


def _stamps_of(entry: Dict[str, Any]) -> Dict[str, str]:
    """When each answer on this entry was given, as {field: ISO date}.

    Accepts both shapes. A bare string is what every overlay written before this
    existed contains, and it means "all of these answers were given that day" --
    which was true, because they were all recorded in one pass. Reading it as a
    stamp on every field is therefore a faithful migration rather than a guess,
    and it needs no rewrite of anybody's file: the next `review` upgrades the
    entry it touches and leaves the rest alone.
    """
    got = (entry or {}).get("asked_on")
    if isinstance(got, dict):
        return {k: v for k, v in got.items() if isinstance(v, str)}
    if isinstance(got, str) and got:
        return {q.key: got for q in QUESTIONS}
    return {}


def needs_annotating(snapshot: Dict[str, Any], self_ids=None, as_of=None,
                     restate_after=None) -> List[Dict[str, Any]]:
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
            if not (stale and p.get("answered")) and not _answer_expired(p, as_of, restate_after):
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


FORM_MARKER = "# nextbrief review"


def _choice_summary(q, cat) -> str:
    parts = []
    for value, key in q.choices:
        label = cat.t(key) if cat is not None else key
        parts.append("%s = %s" % (value, label))
    return "   |   ".join(parts)


def render_review_form(projects: Sequence[Dict[str, Any]], cat=None) -> str:
    """The whole review as one editable file.

    Four heterogeneous questions across a dozen projects is the shape a terminal
    prompt loop handles worst: it asks in a fixed order, shows one project at a
    time, cannot go back, and makes a free-text date as awkward as a menu. A file
    shows every project at once, lets the answers be written in any order, and
    keeps whatever was already known visible while the rest is filled in.

    Blank means unanswered, and unanswered means asked again -- there is no way
    to spell "I looked and I have no opinion", because that is not a state the
    engine can use.
    """
    lines = [FORM_MARKER, "#",
             "# Fill in what you can and save. Anything left blank is simply asked",
             "# again next time. Lines beginning with # are ignored.",
             "#"]
    for q in QUESTIONS:
        prompt = cat.t(q.key) if cat is not None else q.key
        lines.append("# %-12s %s" % (q.field, prompt))
        if q.choices:
            lines.append("# %-12s %s" % ("", _choice_summary(q, cat)))
    lines.append("")

    for proj in projects:
        pid = str(proj.get("id"))
        name = proj.get("name") or pid
        lines.append("[%s] %s" % (pid, name) if name != pid else "[%s]" % pid)
        for q in QUESTIONS:
            have = current_answer(proj, q)
            lines.append("%-12s %s" % (q.field + ":", "" if have is None else have))
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_review_form(text: str, known=None) -> Dict[str, Dict[str, Any]]:
    """Answers out of an edited form. Unparseable lines are skipped, not fatal.

    `known` restricts which ids are accepted, so a typo in a section header is
    dropped rather than silently creating an answer for a project that does not
    exist -- which would be recorded, never displayed, and never explained.
    """
    by_field = {q.field: q for q in QUESTIONS}
    out: Dict[str, Dict[str, Any]] = {}
    pid = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            head = line[1:].split("]", 1)
            pid = head[0].strip() if len(head) == 2 else None
            if known is not None and pid not in known:
                pid = None
            continue
        if pid is None or ":" not in line:
            continue
        field, _, value = line.partition(":")
        q = by_field.get(field.strip())
        value = value.strip()
        if q is None or not value:
            continue
        parsed = coerce_answer(q, value)
        if parsed is None:
            continue
        store_answer(out.setdefault(pid, {}), q, parsed)
    return out


def coerce_answer(q, value: str):
    """A typed answer as the field's own type, or None if it is not one.

    Silently dropping a bad value rather than raising: this file is hand-edited,
    and one mistyped line should cost that line, not the other eleven projects'
    answers.
    """
    if q.kind == "date":
        try:
            dt.date.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        return value
    allowed = [v for v, _k in q.choices]
    for candidate in allowed:
        if value == str(candidate):
            return candidate
    return None


def record_answers(ws: Workspace, answers: Dict[str, Dict[str, Any]],
                   asked_on=None) -> int:
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
        # Stamped so it can go stale. Without a date every answer is permanent,
        # and a permanent answer to "how much does this matter" is wrong more
        # often than it is right.
        #
        # Stamped PER FIELD, and only on the fields this call actually answered.
        # One stamp per project cannot survive what comes next: the four
        # questions go stale at different rates, and correcting a phase is not
        # restating a strategy. A single date launders the cheap answer into the
        # expensive one, so a field goes quietly dead while reporting itself
        # fresh -- which is the Jira default-to-Medium failure reached from the
        # other end.
        stamp = (asked_on or dt.date.today()).isoformat()
        stamps = dict(_stamps_of(entry))
        for key in fields:
            stamps[key] = stamp
        entry["asked_on"] = stamps
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

