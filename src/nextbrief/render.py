#!/usr/bin/env python3
"""Stage 3 -- four gates, hard caps, two renderings. No model involved.

★ This module is where the design lives. ★

Checking evidence at *render* time is what turns "do not invent progress" from a
line in a prompt (which a model can drift away from) into a property of the
pipeline (which drift cannot defeat). The same argument applies to length: an
instruction to "be brief" degrades over time, a renderer that truncates does not.

Design contract (see README, "design contract"):
  * reads snapshot / brief / backlog; writes only derived files, only inside the
    workspace -- every write is asserted against ``Workspace.contains``
  * decides nothing: no item is ever moved to a terminal status, and field edits
    an agent was not allowed to make are reverted
  * idempotent: identical inputs produce byte-identical output. Timestamps are
    read from the snapshot, never from the clock, and unchanged files are not
    rewritten -- the workspace is itself a watched project, so a pointless write
    would register as "activity" and make the whole pipeline non-idempotent
  * fail-open: every step leaves a trace and carries on; nothing aborts the run

The four gates:

1. **evidence** -- a claim whose ``evidence.source`` does not resolve in
   ``snapshot.evidence_index`` is *not rendered*; the original goes to
   ``log/rejected.jsonl``.
2. **non-goals** -- flag, never block. Silently deleting a good suggestion is
   worse than one visible false positive.
3. **write permission** -- field-level diff against ``git HEAD``; fields only a
   human may write are reverted. This is the mechanical enforcement of "no agent
   may set a terminal status", so it reports loudly when it cannot run.
4. **caps** -- the overflow goes to ``log/deferred.jsonl``, which is how the
   brief is made physically unable to grow.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

from .annotate import QUESTIONS, pending_count, question_targets
from .frontmatter import parse_frontmatter
from .fs import append_jsonl, append_text, rewrite_fields, write_text
from .i18n import Catalog, load_catalog
from .jsonc import JSONCError, load_jsonc
from .paths import Workspace, WorkspaceError, expand, resolve_workspace
from .sense import status_of

__all__ = [
    "main", "classify", "render_brief", "score_project", "declared_impact",
    "evidence_phrase",
    "check_evidence", "gate_maps", "gated_text", "md_cell", "non_goal_flag",
    "enforce_write_permissions", "should_notify", "write_day_log", "append_jsonl",
    "read_prev_run",
]

GENERATOR = "nextbrief render"

# Same numbers `sense` uses, and deliberately the same literal 3: a scheduler
# running `check || run` branches on the code without caring which stage decided
# the workspace was out of date.
EXIT_STALE = 3

# Fields an agent must never write. See docs, "what the daily pass may write".
HUMAN_ONLY_FIELDS = ["priority", "is_next_action", "human_confirmed", "project", "id", "created_by"]
TERMINAL_STATUSES = ("done", "dropped")
OPEN_STATUSES = ("open", "in_progress", "waiting")

WEEKDAY_KEYS = [
    "brief.weekday.mon", "brief.weekday.tue", "brief.weekday.wed", "brief.weekday.thu",
    "brief.weekday.fri", "brief.weekday.sat", "brief.weekday.sun",
]

GIT_TIMEOUT = 10

# A missing config key must not take down the nightly run, so the numbers that
# actually bound the output have defaults here as well as in the shipped config.
CAP_DEFAULTS = {
    "max_next_actions": 3, "max_waiting_for": 5, "max_agent_queue": 3,
    "max_decision_pending": 3, "per_project_line_chars": 140,
    # 60 was set against a smaller portfolio and had stopped being a ceiling:
    # measured on a twelve-project workspace the brief wants 72 lines, so the
    # gate fired every single morning. A cap that always fires is not bounding
    # the document, it is deleting the end of it -- and this tool is now aimed at
    # people with more projects, not fewer.
    #
    # 100 rather than "however many it takes": the cap exists because a daily
    # document nobody finishes is a document nobody reads, and that argument does
    # not weaken as the portfolio grows. It leaves real headroom over the
    # reference workspace and still fits on about two screens.
    #
    # Not derived from the project count. A ceiling that rises to meet its
    # contents is not a ceiling, and the one number a reader can hold in their
    # head about this document is how long it can ever get.
    "brief_max_lines": 100,
}
LIMIT_DEFAULTS = {"max_open_items_total": 40, "max_open_per_project": 5}
SCORING_DEFAULTS = {
    "half_life_days": 21, "decay_floor": 0.3, "deadline_boost_max": 3.0,
    # Phase, not importance. Importance is `ice.impact` and is the base; this
    # says how much a project's phase should damp it. `done` is 0.0 rather than a
    # small number: a finished project should leave the ranking, not linger at
    # the bottom of it.
    "status_weight": {"active": 1.0, "maintenance": 0.6, "frozen": 0.3, "done": 0.0},
    # There is deliberately no `tier_weight` fallback here, and the absence is
    # the point. It sat in this dict with a comment promising it was "read only
    # when `status_weight` is absent" -- a promise no line of code kept, because
    # `scoring_of` merges these defaults first, so `status_weight` is never
    # absent and the branch was unreachable by construction.
    #
    # It cannot be written, either. `tier` said two things at once: the old table
    # weighed `flagship` 1.3 and `active` 1.0, and both migrate to the single
    # status `active`. There is no answer to what that weight should now be,
    # which is the ambiguity the split existed to end. A config still naming it
    # is reported by `sense` rather than translated -- see `retired_config_key`.
}


def caps_of(cfg) -> Dict[str, Any]:
    merged = dict(CAP_DEFAULTS)
    merged.update((cfg or {}).get("caps") or {})
    return merged


def limits_of(cfg) -> Dict[str, Any]:
    merged = dict(LIMIT_DEFAULTS)
    merged.update((cfg or {}).get("limits") or {})
    return merged


def scoring_of(cfg) -> Dict[str, Any]:
    """Shipped defaults with the workspace's ``scoring`` block laid over them.

    Nested tables merge key by key rather than being replaced wholesale. A config
    that names one phase -- ``"status_weight": {"frozen": 0.5}`` -- means "that one
    is heavier", not "the other three cease to exist"; under a flat update they
    silently fell back to the 1.0 default and every non-flagship project quietly
    changed weight. The failure is invisible in the config file, which still reads
    as though it says one thing.
    """
    merged = dict(SCORING_DEFAULTS)
    for key, value in ((cfg or {}).get("scoring") or {}).items():
        base = merged.get(key)
        if isinstance(base, dict) and isinstance(value, dict):
            nested = dict(base)
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# workspace-scoped IO
#
# `write_text`, `append_text`, `append_jsonl` and `rewrite_fields` are imported
# from nextbrief.fs, which is the only module in the package that mutates a
# filesystem. They are re-exported here because this is where they used to live
# and where a reader looking for "what does the renderer write" still looks.
# ---------------------------------------------------------------------------


def _run(args, cwd=None, timeout=GIT_TIMEOUT):
    """(ok, stdout). Never raises -- fail-open.

    Deliberately local rather than imported from the sensing stage: rendering
    must not depend on the module that walks the filesystem.
    """
    try:
        proc = subprocess.run(
            args, cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False, ""
    if proc.returncode != 0:
        return False, ""
    return True, proc.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def load_backlog(ws: Workspace, rejected: List[dict]) -> List[dict]:
    items: List[dict] = []
    if not ws.backlog.is_dir():
        return items
    for f in sorted(ws.backlog.glob("*.md")):
        if f.name.startswith("_"):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = parse_frontmatter(text)
        if fm is None:
            rejected.append({"kind": "backlog_parse", "file": f.name,
                             "why": "frontmatter did not parse"})
            continue
        fm["_file"] = f.name
        fm["_path"] = str(f)
        fm["_body"] = body
        items.append(fm)
    return items


def self_project_ids(snap, reg=None, ws=None):
    """Ids of the entry (if any) that represents the workspace itself.

    A workspace registered in its own registry is a good idea -- it gets judged
    by its own decay rules, which is a built-in way for the tool to tell you it
    has stopped being worth keeping. But it must never appear in its own
    portfolio counts, or "nextbrief ran" reads as "you made progress".

    Detected three ways so that no single convention has to hold: an explicit
    ``self`` flag on the entry, ``self_project`` in the registry, or a declared
    path that resolves to the workspace root.
    """
    ids = set()
    reg = reg or {}
    declared = reg.get("self_project")
    if declared:
        ids.add(declared)
    root = None
    if ws is not None:
        base = (reg.get("defaults") or {}).get("root")
        root = expand(base) if base else ws.root
    for p in (snap.get("projects") or []):
        if p.get("self") or p.get("is_self"):
            ids.add(p.get("id"))
            continue
        if root is None or ws is None:
            continue
        for rel in (p.get("paths") or []):
            try:
                if (root / rel).resolve() == ws.root.resolve():
                    ids.add(p.get("id"))
                    break
            except OSError:
                continue
    return ids


# ---------------------------------------------------------------------------
# Gate 3: write permissions. An agent may retract its own guesses; it may never
# touch your commitments.
# ---------------------------------------------------------------------------

class WriteGate(NamedTuple):
    """Tri-state outcome, recorded in log/runs.jsonl.

    An ``int`` return could not distinguish "checked everything, found nothing"
    from "never ran". Since this gate is the enforcement mechanism for the one
    rule the whole system rests on, that ambiguity was the bug: a machine with
    no git binary reported a clean run forever.

    ``unchecked`` is the same argument one level down: a run in which the gate
    ran but could find no baseline for some entries is not a clean run for those
    entries, and saying "0 reverted" without saying "n unchecked" reads as one.
    """

    state: str          # "ran" | "no_repo" | "no_commits"
    reverted: int
    detail: str         # developer-facing; why it could not run
    unchecked: int = 0  # entries with no baseline in HEAD, by name or by id


def _baseline_by_id(git, ws: Workspace, prefix: str) -> Dict[str, str]:
    """``{frontmatter id: HEAD text}`` for every backlog file in HEAD.

    Built only when a lookup by filename misses. Looking the baseline up by name
    alone means *renaming* a backlog file turns this gate off for that item --
    the file looks new, so nothing is compared, and the run still records a clean
    gate. Renaming is a normal editing action, so it must not be a way to slip an
    edit past the one rule the system enforces.
    """
    out: Dict[str, str] = {}
    ok, listing = _run([git, "-C", str(ws.root), "ls-tree", "-r", "--name-only",
                        "--full-name", "HEAD", "--", ws.backlog.name])
    if not ok:
        return out
    for rel in listing.splitlines():
        rel = rel.strip()
        if not rel.endswith(".md"):
            continue
        got, text = _run([git, "-C", str(ws.root), "show", "HEAD:" + rel])
        if not got or not text.strip():
            continue
        fm, _body = parse_frontmatter(text)
        if fm and fm.get("id") is not None:
            # First wins: two files claiming one id is its own problem, and
            # picking deterministically keeps the run byte-identical.
            out.setdefault(str(fm["id"]), text)
    return out


def enforce_write_permissions(ws: Workspace, items, rejected, dry_run=False) -> WriteGate:
    """Diff each backlog entry against git HEAD field by field and revert what an
    agent was not allowed to change.

    The baseline is ``HEAD:<prefix>backlog/<file>`` where ``prefix`` is the
    workspace's own position inside the repository -- assuming the workspace sits
    at the repository root silently disables the gate for anyone who keeps their
    vault in a subdirectory.
    """
    git = shutil.which("git")
    if git is None:
        rejected.append({"kind": "gate_disabled", "gate": "write_permissions",
                         "why": "git executable not found on PATH"})
        return WriteGate("no_repo", 0, "git-missing")

    ok, top = _run([git, "-C", str(ws.root), "rev-parse", "--show-toplevel"])
    if not ok or not top.strip():
        rejected.append({"kind": "gate_disabled", "gate": "write_permissions",
                         "why": "workspace is not inside a git repository"})
        return WriteGate("no_repo", 0, "no-repo")
    ok, prefix = _run([git, "-C", str(ws.root), "rev-parse", "--show-prefix"])
    prefix = prefix.strip() if ok else ""

    # If toplevel+prefix is not the workspace we asked about, we are reading
    # somebody else's history -- reverting fields from it would be worse than
    # not checking at all.
    try:
        located = (Path(top.strip()) / prefix).resolve() == ws.root.resolve()
    except OSError:
        located = False
    if not located:
        rejected.append({"kind": "gate_disabled", "gate": "write_permissions",
                         "why": "git toplevel + prefix does not resolve to the workspace root",
                         "toplevel": top.strip(), "prefix": prefix})
        return WriteGate("no_repo", 0, "prefix-mismatch")

    ok, _ = _run([git, "-C", str(ws.root), "rev-parse", "HEAD"])
    if not ok:
        return WriteGate("no_commits", 0, "repository has no commits yet")

    reverted = 0
    unchecked = 0
    by_id: Optional[Dict[str, str]] = None
    for it in items:
        rel = "%s%s/%s" % (prefix, ws.backlog.name, it["_file"])
        got, old_text = _run([git, "-C", str(ws.root), "show", "HEAD:" + rel])
        if not got or not old_text.strip():
            # The file is not in HEAD under that name. Before believing it is
            # new, look for the same id under any other name.
            if by_id is None:
                by_id = _baseline_by_id(git, ws, prefix)
            old_text = by_id.get(str(it.get("id"))) or ""
            if not old_text:
                unchecked += 1
                rejected.append({"kind": "no_baseline", "gate": "write_permissions",
                                 "file": it["_file"], "id": it.get("id"),
                                 "why": "no copy in HEAD under this name or this id; "
                                        "nothing was compared for this entry"})
                continue
            rejected.append({"kind": "renamed_entry", "gate": "write_permissions",
                             "file": it["_file"], "id": it.get("id"),
                             "why": "baseline located by frontmatter id, not by filename"})
        old_fm, _body = parse_frontmatter(old_text)
        if not old_fm:
            unchecked += 1
            rejected.append({"kind": "no_baseline", "gate": "write_permissions",
                             "file": it["_file"], "id": it.get("id"),
                             "why": "the HEAD copy has no parsable frontmatter"})
            continue

        bad = []
        for field in HUMAN_ONLY_FIELDS:
            if field in old_fm and it.get(field) != old_fm.get(field):
                bad.append((field, old_fm.get(field), it.get(field)))
        # Terminal statuses are written by humans only.
        if str(it.get("status", "")).lower() in TERMINAL_STATUSES and \
           str(old_fm.get("status", "")).lower() not in TERMINAL_STATUSES:
            bad.append(("status", old_fm.get("status"), it.get("status")))
        # Once you have confirmed an entry, its automation block freezes too.
        if old_fm.get("human_confirmed") is True and it.get("automation") != old_fm.get("automation"):
            bad.append(("automation", old_fm.get("automation"), it.get("automation")))

        if not bad:
            continue

        # Nested blocks are restored in memory only: the frontmatter writer works
        # a line at a time, and flattening a mapping into one line would destroy
        # more than the illegal edit did.
        on_disk = {}
        for field, oldv, newv in bad:
            it[field] = oldv
            scalar = not isinstance(oldv, (dict, list))
            if scalar:
                on_disk[field] = oldv
            rejected.append({
                "kind": "illegal_field_write", "file": it["_file"], "field": field,
                "reverted_to": oldv, "attempted": newv,
                "restored": "file" if scalar else "memory-only",
                "why": "field is human-writable only",
            })
            reverted += 1
        if not dry_run and on_disk and ws.contains(it["_path"]):
            try:
                rewrite_fields(ws, it["_path"], on_disk)
            except OSError:
                pass
    return WriteGate("ran", reverted, "", unchecked)


# ---------------------------------------------------------------------------
# Gate 1: evidence
# ---------------------------------------------------------------------------

def _claim_text(claim) -> str:
    """The reader-facing text of a claim, for the rejection log. Tolerates any
    shape: this is called on input that has already failed a structural check."""
    if isinstance(claim, dict):
        return str(claim.get("text", claim.get("title", "")))[:300]
    return str(claim)[:300]


def check_evidence(claim, index, cfg, rejected, where, cat=None) -> bool:
    """True if the claim may be rendered. A claim whose source does not resolve
    is *not rendered* -- there is no "render it with a warning" option, because a
    warning next to a fabricated sentence still reads as a fact.

    Every structural surprise is a rejection, never an exception. ``brief.json``
    is model output: it is malformed sooner or later, and a shape this function
    did not expect used to abort the whole run -- no BRIEF.md, no success
    sentinel, and yesterday's brief left on disk looking current. Refusing to
    render one claim is the correct failure; refusing to render the day is not.
    """
    if not isinstance(claim, dict):
        rejected.append({"kind": "malformed_claim", "where": where,
                         "text": _claim_text(claim),
                         "why": "claim is %s, not an object" % type(claim).__name__})
        return False
    evs = claim.get("evidence") or []
    if not isinstance(evs, list):
        # An object, a string or a number here means the model did not produce an
        # evidence *array*, so there is nothing to resolve -- same outcome as
        # omitting the field, and recorded as such.
        rejected.append({"kind": "no_evidence", "where": where,
                         "text": _claim_text(claim),
                         "why": "evidence is %s, not an array" % type(evs).__name__})
        return False
    if not evs:
        rejected.append({"kind": "no_evidence", "where": where,
                         "text": _claim_text(claim),
                         "why": "claim carries no evidence array"})
        return False
    pat = (cfg.get("evidence") or {}).get("none_allowed_pattern")
    if not pat and cat is not None:
        pat = cat.t("evidence.none_allowed_pattern")
    pat = str(pat) if pat else None
    for ev in evs:
        if not isinstance(ev, dict):
            rejected.append({"kind": "malformed_evidence", "where": where,
                             "text": _claim_text(claim),
                             "why": "evidence entry is %s, not an object" % type(ev).__name__})
            return False
        kind = ev.get("kind")
        src = ev.get("source")
        if kind == "none":
            # The only legitimate use: "no signal since X".
            if pat and pat in str(claim.get("text", "")):
                continue
            rejected.append({"kind": "bad_none", "where": where,
                             "text": _claim_text(claim),
                             "why": "kind=none is only allowed with the %r phrasing" % pat})
            return False
        # A non-string source cannot be looked up at all -- and an unhashable one
        # would raise on the membership test rather than fail the claim.
        if not src or not isinstance(src, str) or src not in index:
            rejected.append({"kind": "unresolvable_evidence", "where": where,
                             "text": _claim_text(claim),
                             "source": src if isinstance(src, str) else repr(src)[:120],
                             "evidence_kind": kind if isinstance(kind, str) else repr(kind)[:60],
                             "why": "source does not resolve in snapshot.evidence_index"})
            return False
        # ★ Only commit / session get their kind checked. ★
        #
        # This gate exists to stop *fabrication*, not *imprecision*. An
        # unresolvable source is fabrication and must go. But one source often
        # legitimately supports several kinds -- a status document has both a
        # self-declared date and an mtime; a backlog file is a file, a
        # human statement, and a structured document at once. Being strict here
        # would silently delete correct claims, which is far worse than an
        # occasionally loose label.
        #
        # commit and session are the exception: they assert visibly more
        # confidence ("178 commits" vs "some file was touched"). Citing a file
        # path to support a commit count really is misleading.
        entry = index.get(src) or {}
        if not isinstance(entry, dict):
            entry = {}
        kinds = entry.get("kinds") or ([entry["kind"]] if entry.get("kind") else [])
        if not isinstance(kinds, list):
            kinds = [kinds]
        if kind in ("commit", "session") and kinds and kind not in kinds:
            rejected.append({"kind": "evidence_kind_mismatch", "where": where,
                             "source": src, "declared": kind, "actual": kinds,
                             "why": "that source cannot supply %s-grade evidence" % kind})
            return False
    return True


GATED_MAPS = ("delegated", "decision_notes")
GATED_KEY = "_gated"


def gate_maps(brief, index, cfg, rejected, cat=None) -> int:
    """Put ``delegated`` and ``decision_notes`` through the evidence gate.

    ★ These were the two pieces of model prose that reached the reader unchecked,
    and ``decision_notes`` reached it only through BRIEF.html -- which is what
    ``nextbrief open`` shows -- three lines above a footer stating that every
    claim had passed the gate. Either the gate covers everything a model wrote or
    the footer is false; there is no third option.

    Both shapes are accepted: a bare string (the shape ``brief.schema.json``
    documents, which carries no evidence and is therefore always dropped and
    logged) and ``{"text": ..., "evidence": [...]}``, which is checked like any
    other claim. What survives is written under ``_gated`` rather than back over
    the input, so calling a renderer directly on an ungated ``brief.json`` cannot
    put the model's sentence on the page.
    """
    if not isinstance(brief, dict):
        return 0
    dropped = 0
    out: Dict[str, Dict[str, str]] = {}
    for section in GATED_MAPS:
        raw = brief.get(section)
        kept: Dict[str, str] = {}
        if raw:
            if not isinstance(raw, dict):
                rejected.append({"kind": "malformed_section", "where": section,
                                 "why": "%s must be an object keyed by project id, got %s"
                                        % (section, type(raw).__name__)})
            else:
                for pid, claim in sorted(raw.items(), key=lambda kv: str(kv[0])):
                    norm = claim if isinstance(claim, dict) else {"text": claim}
                    if not check_evidence(norm, index, cfg, rejected, section, cat):
                        dropped += 1
                        continue
                    text = str(norm.get("text", "")).strip()
                    if text:
                        kept[pid] = text
        out[section] = kept
    brief[GATED_KEY] = out
    return dropped


def gated_text(brief, section: str, key) -> str:
    """The gated text for one project, or ``""``.

    Both renderers read these maps only through here, which is what stops one of
    them from showing a sentence the other dropped.
    """
    sec = (brief or {}).get(GATED_KEY) if isinstance(brief, dict) else None
    if not isinstance(sec, dict):
        return ""
    vals = sec.get(section)
    if not isinstance(vals, dict):
        return ""
    v = vals.get(key)
    return v.strip() if isinstance(v, str) else ""


# ---------------------------------------------------------------------------
# Gate 2: non-goals (flag, do not block)
# ---------------------------------------------------------------------------

SEP_RE = r"[\s/·、，,()（）:：\-—_*`]"


def non_goal_flag(text, non_goals):
    """Flag rather than block: silently dropping a good suggestion is worse than
    one false positive.

    Both sides must be normalised the same way, or a declared non-goal written
    with spaces around its separator will never match the same words written
    without them. Case folds too: in a language that has case, "Build a mobile
    app" and "build a mobile app" are the same non-goal, and a gate that misses
    that is a gate that quietly does nothing.
    """
    if not non_goals or not text:
        return None
    t = re.sub(SEP_RE, "", text).lower()
    for ng in non_goals:
        core = re.sub(SEP_RE, "", ng).lower()
        if len(core) >= 3 and core in t:
            return ng
    return None


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------

def _dated_commitments(p, outcomes=None):
    """Every dated thing this project is on the hook for: its own deadlines, plus
    the dated outcomes it serves.

    The two are scored by identical arithmetic and combined with ``max``, which is
    what keeps a shared commitment from being counted once per contributor. Three
    projects serving one date used to duplicate that date into three registry
    entries and each earn the full boost independently, so one commitment produced
    three urgent rows at the top of the table. Declared once as an outcome, it
    lifts each contributor exactly as much as its own deadline would have.

    Compounding outcomes are absent on purpose. They have no date to be near, and
    a constant standing in for "long-term work counts extra" would be precisely
    the uncitable number this engine refuses to put on a page.
    """
    out = list(p.get("deadlines") or [])
    if not outcomes:
        return out
    for oid in p.get("serves") or []:
        o = outcomes.get(oid)
        # `done` is why a satisfied commitment stops shouting. An outcome whose
        # date has passed is `overdue`, which takes the maximum boost -- correct
        # for one you missed, permanent nonsense for one you met. The engine
        # cannot tell those apart; the person who was there can.
        if o and o.get("kind") == "dated" and not o.get("done"):
            out.append(o)
    return out


def declared_impact(p):
    """The declared importance as a finite number, or None if there is not one.

    `registry.jsonc` is hand-edited and `check_shapes` never sees `ice`, so
    ``"impact": "high"`` reaches the scorer intact. Raising there is the wrong
    answer twice over: the module contract is fail open -- one malformed file
    must not cost the whole brief -- and on the unattended path an exception is a
    stack trace and no brief at all.

    NaN is excluded for a different reason. It compares false against everything,
    so a single NaN score makes the sort key non-total and the resulting order
    depends on where the comparison happened to start. This package guarantees
    byte-identical output for identical input; one NaN quietly withdraws that.

    Booleans are excluded because ``True`` is an ``int`` in Python and would
    otherwise score as impact 1, which is a typo scoring as a judgement.
    """
    value = (p.get("ice") or {}).get("impact")
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def is_judged(p) -> bool:
    """Has a human said how much this project matters?

    `ice` and `status` are judgements, and the sensing stage refuses to invent
    either: a discovered project carries ``None`` for both, on the stated grounds
    that a synthesised midpoint is an assertion rather than a neutral guess.

    This function exists so the renderer keeps that promise. It used to break it
    twice in twenty lines -- absent `ice` was read as {3,3,3} and absent phase
    as "active" -- which meant an unreviewed project was ranked on numbers
    nobody had supplied, while `classify` two functions down read the same absent
    phase honestly and declined to reach a verdict. One missing field, two
    readings, one file.

    Note which kind of absence this is. `days_since` missing means the engine
    looked and found nothing, and scoring that low is honest. `impact` missing
    means nobody was ever asked. The snapshot already distinguishes declared from
    observed from absent; collapsing the third into the second is the error.

    Only `impact` is tested, because it is the only axis the score reads. The
    other two parse from a registry and are ignored -- see `score_project`.
    """
    return declared_impact(p) is not None


def _number(value):
    """A finite number, or None. Signed -- callers that need a floor say so.

    `sense` validates these at the registry boundary now, but this module reads a
    `snapshot.json` off disk without re-sensing, so it still meets values written
    by an older version or edited by hand. Comparing an int with a str is a
    `TypeError`, and one raised from inside `classify`'s sort key costs the whole
    brief rather than one row.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _age_days(value):
    """`days_since` as a non-negative number, or None if it is not a number.

    An age cannot be negative, and this is the layer that has to insist on it.
    `sense` now floors it at 0 too, but `render` re-reads an existing
    `snapshot.json` without re-sensing -- so a snapshot written before that fix,
    or by a future parser with a new bug, still arrives here. The same reasoning
    as `status_of`: a guarantee that lives in only one of two layers is a
    guarantee that lapses on upgrade.

    What a negative value did: the decay term is ``0.5 ** (days / half_life)``,
    which is bounded by 1.0 for days >= 0 and unbounded below it. One file dated
    a year ahead -- a NAS with a wrong clock, an archive unpacked with its
    original timestamps -- scored 477911 against a normal maximum of 4, and
    pinned that project to the top of the ranking. Far enough ahead it stopped
    being a wrong number and became an `OverflowError` raised from inside a sort
    key, which costs the whole brief rather than one row.

    Non-numeric reads as None rather than raising, for the reason
    `declared_impact` gives at length: this file is re-read from disk, the module
    contract is fail open, and on the unattended path an exception is a stack
    trace and no brief at all. Booleans are excluded because ``True`` is an
    ``int`` and would score as one day old.
    """
    number = _number(value)
    return None if number is None else max(0.0, number)


def score_project(p, cfg, outcomes=None):
    """Rank a project that has been judged. Callers must check `is_judged` first.

    Scoring an unjudged project is meaningless rather than merely imprecise:
    every term below multiplies a human's stated importance, and there is no
    number that stands in for one that was never given.
    """
    # The base is the declared importance and nothing else.
    #
    # It used to be ``(impact x confidence) / effort``, with 3s standing in for
    # the two axes `review` does not ask about. That kept hand-written
    # three-axis registries scoring exactly as before -- and produced two
    # incompatible regimes in one list. A reviewed project scored its impact; a
    # hand-written one scored impact x confidence / effort, so a flagship rated 5
    # and divided by effort 5 ranked below an active project rated 4 and divided
    # by 2. The larger project lost for being large, which is the precise failure
    # `annotate.py` cites as the reason effort stopped being asked at all.
    #
    # Both axes remain readable in a registry and are ignored here. Keeping them
    # live to honour old files is what made the ordering incoherent, and an
    # ordering that mixes two definitions is not an ordering.
    base = declared_impact(p) or 0.0

    days = _age_days((p.get("evidence") or {}).get("days_since"))
    sc = scoring_of(cfg)
    floor = sc["decay_floor"]
    if days is None:
        # No readable evidence at all -- no commit, no file mtime, no session.
        # This used to score 1.0, the *maximum* freshness term, so a directory
        # nobody had touched ranked exactly as though it had been worked on this
        # morning. Absence of evidence was being read as evidence of activity.
        #
        # It was close to unreachable while every project was hand-declared. Once
        # discovery began adopting whatever sits in the root, empty and
        # unreadable directories became ordinary, and so did the inversion.
        # Unknown recency now earns no recency credit; the floor still keeps the
        # project on the page, which is the whole point of having a floor.
        decay = 0.0
    else:
        decay = 0.5 ** (days / float(sc["half_life_days"]))
    # ★ Why the floor: pure decay buries exactly the thing you are avoiding.
    decay_term = floor + (1.0 - floor) * decay

    boost = 1.0
    for d in _dated_commitments(p, outcomes):
        # Coerced on the way in for the same reason `_age_days` is: `sense` now
        # validates these at the registry boundary, and this function still has
        # to read a `snapshot.json` written before it did. `days_until` is signed
        # -- an overdue commitment is negative and that is the whole point -- so
        # only the window is floored.
        until = _number(d.get("days_until"))
        lead = _age_days(d.get("lead_days"))
        if until is not None and until < 0:
            boost = max(boost, 1.0 + sc["deadline_boost_max"])
        elif d.get("in_lead_window") and lead and until is not None:
            frac = (lead - until) / float(lead)
            boost = max(boost, 1.0 + sc["deadline_boost_max"] * max(0.0, frac))

    # An undeclared status weighs 1.0 rather than nothing: `review` has simply
    # not asked yet, and a project should not be demoted for a question nobody
    # put to its owner. What an undeclared status does withhold is a *verdict* --
    # see `classify` -- because saying "you have neglected this" requires knowing
    # it was supposed to be moving.
    # Through `status_of`, so that a snapshot written before the split still
    # renders. `render` re-reads an existing snapshot.json without re-sensing --
    # without the fallback an upgrade would silently stop producing verdicts on
    # every workspace that had not re-sensed yet, which is the quietest possible
    # regression.
    weights = sc.get("status_weight") or {}
    phase = status_of(p)
    tw = weights.get(phase, 1.0) if phase else 1.0
    return base * decay_term * boost * tw


# ---------------------------------------------------------------------------
# classification -- computed once, consumed by both renderings
# ---------------------------------------------------------------------------

# What a crowded brief gives up, and in what order. Higher survives longer.
#
# Reading order and drop order are different questions, and conflating them is
# how this went wrong the first time. The file is written in the order it should
# be READ, which puts the reminders near the bottom -- so dropping from the end
# sacrificed the warnings first, the exact outcome the comment above the
# questions block exists to prevent. Measured on a twelve-project workspace:
# nineteen lines over, and what went was the whole reminders section.
#
# So the order stays as it reads and this table says what may be lost. The two
# are allowed to disagree because they answer different questions: what do I read
# first, and what can I afford to lose.
KEEP = {
    # Enrichment. Useful, and none of it is a warning or a decision.
    # Its own tier, below everything, because the block that emits it says so:
    # "a question that waits a night costs nothing". Sharing tier 0 with the
    # agent queue meant the tie-break decided between them by position, and
    # position put the questions last -- so the queue was given up to keep them.
    "questions": -1,
    "automation_review": 0,
    "agent_queue": 0,
    "waiting": 1,
    # The whole portfolio in one table -- the largest section by far, and the
    # one whose every value is recoverable verbatim from state/snapshot.json.
    "projects": 2,
    # Verdicts. Something is wrong and a person has to look at it.
    "stalled": 3,
    "decision_pending": 3,
    "most_urgent": 3,
    # Never given up while anything else can be. The reminders are the brief's
    # only warnings -- which projects have no version control, which status
    # documents contradict each other -- and the next actions are the answer to
    # the question the reader opened the file to ask.
    "next_actions": 4,
    "reminders": 4,
}


def section(lines: List[str], marks: List[dict], key: str, title: str) -> None:
    """Open a section, and record where it starts and what it is worth.

    Recorded as it is written rather than recovered afterwards by scanning for
    lines beginning with "## ". That scan would be right almost always and wrong
    on the day a project is named with a leading hash -- and the failure would be
    a brief silently cut in the wrong place, which is the least debuggable kind.

    An unknown key sorts with the enrichment tier rather than raising: a section
    added later and not listed here should cost its author a review comment, not
    the reader their brief.
    """
    marks.append({"start": len(lines), "key": key, "keep": KEEP.get(key, 0)})
    lines.append("## " + title)


def classify(snap, backlog, cfg, reg=None, ws=None) -> Dict[str, Any]:
    """Decide, once, which projects are stalled / neglected / awaiting a decision.

    Both BRIEF.md and BRIEF.html consume this result and neither is allowed to
    reach its own verdict; that is the only reason the two artifacts cannot drift
    apart.
    """
    projects = snap.get("projects") or []
    self_ids = self_project_ids(snap, reg, ws)
    outcomes = {o["id"]: o for o in (snap.get("outcomes") or []) if o.get("id")}
    # Two lists, because they answer different questions. `ranked` means "things
    # you have judged, in the order they earned"; `unjudged` means "things nobody
    # has been asked about". Merging them would require a score for the second
    # kind, and a low position is itself a claim -- less important than everything
    # above it -- about a project no one ever rated.
    judged = [p for p in projects if is_judged(p)]
    unjudged = [p for p in projects if not is_judged(p)]
    ranked = sorted(judged,
                    key=lambda p: (-score_project(p, cfg, outcomes), str(p.get("id"))))
    unjudged.sort(key=lambda p: str(p.get("id")))


    open_items = [b for b in backlog
                  if str(b.get("status", "open")).lower() in OPEN_STATUSES]
    by_proj: Dict[Any, List[dict]] = {}
    for b in open_items:
        by_proj.setdefault(b.get("project"), []).append(b)

    # An empty backlog means "not bootstrapped yet", not "everything is stalled".
    # Conflating the two turns the very first brief into eight lines of noise,
    # and noise is how systems like this die.
    bootstrapped = len(backlog) > 0

    decision_pending, stalled, neglected = [], [], []
    waiting_on_work = []
    for p in projects:
        pid = p.get("id")
        if pid in self_ids:
            continue
        has_next = any(b.get("is_next_action") for b in by_proj.get(pid, []))
        if p.get("blocked_by") == "decision" and p.get("open_decision"):
            decision_pending.append(p)
            continue
        # A project with its own daily entry point is never "stalled": its next
        # step lives elsewhere, and we rank it without restating its contents.
        if p.get("has_own_daily_entry"):
            continue
        # Waiting on another project is not neglect. Without this the renderer
        # cannot tell a platform deliberately parked until its ecosystem exists
        # from one nobody remembered -- both are simply quiet -- and it called
        # both neglected, which is a verdict about the decision its owner
        # reasoned hardest about. Named here rather than counted, because "why"
        # is the whole content of the answer.
        if p.get("needs"):
            waiting_on_work.append(p)
            continue
        ev = p.get("evidence") or {}
        # Only an *active* project can be neglected or stalled. Maintenance is
        # the declaration that it is meant to be quiet, and being told that a
        # thing doing exactly what you asked of it has gone quiet is the kind of
        # warning that teaches people to stop reading warnings.
        if status_of(p) == "active":
            if bootstrapped and not has_next and not by_proj.get(pid):
                stalled.append(p)
            d = _age_days(ev.get("days_since"))
            limit = _age_days(p.get("neglect_days"))
            if d is not None and d > (30 if limit is None else limit):
                neglected.append(p)
        elif status_of(p) == "frozen" and bootstrapped:
            g = (p.get("git") or [{}])[0]
            if g.get("uncommitted"):
                stalled.append(p)

    return {
        "ranked": ranked,
        "unjudged": unjudged,
        "decision_pending": decision_pending,
        "waiting_on_work": waiting_on_work,
        "stalled": stalled,
        "neglected": neglected,
        "open": open_items,
        "open_items": len(open_items),
        "by_project": by_proj,
        "self_ids": self_ids,
        "bootstrapped": bootstrapped,
        "truncated_lines": 0,
        "decision_ids": {p.get("id") for p in decision_pending},
        "stalled_ids": {p.get("id") for p in stalled},
        "neglected_ids": {p.get("id") for p in neglected},
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

SIGNAL_KEYS = {"hot": "signal.hot", "warm": "signal.warm", "cold": "signal.cold",
               "dormant": "signal.dormant", "unknown": "signal.unknown"}
TIER_KEYS = {"hook": "action.tier.hook", "skill": "action.tier.skill",
             "explore": "action.tier.explore"}


def evidence_phrase(p, cat: Catalog) -> str:
    """★ The brief must name the kind of signal it is quoting.
    "76 files changed (file mtimes; no git here)" and "178 commits" should not
    read the same, because they are not the same claim."""
    ev = p.get("evidence") or {}
    fs = p.get("fs") or {}
    changed = fs.get("changed") or {}
    bits = []
    if p.get("has_git") and p.get("git"):
        r = p["git"][0]
        c30 = (r.get("commits_since") or {}).get("30")
        if c30:
            bits.append(cat.t("evidence.commits_30d", count=c30))
        lc = r.get("last_commit")
        if lc:
            bits.append(cat.t("evidence.last_commit", date=lc.get("date", "")))
        if r.get("uncommitted"):
            bits.append(cat.t("evidence.uncommitted", count=r["uncommitted"]))
    ch7 = changed.get("7") or 0
    if ch7:
        bits.append(cat.t("evidence.files_7d", count=ch7))
    ad = fs.get("distinct_active_days_30d") or 0
    if ad:
        bits.append(cat.t("evidence.active_days_30d", count=ad))
    s = p.get("sessions") or {}
    if s.get("distinct_session_days"):
        bits.append(cat.t("evidence.session_days", count=s["distinct_session_days"]))
    if not bits:
        bits.append(cat.t("evidence.no_signal_since",
                          date=ev.get("best_date") or cat.t("evidence.unknown_date")))
    if ev.get("caveat"):
        bits.append(cat.t("evidence.caveat_mtime"))
    return cat.t("sep.dot").join(bits)


# A sentence ender is a property of the *text*, not of the interface language: a
# brief rendered in English still quotes Chinese documents and vice versa. Keying
# this to the UI locale meant an English render split on every '.', so
# "rotate config.json first" was cut down to "rotate config".
_CLAUSE_RE = re.compile(r"[;；。！？]|[.!?](?=\s|$)")


def _first_clause(text) -> str:
    """First clause only. The detail lives in the backlog file; the brief keeps
    just "what is left for the human", because that is the part you decide on."""
    return _CLAUSE_RE.split(str(text or ""), maxsplit=1)[0].strip()


# ANSI escape sequences reach the terminal of anyone who cats BRIEF.md, so they
# are removed as a sequence; whatever control characters remain are removed
# individually. Tab and newline are left to the whitespace collapse below.
_ANSI_RE = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def md_cell(value) -> str:
    """Make one value safe to interpolate into a Markdown table cell.

    Project names come from a registry a human hand-edits and from directory
    names on disk, so a ``|`` or a newline is an ordinary accident -- and it
    corrupts the table for the *reader*, who has no way to tell a broken row from
    a missing project.
    """
    s = _CTRL_RE.sub("", _ANSI_RE.sub("", str(value if value is not None else "")))
    # Backslash first: escaping the pipe afterwards would otherwise double back
    # over its own escape.
    s = s.replace("\\", "\\\\").replace("|", "\\|")
    return " ".join(s.split())


def _days_until(deadline) -> int:
    """``days_until`` as an int. The field comes from a snapshot, and a snapshot
    that lost it must not decide whether a deadline reads as overdue."""
    try:
        return int(deadline.get("days_until") or 0)
    except (TypeError, ValueError):
        return 0


def render_brief(snap, brief, backlog, cfg, reg, cat: Catalog, notes, meta=None):
    run = snap.get("run") or {}
    gen = dt.datetime.fromisoformat(run["generated_at"])
    as_of = dt.date.fromisoformat(run["as_of_date"])
    caps = caps_of(cfg)
    limits = limits_of(cfg)
    if meta is None:
        meta = classify(snap, backlog, cfg, reg)
    L: List[str] = []
    # Where each section begins and what it is worth, so gate 4 can drop whole
    # ones cheapest-first.
    marks: List[dict] = []

    ranked = meta["ranked"]
    # Ranked first, then the unrated in id order. Excluding a project from the
    # *ordering* is honest -- nobody said what it is worth. Excluding it from the
    # *page* would be the bug this whole split exists to avoid, so the table and
    # the deadline sweep both walk the concatenation.
    listed = ranked + meta.get("unjudged", [])
    self_ids = meta["self_ids"]
    by_proj = meta["by_project"]
    open_items = meta["open"]
    dec_ids, stall_ids, neg_ids = meta["decision_ids"], meta["stalled_ids"], meta["neglected_ids"]
    decision_pending, stalled, neglected = meta["decision_pending"], meta["stalled"], meta["neglected"]
    tracked = [p for p in (snap.get("projects") or []) if p.get("id") not in self_ids]

    # ---- header ----
    L.append("# " + cat.t("brief.header.title", date=as_of.isoformat(),
                          weekday=cat.t(WEEKDAY_KEYS[as_of.weekday()]),
                          time=gen.strftime("%H:%M")))
    head = []
    prev = notes.get("prev_run")
    if prev is None:
        head.append(cat.t("brief.header.first_run"))
    elif prev.get("ok"):
        head.append(cat.t("brief.header.prev_ok", at=str(prev.get("at", ""))[:16].replace("T", " ")))
    else:
        head.append(cat.t("brief.header.prev_incomplete"))
    head.append(cat.t("brief.header.projects", count=len(tracked)))
    if decision_pending:
        head.append(cat.t("brief.header.decision_pending", count=len(decision_pending)))
    if stalled:
        head.append(cat.t("brief.header.stalled", count=len(stalled)))
    if neglected:
        head.append(cat.t("brief.header.neglected", count=len(neglected)))
    head.append(cat.t("brief.header.backlog", count=len(open_items)))
    L.append("> " + " | ".join(head))
    L.append("")

    # ---- what is new since the last run -------------------------------------
    #
    # The counts above say what is true; this says what changed, which is the
    # question a reader actually opens a daily document to ask. Without it a
    # morning with two stalled projects reads identically whether both were
    # stalled last week or one stalled overnight, and a document that reads the
    # same every day trains the person to skim it.
    #
    # One line, above everything, rather than a shorter document on quiet days.
    # A brief whose *shape* varies is a brief whose reader no longer knows where
    # to look, and the saving is a few lines they were not going to read anyway.
    # Nothing is hidden and nothing is deferred: the full brief is still under
    # this line, and this line is the permission to stop reading it.
    #
    # Against the previous run rather than the previous snapshot, for the same
    # reason `should_notify` is: `stalled` depends on the backlog, which the
    # snapshot does not carry. `read_prev_run` already treats a re-render of the
    # snapshot in hand as the same run, so re-rendering does not report the same
    # news twice.
    # Against the last run from an EARLIER DAY, not the last run. See
    # `last_run_before_today`: a second run this evening is not a day the reader
    # has already read.
    yesterday = notes.get("prev_day_run")
    if yesterday is not None:
        since = str(yesterday.get("as_of") or yesterday.get("at", ""))[:10]
        fresh = []
        # Both catalogue keys written out in full rather than built from `key`.
        # The locale guard finds keys by scanning for literals, so a computed one
        # is invisible to it -- and an invisible key is one that can be missing
        # from a translation until the day it is needed and renders as itself.
        for key, locale_key in (("neglected", "brief.whatsnew.neglected"),
                                ("stalled", "brief.whatsnew.stalled")):
            by_id = {str(p.get("id")): p for p in (meta.get(key) or [])}
            for pid in sorted(_newly(meta, yesterday, key)):
                p_ = by_id.get(pid) or {}
                fresh.append(cat.t(locale_key, name=p_.get("name") or pid))
        if fresh:
            L.append("> " + cat.t("brief.whatsnew.some", at=since,
                                  items=cat.t("sep.semicolon").join(fresh)))
        else:
            L.append("> " + cat.t("brief.whatsnew.none", at=since))
        L.append("")

    if run.get("late"):
        hrs = (run.get("lateness_minutes") or 0) // 60
        L.append("> " + cat.t("brief.banner.late", slot=run.get("planned_slot", ""), hours=hrs))
        L.append("")
    if notes.get("stale_brief_days"):
        L.append("> " + cat.t("brief.banner.stale", days=notes["stale_brief_days"]))
        L.append("")
    if len(open_items) >= limits["max_open_items_total"]:
        L.append("> " + cat.t("brief.banner.backlog_full",
                              max=limits["max_open_items_total"],
                              command="nextbrief prune"))
        L.append("")

    # ---- do these first (whole portfolio) ----
    nexts = (brief or {}).get("next_actions") or []
    if nexts:
        section(L, marks, "next_actions", cat.t("brief.section.next_actions"))
        for i, a in enumerate(nexts[:caps["max_next_actions"]], 1):
            who = a.get("who") or cat.t("brief.action.who_default")
            est = (cat.t("sep.dot") + a["estimate"]) if a.get("estimate") else ""
            tier = ""
            if a.get("automation_tier") in TIER_KEYS:
                tier = cat.t("sep.dot") + cat.t(TIER_KEYS[a["automation_tier"]])
            L.append("%d. **%s**%s%s%s%s"
                     % (i, a.get("title", "?"), est, cat.t("sep.dot"), who, tier))
            if a.get("evidence_line"):
                L.append("   " + cat.t("brief.action.evidence_line", text=a["evidence_line"]))
            if a.get("why"):
                L.append("   %s" % a["why"])
            if a.get("non_goal_flag"):
                L.append("   " + cat.t("brief.action.non_goal_flag", non_goal=a["non_goal_flag"]))
        L.append("")
    else:
        # v0: with no model in the loop, deterministic rules still answer
        # "what is most time-critical", which is the part that does not need one.
        urgent = []
        # A deadline is a fact, not a judgement: an unrated project can still be
        # the most overdue thing you own.
        for p in listed:
            if p.get("id") in self_ids:
                continue
            for d in p.get("deadlines") or []:
                days = _days_until(d)
                # "-125 days out" under a heading that reads "Tightest on time"
                # is a sentence nobody parses as "you missed this four months
                # ago". Overdue is a different fact and gets a different string.
                if d.get("overdue") or days < 0:
                    urgent.append(cat.t("brief.urgent_line.overdue",
                                        project=p.get("name", ""),
                                        label=d.get("label", ""), days=abs(days)))
                elif d.get("in_lead_window"):
                    urgent.append(cat.t("brief.urgent_line", project=p.get("name", ""),
                                        label=d.get("label", ""), days=days))
        if urgent:
            section(L, marks, "most_urgent", cat.t("brief.section.most_urgent"))
            for u in urgent[:caps["max_next_actions"]]:
                L.append("- " + u)
            L.append("")
        L.append("> " + cat.t("brief.v0_note", command="nextbrief run"))
        L.append("")

    # ---- one line per project ----
    section(L, marks, "projects", cat.t("brief.section.projects"))
    L.append("")
    L.append("| %s | %s | %s | %s |" % (cat.t("brief.table.project"), cat.t("brief.table.signal"),
                                        cat.t("brief.table.evidence"), cat.t("brief.table.next")))
    L.append("|---|---|---|---|")
    prose = {c.get("project"): c for c in ((brief or {}).get("project_lines") or [])}
    unrated_ids = {p.get("id") for p in meta.get("unjudged", [])}
    for p in listed:
        pid = p.get("id")
        if pid in self_ids:
            continue
        ev = p.get("evidence") or {}
        sig = cat.t(SIGNAL_KEYS.get(ev.get("signal"), "signal.unknown"))
        if pid in unrated_ids:
            sig = cat.t("brief.signal.unrated")
        elif pid in dec_ids:
            sig = cat.t("brief.signal.decision_pending")
        elif pid in neg_ids:
            sig = cat.t("brief.signal.neglected", days=ev.get("days_since"))
        nxt = ""
        if pid in dec_ids:
            nxt = cat.t("brief.next.decision")
        elif p.get("has_own_daily_entry"):
            # ★ A project with its own daily entry gets a count and a link, never
            #   a retelling. Two places describing the same work is how they
            #   start disagreeing.
            n = gated_text(brief, "delegated", pid)
            nxt = n if n else cat.t("brief.next.delegated",
                                    file=Path(str(p["has_own_daily_entry"])).name)
        else:
            na = [b for b in by_proj.get(pid, []) if b.get("is_next_action")]
            if na:
                nxt = "`%s` %s" % (na[0].get("id", ""), str(na[0].get("title", ""))[:40])
            elif prose.get(pid, {}).get("next"):
                nxt = prose[pid]["next"]
            elif pid in stall_ids:
                nxt = cat.t("brief.next.stalled")
        line = "| %s | %s | %s | %s |" % (md_cell(p.get("name", "")), md_cell(sig),
                                          md_cell(evidence_phrase(p, cat)), md_cell(nxt))
        # Cutting between a backslash and what it escapes leaves a dangling
        # backslash, which Markdown reads as a hard line break -- the same
        # corruption the escaping above exists to prevent.
        L.append(line[:caps["per_project_line_chars"] + 60].rstrip("\\"))
    L.append("")

    # ---- awaiting a decision ----
    if decision_pending:
        section(L, marks, "decision_pending", cat.t("brief.section.decision_pending"))
        for p in decision_pending[:caps["max_decision_pending"]]:
            od = p.get("open_decision") or {}
            L.append("- " + cat.t("brief.decision.line", name=p.get("name", ""),
                                  question=od.get("question", "")))
            if od.get("evidence_needed"):
                L.append("  - " + cat.t("brief.decision.evidence_needed",
                                        text=od["evidence_needed"]))
            if od.get("evidence_available") and od.get("evidence_where"):
                L.append("  - " + cat.t("brief.decision.evidence_available",
                                        text=od["evidence_where"]))
            if od.get("why_not_answered"):
                L.append("  - " + cat.t("brief.decision.why_not_answered",
                                        text=od["why_not_answered"]))
            # Rendered here as well as in BRIEF.html: the same gated text has to
            # appear in both artifacts, or "the two cannot drift apart" is a
            # claim about only the parts we happened to check.
            note = gated_text(brief, "decision_notes", p.get("id"))
            if note:
                L.append("  - " + cat.t("brief.decision.note", text=note))
        L.append("")

    # ---- stalled ----
    if stalled:
        section(L, marks, "stalled", cat.t("brief.section.stalled"))
        for p in stalled:
            g = (p.get("git") or [{}])[0]
            if g.get("uncommitted"):
                # A dormant project reaches this section by a different route --
                # uncommitted work in a parked repository is worth saying, and it
                # is the one thing that overrides "parked". So it must not be
                # told to park itself: the generic remedy ends "or set its
                # status to frozen", which for this project is
                # advice to do what it has already done, and does not work.
                key = ("brief.stalled.uncommitted_dormant"
                       if status_of(p) == "frozen" else "brief.stalled.uncommitted")
                L.append("- " + cat.t(key, name=p.get("name", ""),
                                      count=g["uncommitted"]))
            else:
                dep = p.get("external_dependency")
                extra = cat.t("brief.stalled.waiting_on", dep=dep) if dep else ""
                L.append("- " + cat.t("brief.stalled.generic", name=p.get("name", ""), extra=extra))
        L.append("")

    # ---- waiting on other people ----
    waits = [b for b in open_items if b.get("blocked_by") in ("external-party", "approval")]
    ext = [p for p in (snap.get("projects") or [])
           if p.get("external_dependency") and p.get("id") not in stall_ids
           and p.get("id") not in self_ids]
    if waits or ext:
        section(L, marks, "waiting", cat.t("brief.section.waiting"))
        n = 0
        for b in waits[:caps["max_waiting_for"]]:
            L.append("- " + cat.t("brief.waiting.item", id=b.get("id", ""), title=b.get("title", ""),
                                  who=b.get("waiting_on", b.get("blocked_by"))))
            n += 1
        for p in ext:
            if n >= caps["max_waiting_for"]:
                break
            L.append("- " + cat.t("brief.waiting.project", name=p.get("name", ""),
                                  dep=p["external_dependency"]))
            n += 1
        L.append("")

    # ---- agent queue ----
    agentq = [b for b in open_items if b.get("blocked_by") == "agent"
              or (b.get("automation") or {}).get("tier") == "hook"]
    if agentq:
        section(L, marks, "agent_queue", cat.t("brief.section.agent_queue"))
        for b in agentq[:caps["max_agent_queue"]]:
            a = b.get("automation") or {}
            human = _first_clause(a.get("what_needs_human"))
            tail = cat.t("brief.agent.human_left", text=human[:60]) if human else ""
            L.append("- " + cat.t("brief.agent.item", id=b.get("id", ""),
                                  title=b.get("title", ""), human=tail))
        L.append("")

    # ---- Friday: automation review ----
    if as_of.weekday() == 4 and open_items:
        tiers = {"hook": 0, "skill": 0, "explore": 0, "unknown": 0}
        human_perm = 0
        markers = [m for m in cat.t("automation.human_permanent_markers").split(",") if m]
        for b in open_items:
            auto = b.get("automation") or {}
            t = auto.get("tier") or "unknown"
            tiers[t] = tiers.get(t, 0) + 1
            wn = str(auto.get("what_needs_human") or "").lower()
            if any(m.strip().lower() in wn for m in markers):
                human_perm += 1
        section(L, marks, "automation_review", cat.t("brief.section.automation_review"))
        L.append("> " + cat.t("brief.automation.summary", total=len(open_items),
                              hook=tiers.get("hook", 0), skill=tiers.get("skill", 0),
                              explore=tiers.get("explore", 0), unknown=tiers.get("unknown", 0),
                              human_perm=human_perm))
        L.append("")

    # ---- reminders ----
    # The order is the priority: illegal writes / dropped claims > structural
    # risk (a disabled gate, no version control) > contradictions > stale docs >
    # tools. Stale documents are there every single day and would otherwise
    # crowd everything else out, so they are deduplicated down to three plus a
    # count.
    sep = cat.t("sep.list")
    rem: List[str] = []
    if notes.get("dropped_claims"):
        rem.append(cat.t("reminder.dropped_claims", count=notes["dropped_claims"],
                         path="log/rejected.jsonl"))
    if notes.get("reverted_fields"):
        rem.append(cat.t("reminder.reverted_fields", count=notes["reverted_fields"],
                         path="log/rejected.jsonl"))
    gate = notes.get("write_gate")
    if gate == "no_repo":
        key = ("reminder.write_gate_no_git" if notes.get("write_gate_detail") == "git-missing"
               else "reminder.write_gate_no_repo")
        rem.append(cat.t(key, path="log/rejected.jsonl"))
    elif gate == "no_commits":
        rem.append(cat.t("reminder.write_gate_no_commits"))
    if not meta["bootstrapped"]:
        # `nextbrief bootstrap` has never existed; it exits 2. This is the only
        # actionable instruction the very first brief gives, so naming a command
        # that is not there is the worst possible place to be wrong.
        rem.append(cat.t("reminder.empty_backlog", command="nextbrief run"))
    # `git_present` settles it where the two disagree. This line ends "a bad
    # delete is unrecoverable", which is simply untrue of a directory that turns
    # out to hold a repository -- and it fired every morning, because the other
    # half of the condition is "somebody edited a file this week".
    #
    # Read as "not observed present" rather than "declared none", so a snapshot
    # written before the check existed still behaves: `git_present` is absent
    # there, which is falsy, which is the old behaviour exactly.
    # `has_git` as well as `git_present`, because they cover different holes. A
    # registry entry that omits `git` entirely is defaulted to the string "none"
    # by sense, yet the git pass still runs for it (`None != "none"`) -- so the
    # engine reads its history, `git_present` is never computed, and the old
    # condition told the owner of a perfectly ordinary repository that a bad
    # delete would be unrecoverable. Nothing this claim is about is true when the
    # engine has resolved a repository by either route.
    nogit = [p.get("name", "") for p in (snap.get("projects") or [])
             if p.get("git_declared") == "none"
             and not p.get("git_present") and not p.get("has_git")
             and ((p.get("fs") or {}).get("changed") or {}).get("7", 0) > 0]
    if nogit:
        rem.append(cat.t("reminder.no_git", projects=sep.join(nogit)))
    for c in notes.get("conflicts", []):
        rem.append(c)
    stale_docs: Dict[str, int] = {}
    for p in (snap.get("projects") or []):
        for d in p.get("status_docs") or []:
            if d.get("stale") and d.get("declared_age_days"):
                # Deduplicated: one document may be referenced by several projects.
                stale_docs[d["path"]] = d["declared_age_days"]
    if stale_docs:
        # Sorted by age *and* path: ties must not depend on dict ordering, or the
        # brief would differ between two runs over identical inputs.
        top = sorted(stale_docs.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        rem.append(cat.t("reminder.stale_docs", count=len(stale_docs),
                         items=sep.join(cat.t("reminder.stale_doc_item", path=k, days=v)
                                        for k, v in top)))
    if snap.get("tool_missing"):
        # Entries are {"tool", "why"} dicts, not strings. Joining them directly
        # raised TypeError, so the one path that exists to keep a run alive when
        # an optional tool is absent was the path that killed it -- and only on
        # machines without scc or ccusage, never on a developer's own.
        rem.append(cat.t(
            "reminder.tool_missing",
            items=cat.t("sep.semicolon").join(
                cat.t("reminder.tool_missing_item",
                      tool=str(t.get("tool", "?")), why=str(t.get("why", "")))
                for t in sorted(snap["tool_missing"], key=lambda t: str(t.get("tool", "")))
            )))
    if snap.get("parse_failed"):
        rem.append(cat.t("reminder.parse_failed", count=len(snap["parse_failed"])))
    if notes.get("deferred"):
        rem.append(cat.t("reminder.deferred", count=notes["deferred"],
                         path="log/deferred.jsonl"))
    # The unanswered count is a reminder, so it is added before the reminders
    # block renders -- not after, which is where it used to be.
    own = self_project_ids(snap, reg)
    asking = question_targets(snap, own, limit=caps.get("max_questions", 2))
    if asking:
        rem.append(cat.t("review.pending", n=pending_count(snap, own)))

    # Both absences get said out loud rather than defaulted. Neither is a
    # verdict about the project; each is a statement about what the engine was
    # never told, which is the only thing it can honestly report.
    unjudged = [p for p in meta.get("unjudged", []) if p.get("id") not in own]
    if unjudged:
        rem.append(cat.t("reminder.unrated", count=len(unjudged),
                         names=", ".join(str(p.get("id")) for p in unjudged[:4])))

    notes["reminders"] = rem   # the HTML reuses this list, so the two cannot drift

    if rem:
        section(L, marks, "reminders", cat.t("brief.section.reminders"))
        for r in rem[:8]:
            L.append("- " + r)
        L.append("")

    # ---- the one thing only a person can answer -------------------------
    #
    # LAST on purpose, and cheapest in `KEEP` for the same reason. Position no
    # longer decides what a crowded brief loses -- the table does -- but the two
    # agree here. When this section was placed above the reminders it pushed them
    # off the page --
    # and the reminders are the brief's only warnings: which projects have no git
    # and are unrecoverable if deleted, which status documents contradict each
    # other. A question that waits a night costs nothing. A warning that
    # disappears is the failure this engine exists to prevent.
    #
    # Not a claim, so gate 1 has nothing to check: every word is either a fixed
    # string from the catalogue or a fact about the registry's own contents,
    # which the reader can verify by opening it.
    if asking:
        section(L, marks, "questions", cat.t("brief.section.questions"))
        for p_ in asking:
            L.append("- **%s** — %s" % (p_.get("name") or p_.get("id"),
                                        cat.t(QUESTIONS[0].key)))
            for _value, key in QUESTIONS[0].choices:
                L.append("  - " + cat.t(key))
        L.append("")

    # ---- Gate 4: physical truncation ----
    #
    # Whole sections, cheapest first, and the footer is added afterwards rather
    # than trimmed with everything else.
    #
    # Two things were wrong with keeping a prefix of the finished document. The
    # footer went first, because it is last -- so on precisely the busiest days
    # the brief lost the line naming what generated it and stating that every
    # claim had passed the evidence gate. And the cut landed wherever the line
    # count fell, which for a portfolio of any size is inside the projects table:
    # a header, a separator, and no rows. A missing section is legible and says
    # so; a halved table is a document that appears to have run out.
    #
    # Which sections, by the `KEEP` table rather than by position. Dropping from
    # the end looked right -- the file is written in the order it should be read
    # -- and rendering a real portfolio refuted it: nineteen lines over, and what
    # went was the whole reminders section, the brief's only warnings.
    maxl = caps["brief_max_lines"]
    footer = ["---", cat.t("brief.footer", generator=GENERATOR,
                           time=gen.strftime("%Y-%m-%d %H:%M"))]
    truncated = 0
    if len(L) + len(footer) > maxl:
        # Room for the footer and for the blank line and notice that explain the
        # cut. Reserved before choosing, so the explanation can never be the
        # thing that pushes it back over.
        budget = maxl - len(footer) - 2
        bounds = [(m["start"],
                   marks[i + 1]["start"] if i + 1 < len(marks) else len(L),
                   m["keep"])
                  for i, m in enumerate(marks)]
        # Cheapest tier first; within a tier, EARLIER sections are given up
        # before later ones. That looks backwards and is not: the file is written
        # in reading order, so within one tier the section nearer the bottom is
        # the one the author put last on purpose -- and tier 4 holds
        # `next_actions` (first) and `reminders` (nearly last). Breaking the tie
        # the other way would drop the warnings and keep the actions, which is
        # the outcome the whole table exists to prevent.
        #
        # Total either way, so the result never depends on sort stability.
        doomed = set()
        kept = len(L)
        for start, end, _keep in sorted(bounds, key=lambda b: (b[2], b[0])):
            if kept <= budget:
                break
            doomed.add(start)
            kept -= end - start
        body = [line for i, line in enumerate(L)
                if not any(s <= i < e for s, e, _k in bounds if s in doomed)]
        # Even the preamble does not fit: a line cut, because a ceiling that
        # cannot be met is still a ceiling.
        if len(body) > budget:
            body = body[:max(0, budget)]
        truncated = len(L) - len(body)
        L = body
        if truncated:
            L.append("")
            L.append("> " + cat.t("brief.truncated", max=maxl, lines=truncated,
                                  path="state/snapshot.json"))
    L.extend(footer)
    meta["truncated_lines"] = truncated
    return "\n".join(L) + "\n", meta


# ---------------------------------------------------------------------------
# logs / notification
# ---------------------------------------------------------------------------

def read_runs(ws: Workspace) -> List[dict]:
    """Every run record, oldest first. Unreadable or malformed lines are skipped."""
    p = ws.log / "runs.jsonl"
    if not p.exists():
        return []
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def last_run(ws: Workspace) -> Optional[dict]:
    """The most recent run record, same stamp or not.

    Deliberately NOT `read_prev_run`, which skips records whose `at` matches the
    snapshot in hand. That skip exists so the header's "last run" line survives a
    re-render, and it is exactly wrong for the announcement bookkeeping: the
    skipped record is the one holding what was just announced, so reading through
    it made every re-render see its own announcement as unmade and deliver again
    -- and a suppressed re-render write an empty announced set over a full one.

    Notification is not part of the artifact, so nothing here owes anything to
    re-render idempotence. The question is only ever "what does the newest record
    say the reader has been told".
    """
    records = read_runs(ws)
    return records[-1] if records else None


def last_run_before_today(ws: Workspace, as_of) -> Optional[dict]:
    """The most recent run from an earlier day than `as_of`.

    "What is new" has to mean *new since you could last have read this*, and the
    unit a daily document is read in is a day. Measured against the last run
    instead, it collapses: `run` and `v0` re-sense first, and sensing mints a
    fresh `generated_at` every time, so the second invocation of an evening
    becomes its own predecessor and replaces news the reader never saw with
    "nothing new" -- permanently, because the ids are in the record by then.

    `read_prev_run`'s same-stamp skip does not help. It exists to make a
    *re-render* of one snapshot idempotent, and a second full run is a different
    snapshot with a different stamp.

    Records written before `as_of` was recorded are ignored rather than guessed
    at: a run whose date is unknown cannot be shown to be from an earlier day,
    and inventing that it was would suppress real news exactly once, silently.
    """
    best = None
    for rec in read_runs(ws):
        day = rec.get("as_of")
        if not day or str(day) >= str(as_of):
            continue
        if best is None or str(day) >= str(best.get("as_of")):
            best = rec
    return best


def announced_after(prev_run, meta, key: str, delivered: bool) -> List[str]:
    """What the reader will have been told about, once this run is over.

    One carried-forward set per verdict, and the update rule is the whole design:

    * a notification got through -- everything currently in the verdict set has
      now been announced;
    * it did not -- the set is unchanged, **intersected with what is still true**,
      so a project that has recovered stops counting as announced.

    Both halves are load-bearing, and each fixes a defect the other one causes.

    Without the delivery condition, a run told `--no-notify` or whose sink
    returned False -- which `sinks/none.py` always does, and any broken transport
    does -- consumed the edge anyway, and the project it would have told you
    about was never mentioned again. `cmd_open` re-renders whenever BRIEF.html is
    absent, which is enough to spend the evening's notification before the
    scheduled run reaches it.

    Without the intersection, the baseline freezes at the last delivery, so a
    project that is announced, recovers, and relapses is never announced again --
    which is worse than the noisy state test all of this replaced, because it
    fails silently. That was an earlier attempt at this function and it shipped
    for exactly one commit.

    Retrying into a broken sink is deliberate. Nothing reaches the reader while
    the transport is down, so the repetition costs nobody anything, and the first
    run after it is fixed says what has been true all along.
    """
    now = {str(i) for i in (meta.get(key + "_ids") or ())}
    if delivered:
        return sorted(now)
    return sorted(_announced(prev_run, key) & now)


def _announced(prev_run, key: str):
    """The recorded announced set, tolerating anything a hand-edited log holds."""
    recorded = (prev_run or {}).get("announced_" + key + "_ids")
    if not isinstance(recorded, (list, tuple)):
        return set()
    return {str(i) for i in recorded}


def read_prev_run(ws: Workspace, current_at=None) -> Optional[dict]:
    """The last recorded run that is not a re-render of the snapshot in hand.

    Records are stamped with the snapshot's ``generated_at``, so re-rendering the
    same snapshot produces a record with the same stamp -- and skipping those is
    what makes the "last run" line in the header identical across re-renders. A
    second render of unchanged inputs is the same run, not a new one.
    """
    p = ws.log / "runs.jsonl"
    if not p.exists():
        return None
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if current_at is not None and rec.get("at") == current_at:
            continue
        return rec
    return None


def write_day_log(ws: Workspace, as_of, snap, prev_snap, meta, notes, cat: Catalog, dry_run=False):
    """★ Append, never rewrite. A second run on the same day adds `## run N`."""
    path = ws.log / ("%s.md" % as_of.isoformat())
    run_n = 1
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        run_n = len(re.findall(r"^## run \d+", existing, re.M)) + 1

    run = snap.get("run") or {}
    gen = dt.datetime.fromisoformat(run["generated_at"])
    out: List[str] = []
    if run_n == 1:
        out.append("# %s" % as_of.isoformat())
        out.append("")
    out.append("## " + cat.t("log.run_heading", n=run_n, planned=run.get("planned_slot", ""),
                             actual=gen.strftime("%H:%M")))
    out.append("")

    if prev_snap:
        prevp = {p.get("id"): p for p in prev_snap.get("projects", [])}
        deltas = []
        for p in snap.get("projects") or []:
            q = prevp.get(p.get("id"))
            if not q:
                deltas.append(cat.t("log.new_project", name=p.get("name", "")))
                continue
            bits = []
            d7 = ((p.get("fs") or {}).get("changed") or {}).get("7", 0) - \
                 ((q.get("fs") or {}).get("changed") or {}).get("7", 0)
            if d7:
                bits.append(cat.t("log.delta.files_7d", delta="%+d" % d7))
            if p.get("git") and q.get("git"):
                a = (p["git"][0].get("commits_since") or {}).get("30") or 0
                b = (q["git"][0].get("commits_since") or {}).get("30") or 0
                if a != b:
                    bits.append(cat.t("log.delta.commits_30d", delta="%+d" % (a - b)))
                if (p["git"][0].get("uncommitted") or 0) != (q["git"][0].get("uncommitted") or 0):
                    bits.append(cat.t("log.delta.uncommitted",
                                      old=q["git"][0].get("uncommitted") or 0,
                                      new=p["git"][0].get("uncommitted") or 0))
            ps, qs = (p.get("evidence") or {}).get("signal"), (q.get("evidence") or {}).get("signal")
            if ps != qs:
                bits.append(cat.t("log.delta.signal", old=qs, new=ps))
            if bits:
                deltas.append(cat.t("log.delta.line", name=p.get("name", ""),
                                    bits=cat.t("sep.list").join(bits)))
        out.append("### " + cat.t("log.section.changes"))
        if deltas:
            for d in deltas:
                out.append("- " + d)
        else:
            out.append("- " + cat.t("log.no_changes"))
        out.append("")

    out.append("### " + cat.t("log.section.actions"))
    out.append("- " + cat.t("log.counts", open=meta["open_items"],
                            decisions=len(meta["decision_pending"]),
                            stalled=len(meta["stalled"]), neglected=len(meta["neglected"])))
    if notes.get("dropped_claims"):
        out.append("- " + cat.t("log.dropped", count=notes["dropped_claims"]))
    if notes.get("reverted_fields"):
        out.append("- " + cat.t("log.reverted", count=notes["reverted_fields"]))
    if notes.get("deferred"):
        out.append("- " + cat.t("log.deferred", count=notes["deferred"],
                                path="log/deferred.jsonl"))
    if meta.get("truncated_lines"):
        out.append("- " + cat.t("log.truncated", lines=meta["truncated_lines"]))
    out.append("")

    text = ("" if run_n == 1 else "\n") + "\n".join(out)
    if dry_run:
        return
    if not ws.contains(path):
        return
    append_text(ws, path, text)


def _newly(meta, prev_run, key: str, field: Optional[str] = None):
    """Ids in this run's verdict set that were not in the recorded one.

    `field` names which record to compare against, because two callers ask two
    different questions and guessing between them gets one of them wrong. The
    brief's what-is-new line asks "what changed since a day the reader could have
    read", which is that day's raw verdict set. The notifier asks "what has the
    reader been TOLD", which is the announced set -- see `announced_after`.


    A missing record, or one written before these sets were recorded, reads as
    "nothing was known" -- so everything currently in the set looks new and the
    first run after an upgrade notifies once, then goes quiet. The alternative,
    treating absence as "already reported", would swallow a genuinely new
    neglected project on exactly the run where nobody has a reason to expect a
    gap.

    Re-arming falls out of the set difference and needs no timer. A timer
    re-fires about a project you already know about; this fires only when
    something is new again, which is the only thing worth interrupting for.
    """
    now = {str(i) for i in (meta.get(key + "_ids") or ())}
    # `runs.jsonl` is a plain text log a person can edit, and a scalar there --
    # `"neglected_ids": 3` -- iterates as a TypeError out of `render_brief`,
    # costing the whole brief for a stray keystroke in a file the engine only
    # reads. Anything that is not a list or tuple reads as "nothing recorded",
    # which is the same fallback a missing key gets.
    recorded = (prev_run or {}).get(field or (key + "_ids"))
    if not isinstance(recorded, (list, tuple)):
        recorded = ()
    before = {str(i) for i in recorded}
    return now - before


def announced_this_snapshot(ws: Workspace, stamp) -> bool:
    """Has any run of THIS snapshot already delivered a notification?

    Any, not the last one. Checking only the newest record made the guard
    alternate: run 1 delivered, run 2 saw a delivery and went quiet, run 3 saw
    run 2's `notified: false` and delivered again. "One snapshot, one
    notification" is a property of the whole run of records sharing that stamp.
    """
    if not stamp:
        return False
    return any(r.get("at") == stamp and r.get("notified") for r in read_runs(ws))


def should_notify(cfg, snap, prev_snap, meta, notes, prev_run=None,
                  already_announced=False):
    """The quiet rule: a system that tells you punctually every day that nothing
    happened gets muted in week three. Reasons are English on purpose -- they are
    operator diagnostics in runs.jsonl, not part of the brief.

    `neglect` and `new_stalled` used to be *state* tests -- "is anything
    neglected" -- while `change` and `deadline_lead` directly above them diff
    against the previous snapshot. So a project you already knew was neglected
    interrupted you again every morning until you fixed it: the precise
    behaviour this docstring calls fatal, four lines under the docstring.

    They are edge tests now, against the previous run record rather than the
    previous snapshot. `stalled` is why: it depends on the backlog, which the
    snapshot does not carry, so yesterday's verdict cannot be recomputed from
    `prev_snap` and has to have been written down. `read_prev_run` already skips
    a re-render of the snapshot in hand, so rendering twice is one run and does
    not re-fire.

    This reads state, and that is allowed here and nowhere near the artifacts: it
    decides whether to *send* a notification, never what BRIEF.md contains.
    """
    want = set(((cfg or {}).get("notify") or {}).get("only_if") or [])

    # One snapshot, one delivered notification.
    #
    # This is the guard the edge work should have started with. Making `neglect`
    # and `new_stalled` re-render-safe left `change` and `deadline_lead` -- which
    # diff the snapshot against its predecessor, neither of which a re-render
    # touches -- delivering the same news on every render of one snapshot,
    # unbounded. `change` is the first entry of the shipped default, so the
    # branch that fires most often was the one still doing it.
    #
    # Keyed on delivery, not on the record existing, for the same reason
    # `announced_after` is: a first render told `--no-notify` said nothing to
    # anybody, and the real render after it still has news to deliver.
    if already_announced:
        return False, "already announced for this snapshot"

    if prev_snap is None:
        return True, "first run"
    prevp = {p.get("id"): p for p in prev_snap.get("projects", [])}
    if "change" in want:
        for p in snap.get("projects") or []:
            q = prevp.get(p.get("id"))
            if not q:
                return True, "new project registered"
            if (p.get("evidence") or {}).get("best_date") != (q.get("evidence") or {}).get("best_date"):
                return True, "a project changed"
    if "deadline_lead" in want:
        for p in snap.get("projects") or []:
            q = prevp.get(p.get("id")) or {}
            old = {d.get("date"): d for d in (q.get("deadlines") or [])}
            for d in p.get("deadlines") or []:
                if d.get("in_lead_window") and not (old.get(d.get("date"), {}).get("in_lead_window")):
                    return True, "deadline entered its lead window"
    if "neglect" in want and _newly(meta, prev_run, "neglected",
                                    "announced_neglected_ids"):
        return True, "a project is neglected"
    if "new_stalled" in want and _newly(meta, prev_run, "stalled",
                                        "announced_stalled_ids"):
        return True, "a project is stalled"
    # Named in `only_if` like every other reason. Ungated, it ignored the one
    # setting that means "never interrupt me": `only_if: []` delivered anyway,
    # every run, for as long as `brief.json` held a claim the gate drops -- and
    # the body never mentions the drop, so the reader got a byte-identical
    # notification daily about something the brief already states as a reminder.
    if "claims_dropped" in want and (notes.get("dropped_claims")
                                     or notes.get("reverted_fields")):
        return True, "claims were dropped or fields reverted"
    return False, "nothing changed; staying quiet"


def notify_body(meta, brief_obj, cat: Catalog) -> str:
    nexts = (brief_obj or {}).get("next_actions") or []
    if nexts:
        body = str(nexts[0].get("title", ""))[:80]
    elif meta["ranked"]:
        body = cat.t("notify.most_urgent", name=meta["ranked"][0].get("name", ""))
    else:
        body = cat.t("notify.updated")
    tail = []
    if meta["decision_pending"]:
        tail.append(cat.t("brief.header.decision_pending", count=len(meta["decision_pending"])))
    if meta["stalled"]:
        tail.append(cat.t("brief.header.stalled", count=len(meta["stalled"])))
    if tail:
        body += cat.t("sep.dot") + cat.t("sep.dot").join(tail)
    return body


def _send_notification(cfg, meta, brief_obj, cat: Catalog, ws=None) -> bool:
    """Delegate to the sink layer. Wrapped because a desktop notification failing
    is never a reason for the run to fail."""
    title = ((cfg or {}).get("notify") or {}).get("title") or cat.t("notify.title")
    # Clicking the notification should land on the brief it is announcing. Sinks
    # that cannot attach an action ignore this and still deliver the text.
    open_url = None
    if ws is not None:
        try:
            if ws.brief_html.is_file():
                open_url = ws.brief_html.resolve().as_uri()
        except (OSError, ValueError):
            open_url = None
    try:
        from .sinks import notify as sink_notify
        return bool(sink_notify(title, notify_body(meta, brief_obj, cat), cfg, open_url=open_url))
    except Exception as exc:                                   # noqa: BLE001 - fail-open
        print("notification skipped: %s" % exc, file=sys.stderr)
        return False


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="nextbrief render",
        description="stage 3 -- evidence gate, caps, and rendering (no model)",
    )
    ap.add_argument("--workspace", help="workspace directory (default: $NEXTBRIEF_WORKSPACE, "
                                        "the configured pointer, or the nearest registry.jsonc)")
    ap.add_argument("--out", help="where generated files go (default: the workspace itself)")
    ap.add_argument("--locale", help="output locale, e.g. en or zh")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="print BRIEF.md, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="exit 3 if BRIEF.md or BRIEF.html would change; write nothing")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    started = dt.datetime.now()

    try:
        ws = resolve_workspace(args.workspace, out=args.out)
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        cfg = load_jsonc(ws.config_path) if ws.config_path.exists() else {}
        reg = load_jsonc(ws.registry_path)
    except JSONCError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    # Precedence: flag, then environment, then config, then the default. The
    # environment has to outrank config because that is how the CLI forwards a
    # global --locale to this stage; with config first, `nextbrief --locale zh`
    # was silently ignored and only editing config.jsonc had any effect.
    cat = load_catalog(
        args.locale or os.environ.get("NEXTBRIEF_LOCALE") or cfg.get("locale")
    )

    if not ws.snapshot.exists():
        print("no snapshot at %s -- run `nextbrief sense` first" % ws.snapshot, file=sys.stderr)
        return 2
    try:
        snap = json.loads(ws.snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("cannot read %s: %s" % (ws.snapshot, exc), file=sys.stderr)
        return 2

    run = snap.get("run") or {}
    try:
        dt.datetime.fromisoformat(run["generated_at"])
        dt.date.fromisoformat(run["as_of_date"])
    except (KeyError, TypeError, ValueError):
        # Everything downstream is dated from these two fields; a snapshot
        # without them is not a snapshot we can render honestly.
        print("%s has no usable run.generated_at / run.as_of_date -- re-run `nextbrief sense`"
              % ws.snapshot, file=sys.stderr)
        return 2

    prev_snap = None
    if ws.snapshot_prev.exists():
        try:
            prev_snap = json.loads(ws.snapshot_prev.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev_snap = None

    brief = None
    if ws.brief_json.exists():
        try:
            brief = json.loads(ws.brief_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # fail-open: a broken interpretation layer degrades to v0, it does
            # not cost you the deterministic half of the brief.
            print("%s did not parse (%s) -- falling back to v0" % (ws.brief_json, exc),
                  file=sys.stderr)

    # ★ One stamp for the whole run, taken from the snapshot rather than the
    #   clock. Everything downstream -- run records, rejected entries, the "last
    #   run" line -- is derived from it, which is what makes a re-render of the
    #   same snapshot produce byte-identical output.
    stamp = run["generated_at"]

    rejected: List[dict] = []
    notes: Dict[str, Any] = {"prev_run": read_prev_run(ws, stamp), "conflicts": [],
                             "prev_day_run": last_run_before_today(ws, run["as_of_date"])}

    backlog = load_backlog(ws, rejected)
    # One flag for "this invocation may touch the workspace", set once and read
    # everywhere, because the two dry paths return from different places and
    # anything that writes above the earlier return escapes both of them.
    writing = not (args.dry_run or args.check)
    # `--check` counts as dry: the gate reverts out-of-bounds backlog edits, and
    # a command whose job is to answer a question must not change the answer.
    gate = enforce_write_permissions(ws, backlog, rejected, not writing)
    notes["reverted_fields"] = gate.reverted
    notes["write_gate"] = gate.state
    notes["write_gate_detail"] = gate.detail

    # ---- gates 1 + 2 ----
    index = dict(snap.get("evidence_index") or {})
    # Backlog entries are legitimate things to cite -- "this is already on the
    # list" is a checkable fact. The sensing stage does not load the backlog, so
    # we add them here rather than let the evidence gate kill honest claims.
    # Both spellings are indexed: the path relative to the workspace and the one
    # relative to the projects root, which is how the sensing stage names files.
    for b in backlog:
        rel = ws.backlog.name + "/" + b["_file"]
        for src in (rel, ws.root.name + "/" + rel, b.get("id")):
            if src:
                index.setdefault(src, {"kinds": ["file_mtime", "human"], "value": b.get("title")})
    dropped = 0
    if brief and not isinstance(brief, dict):
        # A brief.json that parsed but is a list or a string is no more usable
        # than one that did not parse, and is handled the same way.
        print("%s is %s, not an object -- falling back to v0"
              % (ws.brief_json, type(brief).__name__), file=sys.stderr)
        brief = None
    if brief:
        projects = {p.get("id"): p for p in (snap.get("projects") or [])}
        caps = caps_of(cfg)
        capmap = {"next_actions": caps["max_next_actions"],
                  "agent_queue": caps["max_agent_queue"],
                  "waiting_for": caps["max_waiting_for"]}
        for key in ("next_actions", "project_lines", "agent_queue", "waiting_for"):
            kept = []
            claims = brief.get(key) or []
            if not isinstance(claims, list):
                rejected.append({"kind": "malformed_section", "where": key,
                                 "why": "%s must be an array, got %s"
                                        % (key, type(claims).__name__)})
                claims = []
            for claim in claims:
                if not check_evidence(claim, index, cfg, rejected, key, cat):
                    dropped += 1
                    continue
                ng = (projects.get(claim.get("project")) or {}).get("non_goals")
                flag = non_goal_flag(
                    " ".join(str(claim.get(k, "")) for k in ("title", "text", "why")), ng)
                if flag:
                    claim["non_goal_flag"] = flag
                kept.append(claim)
            # ---- gate 4: caps ----
            if key in capmap and len(kept) > capmap[key]:
                for extra in kept[capmap[key]:]:
                    # Only on a run that is actually writing. This append sits
                    # far above the `--check` and `--dry-run` returns, so both of
                    # them were growing a log in a workspace they had promised
                    # not to touch -- `--dry-run` since it was written, and
                    # `--check` under a docstring that says so in as many words.
                    # `test_dry_run_writes_nothing` did not catch it because it
                    # asserts on runs.jsonl and BRIEF.md, not on this file.
                    if writing:
                        append_jsonl(ws, ws.log / "deferred.jsonl",
                                     {"at": stamp, "section": key, "item": extra,
                                      "why": "over caps.%s" % key})
                notes["deferred"] = notes.get("deferred", 0) + len(kept) - capmap[key]
                kept = kept[:capmap[key]]
            brief[key] = kept
        # The maps keyed by project id go through the same gate. They used to be
        # the only model text in the brief that did not.
        dropped += gate_maps(brief, index, cfg, rejected, cat)
    notes["dropped_claims"] = dropped

    # Conflicts the registry has already adjudicated -- stated once here so the
    # model does not relitigate them every single day.
    for p in (snap.get("projects") or []):
        for c in p.get("conflicts") or []:
            notes["conflicts"].append(cat.t(
                "reminder.conflict", doc=c.get("doc", ""), other=c.get("contradicts", ""),
                about=c.get("about", ""), winner=c.get("authority_wins", "")))

    # How stale the standing brief is, measured against the previous *run* rather
    # than BRIEF.md's mtime: our own write moves that mtime, so using it would
    # make the banner appear on one run and vanish on the next over identical
    # inputs.
    prev = notes.get("prev_run") or {}
    if prev.get("at"):
        try:
            age = (dt.date.fromisoformat(str(snap["run"]["as_of_date"]))
                   - dt.date.fromisoformat(str(prev["at"])[:10])).days
            if age >= 2:
                notes["stale_brief_days"] = age
        except ValueError:
            pass

    meta = classify(snap, backlog, cfg, reg, ws)
    text, meta = render_brief(snap, brief, backlog, cfg, reg, cat, notes, meta)

    if args.check:
        # Stage 3's half of the scheduling contract. `sense --check` compares the
        # snapshot and the digest; nothing compared the artifacts a person
        # actually reads, so `nextbrief check || nextbrief run` reported "current"
        # for a workspace whose BRIEF.md was arbitrarily old -- and then did not
        # re-run, which is the one outcome the command exists to prevent.
        #
        # A byte comparison is meaningful here for the same reason a re-render is
        # idempotent: every date in the output is derived from
        # `snapshot.run.generated_at`, never from the clock.
        #
        # Writes nothing, including the log line and the run record, because a
        # check that mutates the thing it is checking is not a check. The write
        # gate above already ran in dry-run mode for the same reason.
        # Both artifacts. BRIEF.html is what `nextbrief open` shows, and its
        # write is fail-open -- one failed HTML render leaves a stale file that a
        # markdown-only check calls current forever, and `cmd_open` re-renders
        # only when the file is absent, never when it is wrong.
        wanted = [(ws.brief_md, text)]
        html_compared = False
        try:
            from . import html as html_mod
            wanted.append((ws.brief_html,
                           html_mod.render_html(snap, brief, backlog, cfg, reg,
                                                cat, notes, meta)))
            html_compared = True
        except Exception as exc:                               # noqa: BLE001 - fail-open
            # A broken HTML renderer must not raise out of a command whose
            # contract is an exit code. But it must not be reported as agreement
            # either: the write path is fail-open in the same way, so the state
            # that produces a stale BRIEF.html is exactly the state where this
            # branch is taken, and answering "current" there is the hole this
            # comparison was added to close.
            print("render: BRIEF.html could not be rendered for comparison: %s"
                  % exc, file=sys.stderr)
            return EXIT_STALE
        for path, expected in wanted:
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                print("render: %s does not exist yet" % path, file=sys.stderr)
                return EXIT_STALE
            except ValueError:
                # UnicodeDecodeError is a ValueError. A file that cannot be
                # decoded certainly does not match, and this command's contract
                # is an exit code, not a traceback.
                print("render: %s cannot be read back; treating it as out of date"
                      % path, file=sys.stderr)
                return EXIT_STALE
            if current != expected:
                print("render: %s is out of date" % path, file=sys.stderr)
                return EXIT_STALE
        print("render: %s and %s are current"
              % (ws.brief_md.name, ws.brief_html.name)
              if html_compared else "render: %s is current" % ws.brief_md.name)
        return 0

    if args.dry_run:
        sys.stdout.write(text)
        for r in rejected:
            print("REJECTED: " + json.dumps(r, ensure_ascii=False), file=sys.stderr)
        return 0

    ws.ensure_dirs()
    write_text(ws, ws.brief_md, text)

    # ★ Computed once, rendered twice. The HTML re-decides nothing; it receives
    #   the same data that already went through all four gates.
    try:
        from . import html as html_mod
        write_text(ws, ws.brief_html,
                   html_mod.render_html(snap, brief, backlog, cfg, reg, cat, notes, meta))
    except Exception as exc:                                   # noqa: BLE001 - fail-open
        print("BRIEF.html failed to render (BRIEF.md unaffected): %s" % exc, file=sys.stderr)

    for r in rejected:
        r["at"] = stamp
        append_jsonl(ws, ws.log / "rejected.jsonl", r)

    as_of = dt.date.fromisoformat(snap["run"]["as_of_date"])
    write_day_log(ws, as_of, snap, prev_snap, meta, notes, cat, args.dry_run)

    # The record `notes` already holds, not a second read of the same file. Two
    # reads are two chances to disagree about which run was the last one.
    # The newest record's ANNOUNCED set -- what the reader has actually been told,
    # which `announced_after` only advances on delivery and shrinks as projects
    # recover. `last_run`, not `notes["prev_run"]`: the latter skips a record
    # written under this same snapshot stamp, and that record is the one holding
    # the announcement.
    latest = last_run(ws)
    do_notify, why = should_notify(
        cfg, snap, prev_snap, meta, notes, prev_run=latest,
        already_announced=announced_this_snapshot(ws, stamp))
    notified = False
    if do_notify and not args.no_notify:
        notified = _send_notification(cfg, meta, brief, cat, ws)

    # ★ Success sentinel. Never trust a green check: a scheduler reports green
    #   when the session exited without an infrastructure error, which says
    #   nothing about whether the work happened. This line is the only reliable
    #   liveness signal, so it is written last and read by the next run.
    append_jsonl(ws, ws.log / "runs.jsonl", {
        "at": stamp,
        # The run's own date, so a later run can ask "was this an earlier day?"
        # without parsing `at` and hoping it is an ISO timestamp.
        "as_of": snap["run"]["as_of_date"],
        "duration_s": round((dt.datetime.now() - started).total_seconds(), 2),
        "mode": "v1" if brief else "v0",
        "locale": cat.locale,
        "projects": len(snap.get("projects") or []),
        "open_items": meta["open_items"],
        "dropped_claims": dropped,
        "reverted_fields": notes.get("reverted_fields", 0),
        "write_gate": gate.state,
        "write_gate_detail": gate.detail,
        # Reverted-zero only means "clean" next to "and everything had a
        # baseline". An entry the gate could not compare is neither clean nor
        # dirty, and saying nothing about it would report it as the former.
        "write_gate_unchecked": gate.unchecked,
        "deferred": notes.get("deferred", 0),
        "truncated_lines": meta.get("truncated_lines", 0),
        "notified": notified,
        "notify_reason": why,
        # Two records of two different things, because two callers ask two
        # different questions.
        #
        # The plain sets are what was TRUE on this run, and the brief's
        # what-is-new line diffs them against the last run from an earlier day.
        # They live here rather than being recomputed from a snapshot because
        # `stalled` depends on the backlog, which the snapshot does not carry.
        #
        # The announced sets are what the reader has been TOLD, which only
        # advances on a delivered notification -- see `announced_after`.
        #
        # Sorted, because a set's iteration order is not a thing to write into a
        # log that gets diffed.
        "neglected_ids": sorted(str(i) for i in (meta.get("neglected_ids") or ())),
        "stalled_ids": sorted(str(i) for i in (meta.get("stalled_ids") or ())),
        "announced_neglected_ids": announced_after(latest, meta, "neglected", notified),
        "announced_stalled_ids": announced_after(latest, meta, "stalled", notified),
        "ok": True,          # <- success sentinel, must be the last thing written
    })

    # Report what happened, not what the rule decided. Printing the reason alone
    # made a suppressed run read exactly like a delivered one, which is a poor
    # thing to be unclear about in the output of a build or a scripted run.
    if args.no_notify:
        notify_summary = "suppressed (--no-notify; would have been: %s)" % why
    elif notified:
        notify_summary = "sent -- %s" % why
    else:
        notify_summary = why
    print("render: %s | %d lines | %s | notify: %s"
          % (ws.brief_md, len(text.splitlines()), "v1" if brief else "v0 (no model)",
             notify_summary))
    if dropped:
        print("  %d unverifiable claim(s) dropped -> log/rejected.jsonl" % dropped)
    if notes.get("reverted_fields"):
        print("  %d illegal field write(s) reverted -> log/rejected.jsonl"
              % notes["reverted_fields"])
    if gate.state != "ran":
        print("  write-permission gate did not run (%s: %s)" % (gate.state, gate.detail),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
