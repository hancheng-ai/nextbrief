"""Assemble the launch context for ``nextbrief do <id>``.

The premise: **you should not have to re-explain a task to an agent just to start
working on it.** The backlog entry already carries the briefing -- what an agent
can take over, what only you can do, the cheapest probe that would settle the
question, where the item came from, what "done" means. This module turns that
into one opening message, and works out *where* the session should be opened.

Two deliberate choices carried over from the original:

* The session is interactive, never ``-p``. These tasks touch real files, and you
  should be at the keyboard when they do.
* Directories are *proposed*, never chosen. The list is ordered most-likely-first
  and a human picks; see ``cli`` for the picker and for why EOF must cancel.

The original printed shell assignments for ``eval`` to consume. Returning a
dataclass instead keeps quoting bugs impossible and leaves the CLI as the only
component that has to know how to talk to a human.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .frontmatter import parse_frontmatter
from .i18n import Catalog, load_catalog
from .items import (
    AC_DONE,
    AC_DROPPED,
    AC_OPEN,
    AC_YOU,
    HUMAN_ONLY_STATUSES,
    ac_lines,
    needs_you,
)
from .jsonc import JSONCError, load_jsonc
from .paths import Workspace, expand

__all__ = ["LaunchContext", "LaunchError", "build_context", "tr"]

GIT_TIMEOUT_SECONDS = 10


class LaunchError(RuntimeError):
    """The item or the registry could not be read well enough to launch."""


def tr(cat: Optional[Catalog], key: str, default: str, **kwargs: Any) -> str:
    """Translate ``key``, falling back to the English string at the call site.

    ``Catalog.t`` renders an unknown key as the key itself. That is right for a
    rendered brief -- a missing string is loud instead of fatal -- but unreadable
    in an interactive prompt, where ``cli.do.hint`` tells a human nothing. Here a
    catalog that has not caught up with a new string degrades to English.

    Lives in this module rather than in ``cli`` because ``cli`` imports ``launch``
    and not the other way round; a shared helper module for six lines would be
    worse than this.
    """
    if cat is not None and cat.has(key):
        return cat.t(key, **kwargs)
    if not kwargs:
        return default
    try:
        return default.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return default


@dataclass(frozen=True)
class LaunchContext:
    """Everything ``nextbrief do`` needs, with no formatting decisions baked in.

    ``dirs`` is ``[(path, reason)]``, most-likely-first; ``cwd`` is the same as
    ``dirs[0][0]`` and exists so a ``--yes`` run never has to index into a list.
    ``root`` is the *projects* root from the registry, which is what a relative
    path typed at the picker resolves against.
    """

    cwd: str
    title: str
    project: str
    root: str
    dirs: List[Tuple[str, str]]
    prompt: str


def _project_entry(reg: Dict[str, Any], project_id: Any) -> Optional[Dict[str, Any]]:
    for pr in reg.get("projects") or []:
        if isinstance(pr, dict) and pr.get("id") == project_id:
            return pr
    return None


def _git_toplevel(start: str) -> Optional[str]:
    """The enclosing repository root, or None. Never raises: a missing git, a
    directory that is not a repo and a hung filesystem are all "no answer"."""
    try:
        proc = subprocess.run(
            ["git", "-C", start, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", "replace").strip()
    return out or None


def build_context(ws: Workspace, item_path, cat: Optional[Catalog] = None) -> LaunchContext:
    """Build the launch context for one backlog file.

    ``cat`` is optional so that callers which already loaded a catalog do not load
    a second one; the default keeps the two-argument signature usable.
    """
    cat = cat if cat is not None else load_catalog()
    path = Path(item_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LaunchError("cannot read %s: %s" % (path, exc)) from exc

    fm, body = parse_frontmatter(text)
    if not fm:
        raise LaunchError("no readable frontmatter in %s" % path)

    try:
        reg = load_jsonc(ws.registry_path)
    except JSONCError as exc:
        raise LaunchError(str(exc)) from exc

    declared_root = ((reg.get("defaults") or {}).get("root")) or str(ws.root)
    root = expand(declared_root)
    if not root.is_absolute():
        # A relative `defaults.root` ("./projects", as the shipped example
        # declares) is relative to the workspace, never to the directory the
        # command happened to be typed in. Resolving it anywhere else proposes
        # directories that do not exist and lands the session in the wrong tree.
        root = ws.root / root
    proj = _project_entry(reg, fm.get("project"))
    project_name = str((proj or {}).get("name") or fm.get("project") or "")

    # Candidates, ordered "most likely to be right". A human decides; this only
    # lays the options out, with the reason each one is on the list.
    cands: List[Tuple[str, str]] = []

    def add(candidate, why: str) -> None:
        p = Path(candidate)
        if p.is_dir() and not any(c[0] == str(p) for c in cands):
            cands.append((str(p), why))

    for rel in (proj or {}).get("paths") or []:
        add(root / str(rel), tr(cat, "launch.reason.project_dir", "project directory"))

    # The source document's directory is very often where the work actually lives:
    # an item filed under one project ("give the archive repo a git history") can
    # have all of its work sitting in a different tree entirely.
    src = fm.get("source") if isinstance(fm.get("source"), dict) else {}
    src_doc = (src or {}).get("doc")
    if src_doc:
        sp = root / str(src_doc)
        add(
            sp.parent if sp.suffix else sp,
            tr(cat, "launch.reason.source_doc", "directory the item came from"),
        )

    # The repository root, which differs from the project directory whenever repos
    # are nested.
    if cands:
        top = _git_toplevel(cands[0][0])
        if top:
            add(top, tr(cat, "launch.reason.git_root", "git repository root"))

    add(root, tr(cat, "launch.reason.workspace_root", "workspace root"))
    cwd = cands[0][0] if cands else str(root)

    auto = fm.get("automation") if isinstance(fm.get("automation"), dict) else {}
    lines: List[str] = []
    lines.append(
        tr(
            cat,
            "launch.prompt.intro",
            "I am working on backlog item **{id}: {title}** (project: {project}).",
            id=fm.get("id"),
            title=fm.get("title"),
            project=project_name,
        )
    )
    lines.append("")
    lines.append(
        tr(
            cat,
            "launch.prompt.read_item",
            "The full entry is in `{path}`. Read it first.",
            path=str(path),
        )
    )
    lines.append("")
    if (auto or {}).get("what_agent_can_do"):
        lines.append(
            tr(
                cat,
                "launch.prompt.agent_can_do",
                "**What you can do**: {text}",
                text=auto["what_agent_can_do"],
            )
        )
    if (auto or {}).get("what_needs_human"):
        lines.append(
            tr(
                cat,
                "launch.prompt.needs_human",
                "**What I have to do myself**: {text} -- do not do these for me; "
                "stop and tell me when you reach one.",
                text=auto["what_needs_human"],
            )
        )
    if (auto or {}).get("next_probe"):
        lines.append(
            tr(
                cat,
                "launch.prompt.next_probe",
                "**Cheapest first step**: {text}",
                text=auto["next_probe"],
            )
        )
    if (src or {}).get("doc"):
        anchor = ("  " + str(src["anchor"])) if (src or {}).get("anchor") else ""
        stale = ""
        if (src or {}).get("source_last_updated_declared"):
            stale = tr(
                cat,
                "launch.prompt.source_stale",
                " (the source document claims it was last updated {date}, so it may "
                "already be out of date -- check before acting on it)",
                date=src["source_last_updated_declared"],
            )
        lines.append(
            tr(
                cat,
                "launch.prompt.source",
                "**Came from**: `{doc}`{anchor}{stale}",
                doc=src["doc"],
                anchor=anchor,
                stale=stale,
            )
        )

    # Acceptance criteria are whatever `items.ac_lines` says they are. Copied
    # verbatim: they are the definition of done a human wrote, and paraphrasing
    # them would be a way of quietly moving the goalposts.
    #
    # ★ Selected by the shared parser, not by a scan of our own. ★
    #
    # This read its own checkbox lines out of the whole body until NA-0051, which
    # is the second copy `ac_lines` says in its docstring must not exist -- and it
    # cost exactly what that docstring predicts. A line of NOTES *quoting* what a
    # criterion looks like arrived here under "**Done when**", which is not a
    # miscount in a report somebody skims: it is a sentence about criteria handed
    # to an agent as part of the definition of done. Of the three readers that
    # had drifted, this is the expensive one.
    #
    # Except for the ones marked `[~]`, which are the definition of done a human
    # wrote and then withdrew. Listed under "Done when", such a line is an
    # instruction to go and do it -- and `~` reads as "in progress" to anyone who
    # does not know this convention, which is most readers of this prompt. That
    # would be the very failure the mark was added to stop: work being started on
    # a goal somebody deliberately abandoned, one step earlier in the chain than
    # the follow-up drafts where it was caught the first time.
    #
    # The mark comes from the parser too, rather than being re-read off the line
    # here. The old split was equivalent -- a `- [~]` line can never string-equal
    # a `- [ ]` one, so membership was safe -- but it re-derived the mark in a
    # fourth place, and one of the two spellings was going to drift eventually.
    raw = body.splitlines()
    marked = [(mark, text, raw[i].strip()) for i, mark, text in ac_lines(body)]
    set_aside = [ln for mark, _text, ln in marked if mark == AC_DROPPED]
    settled = [ln for mark, _text, ln in marked if mark == AC_DONE]
    still_open = [(text, ln) for mark, text, ln in marked if mark == AC_OPEN]
    # ★ Split by the same predicate `done` asks its questions with, not by a
    # second reading of the marker. ★ See `items.needs_you`: unmarked counts as
    # the human's, which here is the difference between a criterion nobody has
    # classified and a box ticked by something nobody told it owned.
    mine = [ln for text, ln in still_open if needs_you(text)]
    yours = [ln for text, ln in still_open if not needs_you(text)]

    # ★ Three lists, because "which of these already hold" is a question the
    # engine can answer and the reader was being asked to answer again. ★
    #
    # One list titled "Done when" carrying every mark at once put the work of
    # separating settled from open onto the agent, every session, from marks it
    # had to re-parse -- and the cost was not the parsing. An item is routinely
    # older than the work: criteria come true while nobody is looking, and a
    # session that cannot see which ones starts by redoing them. The engine
    # already knows the mark and already knows the owner; withholding both and
    # printing a flat list is this repository's characteristic bug, a fact
    # collected and then given no consumer.
    if settled:
        lines.append("")
        lines.append(tr(cat, "launch.prompt.settled",
                        "**Already settled** -- ticked when somebody checked them, "
                        "with what they ran recorded in NOTES. Do not redo these, "
                        "and do not untick them:"))
        lines.extend(settled)
    if yours:
        lines.append("")
        lines.append(tr(cat, "launch.prompt.acceptance",
                        "**Done when** -- still open, and yours to settle:"))
        lines.extend(yours)
    if mine:
        # Shown, never folded into the list above. An agent that cannot see a
        # criterion cannot tell me it looks done -- and that report is worth
        # having. What it may not do is tick it, which is why the heading says so
        # rather than the paragraph below saying it about criteria it can no
        # longer point at.
        lines.append("")
        lines.append(tr(cat, "launch.prompt.acceptance_mine",
                        "**Done when -- but mine, not yours** -- marked ({you}), or "
                        "carrying no marker at all, which means nobody has said yet. "
                        "Check them if you can and tell me what you found. **Never "
                        "tick one of these**, however plainly it holds:", you=AC_YOU))
        lines.extend(mine)
    if still_open:
        # ★ The boundary is stated where the ticking is asked for, and nowhere
        # else. ★ It is two sentences at the point of use rather than a rule in
        # a document the session may never open -- and the half of it that can
        # be enforced by structure already has been, one heading up.
        lines.append("")
        lines.append(tr(
            cat,
            "launch.prompt.settlement",
            "**Settle what you can before you start, not on the way out.** This "
            "entry can be older than the work, so some of what is still open may "
            "already hold. Go through the open criteria once, first thing. Tick "
            "one only if you ran something and saw the answer, and write into "
            "NOTES what you ran and what you saw -- the output, not the "
            "conclusion, because a tick with nothing under it is a false "
            "completion in a smaller box. One you cannot check stays open with "
            "the reason beside it: \"I could not verify this\" is an answer, "
            "\"probably done\" is not. And never write `status: {statuses}` -- "
            "taking an item off the page stays mine.",
            statuses="/".join(HUMAN_ONLY_STATUSES),
        ))
    if set_aside:
        # Kept, rather than dropped from the prompt. The sentence is the record
        # that the goal moved, and an agent that cannot see it will propose the
        # abandoned thing back as a good idea.
        lines.append("")
        lines.append(tr(cat, "launch.prompt.set_aside",
                        "**No longer applies** -- the design moved past these. "
                        "Do not work on them, and do not propose them back:"))
        lines.extend(set_aside)

    lines.append("")
    lines.append(
        tr(
            cat,
            "launch.prompt.rules",
            "Ground rules: credentials, OAuth consent, publishing or sending anything, "
            "and writes to shared or remote systems all need my go-ahead first. When "
            "you are done, tell me whether this should be closed -- I do the closing "
            "myself (`nextbrief done {id}`).",
            id=fm.get("id"),
        )
    )

    return LaunchContext(
        cwd=cwd,
        title=str(fm.get("title") or ""),
        project=project_name,
        root=str(root),
        dirs=cands,
        prompt="\n".join(lines),
    )
