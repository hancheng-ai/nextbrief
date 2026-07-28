"""Projects the registry did not have to name.

``defaults.root`` is a directory you keep ongoing work in, not a window onto the
disk. Read that way, the registry stops being the list of what *exists* and
becomes the list of what you have said something *about* -- a tier, a goal, a
deadline, a privacy rule. Anything sitting in the root is a project by virtue of
being there.

The alternative, which is what this package did until now, is that a directory
stays invisible until someone declares it. That fails in exactly one way, and it
fails quietly and permanently: you start something new, you never get round to
editing the registry, and every morning the brief reports confidently on
everything except the thing you have actually been doing all week. There is no
error, no empty section, no dropped-claim count -- nothing to notice. A portfolio
with a hole in it is indistinguishable from a calm week.

So discovery adopts rather than offers. What it will not do is invent the human
half. A discovered project states **no** tier, **no** ICE and no goal -- not
neutral values, no values, because those are judgements and the engine does not
have them. It carries ``declared: false`` into the snapshot so anything downstream
can tell an absence from a choice.

The distinction is not pedantry. A synthesised midpoint is an assertion, and the
renderer reads assertions: ``tier`` in ``("flagship", "active")`` is the entry
condition for the *neglected* and *stalled* verdicts, so a placeholder tier makes
the engine invent an importance and then, a month later, report the consequences
of its own invention back to the user as a finding.

Four things are never adopted, and between them they are why pointing this at a
real home directory does not produce nonsense:

* anything the registry already claims -- ``projects``, ``watch``, ``infra``,
  ``archived`` -- matched on the first path segment, so a project declared as
  ``atlas/apps/site`` still claims ``atlas``
* anything in ``ignored``, which until now was read only by ``init`` and did
  nothing at all for a daily run
* dotfile directories and the build-output and home-folder names in
  :data:`SKIP_NAMES`
* the workspace itself, and this package's own source checkout
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .paths import Workspace

__all__ = [
    "DISCOVERED_ICE",
    "DISCOVERED_TIER",
    "DOC_PATTERNS",
    "SKIP_NAMES",
    "claimed_segments",
    "discover",
    "is_engine_checkout",
]

# Directories that are never a project of yours. Kept deliberately short: this is
# a coarse filter in front of a decision the root's contents have already made,
# not a classifier.
SKIP_NAMES = {
    "node_modules", "venv", ".venv", "__pycache__", "dist", "build", "target",
    "vendor", "Library", "Applications", "Downloads", "Desktop", "Pictures",
    "Music", "Movies", "Public",
}

# Documents that tend to say what a project is and where it stands. ``kind`` is
# the registry's own vocabulary; sense treats ``status`` as the one that ought to
# carry a "last updated" line.
DOC_PATTERNS: Sequence[Tuple[str, str]] = (
    ("README.md", "status"),
    ("README.markdown", "status"),
    ("README.rst", "status"),
    ("README.txt", "status"),
    ("ROADMAP.md", "plan"),
    ("PLAN.md", "plan"),
    ("STATUS.md", "status"),
    ("CHANGELOG.md", "changelog"),
    ("docs/README.md", "status"),
    ("docs/ROADMAP.md", "plan"),
    ("docs/STATUS.md", "status"),
)

# Deliberately absent, not neutral.
#
# An earlier version of this module synthesised `tier: "active"` and ICE 3/3/3 on
# the theory that a midpoint is the least opinionated guess. It is not a guess at
# all -- it is an assertion, and the renderer reads it as one. `tier in
# ("flagship", "active")` is the entry condition for the *neglected* and *stalled*
# branches, so a directory somebody made once and abandoned began, on day 31,
# telling its owner it had been neglected. Nobody ever said it mattered. The
# engine said it, then reported it back as news.
#
# So a discovered project states no tier and no ICE. Every consumer already
# tolerates the absence: the score falls back to the same numbers it used before,
# and the nag branches simply do not apply. `declared: false` in the snapshot is
# what says the silence is deliberate.
DISCOVERED_TIER = None
DISCOVERED_ICE = None

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-") or "project"


def _first_segment(rel) -> str:
    """The first meaningful path segment of a human-written relative path.

    Splitting on "/" after stripping slashes looked equivalent to this and was
    not: it turns a leading "./" into a segment of ".", so a registry that wrote
    the dot-slash form -- the form this repo's own example workspace uses for
    ``defaults.root`` -- silently claimed nothing at all. An ``ignored`` entry
    stopped ignoring, and a declared project was adopted a second time as an
    undeclared duplicate with its files counted twice, which is precisely the
    outcome :func:`claimed_segments` exists to prevent.

    Backslashes are normalised too, since these strings are hand-typed.
    """
    for part in str(rel).replace("\\", "/").split("/"):
        part = part.strip()
        if part and part not in (".", ".."):
            return part
    return ""


def claimed_segments(reg: Dict[str, Any]) -> Set[str]:
    """First path segment of everything the registry already speaks for.

    Matching on the first segment rather than the whole path is what stops a
    project declared as ``atlas/apps/site`` from leaving ``atlas`` itself looking
    undeclared -- which would adopt the parent of a tree that is already sensed,
    and count every file in it twice.
    """
    claimed: Set[str] = set()
    for pr in reg.get("projects") or []:
        if not isinstance(pr, dict):
            continue
        for rel in pr.get("paths") or []:
            seg = _first_segment(rel)
            if seg:
                claimed.add(seg)
    for key in ("watch", "infra", "ignored", "archived"):
        for entry in reg.get(key) or []:
            rel = entry.get("path") if isinstance(entry, dict) else entry
            if not rel:
                continue
            seg = _first_segment(rel)
            if seg:
                claimed.add(seg)
    return claimed


def is_engine_checkout(directory: Path) -> bool:
    """True for this package's own source tree.

    A developer's checkout sits in the same directory as their projects more
    often than not, and it is the one directory guaranteed to look busy while
    being the tool rather than the work.
    """
    pyproject = directory / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        head = pyproject.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return False
    return re.search(r'(?m)^\s*name\s*=\s*"nextbrief"', head) is not None


def _looks_versioned(directory: Path) -> bool:
    """Whether ``git: auto`` has any chance of resolving here.

    Discovery has to answer this itself rather than leaving ``auto`` to find out,
    because the sensing stage reports an unresolvable ``auto`` as a parse failure
    reading "declared as a git project" -- which is untrue of an entry nobody
    declared, and which spends the run's parse-failure count on a directory that
    is simply not a repository. A count that includes non-problems is a count
    nobody reads.

    One level down as well as the directory itself, because a directory holding
    several repositories is a shape people keep, and it is not the same thing as
    a directory with no version control anywhere in it.
    """
    if (directory / ".git").exists():
        return True
    try:
        for child in directory.iterdir():
            if child.is_dir() and not child.name.startswith(".") and (child / ".git").exists():
                return True
    except OSError:
        pass
    return False


def _status_docs(directory: Path, rel: str) -> List[Dict[str, str]]:
    """The first document that looks like it states where the project stands.

    One, not all of them: a discovered project has nobody to say which document
    is authoritative, and listing four is how a brief ends up citing a changelog
    as a status declaration.
    """
    for name, kind in DOC_PATTERNS:
        if (directory / name).is_file():
            # `authority: medium` on purpose. High is a claim the owner makes;
            # nobody has made it here.
            return [{"path": "%s/%s" % (rel, name), "kind": kind, "authority": "medium"}]
    return []


def discover(root: Path, reg: Dict[str, Any], ws: Optional[Workspace] = None) -> List[Dict[str, Any]]:
    """Synthesised registry entries for every unclaimed directory in ``root``.

    Returned in name order so a run is deterministic, and shaped exactly like a
    hand-written entry so nothing downstream needs to know the difference --
    except through ``discovered``, which is the one field that says so.
    """
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []

    claimed = claimed_segments(reg)
    taken = {str(pr.get("id")) for pr in (reg.get("projects") or []) if isinstance(pr, dict)}

    reserved = set()
    for candidate in (ws.root if ws else None, ws.out if ws else None):
        if candidate is None:
            continue
        try:
            reserved.add(candidate.resolve())
        except OSError:
            continue

    out: List[Dict[str, Any]] = []
    for directory in entries:
        name = directory.name
        if name.startswith(".") or name in SKIP_NAMES or name in claimed:
            continue
        try:
            if directory.resolve() in reserved:
                continue
        except OSError:
            continue
        if is_engine_checkout(directory):
            continue

        pid = _slug(name)
        if pid in taken:
            pid = "%s-%s" % (pid, _slug(str(directory.parent.name)) or "dir")
        if pid in taken:
            continue
        taken.add(pid)

        out.append({
            "id": pid,
            "name": name,
            "paths": [name],
            # Checked, not assumed. `auto` where a repository plausibly exists,
            # so the project gets commit-grade evidence; `none` where one plainly
            # does not, which is a statement worth making and costs a spurious
            # parse failure if left to `auto` to discover the hard way.
            "git": "auto" if _looks_versioned(directory) else "none",
            "tier": DISCOVERED_TIER,
            "ice": DISCOVERED_ICE,
            "goal_one_line": None,
            "status_docs": _status_docs(directory, name),
            "discovered": True,
        })
    return out
