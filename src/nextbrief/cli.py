"""The ``nextbrief`` command line.

This replaces a zsh script, and the rewrite fixed a class of bug rather than a
bug: the original was zsh-only in ways that were invisible until it ran somewhere
else (``bash -n`` rejected it outright -- ``<->``, the numeric glob used to detect
a menu selection, is not bash syntax), it shelled out to BSD-only ``sed -i ''``,
and it used macOS ``open``. Under argparse, ``webbrowser`` and
``frontmatter.rewrite_fields`` none of those portability questions exist.

What was preserved on purpose:

* ``ok`` / ``done`` / ``drop`` commit immediately -- see ``_commit_human``.
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
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .frontmatter import parse_frontmatter, rewrite_fields
from .i18n import Catalog, load_catalog
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

EPILOG = """\
commands:
  run          all three stages: sense -> a model reads it -> render
  v0           sense + render only, no model at all: zero tokens, nothing to invent
  sense        stage 1 only; refresh state/snapshot.json
  render       stage 3 only; re-render from the existing brief.json
  check        idempotence self-check; exit code 3 means the brief is out of date

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

  init [dir]   create a workspace and get to a first brief
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace").strip(),
        proc.stderr.decode("utf-8", "replace").strip(),
    )


def _commit_human(ws: Workspace, path: Path, action: str, item_id: str) -> bool:
    """Commit a human's own edit immediately. This is not bookkeeping.

    The write-permission gate diffs backlog files against ``git HEAD``. If your
    ``done`` is sitting uncommitted in the working tree, the gate cannot tell
    "the human closed this item" from "an agent quietly wrote status: done", and
    it will revert *your* action. Committing makes your edit the new baseline.

    Identity comes from the user's own git config; there is no fallback identity,
    because a package that commits under someone else's name is worse than one
    that refuses to commit. A refusal here is reported but does not fail the
    command: the field was already changed, and re-running would be a no-op, so
    exiting non-zero would only make the state harder to reason about.
    """
    if not (ws.root / ".git").exists():
        rc, top, _ = _git(ws.root, "rev-parse", "--show-toplevel")
        if rc != 0 or not top:
            _err(
                "note: %s is not a git repository, so this change has no baseline.\n"
                "  The write-permission gate needs one: run `git init && git add -A && "
                "git commit` here." % ws.root
            )
            return False

    # Re-running `done` on an item that is already done changes nothing, and a
    # commit attempt would only produce a scary "nothing to commit" warning about
    # a file that is already the baseline.
    rc, dirty, _ = _git(ws.root, "status", "--porcelain", "--", str(path))
    if rc == 0 and not dirty:
        return True

    rc, email, _ = _git(ws.root, "config", "user.email")
    if rc != 0 or not email:
        _err(
            "error: git has no user.email, so your change was written but not committed.\n"
            "  Set one and commit it yourself, or the write-permission gate may revert it:\n"
            '    git config --global user.email "you@example.com"\n'
            '    git config --global user.name "Your Name"\n'
            "    git -C %s commit -m 'backlog: %s %s' -- %s" % (ws.root, action, item_id, path)
        )
        return False

    _git(ws.root, "add", "--", str(path))
    rc, _, err = _git(ws.root, "commit", "-q", "-m", "backlog: %s %s" % (action, item_id), "--", str(path))
    if rc != 0:
        _err("warning: could not commit %s: %s" % (path.name, err or "git returned %d" % rc))
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
    fields = dict(fields)
    fields["updated_date"] = dt.date.today().isoformat()
    try:
        rewrite_fields(path, fields)
    except OSError as exc:
        _err("error: cannot write %s: %s" % (path, exc))
        return EXIT_FAIL
    _commit_human(ws, path, action, item_id)
    print(message)
    return EXIT_OK


def _open_rows(ws: Workspace) -> List[Tuple[int, int, str, str, str, bool, str]]:
    """Every item that is still live, as sortable tuples."""
    today = dt.date.today()
    rows: List[Tuple[int, int, str, str, str, bool, str]] = []
    for path in sorted(ws.backlog.glob("*.md")):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not fm:
            continue
        status = str(fm.get("status") or "")
        if status not in OPEN_STATUSES:
            continue
        try:
            age = (today - dt.date.fromisoformat(str(fm.get("updated_date")))).days
        except (TypeError, ValueError):
            age = 0
        try:
            priority = int(fm.get("priority"))
        except (TypeError, ValueError):
            priority = 9
        rows.append(
            (
                priority,
                age,
                str(fm.get("id") or path.stem),
                str(fm.get("title") or ""),
                status,
                fm.get("human_confirmed") is True,
                str(fm.get("project") or ""),
            )
        )
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
    # Exit code 3 is the contract: "the brief no longer matches reality". Anything
    # scheduling nextbrief can branch on it without parsing output.
    return _run_sense(["--check"])


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
    for base in (ws.prompts, Path(__file__).resolve().parent / "prompts"):
        for name in names:
            candidate = base / name
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            return text.replace("{workspace_root}", str(ws.root)).replace(
                "{projects_root}", _projects_root(ws)
            )
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
        ws.state.mkdir(parents=True, exist_ok=True)
        ws.brief_json.write_text(text + "\n", encoding="utf-8")
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


def _print_rows(rows: Sequence[Tuple[Any, ...]], cat: Optional[Catalog]) -> None:
    head = "%-9s %-3s %-4s %-16s %-4s %s" % (
        tr(cat, "cli.ls.col.id", "id"),
        tr(cat, "cli.ls.col.priority", "P"),
        tr(cat, "cli.ls.col.age", "age"),
        tr(cat, "cli.ls.col.project", "project"),
        tr(cat, "cli.ls.col.confirmed", "ok"),
        tr(cat, "cli.ls.col.title", "title"),
    )
    print(head)
    print("-" * 92)
    for priority, age, item_id, title, _status, confirmed, project in rows:
        print(
            "%-9s %-3s %-4s %-16s %-4s %s"
            % (item_id, priority, age, project[:15], "*" if confirmed else ".", title[:40])
        )


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


def cmd_prune(ws: Workspace, args: argparse.Namespace, cat: Optional[Catalog]) -> int:
    rows = _open_rows(ws)
    if not rows:
        print(tr(cat, "cli.ls.empty", "Nothing open in the backlog."))
        return EXIT_OK
    _print_rows(rows, cat)
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

    return init_workspace(args.directory, yes=args.yes, cat=cat, scan=not args.no_scan)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

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
        description="A daily brief across every project you own, where every claim is checked against evidence before it prints.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version="nextbrief %s" % __version__)
    sub = ap.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help_text)

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
    p.add_argument("extra", nargs="*", help=argparse.SUPPRESS)

    p = add("render", "stage 3 only")
    p.add_argument("--no-notify", action="store_true", help="do not send a notification")
    p.add_argument("--dry-run", action="store_true", help="print BRIEF.md, write nothing")
    p.add_argument("extra", nargs="*", help=argparse.SUPPRESS)

    add("check", "idempotence self-check; exit 3 means out of date")
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

    p = add("init", "create a workspace")
    p.add_argument("directory", nargs="?", help="where to create it (default: here)")
    p.add_argument("-y", "--yes", action="store_true", help="adopt every discovered project without asking")
    p.add_argument("--no-scan", action="store_true", help="do not look for nearby projects")

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

    _export_env(ws, locale)
    try:
        return _HANDLERS[args.command](ws, args, cat)
    except KeyboardInterrupt:
        _err("")
        return EXIT_FAIL
    except BrokenPipeError:
        # `nextbrief brief | head` is a normal thing to do and must not traceback.
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
