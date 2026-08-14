"""The ``nextbrief`` command line.

This replaces a zsh script, and the rewrite fixed a class of bug rather than a
bug: the original was zsh-only in ways that were invisible until it ran somewhere
else (``bash -n`` rejected it outright -- ``<->``, the numeric glob used to detect
a menu selection, is not bash syntax), it shelled out to BSD-only ``sed -i ''``,
and it used macOS ``open``. Under argparse, ``webbrowser`` and
``frontmatter.rewrite_fields`` none of those portability questions exist.

What was preserved on purpose:

* ``ok`` / ``done`` / ``drop`` commit immediately, and refuse rather than report a
  success the next run would revert -- see ``_mark`` and ``_commit_human``.
* ``do`` proposes directories and never picks one for you -- see ``cmd_do``.
* Stage 2 is allowed to fail. A missing or broken model provider degrades to the
  deterministic brief instead of producing nothing.

Exit codes: ``0`` success, ``1`` failure, ``2`` usage or unresolved workspace,
``3`` from ``check`` when the output would change.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
import sys
import unicodedata
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import build_version, resources
from .annotate import (
    ANNOTATIONS_NAME,
    QUESTIONS,
    coerce_answer,
    current_answer,
    needs_annotating,
    parse_review_form,
    record_answers,
    render_review_form,
    store_answer,
)
from .frontmatter import parse_frontmatter
from .fs import (
    claim_exclusively,
    rewrite_block,
    rewrite_fields,
    write_outside_workspace,
    write_text,
)
from .i18n import Catalog, load_catalog
from .inventory import INVENTORY_NAME
from .items import (
    AC_AGENT,
    AC_DONE,
    AC_DROPPED,
    AC_OPEN,
    AC_YOU,
    CLAIM,
    CLAIM_KEYS,
    DEFERRED,
    IN_PROGRESS,
    SUMMARY_DRAFT,
    SUMMARY_HUMAN,
    SUMMARY_NONE,
    Closing,
    FutureWork,
    ac_lines,
    ac_owner,
    ac_progress,
    blank_item_text,
    claim_age_days,
    claim_lines,
    claim_of,
    days_until_due,
    id_shape,
    is_live,
    is_parked,
    new_item_text,
    next_item_id,
    parse_closing,
    record_promotion,
    slug,
    status_of,
    upsert_closing,
)
from .jsonc import JSONCError, load_jsonc
from .launch import LaunchError, build_context, tr
from .paths import ENV_OUT, ENV_WORKSPACE, Workspace, WorkspaceError, expand, resolve_workspace

__all__ = ["main", "build_parser"]

# The acceptance-criteria vocabulary and its one parser moved to `items`, because
# `sense` reads them too and `sense` may not import this module -- the dependency
# runs the other way. Bound back to the private names this file has always used:
# the functions are unchanged, only their home moved, and seven readers of the
# `~` mark depend on that being all that happened.
_ac_lines = ac_lines
_ac_owner = ac_owner
_ac_progress = ac_progress

ENV_LOCALE = "NEXTBRIEF_LOCALE"
ENV_AGENT = "NEXTBRIEF_AGENT"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

DESCRIPTION = """\
A daily brief across every project you own, where every claim is checked
against evidence before it prints.

commands:
  run          all three stages: sense -> a model reads it -> render
  v0           sense + render only, no model at all: zero tokens, nothing to invent
  sense        stage 1 only; refresh state/snapshot.json
  render       stage 3 only; re-render from the existing brief.json
  check        self-check over both stages; exit 3 if a re-run would change anything

  open         open BRIEF.html in a browser
  brief        print BRIEF.md to the terminal
  log          show the last few runs

  new <title>  open an item, with the next free id taken for you
  do <id>      open an agent session in the right directory, context already loaded
  show <id>    print one item in full
  ok <id>      confirm an item: it is real, and written the way you meant it
  done <id>    close it, and record what actually happened
  drop <id>    drop it (the file stays, and so does its git history)
  defer <id>   park it until a date; it comes back on its own
  followup     turn a closed item's future work into backlog items
  closed       what each project has finished, and what it left behind
  ls           list every open item
  prune        list items worth revisiting, with what to do about them

  probe [id…]  sample the declared external URLs -- the ONLY command that
               goes online. Run it to verify, not on a schedule.

  projects     one line per project: what is here, and how fresh it is
  context      what each project IS -- for other tools; --json to pipe it
  describe     say what a project is, in one sentence
  review       answer the questions only you can answer (multiple choice)
  init [dir]   create a workspace and get to a first brief
  permissions  print, or merge in, the rules an unattended run needs
"""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _err(msg: str) -> None:
    sys.stderr.write("%s\n" % msg)


def _opt(args: argparse.Namespace, name: str):
    """Global flags are declared with ``SUPPRESS`` so that a subparser copy cannot
    overwrite a value the main parser already captured; that makes them absent
    rather than None when unused."""
    return getattr(args, name, None)


def _os_error_line(exc: OSError) -> str:
    """One line, naming the path the OS refused. ``str(exc)`` alone gives
    "[Errno 13] Permission denied" with no clue which file that was."""
    paths = [str(p) for p in (exc.filename, getattr(exc, "filename2", None)) if p]
    detail = exc.strerror or str(exc)
    return "%s: %s" % (" -> ".join(paths), detail) if paths else detail


def _tilde(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def _load_config(ws: Workspace) -> Dict[str, Any]:
    """Config is optional here. Every CLI command that needs a value from it has a
    working default; only the pipeline stages truly require it, and they say so."""
    if not ws.config_path.is_file():
        return {}
    try:
        cfg = load_jsonc(ws.config_path)
    except JSONCError as exc:
        _err("warning: %s" % exc)
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _export_env(ws: Workspace, locale: Optional[str]) -> None:
    """Hand the resolved workspace to the stage modules through the environment.

    They resolve their own workspace via ``paths.resolve_workspace``, and the
    environment is the one channel that resolver is guaranteed to honour. Passing
    flags instead would mean this module hard-coding another module's flag names.
    """
    os.environ[ENV_WORKSPACE] = str(ws.root)
    os.environ[ENV_OUT] = str(ws.out)
    if locale:
        os.environ[ENV_LOCALE] = locale


PASSTHROUGH = ("run", "v0", "sense", "render")
_GLOBAL_FLAGS = ("--workspace", "--out", "--locale")

# Of those, the ones `init` cannot act on, by ``dest``. It is dispatched before
# any workspace is resolved -- being the command that creates one -- so these
# parse and bind to nothing. Adding a global flag above means deciding which
# list it joins: honoured, as --locale is, or refused here.
_INIT_REFUSES = ("workspace", "out")


def _stage_args(argv: Sequence[str], command: str) -> List[str]:
    """Everything typed after the subcommand, verbatim and in order, minus the
    global flags this module already consumed.

    Taken from the raw argv rather than from argparse's leftovers, because the
    leftovers lose adjacency: a stage option that takes a value (``--as-of
    2026-07-01``) comes back as an unknown flag in one list and a stray
    positional in another, and reassembling them would reorder the pair.
    """

    def is_global(token: str) -> bool:
        return token in _GLOBAL_FLAGS or any(token.startswith(f + "=") for f in _GLOBAL_FLAGS)

    tail: List[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _GLOBAL_FLAGS:
            i += 2
            continue
        if is_global(token):
            i += 1
            continue
        if token == command:
            tail = list(argv[i + 1:])
            break
        i += 1

    out: List[str] = []
    skip = False
    for token in tail:
        if skip:
            skip = False
            continue
        if token in _GLOBAL_FLAGS:
            skip = True
            continue
        if is_global(token):
            continue
        out.append(token)
    return out


def _run_sense(argv: Sequence[str]) -> int:
    # Imported at call time: `nextbrief --help` and `nextbrief init` must work even
    # if a stage module is broken, and neither should pay for importing it.
    from . import sense

    return int(sense.main(list(argv)) or 0)


def _run_render(argv: Sequence[str]) -> int:
    from . import render

    return int(render.main(list(argv)) or 0)


# ---------------------------------------------------------------------------
# backlog items
# ---------------------------------------------------------------------------


def _find_item(ws: Workspace, item_id: str, cat: Optional[Catalog]) -> Optional[Path]:
    """Locate the backlog file whose frontmatter ``id`` matches, or explain.

    ★ Two files may claim one id, and picking either of them is the worst
    available answer. ★

    This used to return the first match, which meant `done NA-0043` closed
    whichever file the directory listing reached first -- silently, with the
    other one left open and no way to tell from the output which had happened.
    It is the false-completion failure the design contract's rule 4 exists to
    prevent, arriving through the one door that rule does not watch: not an
    agent writing `done`, but the tool resolving a human's `done` onto the wrong
    object.

    So: every match, and more than one is a refusal. Nothing is read and nothing
    is written, because there is no candidate this can prefer that is not a
    guess -- and a guess here is indistinguishable from having worked.
    """
    found: List[Path] = []
    for path in sorted(ws.backlog.glob("*.md")):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm and str(fm.get("id") or "") == item_id:
            found.append(path)

    if len(found) == 1:
        return found[0]

    if found:
        _err(tr(cat, "cli.item.ambiguous",
                "{n} files claim id {id}, so there is no telling which one you "
                "meant. Nothing was read and nothing was written:",
                n=len(found), id=item_id))
        for path in found:
            _err("  " + path.name)
        _err(tr(cat, "cli.item.ambiguous_fix",
                "Give each of them an id of its own -- `nextbrief new` takes the "
                "next free one -- and run this again."))
        return None

    _err(
        tr(
            cat,
            "cli.item.not_found",
            "No item {id}. Run `nextbrief ls` to see what there is.",
            id=item_id,
        )
    )
    return None


def _git(root: Path, *args: str) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root)] + list(args),
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace").strip(),
        proc.stderr.decode("utf-8", "replace").strip(),
    )


def _baseline_gap(ws: Workspace) -> Optional[str]:
    """Why this workspace has no git baseline, ready to print, or ``None`` when it
    has one.

    A gap is not an error. The write-permission gate disables itself for exactly
    the same two reasons, so with no baseline there is also nothing that will
    revert the edit -- the command succeeds, and says what is missing.
    """
    if shutil.which("git") is None:
        return (
            "note: git is not installed, so this change has no baseline and the "
            "write-permission gate cannot run at all."
        )
    if (ws.root / ".git").exists():
        return None
    rc, top, _ = _git(ws.root, "rev-parse", "--show-toplevel")
    if rc == 0 and top:
        return None
    return (
        "note: %s is not a git repository, so this change has no baseline.\n"
        "  The write-permission gate needs one: run `git init && git add -A && "
        "git commit` here." % ws.root
    )


def _identity_problem(ws: Workspace) -> Optional[str]:
    """Why git could not commit under the user's own name, ready to print, or
    ``None`` when it can.

    Asked *before* anything is written, because a change that cannot be committed
    is a change that will be destroyed: the write-permission gate reverts any
    backlog field that differs from ``git HEAD``, so an uncommitted ``done`` is
    undone by the very next run. There is no fallback identity -- a package that
    commits under someone else's name is worse than one that refuses.
    """
    missing = []
    for key in ("user.email", "user.name"):
        rc, value, _ = _git(ws.root, "config", "--get", key)
        if rc != 0 or not value.strip():
            missing.append(key)
    if not missing:
        return None
    lines = [
        "error: git has no %s here, so this change could not be committed -- and an "
        "uncommitted change is exactly what the write-permission gate reverts on the "
        "next run. Nothing was written." % " or ".join(missing),
        "  Set an identity, then run the same command again:",
    ]
    if "user.email" in missing:
        lines.append('    git config --global user.email "you@example.com"')
    if "user.name" in missing:
        lines.append('    git config --global user.name "Your Name"')
    return "\n".join(lines)


def _commit_human(ws: Workspace, path: Path, action: str, item_id: str) -> bool:
    """Commit a human's own edit immediately. This is not bookkeeping.

    The write-permission gate diffs backlog files against ``git HEAD``. If your
    ``done`` is sitting uncommitted in the working tree, the gate cannot tell
    "the human closed this item" from "an agent quietly wrote status: done", and
    it will revert *your* action. Committing makes your edit the new baseline.

    Callers must treat ``False`` as a failed command. Reporting success for an
    edit the next run will revert is worse than reporting nothing at all.
    """
    # `git add` is a write, and the pathspec below decides what it writes. The
    # containment rule that governs every other mutation governs this one too:
    # a commit is the only way this package changes a file it did not itself
    # author, and a pathspec pointing out of the workspace would stage someone
    # else's work under a message about a backlog item.
    if not ws.contains(path):
        _err("error: refusing to commit %s: it is outside the workspace %s" % (path, ws.root))
        return False

    # Re-running `done` on an item that is already done changes nothing, and a
    # commit attempt would only produce a scary "nothing to commit" warning about
    # a file that is already the baseline.
    rc, dirty, _ = _git(ws.root, "status", "--porcelain", "--", str(path))
    if rc == 0 and not dirty:
        return True

    _git(ws.root, "add", "--", str(path))
    rc, _, err = _git(ws.root, "commit", "-q", "-m", "backlog: %s %s" % (action, item_id), "--", str(path))
    if rc != 0:
        _err(
            "error: %s was written but could not be committed: %s\n"
            "  The write-permission gate reverts uncommitted backlog edits, so commit it\n"
            "  yourself before the next run:\n"
            "    git -C %s commit -m 'backlog: %s %s' -- %s"
            % (path.name, err or "git returned %d" % rc, ws.root, action, item_id, path)
        )
        return False
    return True


def _durability_problem(ws: Workspace) -> Tuple[Optional[str], Optional[str]]:
    """``(gap, error)`` -- whether this edit can be made to stick, asked BEFORE
    anything is written or anybody is prompted.

    A gap is "there is no baseline, so nothing will revert this either"; an error
    is "git is here but cannot commit under your name", which means the next run
    would undo whatever we write. Split out of ``_mark`` so a command that asks
    questions can find out it is doomed before it asks them.
    """
    gap = _baseline_gap(ws)
    if gap is not None:
        return gap, None
    return None, _identity_problem(ws)


def _mark(
    ws: Workspace,
    cat: Optional[Catalog],
    item_id: str,
    fields: Dict[str, Any],
    action: str,
    message: str,
    body: Any = None,
) -> int:
    """Write frontmatter fields (and optionally rewrite the body), then commit.

    ``body`` is a callable taking the file's current text and returning its
    replacement. It runs inside the same write-then-commit sequence as the
    fields, so a closing record and the ``status: done`` that occasions it land
    in one commit -- two commits would mean a window in which the item is closed
    and the reason is not yet in the baseline.
    """
    path = _find_item(ws, item_id, cat)
    if path is None:
        return EXIT_FAIL

    # Order matters: check that the edit can be made durable before making it. A
    # written-but-uncommitted field is reverted by the next run, so writing first
    # and discovering the problem afterwards destroys the user's own action.
    gap, problem = _durability_problem(ws)
    if problem is not None:
        _err(problem)
        return EXIT_FAIL

    fields = dict(fields)
    fields["updated_date"] = dt.date.today().isoformat()
    # An agent's proposal has now been answered, whichever way. Left standing it
    # would be re-asked every morning forever, and a question that survives its
    # own answer trains people to ignore the section it lives in. Only written
    # when the field is actually there, so the common case does not accumulate a
    # `proposed_status: null` line on every item anyone ever touches.
    try:
        current, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        current = None
    if current and current.get("proposed_status"):
        fields["proposed_status"] = None

    # Never append a null. Clearing a field the file does not have would write
    # `deferred_when: null` onto every item anyone ever touches -- frontmatter
    # that documents nothing except which commands have been run on it.
    fields = {k: v for k, v in fields.items()
              if v is not None or (current or {}).get(k) is not None}

    try:
        if body is not None:
            edited = body(path.read_text(encoding="utf-8"))
            if edited is not None:
                write_text(ws, path, edited, skip_identical=True)
        rewrite_fields(ws, path, fields)
    except OSError as exc:
        _err("error: cannot write %s: %s" % (path, exc))
        return EXIT_FAIL

    if gap is None:
        if not _commit_human(ws, path, action, item_id):
            return EXIT_FAIL
    else:
        # Nothing will revert this edit either, so the command did succeed. Say
        # what is missing anyway: the gate is what makes the other guarantees true.
        _err(gap)
    print(message)
    return EXIT_OK


# A row is (priority, age, id, title, status, confirmed, project) -- sortable in
# that order, which is the order the table is meant to be read in.
Row = Tuple[int, int, str, str, str, bool, str]


def _all_entries(ws: Workspace) -> List[Tuple[Path, Dict[str, Any]]]:
    """Frontmatter of every readable backlog item, in filename order."""
    entries: List[Tuple[Path, Dict[str, Any]]] = []
    for path in sorted(ws.backlog.glob("*.md")):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm:
            entries.append((path, fm))
    return entries


def _duplicate_ids(ws: Workspace) -> List[Tuple[str, List[Path]]]:
    """``(id, files)`` for every id more than one backlog file claims.

    Every item, not only the live ones. A closed file still answers to its id --
    `show`, `followup` and `closed` all reach it -- so a collision between an
    open item and a done one is exactly as ambiguous as one between two open
    ones, and rather harder to notice.
    """
    seen: Dict[str, List[Path]] = {}
    for path, fm in _all_entries(ws):
        item_id = str(fm.get("id") or "").strip()
        if item_id:
            seen.setdefault(item_id, []).append(path)
    return [(i, paths) for i, paths in sorted(seen.items()) if len(paths) > 1]


def _open_entries(ws: Workspace) -> List[Tuple[Path, Dict[str, Any]]]:
    """Frontmatter of every item that is still live, in filename order.

    Through ``is_live``, so a deferred item is back the day it comes due without
    anything having been written to bring it back. That matters most for the
    command that never runs: `prune` walks this list, and an item parked until
    November must not be selected for decay in the meantime -- it is not
    forgotten, it is scheduled.
    """
    today = dt.date.today()
    return [(path, fm) for path, fm in _all_entries(ws) if is_live(fm, today)]


def _age_days(fm: Dict[str, Any], today: dt.date) -> Optional[int]:
    """Days since ``updated_date``. ``None`` when the field is missing or unparseable
    -- prune must not select an item on an age it had to invent."""
    try:
        return (today - dt.date.fromisoformat(str(fm.get("updated_date")))).days
    except (TypeError, ValueError):
        return None


def _row(path: Path, fm: Dict[str, Any], today: dt.date) -> Row:
    try:
        priority = int(fm.get("priority"))
    except (TypeError, ValueError):
        priority = 9
    return (
        priority,
        _age_days(fm, today) or 0,
        str(fm.get("id") or path.stem),
        str(fm.get("title") or ""),
        str(fm.get("status") or ""),
        fm.get("human_confirmed") is True,
        str(fm.get("project") or ""),
    )


def _open_rows(ws: Workspace) -> List[Row]:
    """Every item that is still live, as sortable tuples."""
    today = dt.date.today()
    rows = [_row(path, fm, today) for path, fm in _open_entries(ws)]
    rows.sort()
    return rows


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_sense(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    return _run_sense(args.extra)


def cmd_render(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    return _run_render(args.extra)


def cmd_check(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Exit 3 when a re-run would change anything a person reads.

    Both deterministic stages, in order. It used to be `sense --check` alone,
    which compared the snapshot and the digest -- and so reported "current" for a
    workspace whose BRIEF.md was arbitrarily old, or absent altogether. A
    scheduler running `check || run` therefore never re-ran, which is the one
    outcome the exit code exists to prevent.

    Sense first, and short-circuited: if stage 1 is stale then stage 3 is stale
    by construction, and rendering against a snapshot already known to be out of
    date would answer a question nobody asked.

    The criteria warnings ride along here rather than getting a command of their
    own, because a lint nobody runs is a lint that does not exist -- and they do
    not touch the exit code. Exit 3 means "out of date", a scheduler acts on it,
    and an item worded awkwardly is not a reason to re-run the pipeline.

    Duplicate ids are the one thing here that is neither. See below.
    """
    # First, and an error rather than a warning, and exit 1 rather than exit 3.
    #
    # All three of those are the same decision. Two files claiming one id makes
    # every id-addressed command a coin toss -- `done` closes whichever the
    # directory listing reaches first -- so it outranks "your brief is a few
    # hours old", which is what everything below this line is about. A warning
    # would be read the way warnings are read, which is not at all, and the
    # thing being warned about silently closes the wrong item. And exit 3 is the
    # code a scheduler answers by running the pipeline again, which cannot fix
    # this and would produce a brief that is confidently wrong about which item
    # is which; exit 1 says "a person has to look".
    duplicates = _duplicate_ids(ws)
    if duplicates:
        for item_id, paths in duplicates:
            # "error: " in code rather than in the catalog, the way the warnings
            # below do it: a translator should be given the sentence, not the
            # severity label the rest of the tool spells one way.
            _err("error: " + tr(cat, "cli.check.duplicate_id",
                                "{n} files claim id {id}, so `nextbrief done {id}` "
                                "would close whichever one it reached first:",
                                n=len(paths), id=item_id))
            for path in paths:
                _err("  " + path.name)
        _err(tr(cat, "cli.check.duplicate_id_fix",
                "Give each of them an id of its own -- `nextbrief new` takes the "
                "next free one -- before running anything that addresses an item "
                "by id."))
        return EXIT_FAIL

    rc = _run_sense(["--check"])
    for line in (_criteria_warnings(ws, cat) + _abandoned_claims(ws, cat)
                 + _delivered_but_unticked(ws, cat)):
        _err("warning: " + line)
    if rc != EXIT_OK:
        return rc
    return _run_render(["--check", "--no-notify"])


# More than this many criteria needing a person is a design problem in the item,
# not a person who will not cooperate. Two is the author's own number, arrived at
# by counting: across three items that jammed, 20 criteria, 2 of which genuinely
# needed him.
MAX_YOURS = 2

# How many ids a warning names before it stops. A warning that prints thirty ids
# is a warning people learn to scroll past, and the count carries the size.
NAMED = 3


def _named(things: Sequence[str]) -> str:
    """At most ``NAMED`` of them, then a count. Ids, paths, whatever is being
    listed: the first few are what a reader acts on and the count is what tells
    them how big the pile is."""
    head = ", ".join(things[:NAMED])
    return head if len(things) <= NAMED else "%s (+%d)" % (head, len(things) - NAMED)


def _criteria_warnings(ws: Workspace, cat: Optional[Catalog]) -> List[str]:
    """Items whose acceptance criteria are shaped wrong, as at most two lines.

    ★ Two lines total, however big the backlog. ★

    One line per offending item is what this obviously wanted to be, and it is
    what would have killed it: every criterion written before the marker existed
    is unmarked, so on a real backlog the first run would print twenty-odd
    warnings, and a warning that fires twenty times on day one teaches people to
    stop reading warnings -- after which the one that matters goes past unread
    too. So each rule gets one line, naming a few ids and counting the rest.

    Live items only. A closed item's criteria are history: the warning would be
    true, permanent, and impossible to act on, which is the same thing as noise.
    """
    unmarked: List[str] = []
    crowded: List[Tuple[str, int]] = []
    today = dt.date.today()
    for path in sorted(ws.backlog.glob("*.md")):
        try:
            fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not fm or not is_live(fm, today):
            continue
        lines = _ac_lines(body or "")
        if not lines:
            continue
        item_id = str(fm.get("id") or path.stem)
        if any(_ac_owner(t) is None for _i, _m, t in lines):
            unmarked.append(item_id)
        # Only OPEN criteria can be crowding anyone. A `[~]` one is set aside --
        # nobody has to answer it, so counting it says an item is badly shaped on
        # the strength of criteria its author already retired. Found by UAT on the
        # real backlog: an item read 4 when 2 were open.
        #
        # The mark has to be read here for the same reason it has to be read in
        # the other six places, and this warning shipped in the same batch as the
        # mark while still discarding it.
        yours = sum(1 for _i, m, t in lines
                    if m == AC_OPEN and _ac_owner(t) == AC_YOU)
        if yours > MAX_YOURS:
            crowded.append((item_id, yours))

    out: List[str] = []
    if unmarked:
        out.append(tr(cat, "cli.check.unmarked_criteria",
                      "{n} open item(s) have criteria with no ({agent})/({you}) "
                      "marker, so `done` has to ask you about all of them: {ids}",
                      n=len(unmarked), ids=_named(unmarked),
                      agent=AC_AGENT, you=AC_YOU))
    if crowded:
        out.append(tr(cat, "cli.check.crowded_criteria",
                      "{n} open item(s) put more than {max} criteria on you, "
                      "which is a problem with the item rather than with you: "
                      "{ids}",
                      n=len(crowded), max=MAX_YOURS,
                      ids=_named(["%s (%d)" % (i, c) for i, c in crowded])))
    return out


# A claim dated today has had no chance to produce a commit, and a check that
# fires the minute you start work is a check people turn off on the first
# morning. The claim's own resolution is a day, so one day is the soonest it can
# be *late* rather than merely young.
CLAIM_QUIET_DAYS = 1


def _claim_has_commits(where: str, branch: str, since: str) -> Optional[bool]:
    """Whether ``branch`` in ``where`` has any commit since ``since``.

    ``None`` means the question could not be put -- no git, or a directory that
    is gone or was never a repository -- and the caller stays silent on it. A
    branch that does not exist is ``False`` rather than ``None``: that is git
    answering, and "the branch was never made" is the loudest form of the thing
    this is looking for, not a gap in the evidence.
    """
    if shutil.which("git") is None or not Path(where).is_dir():
        return None
    root = Path(where)
    if _git(root, "rev-parse", "--git-dir")[0] != 0:
        return None
    rc, out, _ = _git(root, "log", "-1", "--format=%H",
                      "--since=%s" % since, branch, "--")
    return bool(out.strip()) if rc == 0 else False


def _trunk_of(where: str) -> Optional[str]:
    """The branch this repository treats as its trunk, or ``None``.

    ``origin/HEAD`` first, because that is the repository saying so rather than
    this code guessing. It is unset more often than not -- of the nine
    repositories this was measured against, five had no remote HEAD -- so the
    two names git itself reaches for are the fallback, and anything else is
    ``None``.

    ``None`` means the trunk could not be identified, and the caller stays quiet
    on it. That is the same narrowing as every other one in
    :func:`_abandoned_claims`: this warning is only worth as much as the evidence
    under it, and a guess about which branch is shared is not evidence.
    """
    root = Path(where)
    rc, out, _ = _git(root, "symbolic-ref", "--short", "-q",
                      "refs/remotes/origin/HEAD")
    if rc == 0 and out.strip():
        # ``origin/main`` -> ``main``; split once, so a branch whose own name
        # has slashes in it survives.
        _, _, name = out.strip().partition("/")
        return name or None
    for name in ("main", "master"):
        if _git(root, "rev-parse", "--verify", "-q",
                "refs/heads/%s" % name)[0] == 0:
            return name
    return None


def _abandoned_claims(ws: Workspace, cat: Optional[Catalog]) -> List[str]:
    """Items somebody started, on a branch that has nothing on it.

    ★ One field, two problems. ★ The record that lets `do` show you a second
    claim is the same record that makes this question askable at all, and this is
    the half that pays for the other: nothing here was previously findable by any
    command. NA-0045 was claimed, the session carrying the work went idle, and
    the fact surfaced two days later because somebody read a transcript.

    Deliberately narrow, and each narrowing is a way of not becoming an alarm
    that always rings:

    * a claim younger than ``CLAIM_QUIET_DAYS`` is quiet -- you are working;
    * a claim made on the repository's trunk is quiet, because the question this
      asks cannot be answered there -- see below;
    * a claim whose branch has any commit since the claim date is quiet, even if
      the item is nowhere near done, because something is happening and this
      warning has nothing to add to it;
    * a claim this cannot check -- no branch recorded, no git, a directory that
      has gone, a trunk that cannot be identified -- is quiet, because a warning
      fired on absent evidence teaches the reader that the warning does not mean
      anything.

    ★ Why the trunk is out of scope (NA-0050). ★ The question here is "has this
    branch had a commit", and on a shared branch somebody else answers it for
    you. Replaying pm's 53 items against the real history of the repositories
    they were worked in -- claiming each on the day its work started -- put
    **92% of claims on the trunk**, and **51% of all claims were silenced by
    commits that never touched the item**. In the three repositories actually
    being worked at the time that rose to 61%. The old criterion was not
    conservative there; it was reading someone else's traffic as this item's
    progress, and its answer was decided by whether the *repository* was busy
    rather than by whether the *item* was.

    Restricting it to branches somebody made on purpose is what the same replay
    prefers: warnings drop from 18 to 4 over that fortnight, ten of the
    eighteen having been fired at items that were already finished, and the one
    real abandonment on record -- NA-0045, whose session went idle on
    ``fix/duplicate-ids`` -- is still caught.

    The honest cost, and it is the whole cost: starting an item on the trunk is
    the most common way to start one, and this now says nothing about those at
    all. It said nothing useful about them before either; what changes is that
    the silence is now a rule rather than an accident, and cannot be mistaken
    for a clean bill of health. Narrowing it to the item's own commits was the
    other candidate and the same replay rejected it: no such convention exists
    yet outside pm's own bookkeeping, so it would have fired on 88% of claims.

    A warning rather than an error, and it does not touch the exit code. It is
    telling you where to go and look, which is a thing only a person can act on;
    exit 3 is a signal to a scheduler, and re-running the pipeline cannot make a
    forgotten session commit anything.
    """
    today = dt.date.today()
    found: List[Tuple[str, Dict[str, Any], int]] = []
    for _path, fm in _open_entries(ws):
        if status_of(fm) != IN_PROGRESS:
            continue
        claim = claim_of(fm)
        if claim is None:
            continue
        where, branch = claim.get("where"), claim.get("branch")
        age = claim_age_days(claim, today)
        if not where or not branch or age is None or age < CLAIM_QUIET_DAYS:
            continue
        trunk = _trunk_of(str(where))
        if trunk is None or str(branch) == trunk:
            continue
        if _claim_has_commits(str(where), str(branch), str(claim.get("at"))) is False:
            found.append((str(fm.get("id") or ""), claim, age))
    if not found:
        return []

    out = []
    for item_id, claim, age in found[:NAMED]:
        out.append(tr(cat, "cli.check.abandoned_claim",
                      "{id} has been claimed by {by} since {at} ({days}d), and "
                      "{branch} in {where} has had no commit since. Either the "
                      "work is somewhere else or the session was lost.",
                      id=item_id, by=claim.get("by"), at=claim.get("at"),
                      days=age, branch=claim.get("branch"),
                      where=_tilde(str(claim.get("where")))))
    if len(found) > NAMED:
        out.append(tr(cat, "cli.check.abandoned_claim_more",
                      "and {n} more claimed item(s) with nothing on their branch.",
                      n=len(found) - NAMED))
    return out


# Directories the deliverable scan does not enter.
#
# ``backlog`` is the load-bearing one and the only one that is about meaning
# rather than about cost. An item file is named `NA-0049-some-words.md` -- the
# very convention this scan reads -- so without it every item in every workspace
# reports itself on the first run, which is an alarm that fires on everything.
# It is matched at *any* depth, not just at the workspace root, because a
# checkout sitting inside a workspace can carry another workspace's backlog, and
# another workspace's NA-0001 has nothing to do with this one's.
#
# The rest are generated or vendored trees. Nothing a person delivers lives in
# them, and walking a `.git` or a `node_modules` is how a check that has to stay
# instant stops being run.
SKIP_DIRS = frozenset({
    "backlog",
    ".git", ".hg", ".svn",
    "node_modules", "vendor", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "target",
})


def _deliverables(ws: Workspace, ids: Sequence[str]) -> Dict[str, List[str]]:
    """Workspace-relative paths of files named ``<id>-*``, keyed by id.

    ★ The edge was already in the filenames; nothing was reading them. ★

    Two design spikes were delivered as `docs/design/NA-0033-reconciler.md` and
    `docs/design/NA-0029-decisions-schema.md` while both items read 0/6 and 0/4.
    The convention that ties a file to an item was already in practice -- it was
    just not in any code.

    **The name, and only the name.** The other candidate signal was "a path the
    item's prose mentions exists", and it was measured before either was built:
    7 hits, 0 true positives, every one of them a README or a CLAUDE.md named in
    passing. `<id>-*` scored 2 of 2. A warning at the first precision is worse
    than no warning, because what it teaches is to scroll past warnings.

    **Rooted at the workspace, and it cannot leave.** ``os.walk`` does not follow
    symlinks, so a link out of the tree is not a way around this. That containment
    is the second half of the precision: ids are unique inside one workspace and
    nowhere else, and a scan that wandered up to the portfolio root would read
    the example workspace's invented NA-0001 as evidence about a real one.

    One walk for every id at once. The caller asks about the handful of items
    that are actually at zero, and an empty ``ids`` walks nothing at all.
    """
    wanted = {"%s-" % i: str(i) for i in (str(x).strip() for x in ids) if i}
    found: Dict[str, List[str]] = {}
    if not wanted:
        return found
    root = str(ws.root)
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            for prefix, item_id in wanted.items():
                if name.startswith(prefix):
                    rel = os.path.relpath(os.path.join(dirpath, name), root)
                    found.setdefault(item_id, []).append(rel.replace(os.sep, "/"))
                    break
    return found


def _delivered_but_unticked(ws: Workspace, cat: Optional[Catalog]) -> List[str]:
    """Items with a file named after them and not one criterion ticked.

    ★ It reports. It does not tick. ★ A matching filename proves there is
    something worth looking at; it cannot prove that six separate criteria were
    each met, and deciding that is a reading, not a match. Ticking from a
    filename would be exactly the evidence-free completion the rest of this tool
    is built to refuse -- and it would be worse here than elsewhere, because it
    would arrive wearing the appearance of a check.

    Narrow in three ways, each one a way of not becoming an alarm that always
    rings:

    * **live items only.** A deliverable next to a closed item is what closing an
      item is supposed to leave behind.
    * **zero ticks.** One tick means somebody has already been here with their
      eyes open. The reported failure is an item that reads as never started.
    * **criteria must exist.** At 0/0 there is no box to tick, so the sentence
      would be true, unactionable, and permanent.

    A warning, and it does not touch the exit code -- the same reason as
    :func:`_abandoned_claims`. Exit 3 tells a scheduler to run the pipeline
    again, and no number of re-runs will make a person read a file.
    """
    today = dt.date.today()
    zero: List[Tuple[str, int]] = []
    for path in sorted(ws.backlog.glob("*.md")):
        try:
            fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not fm or not is_live(fm, today):
            continue
        ticked, _dropped, total = _ac_progress(body or "")
        item_id = str(fm.get("id") or "").strip()
        if item_id and total and not ticked:
            zero.append((item_id, total))

    delivered = _deliverables(ws, [i for i, _t in zero])
    found = [(i, t, delivered[i]) for i, t in zero if i in delivered]
    if not found:
        return []

    out = []
    for item_id, total, paths in found[:NAMED]:
        out.append(tr(cat, "cli.check.delivered_unticked",
                      "{id} has {total} criteria and not one is ticked, while "
                      "this workspace holds a file named after it: {paths}. "
                      "Either the work landed and the ticks did not, or the file "
                      "is a beginning -- only reading it says which.",
                      id=item_id, total=total, paths=_named(paths)))
    if len(found) > NAMED:
        out.append(tr(cat, "cli.check.delivered_unticked_more",
                      "and {n} more item(s) with nothing ticked and a file named "
                      "after them.", n=len(found) - NAMED))
    return out


def cmd_v0(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    rc = _run_sense([])
    if rc != 0:
        return rc
    return _run_render(list(args.extra))


def _projects_root(ws: Workspace) -> str:
    """The portfolio root the registry declares. Fails open to the workspace: a
    prompt with a slightly wrong path in it beats no brief at all."""
    try:
        reg = load_jsonc(ws.registry_path)
    except JSONCError:
        return str(ws.root)
    declared = (reg.get("defaults") or {}).get("root") if isinstance(reg, dict) else None
    return str(expand(declared)) if declared else str(ws.root)


def _daily_prompt(ws: Workspace, cat: Optional[Catalog]) -> Optional[str]:
    """The stage-2 prompt, with its two placeholders filled in.

    The workspace copy wins over the packaged one, so editing the prompt never
    means editing an installed package. Substitution is literal, not
    ``str.format``: the prompt is full of example JSON and the first brace would
    end the argument.
    """
    locale = getattr(cat, "locale", None) or "en"
    names = ("daily.%s.md" % locale, "daily.en.md", "daily.md")

    def _found(text):
        return text.replace("{workspace_root}", str(ws.root)).replace(
            "{projects_root}", _projects_root(ws)
        )

    # The workspace copy is a real directory; the packaged fallback may live
    # inside a zipapp, where paths do not exist. Hence two readers rather than
    # one loop over two bases.
    for name in names:
        candidate = ws.prompts / name
        try:
            if candidate.is_file():
                return _found(candidate.read_text(encoding="utf-8"))
        except OSError:
            continue
    for name in names:
        text = resources.read_text("prompts", name)
        if text is not None:
            return _found(text)
    return None


# Appended only for runners with no tools. The prompt tells the model to read the
# digest and write the brief itself, which a bare completion endpoint cannot do,
# so the caller has to supply the input and take delivery of the output. Saying
# so here rather than in the prompt file keeps one prompt for both kinds of
# runner -- and keeps the agentic path from paying for an inlined digest it was
# specifically designed to read once, from disk.
_NO_TOOLS_SUFFIX = """

---

## You have no file access on this run

Ignore the instructions above about reading or writing files. `state/digest.json`
is reproduced below in full -- it is your entire input. Reply with the
`brief.json` document and nothing else: no prose before it, no fence around it.

```json
%s
```
"""


def _stage_interpret(ws: Workspace, cat: Optional[Catalog]) -> None:
    """The one model call. Never raises, and never fails the run.

    Whatever happens here, stage 3 still renders: a brief built only from
    verified facts is the floor of this system, not a failure mode.
    """
    from .providers import AUTO_ORDER, available, canonical, is_agentic, provider_name, run_provider

    cfg = _load_config(ws)
    # Resolve `auto` here rather than letting run_provider do it, because what the
    # caller has to do next differs by runner: an agent reads the digest and
    # writes the brief itself, a completion endpoint needs both handed to it.
    name = canonical(provider_name(cfg))
    if name == "auto":
        probes = available(cfg)
        name = next((n for n in AUTO_ORDER if probes.get(n)), "none")
    if name == "none":
        return  # configured to run without a model; that is a supported mode

    prompt = _daily_prompt(ws, cat)
    if prompt is None:
        _err("warning: no daily prompt found; rendering the deterministic brief only")
        return

    agentic = is_agentic(name)
    if not agentic:
        if not ws.digest.is_file():
            _err("warning: %s is missing; skipping the model stage" % ws.digest)
            return
        try:
            prompt += _NO_TOOLS_SUFFIX % ws.digest.read_text(encoding="utf-8")
        except OSError as exc:
            _err("warning: cannot read %s (%s); skipping the model stage" % (ws.digest, exc))
            return

    result = run_provider(name, cfg, prompt, ws)
    if not result.ok:
        _err("warning: provider %r failed (%s); rendering what is verified" % (name, result.error))
        return
    if agentic:
        return

    text = (result.text or "").strip()
    if not text:
        _err("warning: provider %r returned nothing; keeping the previous brief" % name)
        return
    try:
        json.loads(text)
    except ValueError as exc:
        # Not repaired, not partially written. An unparseable reply leaves
        # yesterday's brief.json in place, and stage 3 will notice it is stale --
        # which is a far better outcome than a half-valid brief that renders.
        _err("warning: provider %r did not return JSON (%s); keeping the previous brief" % (name, exc))
        return
    try:
        write_text(ws, ws.brief_json, text + "\n")
    except OSError as exc:
        _err("warning: cannot write %s: %s" % (ws.brief_json, exc))


def _ingest_adjustments(ws: Workspace) -> None:
    """Fold any corrections the last brief dropped into the overlay.

    Before sensing, because sensing lays the overlay over the registry -- a
    correction read afterwards would not reach today's snapshot and would look
    like it did nothing for a day.

    The staleness stamp is checked against the LAST RENDERED brief, not against
    today. Someone clicking this morning is answering the brief they have open,
    which was written last night; comparing against today's date would refuse
    every correction ever made. What it does refuse is a tab left open across
    several runs, where the state has moved on since they looked.
    """
    from . import inbox  # local: keeps `sense` import-light
    from .annotate import QUESTIONS, record_answers
    from .render import last_run

    latest = last_run(ws)
    if not latest:
        return                               # nothing has been rendered yet
    stamp = str(latest.get("as_of") or "")[:10]
    if not stamp:
        return
    try:
        cfg = load_jsonc(ws.config_path)
        reg = load_jsonc(ws.registry_path)
    except (JSONCError, OSError):
        return
    drop = ((cfg.get("review") or {}).get("drop_dir")) or "~/Downloads"
    ids = [p.get("id") for p in (reg.get("projects") or []) if p.get("id")]

    accepted, refused = inbox.read_adjustments(
        drop, QUESTIONS, ids, latest.get("status_contradicted") or [],
        as_of=dt.date.fromisoformat(stamp) if stamp else None)
    if accepted:
        record_answers(ws, inbox.apply_adjustments(accepted))
        print("run: applied %d correction(s) from %s" % (len(accepted), drop))
    # Refusals are printed, never swallowed. Someone who clicked and saw nothing
    # happen would reasonably conclude the control does not work.
    for reason, count in sorted(refused.items()):
        if count:
            print("run: ignored %d correction(s): %s" % (count, reason))


def cmd_run(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    try:
        _ingest_adjustments(ws)
    except Exception as exc:   # a dropped file must never cost somebody their brief
        _err("warning: could not read corrections (%s); continuing" % exc)
    rc = _run_sense([])
    if rc != 0:
        return rc
    try:
        _stage_interpret(ws, cat)
    except Exception as exc:  # the model stage is an accessory; the brief is not
        _err("warning: the model stage failed (%s); rendering the deterministic brief" % exc)
    return _run_render(list(args.extra))


def cmd_open(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    if not ws.brief_html.is_file():
        rc = _run_render(["--no-notify"])
        if rc != 0:
            return rc
    if not ws.brief_html.is_file():
        _err("error: %s still does not exist" % ws.brief_html)
        return EXIT_FAIL
    # as_uri() rather than a bare path, and webbrowser rather than macOS `open`:
    # the same call now works on Linux, and a space in the path cannot break it.
    webbrowser.open(ws.brief_html.as_uri())
    return EXIT_OK


def cmd_brief(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    if not ws.brief_md.is_file():
        _err("error: no %s yet. Run `nextbrief v0` to build one." % ws.brief_md)
        return EXIT_FAIL
    sys.stdout.write(ws.brief_md.read_text(encoding="utf-8"))
    return EXIT_OK


def cmd_log(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    path = ws.log / "runs.jsonl"
    if not path.is_file():
        _err("error: no %s yet. Run `nextbrief v0` first." % path)
        return EXIT_FAIL
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for line in lines[-max(1, args.lines):]:
        print(line)
    return EXIT_OK


def cmd_show(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """The item in full, under a header saying how much of it is yours.

    ★ The header exists to answer one question in the second the file opens:
    how much of this needs me? ★

    That question was being answered by reading all nine criteria and working it
    out again from scratch, every time, and the answer was almost always "two of
    them". The file cannot show it: the criteria are one flat list, in one shape,
    and the two that need a person look exactly like the seven that do not.

    The file itself is printed byte for byte after it, unchanged. It is the
    record, and the header is a reading of it -- so nothing here is allowed to
    stand between a reader and the words somebody actually wrote.
    """
    path = _find_item(ws, args.item_id, cat)
    if path is None:
        return EXIT_FAIL
    text = path.read_text(encoding="utf-8")
    _fm, body = parse_frontmatter(text)
    for line in _criteria_header(body or "", cat):
        print(line)
    sys.stdout.write(text)
    return EXIT_OK


def _criteria_header(body: str, cat: Optional[Catalog]) -> List[str]:
    """The two-or-three line summary `show` prints above an item.

    Empty for an item with no criteria, where every line here would be zero.
    """
    criteria = _ac_lines(body)
    if not criteria:
        return []
    ticked, dropped, total = _ac_progress(body)
    yours = [(m, t) for _i, m, t in criteria if _ac_owner(t) == AC_YOU]
    unmarked = [(m, t) for _i, m, t in criteria if _ac_owner(t) is None]
    theirs = len(criteria) - len(yours) - len(unmarked)

    try:
        columns = shutil.get_terminal_size().columns
    except Exception:
        columns = 80

    out = [tr(cat, "cli.show.criteria",
              "Acceptance criteria: {total} · {done} ticked · {dropped} set aside",
              total=total, done=ticked, dropped=dropped)]
    # The open ones you own are printed in full rather than counted, because a
    # number cannot be acted on and by design there are never more than two of
    # them. Everything else is a count: it is precisely the part not worth
    # reading right now, and that is the whole claim being made.
    open_yours = [t for m, t in yours if m == AC_OPEN]
    if yours:
        out.append("  " + tr(cat, "cli.show.yours",
                             "{open} of {n} marked ({you}) still open",
                             open=len(open_yours), n=len(yours), you=AC_YOU))
    for t in open_yours:
        out.append("    " + _clip(t, max(20, columns - 6)))
    if theirs:
        out.append("  " + tr(cat, "cli.show.theirs",
                             "{n} marked ({agent}), for the agent to verify",
                             n=theirs, agent=AC_AGENT))
    if unmarked:
        # Named as unclassified rather than silently counted with yours. They ARE
        # treated as yours everywhere else -- `done` asks about them -- and the
        # honest thing is to say that this is a default standing in for an answer
        # nobody has given, not a decision somebody made.
        out.append("  " + tr(cat, "cli.show.unmarked",
                             "{n} with no ({agent})/({you}) marker, asked as "
                             "though they were yours",
                             n=len(unmarked), agent=AC_AGENT, you=AC_YOU))
    out.append("")
    return out


def cmd_ok(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    return _mark(
        ws,
        cat,
        args.item_id,
        {"human_confirmed": True},
        "confirm",
        tr(
            cat,
            "cli.ok.done",
            "{id} confirmed -- automatic decay will never touch it again.",
            id=args.item_id,
        ),
    )


# ---------------------------------------------------------------------------
# saying what is about to be closed, and offering what can be derived
# ---------------------------------------------------------------------------

# The key that accepts a draft. Chosen by the author, and the requirement it was
# chosen against is the only one that matters: it must not be reachable by the
# reflex that answers prompts with Enter. It is also not a letter, so it is
# never the first character of a sentence somebody meant to type.
TICK_ALL = "a"
ACCEPT_DRAFT = "="

# `-` drops the criterion under the cursor. One keypress, no prompt -- the reason
# lands in the summary question that was going to be asked anyway, because a
# third question is precisely the friction this flow exists to remove.
DROP_KEY = "-"


def _needs_you(text: str) -> bool:
    """Whether this criterion is one to put in front of a person.

    ★ Unmarked counts as yours, and that is the load-bearing half. ★

    An unmarked criterion is not the agent's -- it is one nobody has classified
    yet, and *every criterion written before the marker existed is unmarked*.
    Reading the absence as "the agent's" would empty the tick selector for the
    entire existing backlog in one move, and empty is the one thing it must never
    be: `done` could not ask at all until recently, measured at 1 ticked box
    across 25 items, and being askable is the whole point of the step. So the
    default is to ask, and `check` reports how many are still unclassified rather
    than the engine guessing on their behalf.
    """
    return _ac_owner(text) != AC_AGENT


def _unticked_acs(body: str) -> List[str]:
    """The text of every criterion still OPEN, with its ``#n`` left on.

    ★ Dropped criteria are excluded here, and that exclusion is the whole point
    of the third mark. ★

    This list is what `done` drafts as `future_work`, and `followup` turns a
    `future_work` entry into a real backlog item carrying `discovered_from`. A
    criterion the design moved past, left merely unticked, therefore does not
    just sit in the file being wrong -- it mints a task for work that was
    deliberately abandoned, and the next reader has nothing to tell them it is
    dead. That is strictly more expensive than a wrong record, because a wrong
    record is static and a minted task travels.
    """
    return [text for _i, mark, text in _ac_lines(body) if mark == AC_OPEN]


def _ticked_acs(body: str) -> List[str]:
    """The text of every criterion that IS ticked.

    The best available answer to "what actually happened", and the only one that
    is not a guess: a ticked box is a person saying that thing is done, in their
    own words. The engine can read the tick and cannot make it.

    Dropped criteria are not here either. Counting one as done would be the
    original lie the third mark exists to refuse.
    """
    return [text for _i, mark, text in _ac_lines(body) if mark == AC_DONE]


def _apply_marks(body: str, marks: Dict[int, str]) -> str:
    """Write these marks at these line indexes, leaving everything else alone.

    A rewrite of the checkbox character and nothing more: the criterion's own
    text is a sentence a person wrote and this must never touch it. That holds
    hardest for a dropped criterion, where erasing the words would erase what was
    once promised -- and "the goal moved" is the only fact the record is here to
    keep.

    Nothing is ever cleared. The selector only ever offers criteria that are
    still open, so the marks arriving here can turn an open box into a tick or
    into a drop and can do nothing else. An engine that could clear a tick could
    erase a statement its author made, and the file is right there for anyone who
    wants to.
    """
    lines = body.splitlines(True)
    for i, mark in marks.items():
        if 0 <= i < len(lines):
            s = lines[i]
            head = s[:len(s) - len(s.lstrip())]
            rest = s.lstrip()
            lines[i] = head + rest[:3] + mark + rest[4:]
    return "".join(lines)


def _registry(ws: Workspace) -> Dict[str, Any]:
    try:
        reg = load_jsonc(ws.registry_path)
    except (JSONCError, OSError):
        return {}
    return reg if isinstance(reg, dict) else {}


def _project_dirs(ws: Workspace, reg: Dict[str, Any], project_id: Any) -> List[Path]:
    """Existing directories a project declares, resolved the way ``launch`` does.

    A relative ``defaults.root`` is relative to the *workspace*, never to
    wherever the command was typed -- resolving it anywhere else reads git logs
    from a tree nobody meant.
    """
    declared = (reg.get("defaults") or {}).get("root") or str(ws.root)
    root = expand(declared)
    if not root.is_absolute():
        root = ws.root / root
    for pr in reg.get("projects") or []:
        if isinstance(pr, dict) and pr.get("id") == project_id:
            return [d for d in (root / str(rel) for rel in (pr.get("paths") or []))
                    if d.is_dir()]
    return []


def _project_name(reg: Dict[str, Any], project_id: Any) -> str:
    for pr in reg.get("projects") or []:
        if isinstance(pr, dict) and pr.get("id") == project_id:
            return str(pr.get("name") or project_id or "")
    return str(project_id or "")


def _clip(text: str, cells: int) -> str:
    """``text`` cut to ``cells`` terminal columns, with an ellipsis when cut."""
    s = " ".join(str(text).split())
    if _width(s) <= cells:
        return s
    return _pad(s, max(1, cells - 1)).rstrip() + "…"


def _echo_target(ws: Workspace, item_id: str, cat: Optional[Catalog]) -> Optional[Path]:
    """Find the item and say what it is, before anything irreversible happens.

    ★ The id is typed by hand, and `NA-0017` and `NA-0019` differ by one key. ★

    `done`, `drop` and `defer` all write ``human_confirmed: true`` and commit,
    so a mistyped character permanently confirms an item nobody has read and
    leaves a commit saying so. `do` -- which only opens a session -- already
    printed a header; the three commands that cannot be undone did not. That was
    an inconsistency rather than a design.

    The acceptance count is here because it is the number that stops you: an
    item about to be closed with nothing ticked is either finished-and-unticked
    or not finished, and both are worth one second of hesitation. It is omitted
    when the item has no criteria at all, where `0/0` would only be noise.
    """
    path = _find_item(ws, item_id, cat)
    if path is None:
        return None
    try:
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return path
    fm = fm or {}
    print()
    print(tr(cat, "cli.do.header", "> {id} · {title}",
             id=item_id, title=str(fm.get("title") or "")))
    print("  " + tr(cat, "cli.do.project", "Project: {project}",
                    project=_project_name(_registry(ws), fm.get("project"))))
    ticked, dropped, total = _ac_progress(body or "")
    if total and dropped:
        # Said in the same breath as the ratio, because the ratio alone reads as
        # a shortfall: 1/3 with one criterion set aside is not the same item as
        # 1/3 with two still to do, and the reader is about to close it.
        print("  " + tr(cat, "cli.close.acceptance_dropped",
                        "Acceptance criteria: {done}/{total} ticked, {dropped} dropped",
                        done=ticked, total=total, dropped=dropped))
    elif total:
        print("  " + tr(cat, "cli.close.acceptance",
                        "Acceptance criteria: {done}/{total} ticked",
                        done=ticked, total=total))
    print()
    return path


def _project_commits(ws: Workspace, reg: Dict[str, Any], project_id: Any,
                     since: str) -> List[Tuple[str, str]]:
    """``(sha, subject)`` for the project's commits since ``since``, newest first.

    ``--since=2026-08-06`` is read by git at the *current time of day*, so a bare
    date silently drops everything committed earlier the same day -- which is
    every commit that matters when an item is opened and closed on one day, the
    normal case here. Hence the explicit midnight.
    """
    if not since:
        return []
    got: List[Tuple[str, str]] = []
    for directory in _project_dirs(ws, reg, project_id):
        rc, out, _err = _git(directory, "log", "--since=%s 00:00:00" % since,
                             "--no-merges", "--pretty=%h\t%s")
        if rc != 0:
            continue
        for line in out.splitlines():
            if "\t" in line:
                sha, subject = line.split("\t", 1)
                got.append((sha.strip(), subject.strip()))
    return got


def _select_ticks(rows: Sequence[Tuple[int, str]],
                  cat: Optional[Catalog]) -> Optional[Tuple[List[int], List[int]]]:
    """Move with the arrows, space ticks, ``-`` drops, Enter accepts.

    Returns ``(ticked, dropped)`` line indexes, or ``None`` if the terminal
    cannot do this.

    Typing numbers works and is what this falls back to, but a typo there is
    silent in the worst way: `9` on a two-item list ticks nothing, and a
    criterion nobody ticked is indistinguishable from one that was already done.
    Moving a cursor onto a line cannot miss.

    **`setcbreak`, not `setraw`.** Raw mode swallows the interrupt character, so
    Ctrl-C would arrive as the byte 0x03 and the abort path -- the one that stops
    `done` from closing an item you were backing out of -- would have to be
    re-implemented here, correctly, from scratch. cbreak leaves ISIG alone, so
    Ctrl-C still raises KeyboardInterrupt exactly as it does at every other
    prompt.

    Returns ``None`` rather than raising when the terminal cannot do this, which
    is the ordinary case in CI, over a pipe, and under a scheduler. The caller
    asks the numeric question instead.
    """
    try:
        import termios
        import tty
    except ImportError:                      # not POSIX
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    if os.environ.get("TERM", "") in ("", "dumb"):
        return None
    try:
        fd = sys.stdin.fileno()
        before = termios.tcgetattr(fd)
    except Exception:
        return None

    # The mark each row currently carries, and the character drawn in its box.
    # Every row starts open: the caller only ever passes criteria that are still
    # open, so nothing here can undo a decision already recorded in the file.
    state = [AC_OPEN] * len(rows)
    at = 0

    # Every row is cut to one terminal line, and that is what makes the redraw
    # arithmetic true rather than approximately true.
    #
    # The first version moved the cursor up by len(rows) and assumed each row had
    # taken one line. Real criteria are sentences: they wrapped, so the cursor
    # landed inside the list and each keypress appended a fresh copy instead of
    # overwriting -- the list grew down the screen on every arrow. Nine wrapped
    # rows and it was unreadable after one keystroke.
    #
    # `_clip` is measured in terminal CELLS, not characters, so a CJK criterion
    # -- two cells per glyph -- is cut where it actually reaches the edge. The
    # column budget leaves room for the "  > [x] " prefix and one spare cell,
    # because a row filling the last column wraps on some terminals and not
    # others, and a selector that is correct only on some terminals is worse
    # than one that is short.
    try:
        columns = shutil.get_terminal_size().columns
    except Exception:
        columns = 80
    budget = max(20, columns - 10)
    shown = [_clip(text, budget) for _i, text in rows]

    def draw(first: bool) -> None:
        if not first:
            sys.stdout.write("\x1b[%dA" % len(rows))
        for n, text in enumerate(shown):
            sys.stdout.write("\x1b[2K")
            sys.stdout.write("  %s [%s] %s\r\n"
                             % (">" if n == at else " ", state[n], text))
        sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        draw(True)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                return ([rows[n][0] for n, m in enumerate(state) if m == AC_DONE],
                        [rows[n][0] for n, m in enumerate(state) if m == AC_DROPPED])
            if ch == " ":
                state[at] = AC_OPEN if state[at] == AC_DONE else AC_DONE
            elif ch == DROP_KEY:
                # A toggle for the same reason space is one: changing your mind
                # must not need a restart. `-` and space each toggle their own
                # mark, so either key on a row the other one marked switches it
                # outright, and pressing the same key twice puts it back.
                state[at] = AC_OPEN if state[at] == AC_DROPPED else AC_DROPPED
            elif ch in ("j",):
                at = min(at + 1, len(rows) - 1)
            elif ch in ("k",):
                at = max(at - 1, 0)
            elif ch == "\x1b":
                # An arrow is ESC [ A/B. A bare ESC is somebody backing out, and
                # is treated as "mark nothing" rather than as an abort -- Ctrl-C
                # is the abort, and it still is.
                nxt = sys.stdin.read(1)
                if nxt != "[":
                    return [], []
                code = sys.stdin.read(1)
                if code == "A":
                    at = max(at - 1, 0)
                elif code == "B":
                    at = min(at + 1, len(rows) - 1)
            elif ch == "\x04":              # Ctrl-D: no more input, same as EOF
                return [], []
            draw(False)
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, before)
        except Exception:
            pass


def _ask_ticks(body: str, cat: Optional[Catalog],
               include_agent: bool = False) -> Tuple[List[int], List[int]]:
    """Which criteria are done -- and which no longer apply -- asked at the only
    moment anyone can answer. Returns ``(ticked, dropped)`` line indexes.

    Ticking used to be possible only by hand-editing the file, and nothing said
    so -- not the prompts, not the schema, not the README. Measured on a real
    backlog: 1 item of 25 carried a single tick. So `0/9 ticked` printed on
    almost every close, which is a number that stops nobody, and the two rules
    that read ticks (the closing draft, and follow-ups from what is left) were
    dead in all but one case.

    Asked rather than derived, and only ever additive. A tick is a person saying
    a thing is done; an engine that sets one is inventing the statement the whole
    closing record exists to capture honestly.

    This is not a third question. It REPLACES the summary draft with something
    that answers the question -- your own criteria, in your own words -- where
    the draft used to offer a commit count.

    Dropping lives here for the same reason, and stays a keypress rather than
    becoming a prompt: the answer to "why" is already being asked two questions
    down, and a form that grows is a form that gets answered with Enter.

    Only criteria that are still open are offered. A tick and a drop are both
    decisions already recorded in the file, and re-asking them would make this
    step a place where one could be taken back by accident.

    ★ And only the ones only a person can answer. ★

    Criteria marked `(agent)` are asked SECOND, in a list of their own, because
    the thing being protected here is not keystrokes -- it is the question
    "which of these actually need me", which was being asked and answered from
    scratch on every close. Ordering answers it; withholding them did not.

    They were withheld once, and the count was printed with `--all-criteria`
    named beside it. That flag is the only way to reach them, and reaching for
    it means abandoning the close and typing the command again -- so the advice
    arrived at the one moment it was most expensive to take, and the measurement
    says nobody took it: 41 of 72 marks on items closed since were already in
    the file, hand-edited during the work, and NA-0050 closed 1/3 with its own
    commit message recording that the other two had landed.

    `--all-criteria` still exists and now means one list instead of two.
    """
    open_rows = [(i, text) for i, mark, text in _ac_lines(body) if mark == AC_OPEN]
    if not open_rows:
        return [], []
    yours = [r for r in open_rows if _needs_you(r[1])]
    theirs = [r for r in open_rows if not _needs_you(r[1])]
    # Two lists, asked in one run. Holding the agent's criteria back was right
    # and stays: the question being protected is "which of these actually need
    # me", not keystrokes. What was wrong is where the escape hatch lived. The
    # held-back count named `--all-criteria`, which you can only act on by
    # abandoning the close and running it again -- advice delivered at the one
    # moment it is most expensive to take, so nobody took it. Measured on the
    # live backlog: of the criteria settled on items closed since the selector
    # shipped, 41 of 72 marks were already in the file, hand-edited during the
    # work; and NA-0050 closed 1/3 with its own commit message recording that
    # the other two had landed.
    #
    # So the second list is shown here instead of being described. Enter still
    # leaves them open -- nothing is forced, and a close is never blocked on a
    # criterion only the agent can judge -- but answering one costs a keypress
    # rather than a re-run.
    groups = ([(open_rows, False)] if include_agent
              else [g for g in ((yours, False), (theirs, True)) if g[0]])
    print()
    picked: List[int] = []
    dropped: List[int] = []
    for rows, unsettled in groups:
        if unsettled:
            # Said out loud, always. A list shorter than the file with nothing
            # saying why is this repo's characteristic bug: it does not read as
            # something missing, it reads as an item that was always this small.
            # Worded so the count never has to agree with a noun: `t()` has no
            # plural mechanism, and "dropped 1 criteria" already shipped once.
            print("  " + tr(cat, "cli.done.unsettled_criteria",
                            "{n} still open and the agent's to verify. Nothing "
                            "settled them, so they are asked here too -- Enter "
                            "leaves them open and they draft as follow-ups.",
                            n=len(rows)))
        got, gone = _ask_one_list(rows, cat)
        picked.extend(got)
        dropped.extend(gone)
    return sorted(set(picked)), sorted(set(dropped))


def _ask_one_list(rows: List[Tuple[int, str]],
                  cat: Optional[Catalog]) -> Tuple[List[int], List[int]]:
    """One list of criteria, asked once. ``(ticked, dropped)`` line indexes.

    Split out of ``_ask_ticks`` so the second list is asked by the same code as
    the first: a second copy would be the subtraction failure this file keeps
    finding, with the held-back criteria answered by a selector that drifts from
    the one everybody sees.
    """
    # The selector first, the numbers as the fallback. Both exist because the
    # terminal is not always one that can do the first, and a `done` that cannot
    # ask is a `done` that goes back to being unanswerable. That applies to
    # dropping exactly as it applies to ticking, so the fallback below takes
    # `-N` -- otherwise the third state would exist only on terminals that can
    # draw, and the case it was built for would be unrecordable on the rest.
    print("  " + tr(cat, "cli.done.pick_ticks",
                    "Which of these are done? Up/down to move, space to toggle, "
                    "{drop} to drop one, Enter when finished.", drop=DROP_KEY))
    marked = _select_ticks(rows, cat)
    if marked is not None:
        return marked
    # The selector declined -- undo its instruction line, which described keys
    # this terminal will not deliver.
    sys.stdout.write("\x1b[1A\x1b[2K" if sys.stdout.isatty() else "")

    print("  " + tr(cat, "cli.done.ask_ticks",
                    "Which of these are done? Numbers like 1 3, {drop}N to drop "
                    "one, {all} for all, Enter for none.",
                    all=TICK_ALL, drop=DROP_KEY))
    for n, (_i, text) in enumerate(rows, 1):
        print("    %d. %s" % (n, text))
    try:
        raw = input("  > ").strip()
    except EOFError:
        # Same rule as the questions below: EOF means nobody is here, Ctrl-C
        # means stop, and stop propagates.
        print()
        return [], []
    if not raw:
        return [], []
    if raw.lower() in (TICK_ALL, "all"):
        # `a` says everything left is done. There is no bulk drop and there
        # should not be: "we abandoned all of it" is a `drop` of the item, which
        # this CLI already has a verb for.
        return [i for i, _t in rows], []
    picked: List[int] = []
    dropped: List[int] = []
    for token in raw.replace(",", " ").split():
        drop = token.startswith(DROP_KEY)
        digits = token[len(DROP_KEY):] if drop else token
        if digits.isdigit() and 1 <= int(digits) <= len(rows):
            (dropped if drop else picked).append(rows[int(digits) - 1][0])
        else:
            # Named rather than ignored. A mistyped number that silently ticks
            # nothing looks exactly like a criterion that was already done.
            print("    " + tr(cat, "cli.done.tick_ignored",
                              "ignored {token}: not one of 1-{n}",
                              token=token, n=len(rows)))
    return sorted(set(picked)), sorted(set(dropped))


def _closing_drafts(ws: Workspace, fm: Dict[str, Any], body: str,
                    cat: Optional[Catalog],
                    dropped: Sequence[str] = (),
                    asked: bool = False) -> Tuple[str, List[str], str]:
    """``(summary draft, follow-up drafts, scope line)``, from facts on disk.

    No model, no network, nothing that can be slow or cost money: ``done`` has to
    stay instant or it stops being typed.

    **Both drafts are deliberately narrower than they could be**, because the
    failure mode here is not a missing draft, it is a plausible wrong one -- a
    sentence a machine wrote, accepted in a hurry, and filed forever under a
    person's name. That is strictly worse than an empty field, which at least
    says "nobody knows".

    * The summary draft states a **scope**, never authorship. Not one commit in
      this workspace's projects names an item id, so git cannot tell "this
      item's work" from "that day's work" -- a busy repo produced 31 commits on
      the day an item was closed, of which one was the item. Worded as a
      summary, that number is a fabricated finding; worded as "the project saw
      31 commits since this was opened", it is a true observation the reader can
      act on.

    * Follow-ups are drafted from unticked criteria **only when some are
      ticked**. At ``n/n`` there is nothing left; at ``0/n`` the tick habit
      itself failed and the engine cannot tell "not done" from "not ticked" --
      the real case that taught this was an item closed at 0/6 whose six
      untouched boxes had all in fact shipped. Drafting them would have minted
      six backlog items for finished work.

      What it offers there is the criteria' own text, which a human wrote and
      only a human may edit. Accepting it copies your own sentence back to you,
      not the engine's.

    ``dropped`` is the text of the criteria set aside in *this* run, which is why
    it is a parameter rather than something read back out of the file. It is the
    reason the drop key needs no prompt of its own: the record of why gets to
    ride on the question that was going to be asked anyway. Like everything else
    here it is a DRAFT -- offered above the question, taken only by `=`. Enter
    still records nothing, and that is not a detail: a machine sentence filed
    under a person's name by reflex is the failure this whole record exists to
    avoid, and it does not become acceptable because the sentence is about
    something they abandoned.
    """
    reg = _registry(ws)
    since = str(fm.get("created_date") or fm.get("updated_date") or "").strip()[:10]
    ticked, gone, total = _ac_progress(body)
    commits = _project_commits(ws, reg, fm.get("project"), since)
    # ``project`` answers "what is this item about"; the reference line has been
    # reading it as "where is the evidence", and for a design spike those are two
    # different places. `project: nextbrief` sent this to count commits in the
    # engine's repository -- 51 of them, not one belonging to the item, while the
    # thing actually delivered sat in the workspace under the item's own name.
    item_id = str(fm.get("id") or "").strip()
    delivered = _deliverables(ws, [item_id]).get(item_id, []) if item_id else []

    # What the reader is shown, and what `=` will file, are two different things.
    #
    # `scope` is always true and never an answer: a project name, a commit count,
    # a tick ratio. It is worth seeing -- "7 commits since this opened, 0/9
    # ticked" is the number that makes you pause -- but it does not say what
    # happened, and filing it as the summary answers "what did you actually do?"
    # with a statistic. That is the empty field wearing a finding's clothes,
    # which this whole record exists to avoid.
    #
    # `summary` is offered to `=` only when it is a real candidate answer. There
    # are exactly three such cases and all three are somebody's own words rather
    # than the engine's:
    #
    #   - the criteria that are TICKED. A tick is a person saying that thing is
    #     done; accepting them copies your sentences back to you.
    #   - the criteria DROPPED in this run, which are the same sentences pointed
    #     the other way. The file records that each one was set aside; only this
    #     line can record it in the same breath as what was achieved, and `-`
    #     asked nothing at the time on purpose.
    #   - exactly one commit since the item opened, where its subject is evidence
    #     about THIS item because there is nothing to choose between.
    #
    # With several commits and nothing ticked there is no honest draft, so none
    # is offered, and the question is asked plainly.
    scope = ""
    said = []
    done_acs = _ticked_acs(body)
    if done_acs:
        said.append("; ".join(done_acs))
    if dropped:
        said.append(tr(cat, "cli.close.draft_dropped",
                       "dropped {n} criteria: {text}",
                       n=len(dropped), text="; ".join(dropped)))
    summary = " · ".join(said)
    if commits or total or delivered:
        parts = [_project_name(reg, fm.get("project"))]
        if len(commits) == 1:
            # One commit since the item was opened is the only case where a
            # subject line is evidence about *this* item: there is nothing to
            # choose between. Any more and picking by recency names whatever was
            # committed last, which in both items this was regression-tested
            # against belonged to different work entirely -- a true sentence
            # about the wrong thing, which is the failure being guarded here.
            parts.append(tr(cat, "cli.close.draft_commit",
                            "one commit since {since}: \"{subject}\"",
                            since=since, subject=_clip(commits[0][1], 56)))
        elif commits:
            parts.append(tr(cat, "cli.close.draft_commits",
                            "{n} commits since {since}",
                            n=len(commits), since=since))
        if total and gone:
            parts.append(tr(cat, "cli.close.draft_acceptance_dropped",
                            "AC {done}/{total} ({dropped} dropped)",
                            done=ticked, total=total, dropped=gone))
        elif total:
            parts.append(tr(cat, "cli.close.draft_acceptance", "AC {done}/{total}",
                            done=ticked, total=total))
        scope = " · ".join(p for p in parts if p)
        # One commit and nothing ticked: the subject IS the candidate, so the
        # scope line doubles as the draft. Any more commits and it is context.
        if not summary and len(commits) == 1:
            summary = scope
        # Appended *after* the draft is taken, and that ordering is the whole
        # point. A filename says a thing exists; it does not say what happened,
        # and `=` files what it is given under a person's name. On the wrong side
        # of this line it becomes a machine sentence signed by a human -- the
        # failure the draft/reference split exists to prevent. So the deliverable
        # is shown, always, and offered, never.
        if delivered:
            scope = " · ".join(p for p in (
                scope, tr(cat, "cli.close.scope_delivered", "delivered: {paths}",
                          paths=_named(delivered))) if p)

    # Nothing ticked used to mean nothing at all, and that was right while it was
    # ambiguous. The NA-0017 shape is six criteria, none ticked, all six shipped:
    # drafting them would have minted six backlog items for finished work,
    # because the engine cannot tell "not done" from "not ticked", and at 0/n it
    # was the tick habit that failed rather than the work.
    #
    # What changed is not the reading, it is what 0/n can mean. `_ask_ticks` now
    # puts every open criterion in front of somebody -- the agent's second, in a
    # list of their own -- so 0/n on a run that asked is a person looking at each
    # one and declining it, which is evidence. On a run that did NOT ask (no tty,
    # or answers already given as flags) nobody was shown anything, 0/n is still
    # the absence of an answer, and inventing follow-ups from it would be the
    # original mistake with a new coat of paint.
    #
    # Hence `asked`, and not `sys.stdin.isatty()` read a second time here: the
    # question is whether THIS run asked, which is a fact the caller holds and
    # this function cannot re-derive without getting it subtly wrong.
    future = _unticked_acs(body) if (ticked or asked) and ticked < total else []
    return summary, future, scope


def _ask_closing(args: argparse.Namespace, cat: Optional[Catalog],
                 drafts: Any = None) -> Optional[Closing]:
    """The two questions asked when an item is closed, or None to record nothing.

    ★ Exactly two, and both skippable. ★

    Two, because the count is the design. Every extra field is one more thing
    between a person and the command they came to type, and a form that costs
    more than it returns is answered with Enter within a fortnight -- at which
    point it is worse than not asking, because the empty fields look like
    findings. `summary` and `future_work` are here because a real week produced
    an item whose title said "run 3 probes" and whose truth was "migrated all of
    them", and another that uncovered work with nowhere to go.

    Skippable, because a prompt you cannot escape is trained into a reflex.
    Nothing here refuses; nothing here retries.

    Asked only at a terminal. `nextbrief done X` inside a script must not block
    on a question nobody will ever see, so a non-tty run records whatever the
    flags supplied and nothing else.

    ★ A draft is shown above a question and never inside it. ★

    Drafts exist to make these two questions cheaper to answer, which is the
    cure for the fortnight problem above. But the same mechanism, pointed one
    degree differently, is the worst thing this tool could do: if Enter took the
    draft, then the reflex that already answers every form would start producing
    *machine sentences signed by a person* -- a fabricated finding, which is the
    exact failure the evidence gate exists to prevent, wearing the costume of a
    filled-in field.

    So Enter keeps the meaning it always had, to the letter:

        Enter  = skip                     (unchanged, and unchangeable)
        `=`    = take the draft as shown  (deliberate, and recorded as such)
        typing = your own words

    ``summary_source`` is written for the same reason: an accepted draft is a
    real answer, but it is not testimony, and the record has to be able to say
    which one it holds.
    """
    today = dt.date.today().isoformat()

    def record():
        if not summary and not future:
            return None
        return Closing(today, summary, [FutureWork(t, None) for t in future], source)

    summary = (getattr(args, "summary", None) or "").strip()
    future = [t.strip() for t in (getattr(args, "future_work", None) or []) if t.strip()]
    source = SUMMARY_HUMAN if summary else SUMMARY_NONE
    # Flags win outright: someone who typed the answer is not asked it again.
    if summary or future or not sys.stdin.isatty():
        return record()

    # Only now, because deriving a draft reads a git log per project directory
    # and a scripted `done` would pay for one it is never shown. ``drafts`` is a
    # callable for that reason alone.
    summary_draft, future_drafts, scope = (
        drafts() if drafts is not None else ("", [], ""))

    def offer(lines: Sequence[str]) -> None:
        """Show a draft above the prompt, and say what takes it."""
        for line in lines:
            print("    " + tr(cat, "cli.close.draft", "draft: {text}", text=line))
        print("    " + tr(cat, "cli.close.draft_hint",
                          "{accept} to take the draft  ·  Enter to skip  ·  or just "
                          "write your own", accept=ACCEPT_DRAFT))

    print()
    print("  " + tr(cat, "cli.done.ask_summary",
                    "What actually happened? One line -- especially anything the "
                    "item did not say. Enter to skip."))
    # Context first, and never as something `=` can take. It is true and worth
    # seeing -- the tick ratio is the number that makes you pause -- and it is
    # not an answer to the question above it.
    if scope and scope != summary_draft:
        print("    " + tr(cat, "cli.close.scope", "for reference: {text}", text=scope))
    if summary_draft:
        offer([summary_draft])
    try:
        typed = input("  > ").strip()
    except EOFError:
        # EOF only. Ctrl-C is NOT a way to skip an optional question -- it is
        # the reader stopping the command, and this one is about to write
        # `human_confirmed: true` and commit. Catching it here turned an
        # interrupt into a confirmation: the item closed, in the reader's name,
        # because they tried to back out. `main` catches the propagating
        # KeyboardInterrupt and exits non-zero, which is the whole abort path.
        #
        # EOF genuinely does mean skip: a pipe ran dry, or the run is not a tty,
        # and both of those are "nobody is here to answer", not "stop".
        print()
        return record()
    if typed == ACCEPT_DRAFT:
        # With a draft, take it. Without one -- somebody who learned the key and
        # pressed it on an item that had nothing to offer -- take nothing. The
        # alternative is filing a summary that reads `=`, which is precisely the
        # kind of junk in the record this whole item exists to prevent.
        if summary_draft:
            summary, source = summary_draft, SUMMARY_DRAFT
    elif typed:
        summary, source = typed, SUMMARY_HUMAN
    # else: Enter. `summary` stays empty and `source` stays `none`. This branch
    # is the property the whole feature is built around -- see the test that
    # plants a draft and presses Enter.

    print()
    print("  " + tr(cat, "cli.done.ask_future",
                    "Anything this turned up that does not belong to it? One per "
                    "line, Enter on an empty line to finish."))
    if future_drafts:
        offer(future_drafts)
    while True:
        try:
            line = input("  - ").strip()
        except EOFError:
            # As above: EOF finishes the list, Ctrl-C abandons the command.
            print()
            break
        if future_drafts and line == ACCEPT_DRAFT:
            # Taken as a group: these are the criteria that were left unticked,
            # and picking some of them apart from the others is a judgement the
            # engine has no basis for. Anything wrong in the list can be edited
            # in the file, which is prose a person owns.
            future.extend(t for t in future_drafts if t not in future)
            future_drafts = []
            continue
        if not line:
            break
        future.append(line)
    return record()


def cmd_done(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Close an item, and take delivery of what it knows on the way out.

    The questions come after the durability check and before the write, so a
    workspace that cannot commit says so without first asking two questions it
    is about to throw away.

    The header comes before both, because it is the only chance to notice that
    the id was mistyped -- and unlike the questions, that mistake cannot be
    taken back.
    """
    path = _echo_target(ws, args.item_id, cat)
    if path is None:
        return EXIT_FAIL
    _gap, problem = _durability_problem(ws)
    if problem is not None:
        _err(problem)
        return EXIT_FAIL

    # Ticking comes first, because it is the thing that makes the question below
    # answerable. Before the write, like everything else here, and skipped
    # entirely when the run is scripted or the answers were given as flags --
    # a non-tty `done` must stay a single non-interactive command.
    # Skipped on exactly the conditions `_ask_closing` skips its questions on:
    # answers already given as flags, or a run with nobody at the keyboard. A
    # scripted `done` must stay one non-interactive command.
    asking = not (getattr(args, "summary", None)
                  or (getattr(args, "future_work", None) or [])
                  or not sys.stdin.isatty())
    # What `-` set aside in THIS run, in the criteria' own words. Carried to the
    # summary draft rather than re-derived from the file, because the file cannot
    # tell a criterion dropped a moment ago from one dropped last month, and only
    # the first is something the person at the keyboard is in a position to
    # explain.
    dropped_now: List[str] = []
    if asking:
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            original = ""
        if original:
            try:
                picked, dropped = _ask_ticks(
                    original, cat, bool(getattr(args, "all_criteria", False)))
            except KeyboardInterrupt:
                print()
                _err(tr(cat, "cli.done.cancelled",
                        "Cancelled. {id} was not closed and nothing was written.",
                        id=args.item_id))
                return EXIT_FAIL
            if picked or dropped:
                # Whole-text transform, the same shape `record_promotion` uses.
                # Line indexes come from the same text they are applied to.
                # One write for both marks: a tick and a drop are one answer to
                # one question, and half of it landing is not a state worth
                # having.
                marks = dict.fromkeys(picked, AC_DONE)
                marks.update(dict.fromkeys(dropped, AC_DROPPED))
                try:
                    write_text(ws, path, _apply_marks(original, marks))
                except OSError as exc:
                    _err("error: cannot write %s: %s" % (path, exc))
                    return EXIT_FAIL
            gone = set(dropped)
            dropped_now = [t for i, _m, t in _ac_lines(original) if i in gone]

    def drafts():
        try:
            fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            return "", [], ""
        return _closing_drafts(ws, fm or {}, body or "", cat, dropped_now,
                               asked=asking)

    try:
        closing = _ask_closing(args, cat, drafts)
    except KeyboardInterrupt:
        # Ctrl-C stops the command, and this is where that is worth saying out
        # loud. The concern behind the old behaviour was real -- an interrupt
        # that just exits leaves the reader unsure whether the close happened --
        # but the answer to that is to SAY nothing happened, not to close the
        # item anyway. Enter is the affordance for skipping the questions, and
        # the prompt says so.
        print()
        _err(tr(cat, "cli.done.cancelled",
                "Cancelled. {id} was not closed and nothing was written.",
                id=args.item_id))
        return EXIT_FAIL

    message = tr(cat, "cli.done.done", "{id} -> done", id=args.item_id)
    if closing is not None and closing.future_work:
        message += "\n" + tr(
            cat, "cli.done.future_hint",
            "{n} follow-up(s) recorded. `nextbrief followup {id}` turns them into "
            "backlog items, each carrying discovered_from: {id}.",
            n=len(closing.future_work), id=args.item_id)

    # human_confirmed rides along: closing an item is the strongest possible
    # statement that it was real and worded the way you meant.
    return _mark(
        ws,
        cat,
        args.item_id,
        {"status": "done", "human_confirmed": True},
        "done",
        message,
        body=None if closing is None else (lambda text: upsert_closing(text, closing)),
    )


def cmd_drop(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Abandon an item. Terminal, confirming, and committed -- so it says which
    one first, for the same reason `done` does."""
    if _echo_target(ws, args.item_id, cat) is None:
        return EXIT_FAIL
    return _mark(
        ws,
        cat,
        args.item_id,
        {"status": "dropped", "human_confirmed": True},
        "drop",
        tr(
            cat,
            "cli.drop.done",
            "{id} -> dropped (the file stays, and so does its git history)",
            id=args.item_id,
        ),
    )


# How long a defer with no date on it may last before the item comes back to be
# looked at again. Repeated from the shipped config for the same reason every
# other default here is: a missing key must not turn a scheduled return into a
# permanent one.
_REVIEW_AFTER_DAYS = 30


def _review_after_days(cfg: Dict[str, Any]) -> int:
    block = cfg.get("defer")
    block = block if isinstance(block, dict) else {}
    try:
        return max(1, int(block.get("review_after_days", _REVIEW_AFTER_DAYS)))
    except (TypeError, ValueError):
        return _REVIEW_AFTER_DAYS


def cmd_defer(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Park an item until a date. It comes back on its own.

    ★ The missing verb. ★

    `done` and `drop` are the only two ways an item could stop being on the page,
    and the most common thing that actually happens to work is neither: it is
    still real, still worth doing, and not now. Recording that as `drop` writes a
    falsehood somebody has to rebuild later; leaving it open keeps it competing
    for the top of a list it cannot win.

    `--until` is required, and that is the whole safety property: **a deferral
    with no return date is a quiet abandonment**, which is the outcome this
    command exists to make impossible. A date is taken as the date. Anything else
    is taken as the condition you are waiting on -- "Fernwood ships" is a
    perfectly good reason and a useless trigger -- and the item is given a review
    date anyway, so the condition is what you read and the date is what brings it
    back.

    Nothing writes the item open again when the day comes; `items.is_live` reads
    the date. A workspace nobody ran for a fortnight still shows everything that
    came due during it.
    """
    item_id = args.item_id
    today = dt.date.today()

    if getattr(args, "cancel", False):
        path = _echo_target(ws, item_id, cat)
        if path is None:
            return EXIT_FAIL
        fm, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
        if str((fm or {}).get("status") or "").lower() != DEFERRED:
            _err(tr(cat, "cli.defer.not_deferred",
                    "{id} is not deferred, so there is nothing to cancel.", id=item_id))
            return EXIT_FAIL
        return _mark(
            ws, cat, item_id,
            {"status": "open", "deferred_until": None,
             "deferred_when": None, "deferred_because": None},
            "undefer",
            tr(cat, "cli.defer.cancelled", "{id} -> open again, no longer deferred.",
               id=item_id),
        )

    until = (getattr(args, "until", None) or "").strip()
    if not until:
        _err(tr(cat, "cli.defer.needs_until",
                "defer needs --until: a date (2026-09-01) or what you are waiting "
                "on (\"after Fernwood ships\"). A deferral that never comes "
                "back is a drop with better manners, so this one is not optional."))
        return EXIT_USAGE

    # After the usage check, which is about the command line rather than the
    # item, and before anything is written. Deferring the wrong item is the
    # quietest of the three mistakes: the item sinks out of the brief on the
    # spot and nothing surfaces again until its date, so there is no later
    # moment at which the error announces itself.
    if _echo_target(ws, item_id, cat) is None:
        return EXIT_FAIL

    fields: Dict[str, Any] = {"status": DEFERRED, "human_confirmed": True,
                              "deferred_when": None}
    if _looks_like_date(until):
        due = dt.date.fromisoformat(until.strip())
        fields["deferred_until"] = due.isoformat()
        message = tr(cat, "cli.defer.until_date",
                     "{id} -> deferred until {date}. It comes back into the brief "
                     "that morning, on its own.", id=item_id, date=due.isoformat())
    else:
        # A condition cannot fire, so it gets a date as well. Both are kept: the
        # condition is what the reader needs to understand the deferral, the date
        # is the only part a machine can act on, and collapsing them would lose
        # whichever one you did not keep.
        days = _review_after_days(_load_config(ws))
        due = today + dt.timedelta(days=days)
        fields["deferred_until"] = due.isoformat()
        fields["deferred_when"] = until
        message = tr(cat, "cli.defer.until_condition",
                     "{id} -> deferred until: {when}. Nothing can watch for that, "
                     "so it also comes back on {date} ({days} days) to be looked "
                     "at again.", id=item_id, when=until, date=due.isoformat(), days=days)

    reason = (getattr(args, "reason", None) or "").strip()
    if reason:
        fields["deferred_because"] = reason
    if due < today:
        _err(tr(cat, "cli.defer.past_date",
                "note: {date} is in the past, so {id} is deferred and immediately "
                "due -- it stays in the brief.", date=due.isoformat(), id=item_id))

    return _mark(ws, cat, item_id, fields, "defer", message)


# ---------------------------------------------------------------------------
# what a closed item left behind
# ---------------------------------------------------------------------------


def _closed_entries(ws: Workspace, project: Optional[str] = None):
    """``(path, frontmatter, closing, dropped)`` for every done item, newest first.

    No new store, which was the constraint and is also the point: a done item's
    file stays in ``backlog/`` forever and is already under version control, so
    the record was never missing a home -- only a reader.

    ``dropped`` is the text of the criteria this item set aside, read off the
    body. It travels with the row rather than being fetched later because it is
    the same kind of fact as the closing record itself -- part of the answer to
    "what became of this" -- and because the file has already been read here.
    """
    rows = []
    for path in sorted(ws.backlog.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = parse_frontmatter(text)
        if not fm or str(fm.get("status") or "").lower() != "done":
            continue
        if project and str(fm.get("project") or "") != project:
            continue
        dropped = [t for _i, mark, t in _ac_lines(body or "") if mark == AC_DROPPED]
        rows.append((path, fm, parse_closing(text), dropped))
    rows.sort(key=lambda r: (str((r[2].closed_on if r[2] else "")
                                 or r[1].get("updated_date") or ""),
                             str(r[1].get("id") or "")), reverse=True)
    return rows


def cmd_closed(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """What each project has finished, and what it left behind.

    Three different things, and the reader has to be able to tell them apart at a
    glance, because they call for three different responses:

        (summary)   what was actually done
        -> / -      work this uncovered, which is somebody's to pick up
        ~           a promise the design moved past, which is nobody's

    The third one had no shape here at all. A criterion set aside would show up
    only if the person happened to have written about it in the summary, so a
    project's history read as though it had always intended exactly what it
    shipped -- and "we changed our minds, here is what we stopped meaning to do"
    is not a footnote to that history, it is most of it. It gets the file's own
    mark rather than a word, so the list and the item agree on sight.
    """
    project = (getattr(args, "project", None) or "").strip() or None
    rows = _closed_entries(ws, project)
    if not rows:
        print(tr(cat, "cli.closed.empty",
                 "Nothing closed yet{scope}.",
                 scope=(" in %s" % project) if project else ""))
        return EXIT_OK

    by_project: Dict[str, List[Any]] = {}
    for row in rows:
        by_project.setdefault(str(row[1].get("project") or "-"), []).append(row)

    no_record = 0
    follow_ups = 0
    unpromoted = 0
    set_aside = 0
    for name in sorted(by_project):
        print()
        print("== %s ==" % name)
        for _path, fm, closing, dropped in by_project[name]:
            when = str((closing.closed_on if closing else "")
                       or fm.get("updated_date") or "")
            print("  %s  %s  %s" % (fm.get("id"), when or "-", fm.get("title") or ""))
            if closing is None or not closing.summary:
                no_record += 1
                print("     " + tr(cat, "cli.closed.no_summary",
                                   "(no closing record)"))
            else:
                body = closing.summary if getattr(args, "full", False) \
                    else closing.summary.split("\n")[0]
                for line in body.split("\n"):
                    print("     " + line)
            for entry in (closing.future_work if closing else []):
                follow_ups += 1
                if entry.promoted_to:
                    print("     -> %s  %s" % (entry.promoted_to, entry.text))
                else:
                    unpromoted += 1
                    print("     -  %s" % entry.text)
            for text in dropped:
                # Never abbreviated by `--full`, and never folded into the
                # follow-ups above it. A dropped criterion is the one line here
                # that nobody is going to act on, and mixing it into the list of
                # things somebody should pick up is the original mistake wearing
                # a different hat.
                set_aside += 1
                print("     %s  %s" % (AC_DROPPED, text))

    print()
    print(tr(cat, "cli.closed.footer",
             "{n} closed item(s); {blank} with no record of what happened; "
             "{fw} piece(s) of future work, {open} still not turned into items.",
             n=len(rows), blank=no_record, fw=follow_ups, open=unpromoted))
    if set_aside:
        # Worded so the number never has to agree with a noun. `t()` has no
        # plural mechanism, and the first line written against it here shipped as
        # "dropped 1 criteria" -- which is a small enough wart to leave and a
        # cheap enough one to not repeat.
        print(tr(cat, "cli.closed.set_aside",
                 "Set aside ({mark}): {n}. Promised, then the design moved past "
                 "them -- nobody is meant to pick these up.",
                 n=set_aside, mark=AC_DROPPED))
    if unpromoted:
        print(tr(cat, "cli.closed.how",
                 "nextbrief followup <id> turns those into backlog items."))
    return EXIT_OK


# How many numbers the allocator will walk past before giving up. Far above any
# real contention -- this workspace has single-digit writers and arguments lasting
# seconds -- and here only so that a directory somebody made unwritable produces a
# sentence rather than a spin.
ALLOC_ATTEMPTS = 50


def _allocate_id(ws: Workspace) -> Optional[str]:
    """Take an id nothing else can also take. ``None`` if the numbers ran out.

    ★ The directory scan proposes; the exclusive create decides. ★

    Reading the working tree for the highest id is right and is not enough: two
    sessions nine hours apart both read a directory whose highest id was NA-0042,
    both concluded NA-0043, and both were correct -- neither had written anything
    down yet, so there was nothing for the other to have seen. Any allocator built
    only out of reads has that gap, however carefully it reads.

    So the number is taken by creating a file named after it, in one syscall that
    cannot half-happen. The loser of that race finds out *at the moment of
    losing* rather than two days later in the brief, and its answer is to step to
    the next number and try again -- a silent duplicate becomes a retry, which is
    the entire change.

    The marker is never removed. An id burned by a run that died between taking
    the number and writing the file is a gap in the numbering, and a gap in the
    numbering costs nothing: ids are names, not a count of anything. Reusing one
    is what costs -- it puts a second file under a name the first has already been
    announced under, which is the failure this exists to prevent, arriving by the
    tidier-looking road.
    """
    known = [str(fm.get("id") or "") for _p, fm in _all_entries(ws)]
    item_id = next_item_id(known, id_shape(known))
    for _attempt in range(ALLOC_ATTEMPTS):
        try:
            won = claim_exclusively(ws, ws.ids / item_id)
        except OSError:
            # No ledger available -- an unwritable or missing `state`. The scan
            # above still refuses every id on disk, so this degrades to the
            # behaviour that shipped, rather than refusing to write down a task.
            return item_id
        if won:
            return item_id
        known.append(item_id)
        item_id = next_item_id(known, item_id)
    return None


def cmd_new(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Open a backlog item, with the next free id assigned rather than eyeballed.

    ★ The point is not the keystrokes. It is that "what is the next id" stops
    being a question a person answers by looking. ★

    Two sessions answered it nine hours apart on the same evening, and both were
    right about what they had seen: the highest id in the directory, plus one.
    Neither had written anything down yet. The result was two files claiming
    NA-0043 -- one of them a P0 -- and every command that takes an id would have
    picked between them silently.

    Two properties, and the second is the one that is easy to leave out:

    * **It reads the working tree, not ``git HEAD``.** An entry that exists but
      is not committed is precisely the one the next person will not see. That
      is not hypothetical here: the same night's brief reported three backlog
      files with no committed version to compare against.
    * **It writes the file in the command that picks the number.** An allocator
      that prints an id and trusts somebody to use it reproduces the original
      failure exactly, only faster -- the gap between deciding and recording is
      the whole bug.

    Reading the directory narrows that window to the width of one command and
    does not close it, so the number itself is taken by **exclusive creation** --
    see ``_allocate_id``. The last step re-reads the directory anyway and refuses
    to commit an id that something else claimed in between, and
    ``nextbrief check`` is the backstop for whatever still gets through.
    """
    # Whitespace-collapsed: the title becomes a filename slug and the first line
    # of `ls`, and a newline pasted into either is a mess in two places.
    title = " ".join(str(getattr(args, "title", "") or "").split())
    if not title:
        _err(tr(cat, "cli.new.no_title",
                "An item needs a title -- it is the sentence you read tomorrow "
                "and decide from."))
        return EXIT_USAGE

    project = str(getattr(args, "project", "") or "").strip()
    reg = _registry(ws)
    known_projects = sorted(
        str(p.get("id")) for p in (reg.get("projects") or [])
        if isinstance(p, dict) and p.get("id")
    )
    # Only when the registry could be read and has projects in it. An
    # unreadable registry is not a reason to refuse to write down a task, and
    # this command has no business being the one that reports it.
    if known_projects and project not in known_projects:
        _err(tr(cat, "cli.new.unknown_project",
                "{project} is not a project here, so the item would never appear "
                "under one. This workspace has: {known}",
                project=project or "-", known=", ".join(known_projects)))
        return EXIT_USAGE

    # Before writing, for the reason `_mark` gives: an uncommitted backlog file
    # is what the write-permission gate reverts, so a workspace that cannot
    # commit should say so instead of minting a file that will not survive.
    gap, problem = _durability_problem(ws)
    if problem is not None:
        _err(problem)
        return EXIT_FAIL
    if gap is not None:
        _err(gap)

    item_id = _allocate_id(ws)
    if item_id is None:
        _err(tr(cat, "cli.new.no_free_id",
                "Gave up looking for a free id after {n} tries. Something else is "
                "minting items as fast as this is; run it again.", n=ALLOC_ATTEMPTS))
        return EXIT_FAIL
    path = ws.backlog / ("%s-%s.md" % (item_id, slug(title)))
    if path.exists():
        _err("error: %s already exists" % path.name)
        return EXIT_FAIL

    today = dt.date.today().isoformat()
    try:
        write_text(ws, path, blank_item_text(item_id, title, project, today))
    except OSError as exc:
        _err("error: cannot write %s: %s" % (path, exc))
        return EXIT_FAIL

    # The window this command narrows but does not close, checked rather than
    # assumed away. The file stays: it holds a sentence somebody just typed, and
    # deleting that to tidy up a numbering problem is the wrong trade. Not
    # committed, though -- an id collision is not something to make durable.
    claimants = [p for p, fm in _all_entries(ws) if str(fm.get("id") or "") == item_id]
    if len(claimants) > 1:
        _err(tr(cat, "cli.new.raced",
                "Something else claimed {id} while this was being written. "
                "{file} was kept but not committed; renumber it by hand:",
                id=item_id, file=path.name))
        for other in claimants:
            _err("  " + other.name)
        return EXIT_FAIL

    if gap is None and not _commit_human(ws, path, "add", item_id):
        return EXIT_FAIL

    print(tr(cat, "cli.new.made", "{id}  {title}", id=item_id, title=title))
    print("  %s" % path.name)
    print()
    print(tr(cat, "cli.new.next",
             "Nothing in it is sized, scoped or agreed yet. `nextbrief show "
             "{id}` to fill it in, `nextbrief do {id}` to start on it.",
             id=item_id))
    return EXIT_OK


def cmd_followup(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Turn a closed item's future work into backlog items of its own.

    ★ Without this, `future_work` is another `proposed_status`. ★

    A list of follow-ups inside a file nobody reopens is a list that decays into
    prose, and the whole reason the field exists is that the alternative -- one
    person remembering -- had already failed. So promotion has to be one command,
    and the new item has to carry ``discovered_from`` back to where it came from:
    that edge is what makes an incidental finding traceable to the work that
    turned it up, rather than a task with no story.

    Listing is the default and promoting takes a flag, because minting backlog
    entries is the kind of thing that should never happen because somebody typed
    a command to have a look.
    """
    path = _find_item(ws, args.item_id, cat)
    if path is None:
        return EXIT_FAIL
    text = path.read_text(encoding="utf-8")
    fm, _body = parse_frontmatter(text)
    closing = parse_closing(text)
    if closing is None or not closing.future_work:
        print(tr(cat, "cli.followup.none",
                 "{id} has no future work recorded.", id=args.item_id))
        return EXIT_OK

    wanted: List[int] = []
    if getattr(args, "all", False):
        wanted = list(range(len(closing.future_work)))
    else:
        for raw in (getattr(args, "promote", None) or []):
            try:
                n = int(raw)
            except (TypeError, ValueError):
                _err(tr(cat, "cli.followup.not_a_number",
                        "{value} is not one of the numbers below.", value=raw))
                return EXIT_USAGE
            if not 1 <= n <= len(closing.future_work):
                _err(tr(cat, "cli.followup.out_of_range",
                        "There is no #{n} -- {id} has {total}.",
                        n=n, id=args.item_id, total=len(closing.future_work)))
                return EXIT_USAGE
            wanted.append(n - 1)

    if not wanted:
        print(tr(cat, "cli.followup.header",
                 "Future work recorded when {id} was closed:", id=args.item_id))
        # The column exists only once there is something in it.
        #
        # It used to print `  .` as a placeholder against the `-> NA-0026` it was
        # aligning to. On a freshly closed item nothing has been promoted, so
        # every row got a lone `.` standing in for a shape the reader had never
        # seen -- a legend legible only to someone who no longer needs it. Worse,
        # `.` already means "unconfirmed" in `nextbrief ls`, where a footer
        # explains it; the same character meaning two things, unexplained in the
        # place it cannot be guessed.
        #
        # So: words rather than a symbol, and only when at least one has been
        # promoted. No mark is itself the information, and the footer's "every
        # one not already promoted" says the rest.
        marks = []
        if any(e.promoted_to for e in closing.future_work):
            marks = [tr(cat, "cli.followup.promoted", "already {id}", id=e.promoted_to)
                     if e.promoted_to
                     else tr(cat, "cli.followup.not_promoted", "not promoted")
                     for e in closing.future_work]
        # `_pad`, not `%-*s`: these strings are localised, and a CJK glyph is two
        # terminal cells wide but one character, so `%-12s` drifts every column
        # to its right. The helper was written for `ls` and this table needed it
        # just as much.
        width = max((_width(m) for m in marks), default=0)
        for i, entry in enumerate(closing.future_work, 1):
            prefix = ("%s  " % _pad(marks[i - 1], width)) if marks else ""
            print("  %d) %s%s" % (i, prefix, entry.text))
        print()
        print(tr(cat, "cli.followup.how",
                 "nextbrief followup {id} --promote <n>  ·  --all for every one "
                 "not already promoted.", id=args.item_id))
        return EXIT_OK

    gap, problem = _durability_problem(ws)
    if problem is not None:
        _err(problem)
        return EXIT_FAIL
    if gap is not None:
        _err(gap)

    today = dt.date.today().isoformat()
    known = [str(fm2.get("id") or "") for _p, fm2 in _all_entries(ws)]

    # Work out the whole plan, say it, and only then write. `--promote` mints
    # files and produces two commits per item, and until now it described what
    # it had done rather than what it was about to do. Same shape as `done`
    # closing an item it never named: a hand-typed command doing something
    # irreversible without showing you first.
    plan: List[Tuple[int, str, str]] = []
    for index in wanted:
        entry = closing.future_work[index]
        if entry.promoted_to:
            print(tr(cat, "cli.followup.already",
                     "#{n} is already {other}; leaving it alone.",
                     n=index + 1, other=entry.promoted_to))
            continue
        # Assigned here rather than in the write loop so the announcement names
        # the ids that will actually be used, instead of promising ids a second
        # pass might renumber.
        new_id = next_item_id(known, str(fm.get("id") or args.item_id))
        known.append(new_id)
        plan.append((index, new_id, entry.text))

    if not plan:
        return EXIT_OK
    project = str(fm.get("project") or "")
    print(tr(cat, "cli.followup.about_to",
             "About to create {n} backlog item(s) in {project}, each carrying "
             "discovered_from: {src}:",
             n=len(plan), project=project or "-",
             src=fm.get("id") or args.item_id))
    # Not `text`: that name holds the item file's contents for the whole
    # function, and the write below rewrites the file from it.
    for _index, new_id, what in plan:
        print("  %s  %s" % (new_id, what))
    print()

    made: List[Tuple[str, str]] = []
    for index, new_id, _what in plan:
        entry = closing.future_work[index]
        new_path = ws.backlog / ("%s-%s.md" % (new_id, slug(entry.text)))
        try:
            write_text(ws, new_path, new_item_text(
                new_id, entry.text, str(fm.get("project") or ""),
                str(fm.get("id") or args.item_id), today, source_note=path.name))
        except OSError as exc:
            _err("error: cannot write %s: %s" % (new_path, exc))
            return EXIT_FAIL
        # The edge is recorded on both sides before the next one is minted, so an
        # interrupted run leaves a backlog that is still true rather than a new
        # item nothing points at.
        text = record_promotion(text, index, new_id)
        try:
            write_text(ws, path, text)
        except OSError as exc:
            _err("error: cannot write %s: %s" % (path, exc))
            return EXIT_FAIL
        made.append((new_id, entry.text))
        if gap is None:
            if not _commit_human(ws, new_path, "add", new_id):
                return EXIT_FAIL
            if not _commit_human(ws, path, "promote %s from" % new_id,
                                 str(fm.get("id") or "")):
                return EXIT_FAIL

    if not made:
        return EXIT_OK
    for new_id, what in made:
        print(tr(cat, "cli.followup.made", "{id}  {title}", id=new_id, title=what))
    print(tr(cat, "cli.followup.made_footer",
             "{n} new item(s), each carrying discovered_from: {src}.",
             n=len(made), src=fm.get("id") or args.item_id))
    return EXIT_OK


def _width(text: str) -> int:
    """Display width in terminal cells, not characters.

    A CJK glyph occupies two cells, so "%-4s" pads a two-character Chinese header
    to four *characters* and six *cells*, and every column to its right drifts.
    Localised headers are the normal case for half this tool's readers, so the
    table has to measure what the terminal will actually draw.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: Any, cells: int) -> str:
    """Left-align `text` in `cells` terminal columns, truncating on cell width."""
    s = str(text)
    if _width(s) <= cells:
        return s + " " * (cells - _width(s))
    out, used = "", 0
    for ch in s:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > cells:
            break
        out += ch
        used += w
    return out + " " * (cells - used)


# Column widths in terminal cells. The title is last and takes what is left.
_LS_COLS = (9, 3, 4, 16, 4)


def _print_rows(rows: Sequence[Tuple[Any, ...]], cat: Optional[Catalog]) -> None:
    headers = (
        tr(cat, "cli.ls.col.id", "id"),
        tr(cat, "cli.ls.col.priority", "P"),
        tr(cat, "cli.ls.col.age", "age"),
        tr(cat, "cli.ls.col.project", "project"),
        tr(cat, "cli.ls.col.confirmed", "ok"),
    )
    # A header wider than its column widens that column rather than shifting the
    # ones after it; the alternative is a table that only lines up in English.
    widths = [max(w, _width(h)) for w, h in zip(_LS_COLS, headers)]
    # Same reasoning for the ids themselves, and here it is not cosmetic: an id is
    # meant to be pasted straight into `nextbrief ok <id>`, and a truncated one is
    # an id that does not exist.
    if rows:
        widths[0] = max(widths[0], max(_width(str(r[2])) for r in rows))

    # No rstrip: the last fixed column's padding is what puts the title header
    # over the title data.
    print(" ".join(_pad(h, w) for h, w in zip(headers, widths))
          + " " + tr(cat, "cli.ls.col.title", "title"))
    print("-" * 92)
    for priority, age, item_id, title, _status, confirmed, project in rows:
        cells = (item_id, priority, age, project, "*" if confirmed else ".")
        print(" ".join(_pad(c, w) for c, w in zip(cells, widths)) + " " + _pad(title, 40).rstrip())


def _parked_entries(ws: Workspace, today: dt.date):
    """Deferred items that are not due yet, soonest first."""
    parked = [(path, fm) for path, fm in _all_entries(ws) if is_parked(fm, today)]
    parked.sort(key=lambda pair: (str(pair[1].get("deferred_until") or ""),
                                  str(pair[1].get("id") or "")))
    return parked


def cmd_ls(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    today = dt.date.today()
    parked = _parked_entries(ws, today)

    if getattr(args, "deferred", False):
        # A separate view rather than a column on the main table. These items are
        # deliberately not on your list today; mixing them in would undo the one
        # thing deferring them accomplished.
        if not parked:
            print(tr(cat, "cli.ls.no_parked", "Nothing is deferred."))
            return EXIT_OK
        for _path, fm in parked:
            days = days_until_due(fm, today)
            print("  %s  %s  %s" % (fm.get("id"), fm.get("deferred_until") or "?",
                                    fm.get("title") or ""))
            detail = []
            if fm.get("deferred_when"):
                detail.append(tr(cat, "cli.ls.parked_when", "waiting on: {what}",
                                 what=fm["deferred_when"]))
            if fm.get("deferred_because"):
                detail.append(tr(cat, "cli.ls.parked_because", "because: {why}",
                                 why=fm["deferred_because"]))
            if days is not None:
                detail.append(tr(cat, "cli.ls.parked_in", "back in {n} day(s)", n=days))
            if detail:
                print("     " + (cat.t("sep.dot") if cat else " | ").join(detail))
        return EXIT_OK

    rows = _open_rows(ws)
    if not rows:
        print(tr(cat, "cli.ls.empty", "Nothing open in the backlog."))
        if parked:
            print(tr(cat, "cli.ls.parked",
                     "{n} item(s) deferred and not due yet -- nextbrief ls --deferred",
                     n=len(parked)))
        return EXIT_OK
    _print_rows(rows, cat)
    unconfirmed = sum(1 for r in rows if not r[5])
    print()
    if parked:
        print(tr(cat, "cli.ls.parked",
                 "{n} item(s) deferred and not due yet -- nextbrief ls --deferred",
                 n=len(parked)))
    if unconfirmed:
        print(
            tr(
                cat,
                "cli.ls.unconfirmed",
                "{n} item(s) not confirmed yet (a '.' in the ok column). Confirming "
                "means: this is real, and written the way you meant it.",
                n=unconfirmed,
            )
        )
        print(
            tr(
                cat,
                "cli.ls.unconfirmed_how",
                "nextbrief ok <id> to confirm  ·  drop <id> to reject  ·  show <id> to read it",
            )
        )
    return EXIT_OK


# The shipped config.jsonc values, repeated here because prune must still answer
# the question when the `decay` block is missing or damaged -- and because both
# defaults are the conservative direction: a missing prefix must not widen
# selection to items a human wrote, and an unreadable window must not shorten it.
_DECAY_AFTER_DAYS = 60
_DECAY_PREFIX = "nextbrief-"


def _decay_rules(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The `decay` block of config.jsonc, normalised."""
    block = cfg.get("decay")
    block = block if isinstance(block, dict) else {}
    requires = block.get("auto_drop_requires")
    requires = requires if isinstance(requires, dict) else {}
    try:
        after = int(block.get("auto_drop_after_days", _DECAY_AFTER_DAYS))
    except (TypeError, ValueError):
        after = _DECAY_AFTER_DAYS
    prefix = requires.get("created_by_prefix")
    return {
        "after_days": max(1, after),
        "prefix": prefix if isinstance(prefix, str) and prefix else _DECAY_PREFIX,
        # Absent means required. Only an explicit `false` turns the evidence
        # condition off, and turning it off can only ever select more items.
        "zero_evidence": requires.get("zero_project_evidence") is not False,
    }


def _project_evidence(ws: Workspace) -> Optional[Dict[str, Any]]:
    """Days since each project's freshest evidence, from the last sense run, or
    ``None`` when there is no readable snapshot.

    This command never walks the projects itself -- that is stage 1's job, and it
    takes seconds. Without a snapshot there is no honest answer to "has this
    project shown any evidence", so prune reports the gap instead of guessing.
    """
    if not ws.snapshot.is_file():
        return None
    try:
        snap = json.loads(ws.snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict):
        return None
    days: Dict[str, Optional[int]] = {}
    for entry in snap.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        evidence = entry.get("evidence")
        value = evidence.get("days_since") if isinstance(evidence, dict) else None
        days[str(entry.get("id"))] = value if isinstance(value, int) else None
    run = snap.get("run")
    return {"days": days, "as_of": str((run or {}).get("as_of_date") or "")}


def _decay_candidates(
    ws: Workspace, cfg: Dict[str, Any]
) -> Tuple[List[Tuple[Row, List[str]]], List[Tuple[Row, List[str]]], Dict[str, Any]]:
    """Split the open backlog into (selected, blocked-on-missing-evidence, rules).

    An item reaches the first list only when every configured condition is met.
    Two of them are the promise the whole feature rests on and are enforced here
    rather than trusted to config: an item a human confirmed, or one a human
    created, is never a candidate.
    """
    rules = _decay_rules(cfg)
    evidence = _project_evidence(ws)
    today = dt.date.today()
    selected: List[Tuple[Row, List[str]]] = []
    unknown: List[Tuple[Row, List[str]]] = []

    for path, fm in _open_entries(ws):
        if fm.get("human_confirmed") is True:
            continue
        created_by = str(fm.get("created_by") or "")
        if not created_by.startswith(rules["prefix"]):
            continue
        age = _age_days(fm, today)
        if age is None or age < rules["after_days"]:
            continue

        why = [
            "created by %s, not by you" % created_by,
            "you have never confirmed it",
            "untouched for %d days (rule: %d or more)" % (age, rules["after_days"]),
        ]
        row = _row(path, fm, today)
        if not rules["zero_evidence"]:
            selected.append((row, why))
            continue

        project = str(fm.get("project") or "")
        if evidence is None or project not in evidence["days"]:
            unknown.append((row, why + ["project evidence unknown"]))
            continue
        days = evidence["days"][project]
        if days is None:
            selected.append((row, why + ["%s has never shown any evidence" % project]))
        elif days >= rules["after_days"]:
            selected.append(
                (row, why + ["%s has shown no evidence for %d days" % (project, days)])
            )
        # Anything else means the project is alive, so the item is not decaying.

    return selected, unknown, rules


def _print_why(pairs: Sequence[Tuple[Row, List[str]]]) -> None:
    """One line per item: the id, then every condition that put it there.

    Selection a reader cannot audit is selection a reader has to trust, and this
    list exists precisely for people who do not want to trust it.
    """
    width = max(_width(row[2]) for row, _why in pairs)
    for row, why in pairs:
        print("  %s  %s" % (_pad(row[2], width), " · ".join(why)))


def cmd_prune(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    selected, unknown, rules = _decay_candidates(ws, _load_config(ws))
    print(
        tr(
            cat,
            "cli.prune.rules",
            "Decay rules: open for {days} days or more · created_by starts with "
            "'{prefix}' · never confirmed by you · the project has shown no evidence "
            "in the same window.",
            days=rules["after_days"],
            prefix=rules["prefix"],
        )
    )

    if selected:
        print()
        _print_rows([row for row, _why in selected], cat)
        print()
        print(tr(cat, "cli.prune.why", "Why each one is here:"))
        _print_why(selected)
    elif not unknown:
        # Only when there is nothing to report at all: with items held back for
        # want of a snapshot, "nothing matches" would read as a verdict on them.
        print()
        print(tr(cat, "cli.prune.none", "Nothing matches those rules."))

    if unknown:
        print()
        print(
            tr(
                cat,
                "cli.prune.no_snapshot",
                "These match every other rule, but project evidence cannot be checked "
                "without state/snapshot.json. Run `nextbrief sense` and ask again.",
            )
        )
        _print_why(unknown)

    print()
    # Say out loud what decay can and cannot do. A user who believes the tool might
    # silently delete their own commitments will stop trusting the whole brief.
    print(
        tr(
            cat,
            "cli.prune.scope",
            "Automatic decay only withdraws an agent's own unconfirmed proposals, and "
            "only when the project has shown no evidence at all for the whole window. "
            "Anything you wrote or confirmed is never dropped for you.",
        )
    )
    if selected or unknown:
        print(
            tr(
                cat,
                "cli.prune.how",
                "Work through them with: nextbrief ok <id>  ·  done <id>  ·  drop <id>",
            )
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# do
# ---------------------------------------------------------------------------


def _session_command(cfg: Dict[str, Any]) -> List[str]:
    """The interactive agent to hand the session to.

    Not the same thing as the stage-2 provider: that one is a batch call with a
    prompt in and text out, this one takes over your terminal.
    """
    env = os.environ.get(ENV_AGENT)
    if env:
        return shlex.split(env)
    session = cfg.get("session")
    if isinstance(session, dict):
        cmd = session.get("command")
        if isinstance(cmd, str) and cmd.strip():
            return shlex.split(cmd)
        if isinstance(cmd, list) and cmd:
            return [str(c) for c in cmd]
    return ["claude"]


def _exec_session(cfg: Dict[str, Any], target: str, prompt: str) -> int:
    cmd = _session_command(cfg)
    try:
        os.chdir(target)
    except OSError as exc:
        _err("error: cannot enter %s: %s" % (target, exc))
        return EXIT_FAIL
    try:
        # exec, not spawn: the agent should own the terminal outright -- tty,
        # signals, exit code -- with no wrapper process left waiting behind it.
        os.execvp(cmd[0], cmd + [prompt])
    except OSError as exc:
        _err("error: cannot run %r (%s)." % (" ".join(cmd), exc))
        _err("  Set %s, or session.command in config.jsonc." % ENV_AGENT)
        _err("  The opening message, so you do not lose it:\n")
        _err(prompt)
        return EXIT_FAIL
    return EXIT_OK  # unreachable; exec does not return


def _who(ws: Workspace) -> str:
    """Whoever is at the keyboard, in the terms this workspace already uses.

    The workspace's own git identity first -- the name ``_identity_problem``
    insists on and every backlog commit is signed with -- so the claim and the
    commits it is about say the same word. Read from ``ws.root`` rather than from
    the current directory, because the commit that carries the claim is made
    there and a per-repository ``user.name`` would otherwise file the claim under
    one name and commit it under another.

    Nothing new is collected about the machine or the person: a claim is meant to
    tell you who to go and ask, and any name you would not recognise fails at
    that.
    """
    rc, name, _ = _git(ws.root, "config", "--get", "user.name")
    if rc == 0 and name.strip():
        return name.strip()
    try:
        import getpass

        return getpass.getuser() or "unknown"
    except Exception:  # pragma: no cover - getuser consults four env vars and pwd
        return "unknown"


def _branch_of(directory: str) -> Optional[str]:
    """The branch ``directory`` is on, or ``None``.

    ``None`` for a detached HEAD as well as for a directory that is not a
    repository. Both are honest: `check` asks "has this branch had any commits",
    and there is no branch here to have had any. Recording the literal string
    ``HEAD`` would give that question an answer that looks like a branch name and
    is not one.

    ``symbolic-ref`` rather than ``rev-parse --abbrev-ref``, because the second
    one resolves HEAD and so fails on a repository with no commits yet -- which
    is a branch, and a very plausible one to start an item on.
    """
    rc, out, _ = _git(Path(directory), "symbolic-ref", "--short", "-q", "HEAD")
    return out.strip() or None if rc == 0 else None


def _record_claim(ws: Workspace, cat: Optional[Catalog], path: Path,
                  item_id: str, target: str) -> None:
    """Write down that this item has been started, and where.

    ★ Never raises and never refuses. ★ The whole argument for a claim being a
    note rather than a lock collapses if failing to write the note can stop the
    work: that would be a lock with an unreliable trigger, which is worse than
    either honest design. So every failure below is reported and stepped over.

    Not committed through ``_mark``/``_commit_human``'s contract either, for the
    same reason in the other direction: those treat a failed commit as a failed
    command because the write-permission gate reverts uncommitted human edits.
    It does not revert this one -- ``status`` and ``claim`` are not on
    ``HUMAN_ONLY_FIELDS`` and ``in_progress`` is not a human-only status -- so an
    uncommitted claim is still a claim, still on disk, and still the thing the
    next reader sees.
    """
    claim = {
        "by": _who(ws),
        "at": dt.date.today().isoformat(),
        "where": target,
        "branch": _branch_of(target),
    }
    try:
        rewrite_fields(ws, path, {"status": IN_PROGRESS,
                                  "updated_date": claim["at"]})
        rewrite_block(ws, path, CLAIM, claim)
    except (OSError, WorkspaceError) as exc:
        _err("warning: " + tr(cat, "cli.do.claim_unwritten",
                              "could not record the claim on {id} ({why}); the "
                              "session is opening anyway.",
                              id=item_id, why=_os_error_line(exc)
                              if isinstance(exc, OSError) else str(exc)))
        return

    print("  " + tr(cat, "cli.do.claimed",
                    "Recorded on {id}: in_progress, claimed by {by} in {where}.",
                    id=item_id, by=claim["by"], where=_tilde(target)))
    if _baseline_gap(ws) is None and not _commit_human(ws, path, "claim", item_id):
        _err("warning: " + tr(cat, "cli.do.claim_uncommitted",
                              "the claim on {id} is written but not committed. It "
                              "still reads from the working tree; it is just not "
                              "in the history yet.", id=item_id))


def _show_existing_claim(claim: Dict[str, Any], text: str,
                         cat: Optional[Catalog]) -> None:
    """Print the claim that is already on the item, verbatim."""
    print("  " + tr(cat, "cli.do.already_claimed",
                    "Somebody has already started on this:"))
    print()
    lines = claim_lines(text)
    if not lines:
        # Only reachable from a file whose claim came from somewhere other than
        # the frontmatter writer. Print the parsed values rather than nothing:
        # this notice exists to make the claim visible, and a blank space where
        # it should be is the failure it was written against.
        lines = ["%s:" % CLAIM] + ["  %s: %s" % (k, claim[k])
                                   for k in CLAIM_KEYS if claim.get(k) is not None]
    for line in lines:
        print("    " + line)
    print()


def cmd_do(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    path = _find_item(ws, args.item_id, cat)
    if path is None:
        return EXIT_FAIL
    try:
        ctx = build_context(ws, path, cat)
    except LaunchError as exc:
        _err("error: %s" % exc)
        return EXIT_FAIL

    cfg = _load_config(ws)
    print()
    print(tr(cat, "cli.do.header", "> {id} · {title}", id=args.item_id, title=ctx.title))
    print("  " + tr(cat, "cli.do.project", "Project: {project}", project=ctx.project))
    print()

    # Before the picker, because it is the one thing that might change your mind
    # about opening a session at all -- and after it would mean asking the
    # question at the moment the answer has stopped being useful.
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    fm, _body = parse_frontmatter(text)
    existing = claim_of(fm or {})
    if existing is not None:
        _show_existing_claim(existing, text, cat)
        # Shown, then asked, and the answer is allowed to be "go ahead". A claim
        # that could refuse would seal shut exactly the items that most need
        # picking up: the ones somebody started and walked away from, which is
        # the only one of these failures that has actually happened.
        if not args.yes:
            print("  " + tr(cat, "cli.do.claim_advisory",
                            "That is a note, not a lock -- nothing here stops you. "
                            "Enter to carry on and take it over  ·  q to leave it "
                            "alone and go and ask."))
            try:
                answer = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print("  " + tr(cat, "cli.do.cancelled", "Cancelled."))
                return EXIT_OK
            if answer.lower() in ("q", "n", "no"):
                print("  " + tr(cat, "cli.do.cancelled", "Cancelled."))
                return EXIT_OK
            print()

    if args.yes:
        target = ctx.cwd
    else:
        print("  " + tr(cat, "cli.do.suggested", "Where should this happen?"))
        for i, (directory, why) in enumerate(ctx.dirs, 1):
            mark = "> " if i == 1 else "  "
            print("  %s%d) %-58s %s" % (mark, i, _tilde(directory), why))
        print()
        print(
            "  "
            + tr(
                cat,
                "cli.do.hint",
                "Enter for the first  ·  a number  ·  or type a path  ·  "
                "p to see the prompt  ·  q to cancel",
            )
        )
        while True:
            try:
                answer = input("  > ").strip()
            except EOFError:
                # A failed read means EOF -- a pipe ran dry, or Ctrl-D. It MUST
                # cancel. Falling back to the suggested directory here would open
                # an agent session in a directory nobody ever agreed to, which is
                # exactly the failure this whole picker exists to prevent.
                print()
                print("  " + tr(cat, "cli.do.eof", "End of input; cancelled."))
                return EXIT_OK
            except KeyboardInterrupt:
                print()
                print("  " + tr(cat, "cli.do.cancelled", "Cancelled."))
                return EXIT_OK

            if answer in ("q", "Q"):
                print("  " + tr(cat, "cli.do.cancelled", "Cancelled."))
                return EXIT_OK
            if answer in ("p", "P"):
                print()
                print("---- " + tr(cat, "cli.do.prompt_header", "session opening message") + " ----")
                print(ctx.prompt)
                print("-" * 40)
                print()
                continue
            if answer == "":
                target = ctx.cwd
                break
            if answer.isdigit():
                index = int(answer)
                if 1 <= index <= len(ctx.dirs):
                    target = ctx.dirs[index - 1][0]
                    break
                print("  " + tr(cat, "cli.do.no_option", "There is no option {n}.", n=answer))
                continue
            chosen = Path(answer).expanduser()
            if not chosen.is_absolute():
                chosen = Path(ctx.root) / chosen  # relative paths resolve against the projects root
            if not chosen.is_dir():
                print("  " + tr(cat, "cli.do.no_dir", "No such directory: {path}", path=str(chosen)))
                continue
            target = str(chosen)
            break

    print()
    # After the last chance to cancel and before the exec, which is the only
    # moment both facts are known: that a session is definitely being opened, and
    # which directory it is being opened in. `_exec_session` does not return.
    _record_claim(ws, cat, path, args.item_id, target)
    print("  " + tr(cat, "cli.do.opening", "Opening a session in {path}", path=_tilde(target)))
    print()
    return _exec_session(cfg, target, ctx.prompt)


def cmd_init(args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    from .init import init_workspace

    return init_workspace(args.directory, yes=args.yes, cat=cat, scan=not args.no_scan,
                          set_default=getattr(args, 'set_default', False))


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def cmd_permissions(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Print the pre-approval rules this workspace needs, or merge them into a
    settings file.

    Exists because the alternative is asking every user to hand-write JSON they
    have no way to verify, in a file where a typo is silent: a malformed rule
    does not error, it simply never matches, and the failure surfaces days later
    as a scheduled run that quietly stopped happening.

    Merging is additive and nothing else. Existing keys, existing rules, key
    order and formatting are preserved; only rules that are absent get appended.
    A settings file holds a person's whole agent configuration, and a tool that
    rewrites more than it was asked to is a tool nobody runs twice.
    """
    from .init import agent_permissions

    wanted = agent_permissions(ws.root)
    target = _opt(args, "merge_into")

    if not target:
        print(json.dumps(wanted, indent=2))
        print()
        print(tr(cat, "perm.hint",
                 "To apply: nextbrief permissions --merge-into <settings.json>\n"
                 "  Workspace-level rules only apply to an agent session rooted at\n"
                 "  {root}. For a scheduler that starts elsewhere, merge into your\n"
                 "  user-level settings instead.", root=str(ws.root)))
        return EXIT_OK

    path = expand(target)
    existing: Dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _err("error: %s is not readable JSON (%s); refusing to touch it." % (path, exc))
            return EXIT_FAIL
        if not isinstance(existing, dict):
            _err("error: %s does not contain a JSON object; refusing to touch it." % path)
            return EXIT_FAIL

    merged = dict(existing)
    perms = dict(merged.get("permissions") or {})
    added: List[str] = []
    for section, rules in wanted["permissions"].items():
        current = list(perms.get(section) or [])
        for rule in rules:
            if rule not in current:
                current.append(rule)
                added.append("%s: %s" % (section, rule))
        perms[section] = current
    merged["permissions"] = perms

    if not added:
        print(tr(cat, "perm.already", "{path} already has every rule; nothing to do.",
                 path=str(path)))
        return EXIT_OK

    if path.is_file():
        # A copy before touching someone's agent configuration. Cheap, and the
        # one moment they would want it is the one moment it would not exist.
        backup = path.with_suffix(path.suffix + ".nextbrief-backup")
        try:
            write_outside_workspace(
                backup, path.read_text(encoding="utf-8"), "permissions:backup")
        except OSError as exc:
            _err("error: cannot write %s (%s); refusing to modify the original." % (backup, exc))
            return EXIT_FAIL

    try:
        write_outside_workspace(
            path,
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            "permissions:merge-into",
        )
    except OSError as exc:
        _err("error: cannot write %s (%s)" % (path, exc))
        return EXIT_FAIL

    print(tr(cat, "perm.added", "Added {n} rule(s) to {path}:", n=len(added), path=str(path)))
    for line in added:
        print("  " + line)
    print(tr(cat, "perm.kept", "Everything already in that file was left exactly as it was."))
    return EXIT_OK



def _editor_command() -> Optional[List[str]]:
    """The editor to open, or None if the environment names none.

    `$VISUAL` before `$EDITOR`, which is the convention every other tool that
    does this follows. No fallback to a guessed binary: opening something the
    reader did not choose, in a terminal, is how people end up trapped in an
    editor they cannot exit.
    """
    for name in ("VISUAL", "EDITOR"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            try:
                parts = shlex.split(raw)
            except ValueError:
                parts = [raw]
            if parts:
                return parts
    return None


def _edit_text(seed: str, suffix: str = ".txt") -> Optional[str]:
    """Open `seed` in the reader's editor and return what came back.

    None when there is no editor, the editor failed, or the text came back
    unchanged -- all three mean "no answers", and none of them is an error worth
    a stack trace.

    The scratch file is a real temporary file rather than something inside the
    workspace: it is not the reader's data and it should not survive a crash as
    though it were.
    """
    command = _editor_command()
    if command is None:
        return None
    import tempfile

    handle, path = tempfile.mkstemp(prefix="nextbrief-review-", suffix=suffix)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(seed)
        try:
            proc = subprocess.run(command + [path])
        except OSError as exc:
            _err("could not start %s: %s" % (command[0], exc))
            return None
        if proc.returncode != 0:
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                edited = fh.read()
        except OSError:
            return None
        return None if edited == seed else edited
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _looks_like_date(text: str) -> bool:
    """A date the renderer can actually use.

    Validated at the point of entry rather than trusted, because a deadline is
    the one answer that changes ranking on its own: the boost keys on days
    remaining, and a string that never parses is a deadline that silently never
    fires. Refusing it here costs one retype; accepting it costs a date nobody
    is warned about.
    """
    try:
        dt.date.fromisoformat(text.strip())
    except (TypeError, ValueError):
        return False
    return True


def cmd_review(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Ask, in one sitting, the questions only a person can answer.

    Everything here is multiple choice. The registry wants three integers per
    project and nobody supplies them, because "impact 4" is an absolute number
    on a scale nobody defined -- unanswerable in the moment and unreadable a
    month later. A consequence is answerable instantly and stays comparable:
    "if this slipped a month, what happens?"

    Effort is never asked. It is measured, and on that axis a guess is worse
    than a count.

    Refuses to ask anything when stdin is not a terminal, and says what it would
    have asked. A scheduled run that blocks on a prompt at 21:30 is a run that
    silently produces nothing, and this command is reachable from a brief.
    """
    snap_path = ws.snapshot
    if not snap_path.is_file():
        _err("error: no snapshot yet -- run `nextbrief sense` first.")
        return EXIT_FAIL
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _err("error: cannot read %s (%s)" % (snap_path, exc))
        return EXIT_FAIL

    # The overlay is applied at sense time, so a snapshot written before the last
    # `review` still shows no answers. Reading it raw meant re-asking what had
    # just been answered, every time, until the next sense.
    from .annotate import apply_annotations, load_annotations
    from .render import self_project_ids

    answered = load_annotations(ws)
    merged = dict(snap)
    merged["projects"] = apply_annotations(
        {"projects": snap.get("projects") or []}, answered)["projects"]
    # `--all` restates everything rather than only what has expired. The routine
    # path is the expiry: an answer older than RESTATE_AFTER_DAYS comes back on
    # its own, so a judgement that drifted is not left standing because nobody
    # remembered to correct it. This flag is for the day you change your mind.
    restate = 0 if getattr(args, "all", False) else None
    targets = needs_annotating(merged, self_project_ids(snap, None, ws),
                               restate_after=restate)
    if not targets:
        print(tr(cat, "review.nothing", "Nothing to ask about -- every active project has an answer."))
        return EXIT_OK

    # The form comes first, and the prompt loop is the fallback rather than the
    # other way round. Four heterogeneous questions across a dozen projects is
    # what a prompt loop handles worst: fixed order, one project visible at a
    # time, no way back, and a free-text date made as awkward as a menu.
    if getattr(args, "web", False):
        from .webform import collect

        raw = collect(targets, cat)
        if raw is None:
            print(tr(cat, "review.nothing_filled",
                     "Nothing filled in, so nothing was recorded."))
            return EXIT_OK
        # Through the same coercion the editor form uses, so two input paths
        # cannot disagree about what a valid answer is.
        by_field = {q.field: q for q in QUESTIONS}
        answers: Dict[str, Any] = {}
        for pid, fields in raw.items():
            got: Dict[str, Any] = {}
            for field, value in fields.items():
                q = by_field.get(field)
                if q is None:
                    continue
                parsed = coerce_answer(q, value)
                if parsed is not None:
                    store_answer(got, q, parsed, cat)
            if got:
                answers[pid] = got
        n = record_answers(ws, answers)
        print(tr(cat, "review.saved", "Recorded {n} project(s) in {path}.",
                 n=n, path=ANNOTATIONS_NAME) if n
              else tr(cat, "review.nothing_filled",
                      "Nothing filled in, so nothing was recorded."))
        return EXIT_OK

    if not getattr(args, "prompt", False):
        seed = render_review_form(targets, cat)
        edited = _edit_text(seed)
        if edited is not None:
            answers = parse_review_form(edited, known={str(p.get("id")) for p in targets})
            n = record_answers(ws, answers)
            print(tr(cat, "review.saved", "Recorded {n} project(s) in {path}.",
                     n=n, path=ANNOTATIONS_NAME) if n
                  else tr(cat, "review.nothing_filled",
                          "Nothing filled in, so nothing was recorded."))
            return EXIT_OK
        if _editor_command() is None and sys.stdin.isatty():
            print(tr(cat, "review.no_editor",
                     "No $EDITOR set, so asking here instead."))

    if not sys.stdin.isatty():
        print(tr(cat, "review.not_a_tty",
                 "Not an interactive terminal, so nothing was asked. It would ask about: {names}",
                 names=", ".join(str(p.get("id")) for p in targets)))
        return EXIT_OK

    answers: Dict[str, Any] = {}
    for proj in targets:
        pid = str(proj.get("id"))
        print("")
        print("== %s ==" % (proj.get("name") or pid))
        got: Dict[str, Any] = {}
        for q in QUESTIONS:
            if current_answer(proj, q) is not None:
                continue
            print("")
            print("   " + tr(cat, q.key, q.key))
            if q.kind == "date":
                typed = input("     > ").strip()
                if not typed:
                    continue
                if not _looks_like_date(typed):
                    print("     " + tr(cat, "review.bad_date",
                                       "Not a date I can read -- skipping."))
                    continue
                value = typed
            else:
                for i, (_v, key) in enumerate(q.choices, 1):
                    print("     %d) %s" % (i, tr(cat, key, key)))
                picked = _ask_choice(len(q.choices), tr(cat, "review.skip", "Enter to skip"))
                if picked is None:
                    continue
                value = q.choices[picked - 1][0]
            store_answer(got, q, value, cat)
        # Only record a project the user actually answered something for.
        if got:
            answers[pid] = got

    n = record_answers(ws, answers)
    if n:
        print("")
        print(tr(cat, "review.saved", "Recorded {n} project(s) in {path}.",
                 n=n, path=ANNOTATIONS_NAME))
    return EXIT_OK


def _ask_choice(count: int, skip_hint: str) -> Optional[int]:
    """Read a 1..count choice. Anything else, including EOF, means skip.

    Ctrl-C is not "anything else" -- it propagates, and the review saves nothing.
    """
    try:
        raw = input("   [1-%d, %s] " % (count, skip_hint)).strip()
    except EOFError:
        # Same rule. Ctrl-C partway through a review used to SAVE the answers
        # given so far, because skipping and stopping were the same branch.
        return None
    if not raw.isdigit():
        return None
    n = int(raw)
    return n if 1 <= n <= count else None



def cmd_projects(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """One line per project, straight from the snapshot.

    `ls` lists backlog items and there was nothing that listed projects, so the
    only way to see the portfolio was to render a whole brief and read the table
    inside it. That was tolerable while the registry was the project list. It
    stopped being tolerable when discovery started adopting directories on its
    own: the set can now change without anyone editing anything, and "what is
    the tool actually watching?" became a question with no cheap answer.

    Reads the snapshot and prints. No model, no render, no writes.
    """
    if not ws.snapshot.is_file():
        _err(tr(cat, "cli.projects.empty", "No snapshot yet -- run `nextbrief sense` first."))
        return EXIT_FAIL
    try:
        snap = json.loads(ws.snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _err("error: cannot read %s (%s)" % (ws.snapshot, exc))
        return EXIT_FAIL

    from .render import self_project_ids

    own = self_project_ids(snap, None, ws)
    rows = []
    for p in snap.get("projects") or []:
        ev = p.get("evidence") or {}
        days = ev.get("days_since")
        rows.append((
            str(p.get("id") or ""),
            str(ev.get("signal") or "?"),
            "-" if days is None else str(days),
            str(ev.get("best_kind") or "-"),
            str(p.get("status") or "-"),
            str(p.get("name") or p.get("id") or ""),
            bool(p.get("declared", True)),
        ))
    if not rows:
        print(tr(cat, "cli.projects.empty", "No projects."))
        return EXIT_OK

    heads = tuple(tr(cat, "cli.projects.head." + k, k)
                  for k in ("id", "signal", "days", "evidence", "status", "name"))
    widths = [max(_width(r[i]) for r in rows + [heads]) for i in range(6)]
    print("  ".join(_pad(h, w) for h, w in zip(heads, widths)))
    print("-" * (sum(widths) + 12))
    for r in rows:
        line = "  ".join(_pad(r[i], widths[i]) for i in range(6))
        if not r[6]:
            line += "  " + tr(cat, "cli.projects.undeclared", "(not in the registry)")
        print(line)

    unanswered = len(needs_annotating(snap, own))
    print("")
    print(tr(cat, "cli.projects.footer",
             "{n} project(s); {undeclared} discovered, {unanswered} still unanswered.",
             n=len(rows), undeclared=sum(1 for r in rows if not r[6]),
             unanswered=unanswered))
    return EXIT_OK



def cmd_context(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """What the portfolio *is*, for whoever needs to know without walking it.

    The digest answers what moved; this answers what exists. Different question,
    different consumer, and a tenth the size -- an artifact that costs as much to
    read as re-deriving it would have saved nobody anything.

    `--json` prints the file verbatim, because the point is that another agent
    can consume it rather than parse a table meant for a person.
    """
    path = ws.state / INVENTORY_NAME
    if not path.is_file():
        _err(tr(cat, "cli.context.empty", "No inventory yet -- run `nextbrief sense` first."))
        return EXIT_FAIL
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        _err("error: cannot read %s (%s)" % (path, exc))
        return EXIT_FAIL

    if getattr(args, "json", False):
        sys.stdout.write(raw if raw.endswith("\n") else raw + "\n")
        return EXIT_OK

    projects = data.get("projects") or []
    absent = 0
    for e in projects:
        d = e.get("description") or {}
        kind, what, src = d.get("kind"), d.get("what"), d.get("source")
        print("")
        print("%s  (%s)" % (e.get("name") or e.get("id"), e.get("id")))
        if what:
            # The label is the point: a reader must be able to tell a sentence
            # the owner typed from one lifted out of a manifest.
            label = tr(cat, "cli.context." + str(kind), str(kind))
            print("  %s   [%s: %s]" % (what, label, src))
        else:
            absent += 1
            print("  %s" % tr(cat, "cli.context.absent", "no description anywhere"))
        # A second statement, kept visibly separate rather than folded into the
        # description, because it answers a different question and carries a
        # different warrant. The description may have been lifted out of a
        # manifest; this is always somebody's judgement about what the thing
        # built here could become, and a reader deciding whether to reuse it
        # rather than rebuild it needs to know which of the two they are reading.
        # No `[declared: registry]` tag: `capability` has exactly one provenance,
        # so a label distinguishing it from nothing is noise.
        # The text is interpolated by the catalogue rather than concatenated here,
        # so each language owns its own spacing: a space after the full-width
        # colon Chinese uses is a typographic error, and an English colon without
        # one is too.
        cap = (e.get("capability") or {}).get("what")
        if cap:
            print("  %s" % tr(cat, "cli.context.capability",
                              "could also serve: {what}", what=cap))
        bits = []
        if e.get("stacks"):
            bits.append("/".join(e["stacks"]))
        if e.get("needs"):
            bits.append("needs " + ", ".join(e["needs"]))
        if e.get("unlocks"):
            bits.append("unlocks " + ", ".join(e["unlocks"]))
        if e.get("serves"):
            bits.append("serves " + ", ".join(e["serves"]))
        if bits:
            print("  " + cat.t("sep.dot").join(bits) if cat else "  " + " | ".join(bits))
        for r in (e.get("run") or [])[:4]:
            print("    $ %s" % r)

    print("")
    print(tr(cat, "cli.context.footer",
             "{n} project(s); {absent} with no description.",
             n=len(projects), absent=absent))
    return EXIT_OK



def cmd_describe(ws, args, cat):
    """Say what a project is, in one sentence, without opening a JSON file.

    Descriptions had no path in. `review` captures answers to fixed questions,
    but a description is free text and cannot be multiple choice, so the only way
    to supply one was to hand-edit `registry.jsonc` -- exactly the friction the
    overlay exists to remove.

    Written to `annotations.jsonc`, never the registry, same rule as everything
    else this tool captures. A `description` typed into the registry by hand
    still wins, because opening your own file is the more deliberate act.
    """
    pid = (getattr(args, "id", None) or "").strip()
    text = (getattr(args, "text", None) or "").strip()
    cap = getattr(args, "capability", None)
    if not pid:
        _err(tr(cat, "cli.describe.missing", "Which project, and what is it?"))
        return EXIT_USAGE

    # Refuse an id that is not a project rather than recording a description for
    # something nothing will ever read. The snapshot is the list of what exists.
    if ws.snapshot.is_file():
        try:
            snap = json.loads(ws.snapshot.read_text(encoding="utf-8"))
            known = {str(p.get("id")) for p in (snap.get("projects") or [])}
        except (OSError, ValueError):
            known = set()
        if known and pid not in known:
            _err(tr(cat, "cli.describe.unknown", "No project called {id}.", id=pid))
            return EXIT_FAIL

    # Only touch what was actually supplied. `describe id --capability "..."`
    # must not blank the description as a side effect of not repeating it.
    fields = {}
    if text or getattr(args, "text", None) is not None:
        fields["description"] = text
    if cap is not None:
        fields["capability"] = cap.strip()
    if not fields:
        _err(tr(cat, "cli.describe.missing", "Which project, and what is it?"))
        return EXIT_USAGE

    record_answers(ws, {pid: fields})
    if fields.get("description"):
        print(tr(cat, "cli.describe.saved", "{id}: {what}",
                 id=pid, what=fields["description"]))
    elif "description" in fields:
        print(tr(cat, "cli.describe.cleared", "{id}: description removed.", id=pid))
    if fields.get("capability"):
        print(tr(cat, "cli.describe.capability", "{id} can also: {what}",
                 id=pid, what=fields["capability"]))
    print(tr(cat, "cli.describe.where", "Recorded in {path}.", path=ANNOTATIONS_NAME))
    return EXIT_OK


def cmd_probe(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    """Sample the declared external probes. **The only command that goes online.**

    Deliberately explicit, and deliberately not part of `run`. A probe answers
    "is this actually true?", which is a question you ask at specific moments --
    before closing an item, before calling a project stalled, when you want to
    know where something really stands. Wiring it into the nightly schedule would
    answer it 365 times a year unasked, and would pay for that with three
    properties worth more than the number: a brief that fails when somebody
    else's site is down, local noise every time that site is redesigned, and a
    daily outbound request from a tool whose whole pitch is that it reads only
    your disk.

    Writes `state/probes.json` and nothing else. `sense` reads that file.
    """
    from . import probe as probe_mod

    try:
        reg = load_jsonc(ws.registry_path)
    except (JSONCError, OSError) as exc:
        _err("error: cannot read %s (%s)" % (ws.registry_path, exc))
        return EXIT_FAIL

    problems: List[dict] = []
    specs = probe_mod.probes_of(reg, problems)
    for bad in problems:
        _err("warning: %s: %s" % (bad.get("path"), bad.get("why")))

    wanted = list(getattr(args, "project", None) or [])
    if wanted:
        unknown = [p for p in wanted if p not in specs]
        if unknown:
            _err(tr(cat, "cli.probe.unknown",
                    "no probe declared for: {ids}", ids=", ".join(sorted(unknown))))
            # Usage, not failure: the id may be a typo, or the project may simply
            # have no `evidence_probe`. Either way nothing was sampled.
            return EXIT_USAGE
        specs = {k: v for k, v in specs.items() if k in wanted}

    if not specs:
        print(tr(cat, "cli.probe.none",
                 "No project declares `evidence_probe`. Nothing to sample."))
        return EXIT_OK

    cfg = _load_config(ws)
    pcfg = cfg.get("probe") or {}
    timeout = getattr(args, "timeout", None) or pcfg.get("timeout_seconds") \
        or probe_mod.DEFAULT_TIMEOUT

    # Printed before the first request, not after the last: these are outbound
    # requests from a tool that otherwise makes none, so the URLs being touched
    # are named where the person who typed the command can see them.
    for pid in sorted(specs):
        print(tr(cat, "cli.probe.fetching", "→ {id}  {url}", id=pid, url=specs[pid]["url"]))

    cache = probe_mod.load_cache(ws.probes)
    cache, results = probe_mod.run_probes(specs, cache, timeout=float(timeout))

    ws.ensure_dirs()
    write_text(ws, ws.probes, json.dumps(cache, ensure_ascii=False, indent=2,
                                         sort_keys=True) + "\n", skip_identical=False)

    failed = 0
    for entry in results:
        if entry.get("ok"):
            bits = []
            if entry.get("count") is not None:
                bits.append(str(entry["count"]) + (
                    " " + entry["label"] if entry.get("label") else ""))
            if entry.get("date"):
                bits.append(tr(cat, "cli.probe.newest", "newest {date}", date=entry["date"]))
            print("  %s  %s" % (tr(cat, "cli.probe.ok", "ok"),
                                " · ".join(bits) or tr(cat, "cli.probe.empty", "(no fields)")))
        else:
            failed += 1
            print("  %s  %s: %s" % (tr(cat, "cli.probe.failed", "FAILED"),
                                    entry.get("error_code"), entry.get("error_detail") or ""))
            aged = entry.get("last_ok") or {}
            if aged.get("sampled_at"):
                print("    " + tr(cat, "cli.probe.keeping",
                                  "keeping the reading from {at}", at=aged["sampled_at"]))

    print("")
    print(tr(cat, "cli.probe.footer",
             "{n} probe(s), {failed} failed. Written to {path}; `sense` reads it from there.",
             n=len(results), failed=failed, path="state/probes.json"))
    # A failed probe is a fact about the world that the brief will report, not a
    # broken command -- so this exits 0. Exiting non-zero would make the shell
    # treat "that site is down" as "nextbrief is broken", and a `&&` chain would
    # stop on it.
    return EXIT_OK


_HANDLERS = {
    "run": cmd_run,
    "probe": cmd_probe,
    "v0": cmd_v0,
    "sense": cmd_sense,
    "render": cmd_render,
    "check": cmd_check,
    "open": cmd_open,
    "brief": cmd_brief,
    "log": cmd_log,
    "do": cmd_do,
    "show": cmd_show,
    "ok": cmd_ok,
    "done": cmd_done,
    "drop": cmd_drop,
    "defer": cmd_defer,
    "new": cmd_new,
    "followup": cmd_followup,
    "closed": cmd_closed,
    "ls": cmd_ls,
    "prune": cmd_prune,
    "permissions": cmd_permissions,
    "review": cmd_review,
    "projects": cmd_projects,
    "context": cmd_context,
    "describe": cmd_describe,
}


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS so that `nextbrief --workspace X ls` is not undone by the `ls`
    # subparser re-declaring the same flag and defaulting it back to None.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--workspace", metavar="DIR", default=argparse.SUPPRESS,
        help="workspace to use (default: $NEXTBRIEF_WORKSPACE, the pointer written by init, or the nearest registry.jsonc)",
    )
    common.add_argument(
        "--out", metavar="DIR", default=argparse.SUPPRESS,
        help="where generated files go (default: the workspace itself)",
    )
    common.add_argument(
        "--locale", metavar="LANG", default=argparse.SUPPRESS,
        help="language for rendered output, e.g. en or zh",
    )

    ap = argparse.ArgumentParser(
        prog="nextbrief",
        parents=[common],
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # The composed build string, not the bare release constant: an editable
    # install and the wheel on PATH print the same three digits otherwise, and
    # the documented way to tell them apart was to grep both installs for a
    # function name.
    ap.add_argument("--version", action="version", version="nextbrief %s" % build_version())
    # SUPPRESS, because argparse would otherwise print its own list of the same
    # twenty subcommands underneath the hand-written one in the description --
    # the same information twice, in two different orders and two different
    # levels of detail. The written list wins: it groups the commands by what
    # you are trying to do, which the alphabetical machine version cannot.
    sub = ap.add_subparsers(dest="command", metavar="<command>", help=argparse.SUPPRESS)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        # `help` is deliberately not forwarded; see the SUPPRESS note above.
        return sub.add_parser(name, parents=[common])

    # The four pipeline commands document the stage flags they are most often
    # given, but forward whatever was typed verbatim (see `_stage_args`), so a
    # flag a stage grows later needs no change here.
    p = add("run", "all three stages")
    p.add_argument("extra", nargs="*", help="passed through to render")

    p = add("v0", "sense + render, no model")
    p.add_argument("extra", nargs="*", help="passed through to render")

    p = add("sense", "stage 1 only")
    p.add_argument("--check", action="store_true", help="exit 3 if the output would change")
    p.add_argument("--stdout", action="store_true", help="print instead of writing files")
    # Declared, though `_stage_args` forwards them from the raw argv either way:
    # a flag the README documents and `--help` does not mention reads as a flag
    # that was removed.
    p.add_argument("--as-of", dest="as_of", metavar="ISO",
                   help="pin the run date (YYYY-MM-DD or a full ISO timestamp)")
    p.add_argument("--timing", action="store_true", help="print phase timings to stderr")
    p.add_argument("extra", nargs="*", help=argparse.SUPPRESS)

    p = add("render", "stage 3 only")
    p.add_argument("--no-notify", action="store_true", help="do not send a notification")
    p.add_argument("--dry-run", action="store_true", help="print BRIEF.md, write nothing")
    p.add_argument("extra", nargs="*", help=argparse.SUPPRESS)

    add("check", "self-check over sense and render; exit 3 means out of date")
    add("open", "open BRIEF.html in a browser")
    add("brief", "print BRIEF.md")

    p = add("log", "show the last few runs")
    p.add_argument("-n", "--lines", type=int, default=5, metavar="N", help="how many (default 5)")

    p = add("do", "open an agent session for one item")
    p.add_argument("item_id", metavar="<id>")
    p.add_argument("-y", "--yes", action="store_true", help="use the suggested directory without asking")

    for name, help_text in (
        ("show", "print one item in full"),
        ("ok", "confirm an item"),
        ("drop", "drop an item"),
    ):
        p = add(name, help_text)
        p.add_argument("item_id", metavar="<id>")

    p = add("done", "close an item and record what happened")
    p.add_argument("item_id", metavar="<id>")
    p.add_argument("--summary", metavar="TEXT",
                   help="what actually happened, especially where it differed from the item")
    p.add_argument("--future-work", dest="future_work", metavar="TEXT", action="append",
                   help="something this turned up that does not belong to it; repeatable")
    p.add_argument("--all-criteria", dest="all_criteria", action="store_true",
                   help="ask about every criterion in one list, rather than yours first and the rest after")

    p = add("defer", "park an item until a date")
    p.add_argument("item_id", metavar="<id>")
    p.add_argument("--until", metavar="DATE|TEXT",
                   help="a date (2026-09-01), or what you are waiting on")
    p.add_argument("--reason", metavar="TEXT", help="why it is being put off")
    p.add_argument("--cancel", action="store_true",
                   help="bring it back now instead of on its date")

    p = add("new", "open an item, taking the next free id")
    p.add_argument("title", metavar="<title>", help="the sentence you will read tomorrow")
    # Required rather than guessed. An item filed under a project that does not
    # exist never appears under one in the brief, and that failure is silent.
    p.add_argument("--project", metavar="ID", required=True,
                   help="project id, as `nextbrief projects` lists it")

    p = add("followup", "promote a closed item's future work")
    p.add_argument("item_id", metavar="<id>")
    p.add_argument("--promote", metavar="N", action="append",
                   help="turn entry N into a backlog item; repeatable")
    p.add_argument("--all", action="store_true", help="promote every entry not already promoted")

    p = add("closed", "what each project finished, and what it left behind")
    p.add_argument("project", nargs="?", help="one project id, as `nextbrief projects` lists it")
    p.add_argument("--full", action="store_true", help="print whole summaries, not first lines")

    p = add("ls", "list open items")
    p.add_argument("--deferred", action="store_true",
                   help="list what is parked and when it comes back, instead")
    add("prune", "list items worth revisiting")

    p = add("probe", "sample declared external URLs (the only command that goes online)")
    p.add_argument("project", nargs="*",
                   help="project ids to sample; default every project that declares one")
    p.add_argument("--timeout", type=float, metavar="SECONDS",
                   help="per-request timeout (default %g)" % 10.0)

    add("projects", "one line per project")
    p = add("context", "what each project is, for other tools")
    p.add_argument("--json", action="store_true",
                   help="print state/inventory.json verbatim")

    p = add("describe", "say what a project is, in one sentence")
    p.add_argument("id", nargs="?", help="project id, as `nextbrief projects` lists it")
    p.add_argument("text", nargs="?", default=None, help="one sentence; empty string clears it")
    p.add_argument("--capability", metavar="TEXT",
                   help="what the thing built here could also serve, beyond its current use")
    p = add("review", "answer the questions only you can answer")
    p.add_argument("--all", action="store_true",
                   help="restate every answer, not only the ones that have expired")
    p.add_argument("--prompt", action="store_true",
                   help="ask in the terminal instead of opening an editor")
    p.add_argument("--web", action="store_true",
                   help="answer in a browser form instead (loopback only)")

    p = add("permissions", "print or install the pre-approval rules an agent needs")
    p.add_argument("--merge-into", metavar="FILE",
                   help="merge the rules into this settings.json, preserving everything else")

    p = add("init", "create a workspace")
    p.add_argument("directory", nargs="?", help="where to create it (default: here)")
    p.add_argument("-y", "--yes", action="store_true", help="adopt every discovered project without asking")
    p.add_argument("--no-scan", action="store_true", help="do not look for nearby projects")
    p.add_argument("--set-default", action="store_true",
                   help="make this the default workspace even if another one already is")

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    args, unknown = parser.parse_known_args(raw)

    if not args.command:
        parser.print_help()
        return EXIT_OK

    # The pipeline stages own their own options and may grow more, so whatever
    # follows those subcommands is forwarded untouched. Anywhere else an unknown
    # flag is a typo, and a typo that is silently ignored is a bug you find much
    # later, in the form of an option that never took effect.
    if args.command in PASSTHROUGH:
        args.extra = _stage_args(raw, args.command)
    elif unknown:
        parser.error("unrecognized arguments: %s" % " ".join(unknown))

    locale = _opt(args, "locale")
    try:
        cat = load_catalog(locale)
    except (OSError, ValueError) as exc:
        # Fail open. The nightly run is the one that matters, and a catalog that
        # cannot be read is a packaging problem, not a reason to produce nothing:
        # every string in this module carries its English original anyway.
        _err("warning: no locale catalog (%s); falling back to English" % exc)
        cat = None

    if args.command == "init":
        # Refuse rather than reinterpret. `--workspace` means "the existing
        # workspace to operate on" for every other command, and silently
        # redefining it as "where to create one" for this command alone would
        # trade a visible failure for an invisible one. Accepting it and reading
        # nothing was the worst of the three: `nextbrief --workspace /tmp/safe
        # init -y --no-scan` scaffolded a whole workspace into the current
        # directory instead, and argparse's silence read as consent.
        stray = [name for name in _INIT_REFUSES if _opt(args, name) is not None]
        if stray:
            parser.error(
                "%s cannot be used with init, which creates a workspace rather "
                "than operating on one. Say where to create it as the argument: "
                "nextbrief init DIR" % " and ".join("--" + n for n in stray)
            )
        return cmd_init(args, cat)

    try:
        ws = resolve_workspace(_opt(args, "workspace"), _opt(args, "out"))
    except WorkspaceError as exc:
        _err("error: %s" % exc)
        return EXIT_USAGE

    # The catalog above could only see the flag and the environment, because a
    # workspace had not been resolved yet and config.jsonc lives inside one.
    # Reload once it can: otherwise a workspace that sets "locale": "zh" still
    # gets English from every command that does not go through a stage.
    if not locale and not os.environ.get(ENV_LOCALE):
        configured = _load_config(ws).get("locale")
        if configured:
            try:
                cat = load_catalog(configured)
                locale = str(configured)
            except (OSError, ValueError) as exc:
                _err("warning: locale %r from config is unusable (%s)" % (configured, exc))

    _export_env(ws, locale)
    try:
        return _HANDLERS[args.command](ws, args, cat)
    except KeyboardInterrupt:
        _err("")
        return EXIT_FAIL
    except BrokenPipeError:
        # `nextbrief brief | head` is a normal thing to do and must not traceback.
        # Listed before OSError, which it is a subclass of.
        return EXIT_OK
    except OSError as exc:
        # An unreadable file, a vanished directory, a full disk: operational
        # failures, not defects in this program. A traceback buries the only fact
        # the reader can act on -- which path failed -- under a call stack that
        # concerns nobody but a maintainer.
        _err("error: %s" % _os_error_line(exc))
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
