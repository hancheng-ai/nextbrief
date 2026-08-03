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

from . import __version__, resources
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
from .fs import rewrite_fields, write_outside_workspace, write_text
from .i18n import Catalog, load_catalog
from .inventory import INVENTORY_NAME
from .jsonc import JSONCError, load_jsonc
from .launch import LaunchError, build_context, tr
from .paths import ENV_OUT, ENV_WORKSPACE, Workspace, WorkspaceError, expand, resolve_workspace

__all__ = ["main", "build_parser"]

ENV_LOCALE = "NEXTBRIEF_LOCALE"
ENV_AGENT = "NEXTBRIEF_AGENT"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

OPEN_STATUSES = ("open", "waiting", "in_progress")

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

  do <id>      open an agent session in the right directory, context already loaded
  show <id>    print one item in full
  ok <id>      confirm an item: it is real, and written the way you meant it
  done <id>    mark it done
  drop <id>    drop it (the file stays, and so does its git history)
  ls           list every open item
  prune        list items worth revisiting, with what to do about them

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
    """Locate the backlog file whose frontmatter ``id`` matches, or explain."""
    for path in sorted(ws.backlog.glob("*.md")):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm and str(fm.get("id") or "") == item_id:
            return path
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


def _mark(
    ws: Workspace,
    cat: Optional[Catalog],
    item_id: str,
    fields: Dict[str, Any],
    action: str,
    message: str,
) -> int:
    path = _find_item(ws, item_id, cat)
    if path is None:
        return EXIT_FAIL

    # Order matters: check that the edit can be made durable before making it. A
    # written-but-uncommitted field is reverted by the next run, so writing first
    # and discovering the problem afterwards destroys the user's own action.
    gap = _baseline_gap(ws)
    if gap is None:
        problem = _identity_problem(ws)
        if problem is not None:
            _err(problem)
            return EXIT_FAIL

    fields = dict(fields)
    fields["updated_date"] = dt.date.today().isoformat()
    try:
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


def _open_entries(ws: Workspace) -> List[Tuple[Path, Dict[str, Any]]]:
    """Frontmatter of every item that is still live, in filename order."""
    entries: List[Tuple[Path, Dict[str, Any]]] = []
    for path in sorted(ws.backlog.glob("*.md")):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not fm:
            continue
        if str(fm.get("status") or "") not in OPEN_STATUSES:
            continue
        entries.append((path, fm))
    return entries


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
    """
    rc = _run_sense(["--check"])
    if rc != EXIT_OK:
        return rc
    return _run_render(["--check", "--no-notify"])


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


def cmd_run(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
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
    path = _find_item(ws, args.item_id, cat)
    if path is None:
        return EXIT_FAIL
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return EXIT_OK


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


def cmd_done(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    # human_confirmed rides along: closing an item is the strongest possible
    # statement that it was real and worded the way you meant.
    return _mark(
        ws,
        cat,
        args.item_id,
        {"status": "done", "human_confirmed": True},
        "done",
        tr(cat, "cli.done.done", "{id} -> done", id=args.item_id),
    )


def cmd_drop(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
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


def cmd_ls(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    rows = _open_rows(ws)
    if not rows:
        print(tr(cat, "cli.ls.empty", "Nothing open in the backlog."))
        return EXIT_OK
    _print_rows(rows, cat)
    unconfirmed = sum(1 for r in rows if not r[5])
    print()
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
    """Read a 1..count choice. Anything else, including EOF, means skip."""
    try:
        raw = input("   [1-%d, %s] " % (count, skip_hint)).strip()
    except (EOFError, KeyboardInterrupt):
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


_HANDLERS = {
    "run": cmd_run,
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
    ap.add_argument("--version", action="version", version="nextbrief %s" % __version__)
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
        ("done", "mark an item done"),
        ("drop", "drop an item"),
    ):
        p = add(name, help_text)
        p.add_argument("item_id", metavar="<id>")

    add("ls", "list open items")
    add("prune", "list items worth revisiting")

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
