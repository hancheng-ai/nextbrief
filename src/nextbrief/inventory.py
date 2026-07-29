"""What each project *is* — as opposed to what it did this week.

`digest.json` is an activity report. It answers what moved, how fresh it is and
what is due, which is exactly what a daily brief needs and never once says what a
project is *for*. An agent asked "should we build X?" needs the other question
answered: what exists already, and what is it. Re-deriving that by walking the
tree is what every agent otherwise does, separately, every session.

So this is a second artifact rather than a heavier digest, and the split is not
tidiness. The two have different consumers, different content and — decisively —
different cadence: activity changes daily, capability changes maybe monthly. One
is read by the model that writes tonight's brief; the other is read by whatever
else needs to know the portfolio without paying to rediscover it.

**Derived where it can be, declared where it cannot, and never blended.** A
project's own manifest already states what it is — `package.json`, `pyproject.toml`,
a plugin manifest — and a README's opening line usually does too. Those are
observations, and they carry the file they came from so a reader can check them.
Where no manifest exists, which on a real portfolio is roughly the content
projects, nothing is invented: the entry says the description is *declared* and
names the registry, or says there is none.

That distinction is the whole safety property. An agent reading this must be able
to tell "orchard is a tenancy API" — which its `package.json` says,
checkably — from "orchard is our flagship" — which is a thing a person
typed. Blend them and the second reads as a finding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["INVENTORY_NAME", "MANIFESTS", "build_inventory", "capability",
           "describe"]

INVENTORY_NAME = "inventory.json"

# (filename, how to pull a description out of it, what stack it implies).
# Ordered: the first that exists and yields a description wins, so a package
# manifest beats a README, which is usually more specific and always shorter.
MANIFESTS: Sequence[Tuple[str, str, str]] = (
    ("package.json", "json:description", "node"),
    ("pyproject.toml", "toml:project.description", "python"),
    (".claude-plugin/plugin.json", "json:description", "claude-plugin"),
    ("Cargo.toml", "toml:package.description", "rust"),
    ("composer.json", "json:description", "php"),
    ("go.mod", "gomod", "go"),
)

# One sentence is the target; the cap is the backstop for prose that never ends.
# A manifest description is often a full paragraph -- useful on a package page,
# noise in a list of twelve -- and the first sentence of one is almost always the
# sentence that says what the thing is.
MAX_DESCRIPTION = 200

# Capability gets more room and is not cut to one sentence. A description says
# what a thing is, which one sentence covers; capability says what the thing
# built could also serve, and that is inherently a clause about scope.
MAX_CAPABILITY = 400

_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s")


def _one_sentence(text: str) -> str:
    text = " ".join(str(text).split())
    first = _SENTENCE_END.split(text, 1)[0].strip()
    if first and len(first) <= MAX_DESCRIPTION:
        return first
    return text[:MAX_DESCRIPTION].rstrip()


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(str(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_toml(path: Path) -> Optional[Dict[str, Any]]:
    # tomllib is 3.11+; the floor is 3.9, so a pyproject description is simply
    # unavailable on older interpreters rather than a crash. Recorded as absent,
    # like any other fact that could not be read.
    try:
        import tomllib
    except ImportError:
        return None
    try:
        with open(str(path), "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return None


def _dig(data, dotted: str):
    for part in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _from_manifest(root: Path, rel: str, how: str):
    path = root / rel
    if not path.is_file():
        return None
    if how == "gomod":
        # go.mod carries no description; its presence is the fact worth having.
        return ""
    kind, _, where = how.partition(":")
    data = _read_json(path) if kind == "json" else _read_toml(path)
    if data is None:
        return None
    value = _dig(data, where)
    return str(value).strip() if isinstance(value, str) else ""


_HEADING = re.compile(r"^\s*(#|=+\s*$|-+\s*$)")
_BADGE = re.compile(r"^\s*(\[!\[|!\[|<)")


def _from_readme(root: Path) -> Optional[Tuple[str, str]]:
    """First paragraph that is prose rather than decoration, and its filename."""
    for name in ("README.md", "README.markdown", "README.rst", "README.txt"):
        path = root / name
        if not path.is_file():
            continue
        try:
            with open(str(path), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    text = line.strip()
                    if not text or _HEADING.match(text) or _BADGE.match(text):
                        continue
                    return _one_sentence(text), name
        except OSError:
            continue
    return None


def _entry_points(root: Path) -> List[str]:
    """How a person runs this, taken from what the project already declares."""
    out: List[str] = []
    pkg = _read_json(root / "package.json")
    if pkg:
        scripts = pkg.get("scripts")
        if isinstance(scripts, dict):
            out.extend("npm run %s" % k for k in sorted(scripts)[:6])
    toml = _read_toml(root / "pyproject.toml")
    if toml:
        scripts = _dig(toml, "project.scripts")
        if isinstance(scripts, dict):
            out.extend(sorted(scripts))
    return out[:8]


def describe(root: Path, declared: Optional[str] = None) -> Dict[str, Any]:
    """What this directory says about itself, with where each part came from.

    ``declared`` is the registry's own description if the owner wrote one. It
    wins, and is labelled as a declaration rather than an observation — the point
    is never that one is better, only that a reader can tell which is which.
    """
    if declared:
        return {"what": _one_sentence(declared),
                "kind": "declared", "source": "registry"}

    for rel, how, _stack in MANIFESTS:
        got = _from_manifest(root, rel, how)
        if got:
            return {"what": _one_sentence(got), "kind": "observed", "source": rel}

    readme = _from_readme(root)
    if readme:
        return {"what": readme[0], "kind": "observed", "source": readme[1]}

    # Nothing invented. "No description anywhere" is itself worth reporting: it
    # is the one thing a person can fix in ten seconds.
    return {"what": None, "kind": "absent", "source": None}


def capability(declared: Optional[str]) -> Dict[str, Any]:
    """What was built here that generalises beyond its current purpose.

    Always declared, never derived, and there is no fallback -- because unlike a
    description this cannot be observed. A manifest says what a package is; no
    file on disk says "the scheduling core in here would serve a domain it
    was never written for". That is a judgement about potential, and the most
    speculative thing in this artifact.

    Which is exactly why it is a separate field carrying its own label. An agent
    weighing "should we build X, or is there something to reuse?" needs this more
    than it needs the current purpose -- and needs to know it is reading somebody
    optimism rather than a fact about the tree.
    """
    if not declared:
        return {"what": None, "kind": "absent", "source": None}
    return {"what": " ".join(str(declared).split())[:MAX_CAPABILITY],
            "kind": "declared", "source": "registry"}


def _stacks(root: Path) -> List[str]:
    return sorted({stack for rel, _how, stack in MANIFESTS if (root / rel).is_file()})


def build_inventory(root: Path, projects: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per sensed project: what it is, how to run it, what it links to.

    Deliberately small. This is read to answer "what do we have?", and an artifact
    that costs as much to read as walking the tree has saved nobody anything.
    """
    out = []
    for p in projects:
        pid = str(p.get("id"))
        paths = p.get("paths") or []
        home = (root / paths[0]) if paths else root

        entry = {
            "id": pid,
            "name": p.get("name") or pid,
            "path": paths[0] if paths else None,
            "description": describe(home, p.get("description")),
            "capability": capability(p.get("capability")),
            "goal": p.get("goal_one_line"),
            "stacks": _stacks(home),
            "run": _entry_points(home),
            "declared": bool(p.get("declared", True)),
            "tier": p.get("tier"),
            "serves": p.get("serves") or [],
            "needs": p.get("needs") or [],
            "unlocks": p.get("unlocks") or [],
            "has_git": bool(p.get("has_git")),
        }
        out.append(entry)
    return sorted(out, key=lambda e: e["id"])
