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

Three smaller invariants:

* Nothing is invented. The registry init writes lists the projects it actually
  found and nothing else. The packaged template ships six worked examples
  because it doubles as the registry's documentation; carrying them into a real
  workspace would have the first brief report a portfolio nobody owns, under a
  footer claiming every line of it passed the evidence gate.
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

from . import resources
from .i18n import Catalog, load_catalog
from .jsonc import JSONCError, loads_jsonc
from .launch import tr
from .paths import expand, pointer_file

__all__ = ["init_workspace", "main"]

# Bundled data is read through `resources`, not through a filesystem path: the
# package may be running from inside a zipapp, where __file__ names nothing real.
TEMPLATE_DIR = "templates"
PROMPT_DIR = "prompts"
SCHEMA_DIR = "schema"

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

# Illustrative lists in the packaged template, alongside "projects". They name
# directories that exist in the worked example and nowhere else, so carrying any
# of them into a real workspace feeds the snapshot -- and stage 2 -- claims about
# a portfolio nobody has.
DEMO_LISTS: Sequence[str] = ("watch", "infra", "ignored", "archived")

# What stands in for the array when nothing was adopted. A bare `[]` reads as a
# failed install; this is the one place a new reader is guaranteed to look, so it
# says what to type instead.
EMPTY_PROJECTS = """\
    // Empty on purpose: init found nothing, and a brief invents nothing --
    // including its own subject. Add a project here. `paths` are relative to
    // defaults.root above; everything else has a sensible default.
    //
    //   {
    //     "id": "my-project",
    //     "name": "My Project",
    //     "paths": ["my-project"],
    //     "git": "auto",
    //     "tier": "active",
    //     "horizon": "month",
    //     "goal_one_line": "in one sentence, what does done look like?",
    //     "ice": { "impact": 3, "confidence": 3, "effort": 3 }
    //   }"""


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


def _set_array(
    text: str,
    key: str,
    entries: Sequence[Dict[str, Any]],
    empty_body: Optional[str] = None,
) -> Optional[str]:
    """Replace the array at ``key`` with ``entries``. None if it cannot be found."""
    at = text.find('"%s"' % key)
    if at == -1:
        return None
    start = text.find("[", at)
    if start == -1:
        return None
    end = _array_end(text, start)
    if end is None:
        return None
    if entries:
        body = ",\n".join(
            "\n".join("    " + line for line in json.dumps(e, indent=2, ensure_ascii=False).splitlines())
            for e in entries
        )
    elif empty_body:
        body = empty_body
    else:
        return text[: start + 1] + text[end:]
    return text[: start + 1] + "\n" + body + "\n  " + text[end:]


def _registry_is_clean(text: str, entries: Sequence[Dict[str, Any]]) -> bool:
    """Parse the rendered registry and prove it says only what init actually found.

    The template doubles as the registry's documentation, so it ships six worked
    examples. A substitution that quietly missed used to leave all six in a
    stranger's workspace, and the first brief then reported six projects, a
    pending decision and a deadline months overdue -- every word of it invented,
    under a footer asserting the evidence gate had passed. So the result is
    checked rather than assumed, on both the textual path and the JSON fallback.
    """
    try:
        data = loads_jsonc(text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("projects") != list(entries):
        return False
    return all(not data.get(key) for key in DEMO_LISTS)


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
    if text is not None:
        # Unconditional, entries or not. Doing this only when something was
        # adopted is what shipped the worked examples to everyone who adopted
        # nothing, which is the majority of first runs.
        text = _set_array(text, "projects", entries, EMPTY_PROJECTS)
    if text is not None:
        for key in DEMO_LISTS:
            emptied = _set_array(text, key, ())
            if emptied is not None:
                text = emptied
    if text is not None and _registry_is_clean(text, entries):
        return text

    data = loads_jsonc(template_text)
    if not isinstance(data, dict):
        raise JSONCError("%s is not a JSON object" % REGISTRY_TEMPLATE)
    data.setdefault("defaults", {})["root"] = root
    data["projects"] = list(entries)
    for key in DEMO_LISTS:
        if key in data:
            data[key] = []
    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if not entries:
        # json.dumps cannot write the comment, but the file is JSONC and the
        # reader still needs to be told what to type. Re-run the same textual
        # substitution over the dumped `"projects": []`, and keep the plain
        # version if that does not take.
        commented = _set_array(out, "projects", (), EMPTY_PROJECTS)
        if commented is not None and _registry_is_clean(commented, entries):
            return commented
    return out


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


def _write_if_absent(dst: Path, text: Optional[str]) -> bool:
    """Write a bundled file into the workspace unless something is already there.

    Never overwrites: on a re-run the file on disk is the one the user may have
    edited, and init is idempotent by contract.
    """
    if text is None or dst.exists():
        return False
    dst.write_text(text, encoding="utf-8")
    return True


def _next_steps(root: Path, cat: Optional[Catalog], pointed: bool) -> None:
    print()
    print(tr(cat, "init.next.header", "Next:"))
    print("  nextbrief v0     " + tr(cat, "init.next.v0", "build a brief with no model at all -- zero tokens"))
    print("  nextbrief open   " + tr(cat, "init.next.open", "read it in your browser"))
    print("  nextbrief ls     " + tr(cat, "init.next.ls", "see what is in the backlog"))
    print("  nextbrief run    " + tr(cat, "init.next.run", "the full three stages, once you have a provider configured"))
    print()
    # Only claim the pointer when it is on disk. This line used to print whether
    # or not the write succeeded, so a read-only config home produced a confident
    # "now points here" followed by "no workspace found" from every later command.
    if pointed:
        print(
            tr(
                cat,
                "init.next.anywhere",
                "Those work from any directory -- {file} now points here.",
                file=str(pointer_file()),
            )
        )
    else:
        print(
            tr(
                cat,
                "init.next.no_pointer",
                "No pointer file was written, so say where the workspace is:\n"
                "    nextbrief --workspace {root} v0\n"
                "  or, once per shell:\n"
                "    export NEXTBRIEF_WORKSPACE={root}",
                root=str(root),
            )
        )


def init_workspace(
    target=None,
    yes: bool = False,
    cat: Optional[Catalog] = None,
    scan: bool = True,
    set_default: bool = False,
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

    registry_template = resources.read_text(TEMPLATE_DIR, REGISTRY_TEMPLATE)
    config_template = resources.read_text(TEMPLATE_DIR, CONFIG_TEMPLATE)
    if registry_template is None or config_template is None:
        _err("error: packaged templates are missing -- reinstall nextbrief.")
        return 1

    registry_path = root / "registry.jsonc"
    already = registry_path.is_file()

    try:
        # schema/ exists because the stage-2 prompt tells the model to read
        # schema/brief.schema.json and schema/BACKLOG_TEMPLATE.md out of the
        # workspace. Shipping the prompt without the files it names sends the
        # model looking for paths that do not exist, and the two contracts it is
        # meant to honour -- the brief's shape and the backlog's -- go unstated.
        for sub in ("backlog", "prompts", "schema", "state", "log"):
            (root / sub).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _err("error: cannot create %s: %s" % (root, exc))
        return 1

    created: List[str] = []
    notes: List[str] = []

    if not (root / ".gitignore").exists():
        (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        created.append(".gitignore")
    if _write_if_absent(root / "config.jsonc", config_template):
        created.append("config.jsonc")
    for name in resources.list_names(PROMPT_DIR, ".md"):
        if _write_if_absent(root / "prompts" / name, resources.read_text(PROMPT_DIR, name)):
            created.append("prompts/%s" % name)
    for name in resources.list_names(SCHEMA_DIR):
        if _write_if_absent(root / "schema" / name, resources.read_text(SCHEMA_DIR, name)):
            created.append("schema/%s" % name)

    if already:
        # Idempotent by contract: a registry someone has edited is the most
        # valuable file here, and re-running init must never be the thing that
        # loses it.
        print(tr(cat, "init.exists", "{path} is already a workspace; leaving registry.jsonc alone.", path=str(root)))
        if created:
            print(tr(cat, "init.added", "Added: {files}", files=", ".join(created)))
        pointed = _write_pointer(root, notes, set_default)
        for note in notes:
            print(note)
        _next_steps(root, cat, pointed)
        return 0

    adopted: List[Candidate] = []
    if scan:
        adopted = choose_candidates(scan_projects(root.parent, root), yes, cat)
    entries = []
    used: set = set()
    for cand in adopted:
        entries.append(draft_entry(cand, used))

    try:
        text = render_registry(registry_template, str(root.parent), entries)
    except (JSONCError, ValueError) as exc:
        _err("error: packaged %s is not valid JSONC (%s)" % (REGISTRY_TEMPLATE, exc))
        return 1
    registry_path.write_text(text, encoding="utf-8")
    created.append("registry.jsonc")

    _git_baseline(root, notes)
    pointed = _write_pointer(root, notes, set_default)

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
    _next_steps(root, cat, pointed)
    return 0


def _write_pointer(root: Path, notes: List[str], set_default: bool = False) -> bool:
    """Record the default workspace so later commands need no flags at all.

    Refuses to steal an existing pointer. The pointer is global mutable state:
    one line that decides which workspace every later bare command reads. If
    ``init`` repoints it whenever it runs, then creating a second workspace --
    to try something, to help someone, inside a test -- silently redirects the
    daily brief, and the result still looks like a brief. You would find out the
    next morning, if at all, because a confident report about the wrong
    workspace reads exactly like a correct one.

    That is not hypothetical: it happened during development. A test run of
    ``init`` in a scratch directory took the pointer, and the next
    ``nextbrief ls`` reported an empty backlog for a workspace whose backlog
    was not empty at all.

    So: claim the pointer when there is none, keep it when it already names this
    workspace, and otherwise leave it alone and say so. ``--set-default``
    repoints deliberately.

    Reports whether it worked, because the caller prints a promise about it and
    a promise that is not checked is worse than no pointer at all.
    """
    pointer = pointer_file()

    if not set_default:
        try:
            current = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            current = ""
        if current:
            try:
                same = Path(current).expanduser().resolve() == root.resolve()
            except OSError:
                same = False
            if same:
                return True
            notes.append(
                "The default workspace is still %s, so bare commands keep reporting on it.\n"
                "  For this one, use `nextbrief --workspace %s ...`,\n"
                "  or re-run with `nextbrief init %s --set-default` to switch."
                % (current, root, root)
            )
            return False

    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(root) + "\n", encoding="utf-8")
    except OSError as exc:
        notes.append(
            "Could not write %s (%s); pass --workspace %s or set NEXTBRIEF_WORKSPACE."
            % (pointer, exc, root)
        )
        return False
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m nextbrief.init [dir]``, for when the console script is not on PATH."""
    import argparse

    ap = argparse.ArgumentParser(prog="nextbrief init", description="Create a nextbrief workspace.")
    ap.add_argument("directory", nargs="?")
    ap.add_argument("-y", "--yes", action="store_true")
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--set-default", action="store_true",
                    help="make this the default workspace even if another already is")
    args = ap.parse_args(list(argv) if argv is not None else None)
    return init_workspace(args.directory, yes=args.yes, scan=not args.no_scan,
                          set_default=args.set_default)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
