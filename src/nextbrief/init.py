"""``nextbrief init`` -- from an empty directory to a first brief.

This is the whole onboarding path, so it is written against one measurable goal: a
stranger who has just run ``pip install nextbrief`` should be reading a real brief
about their own projects in under five minutes, without having learned the
registry schema first.

That is why init does not stop at scaffolding. An empty ``registry.jsonc`` would
render a clean, plausible, entirely content-free brief -- the worst first
impression this tool can make -- so init looks at the directory the workspace was
created in, works out which neighbours look like projects, and *offers* them with
a drafted registry entry each. Offers, never adopts: everything it infers is a
guess, and a guess that installed itself without a keystroke is indistinguishable
from a bug. ``--yes`` exists for scripted setups and is the only way to skip the
question.

Two smaller invariants:

* Re-running is safe. Nothing that already exists is overwritten, least of all a
  registry someone has since edited by hand.
* The workspace ends up as a git repository with one commit in it. The
  write-permission gate diffs backlog files against ``HEAD``; without a baseline
  commit there is nothing to diff against and the gate is silently inert.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .i18n import Catalog, load_catalog
from .jsonc import JSONCError, loads_jsonc
from .launch import tr
from .paths import expand, pointer_file

__all__ = ["init_workspace", "main"]

_HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = _HERE / "templates"
PROMPT_DIR = _HERE / "prompts"

REGISTRY_TEMPLATE = "registry.example.jsonc"
CONFIG_TEMPLATE = "config.example.jsonc"

GITIGNORE = """\
# Generated on every run. The brief is a view of the workspace, not a record of
# it -- the record is backlog/ and log/, which are committed.
/state/
/BRIEF.md
/BRIEF.html
.DS_Store
"""

# Directories that are never a project of yours. Kept deliberately short: this is
# a first guess a human is about to review, not a classifier.
SKIP_NAMES = {
    "node_modules", "venv", ".venv", "__pycache__", "dist", "build", "target",
    "vendor", "Library", "Applications", "Downloads", "Desktop", "Pictures",
    "Music", "Movies", "Public",
}

# Documents that tend to say what a project is and where it stands. `kind` is the
# registry's own vocabulary; sense treats `status` as the one that ought to carry
# a "last updated" line.
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

# Bounds on the mtime walk. A stranger's home directory can contain a tree with a
# hundred thousand files in it, and init must feel instant or it will be read as
# "hung" and killed.
MTIME_MAX_FILES = 400
MTIME_MAX_DEPTH = 2
MAX_CANDIDATES = 40

ACTIVE_DAYS = 30
MAINTENANCE_DAYS = 180


def _err(msg: str) -> None:
    """Errors go to stderr; everything else init prints is a transcript a human
    reads on stdout while it works."""
    sys.stderr.write("%s\n" % msg)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    path: Path
    name: str
    is_git: bool
    last_activity: Optional[str]  # ISO date, or None when nothing could be dated
    docs: List[Tuple[str, str]] = field(default_factory=list)  # (relative path, kind)


def _git_last_commit(directory: Path) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(directory), "log", "-1", "--date=short", "--format=%cd"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", "replace").strip()
    return out or None


def _newest_mtime(directory: Path) -> Optional[str]:
    """Newest file mtime in a shallow, bounded walk. Only used for directories
    with no git history, where a timestamp is the only evidence available."""
    newest = 0.0
    seen = 0
    for dirpath, dirnames, filenames in os.walk(str(directory)):
        depth = len(Path(dirpath).relative_to(directory).parts)
        if depth >= MTIME_MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_NAMES]
        for name in filenames:
            if name.startswith("."):
                continue
            seen += 1
            try:
                mtime = (Path(dirpath) / name).stat().st_mtime
            except OSError:
                continue
            newest = max(newest, mtime)
            if seen >= MTIME_MAX_FILES:
                break
        if seen >= MTIME_MAX_FILES:
            break
    if not newest:
        return None
    return dt.date.fromtimestamp(newest).isoformat()


def scan_projects(parent: Path, exclude: Path) -> List[Candidate]:
    """Look one level down from ``parent`` for things that look like projects."""
    out: List[Candidate] = []
    try:
        # Case-insensitive, so the offered list reads the way a file browser shows
        # it rather than putting every capitalised name first.
        entries = sorted((p for p in parent.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return out
    for directory in entries:
        if directory.name.startswith(".") or directory.name in SKIP_NAMES:
            continue
        try:
            if directory.resolve() == exclude.resolve():
                continue
        except OSError:
            continue
        # A directory holding a registry is somebody's workspace, not a project.
        # Offering one as a project would have the brief report on itself.
        if (directory / "registry.jsonc").is_file():
            continue
        is_git = (directory / ".git").exists()
        last = _git_last_commit(directory) if is_git else None
        if last is None:
            last = _newest_mtime(directory)
        docs = [
            (rel, kind)
            for rel, kind in DOC_PATTERNS
            if (directory / rel).is_file()
        ]
        out.append(Candidate(directory, directory.name, is_git, last, docs))
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def _age_days(iso_date: Optional[str]) -> Optional[int]:
    if not iso_date:
        return None
    try:
        return (dt.date.today() - dt.date.fromisoformat(iso_date)).days
    except ValueError:
        return None


def _tier(cand: Candidate) -> str:
    age = _age_days(cand.last_activity)
    if age is None:
        return "maintenance"
    if age <= ACTIVE_DAYS:
        return "active"
    if age <= MAINTENANCE_DAYS:
        return "maintenance"
    return "dormant"


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def draft_entry(cand: Candidate, used_ids: set) -> Dict[str, Any]:
    """Turn a candidate into a registry entry a human can correct in one pass.

    Every field that requires judgement is filled with a neutral placeholder
    rather than a plausible guess. ``goal_one_line`` in particular is left as a
    visible TODO: an invented goal would be repeated back in the brief as if you
    had written it, which is precisely the failure this project exists to avoid.
    """
    ident = _slug(cand.name)
    if ident in used_ids:
        n = 2
        while "%s-%d" % (ident, n) in used_ids:
            n += 1
        ident = "%s-%d" % (ident, n)
    used_ids.add(ident)

    entry: Dict[str, Any] = {
        "id": ident,
        "name": cand.name,
        "paths": [cand.name],
        # "auto" means "find the repository by asking git"; "none" is a claim worth
        # stating, because "this project has no version control" is itself a fact
        # the brief should be able to report.
        "git": "auto" if cand.is_git else "none",
        "tier": _tier(cand),
        "goal_one_line": "TODO: in one sentence, what does done look like?",
        "horizon": "month",
        "ice": {"impact": 3, "confidence": 3, "effort": 3},
    }
    if cand.docs:
        entry["status_docs"] = [
            {"path": "%s/%s" % (cand.name, rel), "kind": kind, "authority": "medium"}
            for rel, kind in cand.docs
        ]
    entry["notes"] = "Drafted by `nextbrief init` on %s%s. Check it." % (
        dt.date.today().isoformat(),
        (" (last activity %s)" % cand.last_activity) if cand.last_activity else "",
    )
    return entry


# ---------------------------------------------------------------------------
# registry rendering
# ---------------------------------------------------------------------------


def _skip_string(text: str, i: int) -> int:
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return i


def _array_end(text: str, start: int) -> Optional[int]:
    """Index of the ``]`` closing the array that opens at ``start``.

    Comment-aware, because the template is JSONC and its comments are prose: a
    bracket or an apostrophe inside one would otherwise throw the count off.
    """
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _set_root(text: str, root: str) -> Optional[str]:
    pattern = re.compile(r'("root"\s*:\s*)"(?:[^"\\]|\\.)*"')
    if not pattern.search(text):
        return None
    return pattern.sub(lambda m: m.group(1) + json.dumps(root), text, count=1)


def _set_projects(text: str, entries: Sequence[Dict[str, Any]]) -> Optional[str]:
    key = text.find('"projects"')
    if key == -1:
        return None
    start = text.find("[", key)
    if start == -1:
        return None
    end = _array_end(text, start)
    if end is None:
        return None
    body = ",\n".join(
        "\n".join("    " + line for line in json.dumps(e, indent=2, ensure_ascii=False).splitlines())
        for e in entries
    )
    return text[: start + 1] + "\n" + body + "\n  " + text[end:]


def render_registry(template_text: str, root: str, entries: Sequence[Dict[str, Any]]) -> str:
    """Fill the template in, editing the text rather than round-tripping the data.

    The template's comments are the documentation -- they explain what a tier is,
    why the file is a single document, what ``git: auto`` means -- and a
    ``json.dumps`` round trip would delete every one of them on the way to the
    first brief. So the substitutions are textual, and then the result is parsed
    to prove it is still valid. If anything about that fails we fall back to
    emitting plain JSON: a file with no comments is a disappointment, a file that
    does not parse is a broken install.
    """
    text = _set_root(template_text, root)
    if text is not None and entries:
        text = _set_projects(text, entries)
    if text is not None:
        try:
            loads_jsonc(text)
            return text
        except ValueError:
            pass

    data = loads_jsonc(template_text)
    if not isinstance(data, dict):
        raise JSONCError("%s is not a JSON object" % REGISTRY_TEMPLATE)
    data.setdefault("defaults", {})["root"] = root
    if entries:
        data["projects"] = list(entries)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# interaction
# ---------------------------------------------------------------------------


def _describe(cand: Candidate, cat: Optional[Catalog]) -> str:
    bits = [
        tr(cat, "init.cand.git", "git") if cand.is_git else tr(cat, "init.cand.plain", "plain directory")
    ]
    if cand.last_activity:
        bits.append(
            tr(cat, "init.cand.last_activity", "last change {date}", date=cand.last_activity)
        )
    if cand.docs:
        bits.append(", ".join(rel for rel, _ in cand.docs[:2]))
    return "  ·  ".join(bits)


def _parse_selection(answer: str, count: int) -> Optional[List[int]]:
    """``1,3-5`` -> ``[0,2,3,4]``. None when the input is not a selection at all."""
    picked: List[int] = []
    for token in re.split(r"[,\s]+", answer.strip()):
        if not token:
            continue
        m = re.match(r"^(\d+)(?:-(\d+))?$", token)
        if not m:
            return None
        lo = int(m.group(1))
        hi = int(m.group(2) or m.group(1))
        for n in range(lo, hi + 1):
            if 1 <= n <= count and (n - 1) not in picked:
                picked.append(n - 1)
    return picked


def choose_candidates(cands: Sequence[Candidate], yes: bool, cat: Optional[Catalog]) -> List[Candidate]:
    if not cands:
        return []
    if yes:
        return list(cands)

    print()
    print(
        tr(
            cat,
            "init.scan.found",
            "Found {n} possible project(s) next door, in {dir}:",
            n=len(cands),
            dir=str(cands[0].path.parent),
        )
    )
    print()
    for i, cand in enumerate(cands, 1):
        print("  %2d) %-28s %s" % (i, cand.name[:28], _describe(cand, cat)))
    print()
    print(
        tr(
            cat,
            "init.scan.hint",
            "Enter to add them all  ·  numbers like 1,3-5 for some  ·  n for none",
        )
    )
    while True:
        try:
            answer = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            # No keystroke happened, so nothing may be adopted. Same rule as the
            # `do` picker: inference never installs itself unattended.
            print()
            print(tr(cat, "init.scan.none", "Nothing adopted. Add projects by editing registry.jsonc."))
            return []
        if answer in ("n", "N", "none", "q", "Q"):
            print(tr(cat, "init.scan.none", "Nothing adopted. Add projects by editing registry.jsonc."))
            return []
        if answer == "" or answer in ("a", "A", "all"):
            return list(cands)
        picked = _parse_selection(answer, len(cands))
        if picked:
            return [cands[i] for i in picked]
        print("  " + tr(cat, "init.scan.reask", "Sorry -- Enter, or numbers like 1,3-5, or n."))


# ---------------------------------------------------------------------------
# git baseline
# ---------------------------------------------------------------------------


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


def _git_baseline(root: Path, notes: List[str]) -> None:
    """Make sure a HEAD exists to diff against. Never fatal: a workspace without
    git still produces briefs, it just loses the rollback of unauthorised edits."""
    rc, top, _ = _git(root, "rev-parse", "--show-toplevel")
    if rc == 0 and top:
        if Path(top).resolve() != root.resolve():
            notes.append("This workspace lives inside the git repository at %s; leaving it there." % top)
            return
    else:
        rc, _, err = _git(root, "init", "-q")
        if rc != 0:
            notes.append("Could not run git init (%s). The write-permission gate needs a git repo." % (err or rc))
            return

    rc, head, _ = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    if rc == 0 and head:
        return  # a baseline already exists; do not add commits to someone's history

    rc, email, _ = _git(root, "config", "user.email")
    if rc != 0 or not email:
        # Same rule as `nextbrief done`: never commit under an invented identity.
        notes.append(
            "git has no user.email, so nothing was committed. Set one and make the "
            "first commit yourself:\n"
            '    git config --global user.email "you@example.com"\n'
            '    git config --global user.name "Your Name"\n'
            "    git -C %s add -A && git -C %s commit -m 'nextbrief: workspace'" % (root, root)
        )
        return

    _git(root, "add", "-A")
    rc, _, err = _git(root, "commit", "-q", "-m", "nextbrief: initial workspace")
    if rc != 0:
        notes.append("Could not make the first commit (%s)." % (err or rc))


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def _copy_if_absent(src: Path, dst: Path) -> bool:
    if dst.exists() or not src.is_file():
        return False
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def _next_steps(root: Path, cat: Optional[Catalog]) -> None:
    print()
    print(tr(cat, "init.next.header", "Next:"))
    print("  nextbrief v0     " + tr(cat, "init.next.v0", "build a brief with no model at all -- zero tokens"))
    print("  nextbrief open   " + tr(cat, "init.next.open", "read it in your browser"))
    print("  nextbrief ls     " + tr(cat, "init.next.ls", "see what is in the backlog"))
    print("  nextbrief run    " + tr(cat, "init.next.run", "the full three stages, once you have a provider configured"))
    print()
    print(
        tr(
            cat,
            "init.next.anywhere",
            "Those work from any directory -- {file} now points here.",
            file=str(pointer_file()),
        )
    )


def init_workspace(
    target=None,
    yes: bool = False,
    cat: Optional[Catalog] = None,
    scan: bool = True,
) -> int:
    if cat is None:
        try:
            cat = load_catalog()
        except (OSError, ValueError):
            # Every string below carries its English original, so a missing
            # catalog must not be the thing that stops someone installing.
            cat = None
    root = expand(target if target else Path.cwd())
    try:
        root = root.resolve()
    except OSError:
        pass

    registry_template = TEMPLATE_DIR / REGISTRY_TEMPLATE
    config_template = TEMPLATE_DIR / CONFIG_TEMPLATE
    if not registry_template.is_file() or not config_template.is_file():
        _err("error: packaged templates are missing from %s -- reinstall nextbrief." % TEMPLATE_DIR)
        return 1

    registry_path = root / "registry.jsonc"
    already = registry_path.is_file()

    try:
        for sub in ("backlog", "prompts", "state", "log"):
            (root / sub).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _err("error: cannot create %s: %s" % (root, exc))
        return 1

    created: List[str] = []
    notes: List[str] = []

    if not (root / ".gitignore").exists():
        (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        created.append(".gitignore")
    if _copy_if_absent(config_template, root / "config.jsonc"):
        created.append("config.jsonc")
    for prompt in sorted(PROMPT_DIR.glob("*.md")) if PROMPT_DIR.is_dir() else []:
        if _copy_if_absent(prompt, root / "prompts" / prompt.name):
            created.append("prompts/%s" % prompt.name)

    if already:
        # Idempotent by contract: a registry someone has edited is the most
        # valuable file here, and re-running init must never be the thing that
        # loses it.
        print(tr(cat, "init.exists", "{path} is already a workspace; leaving registry.jsonc alone.", path=str(root)))
        if created:
            print(tr(cat, "init.added", "Added: {files}", files=", ".join(created)))
        _write_pointer(root, notes)
        for note in notes:
            print(note)
        _next_steps(root, cat)
        return 0

    adopted: List[Candidate] = []
    if scan:
        adopted = choose_candidates(scan_projects(root.parent, root), yes, cat)
    entries = []
    used: set = set()
    for cand in adopted:
        entries.append(draft_entry(cand, used))

    try:
        text = render_registry(registry_template.read_text(encoding="utf-8"), str(root.parent), entries)
    except (JSONCError, ValueError) as exc:
        _err("error: packaged %s is not valid JSONC (%s)" % (REGISTRY_TEMPLATE, exc))
        return 1
    registry_path.write_text(text, encoding="utf-8")
    created.append("registry.jsonc")

    _git_baseline(root, notes)
    _write_pointer(root, notes)

    print()
    print(tr(cat, "init.done", "Workspace ready: {path}", path=str(root)))
    print("  " + tr(cat, "init.created", "Created: {files}", files=", ".join(sorted(created))))
    if entries:
        print(
            "  "
            + tr(
                cat,
                "init.drafted",
                "Drafted {n} project(s) in registry.jsonc: {names}. They are guesses -- "
                "read them, and fill in each goal_one_line.",
                n=len(entries),
                names=", ".join(e["id"] for e in entries),
            )
        )
    else:
        print(
            "  "
            + tr(
                cat,
                "init.no_projects",
                "No projects yet. Add one to registry.jsonc: an id, a name, and the "
                "path it lives at, relative to defaults.root.",
            )
        )
    for note in notes:
        print("  " + note)
    _next_steps(root, cat)
    return 0


def _write_pointer(root: Path, notes: List[str]) -> None:
    """Record the default workspace so later commands need no flags at all."""
    pointer = pointer_file()
    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(root) + "\n", encoding="utf-8")
    except OSError as exc:
        notes.append(
            "Could not write %s (%s); pass --workspace %s or set NEXTBRIEF_WORKSPACE."
            % (pointer, exc, root)
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m nextbrief.init [dir]``, for when the console script is not on PATH."""
    import argparse

    ap = argparse.ArgumentParser(prog="nextbrief init", description="Create a nextbrief workspace.")
    ap.add_argument("directory", nargs="?")
    ap.add_argument("-y", "--yes", action="store_true")
    ap.add_argument("--no-scan", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    return init_workspace(args.directory, yes=args.yes, scan=not args.no_scan)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
