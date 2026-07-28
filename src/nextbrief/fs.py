"""Every filesystem mutation the engine performs, in one place.

The engine's central promise is that it *reads* a portfolio and *writes* a
workspace. Until this module existed that promise was kept by two helpers in the
renderer and by nothing at all in the sensing stage, the CLI, or the frontmatter
writer -- so it held for the code that happened to import the right function and
lapsed everywhere else. A guarantee with three-quarters coverage is not a
guarantee, it is a habit, and habits are what a refactor six months from now
quietly breaks.

So every write, append, rewrite, rename and delete in this package goes through
here, and each one asks the same question before touching anything: is the target
inside the resolved workspace? A caller cannot forget to check, because there is
no unchecked function left to reach for. Writing anywhere else means naming
yourself in :data:`ESCAPES`, which is a short hand-maintained list and is meant to
be conspicuous in a diff.

Two gates live in this file.

**Containment.** Nothing outside ``Workspace.root`` or ``Workspace.out`` may be
created, modified or removed. This is what makes it safe to point the engine at a
directory full of unrelated repositories: the neighbours are input, and the engine
is *structurally* unable to treat them as output. It is not a rule someone has to
remember. It is a precondition on every mutating call.

**Human-only paths.** Deletion is the one edit with no undo and no next-morning
correction. An entry an agent may not mark ``done`` is certainly one it may not
unlink -- deleting is how you close something *without leaving the evidence that
you closed it*, which is strictly worse than the terminal status gate 3 already
refuses. The registry and the config are protected for a different reason: they
declare what the engine is allowed to look at, so a run that could delete them
could widen its own scope.

Both gates raise. That is deliberate, and it is the one place this module departs
from the package's usual fail-open posture: a parser that cannot read a file
should record the gap and continue, but a write aimed outside the workspace is not
a gap in the input, it is a bug in the caller, and continuing would mean the bug
lands on someone's disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .frontmatter import rewrite_fields as _rewrite_fields_unchecked
from .paths import CONFIG_NAME, REGISTRY_NAME, Workspace, WorkspaceError

__all__ = [
    "ESCAPES",
    "ProtectedPathError",
    "append_jsonl",
    "append_text",
    "ensure_dir",
    "human_only",
    "remove",
    "replace",
    "rewrite_fields",
    "write_outside_workspace",
    "write_text",
]


class ProtectedPathError(WorkspaceError):
    """The target is inside the workspace, but is not the engine's to remove.

    A subclass rather than a separate exception so that a caller who wants to
    treat "outside the workspace" and "human-only" alike -- most of them do --
    can still catch :class:`WorkspaceError` and get both.
    """


# ---------------------------------------------------------------------------
# containment
# ---------------------------------------------------------------------------


def _require_inside(ws: Workspace, path: Path, verb: str) -> None:
    """Raise unless ``path`` is inside the workspace.

    The message names the workspace as well as the target. "Refusing to write
    outside the workspace" on its own sends the reader looking for a bug in the
    path they passed, when the answer is nearly always that the workspace
    resolved somewhere they did not expect.
    """
    if not ws.contains(path):
        raise WorkspaceError(
            "refusing to %s outside the workspace: %s\n"
            "  The workspace is %s (resolved from %s).\n"
            "  Everything outside it is input, and the engine does not write to input."
            % (verb, path, ws.root, ws.source or "the default search")
        )


def human_only(ws: Workspace, path) -> bool:
    """True for the paths only a person may delete or rename.

    Backlog entries, the registry and the config. The list is deliberately short:
    a protection that covers everything is one that gets switched off the first
    time it is inconvenient.
    """
    p = Path(path)
    try:
        resolved = p.resolve()
    except OSError:
        # Unresolvable means we cannot prove it is safe, and this predicate is
        # only ever consulted to decide whether to refuse.
        return True

    for reserved in (ws.registry_path, ws.config_path):
        try:
            if resolved == reserved.resolve():
                return True
        except OSError:
            continue

    try:
        resolved.relative_to(ws.backlog.resolve())
        return True
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


def write_text(ws: Workspace, path, text: str, skip_identical: bool = True) -> bool:
    """Write ``text`` to ``path``. Returns True if the file changed.

    ``skip_identical`` keeps mtimes stable when the content has not moved,
    without which a re-run over an unchanged tree looks like fresh activity to
    the next sensing pass. The sensing stage itself passes ``False``: its own
    ``--check`` compares content, and rewriting unconditionally is the behaviour
    its determinism test was written against.
    """
    p = Path(path)
    _require_inside(ws, p, "write")
    if skip_identical:
        try:
            if p.read_text(encoding="utf-8") == text:
                return False
        except (OSError, UnicodeDecodeError):
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return True


def append_text(ws: Workspace, path, text: str) -> bool:
    """Append to a log file. Returns False if the append did not happen.

    Note the split posture, which is the whole reason this is not one function
    with the containment check bolted on: a target outside the workspace *raises*,
    because that is a caller bug, while an ``OSError`` on a legitimate target is
    swallowed, because losing a log line is never a reason to abandon a run that
    has real output to produce.
    """
    p = Path(path)
    _require_inside(ws, p, "append to")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(str(p), "a", encoding="utf-8") as fh:
            fh.write(text)
        return True
    except OSError:
        return False


def append_jsonl(ws: Workspace, path, obj) -> bool:
    """One JSON object, one line. ``sort_keys`` so a diff of the log is readable."""
    return append_text(ws, path, json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def rewrite_fields(ws: Workspace, path, fields) -> bool:
    """Rewrite frontmatter scalars in place, inside the workspace only.

    The unchecked writer in :mod:`nextbrief.frontmatter` stays private to this
    module. It is the function the write-permission gate uses to revert an
    illegal edit, which makes it the single most dangerous call in the package:
    it is aimed at a path that came out of a file an agent just wrote.
    """
    p = Path(path)
    _require_inside(ws, p, "rewrite")
    return _rewrite_fields_unchecked(p, fields)


def ensure_dir(ws: Workspace, path) -> None:
    """``mkdir -p`` inside the workspace."""
    p = Path(path)
    _require_inside(ws, p, "create")
    p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# the delete gate
# ---------------------------------------------------------------------------


def remove(ws: Workspace, path, missing_ok: bool = True) -> bool:
    """Delete one file inside the workspace. Returns True if it was there.

    Refuses three things, in this order: anything outside the workspace, anything
    on the human-only list, and directories. The last is not timidity -- a
    recursive delete has no failure mode that is merely inconvenient, and nothing
    in this engine has ever needed one. If something does, it should arrive with
    its own gate and its own tests rather than as a flag on this one.
    """
    p = Path(path)
    _require_inside(ws, p, "delete")
    if human_only(ws, p):
        raise ProtectedPathError(
            "refusing to delete %s.\n"
            "  Backlog entries, %s and %s are human-only. Nothing automated may "
            "close an item, and deleting one closes it without leaving the record "
            "that it was closed." % (p, REGISTRY_NAME, CONFIG_NAME)
        )
    if p.is_dir():
        raise ProtectedPathError(
            "refusing to delete the directory %s: this gate removes files only." % p
        )
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        if missing_ok:
            return False
        raise


def replace(ws: Workspace, src, dst) -> None:
    """Rename inside the workspace. Both ends are checked, because a rename is a
    delete of the destination and a delete of the source's old name."""
    source, target = Path(src), Path(dst)
    _require_inside(ws, source, "rename")
    _require_inside(ws, target, "rename onto")
    for candidate in (source, target):
        if human_only(ws, candidate):
            raise ProtectedPathError(
                "refusing to rename %s: backlog entries, %s and %s are human-only."
                % (candidate, REGISTRY_NAME, CONFIG_NAME)
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(source), str(target))


# ---------------------------------------------------------------------------
# the declared exits
# ---------------------------------------------------------------------------

# Every reason the engine is allowed to write outside a workspace. Adding an
# entry is the review checkpoint: it should be hard to do by accident, obvious in
# a diff, and accompanied by a sentence explaining why no workspace can hold the
# file. Three entries is not a target to grow towards.
ESCAPES = {
    "init:pointer":
        "The pointer under the config home recording which workspace bare "
        "commands act on. It is by definition outside every workspace, since its "
        "job is to name one.",
    "permissions:merge-into":
        "The agent settings file named by `nextbrief permissions --merge-into`. "
        "The command exists to edit a file the agent reads, and that file lives "
        "with the agent rather than with the workspace.",
    "permissions:backup":
        "The .nextbrief-backup copy written beside that settings file before it "
        "is modified, so the one moment its owner wants a copy is not the one "
        "moment there is none.",
}


def write_outside_workspace(path, text: str, reason: str) -> bool:
    """The only door out. Every caller names itself, and every name is in ESCAPES.

    ``reason`` is checked against the list rather than merely recorded, so a new
    exit cannot be opened at the call site. It has to be opened here, in a
    dictionary a reviewer reads in full, next to the two or three that already
    earned their place.
    """
    if reason not in ESCAPES:
        raise WorkspaceError(
            "%r is not a declared reason to write outside a workspace.\n"
            "  Declared: %s\n"
            "  Opening a new exit is deliberate: add it to nextbrief.fs.ESCAPES "
            "with a sentence saying why no workspace can hold this file."
            % (reason, ", ".join(sorted(ESCAPES)))
        )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return True
