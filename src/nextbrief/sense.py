#!/usr/bin/env python3
"""Stage 1: deterministic sensing. No model is involved anywhere in this file.

Design contract (the red lines -- see README "design contract"):
  * Read-only everywhere. The only files written are the two derived artifacts
    below, both inside the workspace.
  * Two outputs: ``state/snapshot.json`` (full, rotates to ``snapshot.prev.json``)
    and ``state/digest.json`` (compact, the model's only input).
  * Idempotent: apart from the ``run`` block, identical inputs produce
    byte-identical output. That property is what makes ``--check`` meaningful,
    and it is why nothing here may use the wall clock for ordering or grouping.
  * No judgement: report facts and threshold buckets. Deciding is stage 2's job.
  * Fail open: a parser that fails returns None and appends to ``parse_failed``.
    One unreadable file must never take down the nightly run.
  * External tools (``scc``) are optional. A missing one is recorded in
    ``tool_missing`` and the run continues with a documented cheaper proxy.

PRIVACY
  A path covered by a project's ``privacy.never_read`` may contribute exactly one
  thing to the snapshot: an integer count. Its contents are never read and *its
  file names never appear anywhere in the output*. Activity stays visible;
  content does not.

  This is enforced in three independent places on purpose, so that it survives a
  future refactor that only understands one of them:
    1. the walk filter prunes those directories, so no name is ever collected;
    2. the git pathspec excludes them, so no name can arrive via git either;
    3. :func:`main` refuses to write a snapshot in which any such path appears.

  Originally only (1) existed, and only by coincidence: each private directory
  also happened to be listed by hand in ``ignore_globs``. Worse, a private
  directory nested inside *another* project's tree was walked by that project and
  its file names did reach the snapshot. A privacy guarantee that depends on a
  human maintaining a second, unrelated list is not a guarantee.

  All four enforcement points match declarations as globs, against the same
  rules (see :class:`GlobSet`): ``x``, ``x/`` and ``x/**`` all mean "everything
  under x", ``**/x/**`` means "any directory called x, at any depth", and an
  extension pattern is anchored where it is written -- ``*.key`` beside the
  declaration, ``**/*.key`` at any depth. Reducing a glob to a literal prefix
  instead is how a declared rule came to enforce nothing, count zero, and say
  nothing about either.

Usage:
  sense                write state/snapshot.json and state/digest.json
  sense --check        exit 3 if the snapshot or the digest would change
  sense --stdout       print the snapshot, write nothing
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .discovery import discover
from .frontmatter import parse_frontmatter
from .fs import write_text
from .jsonc import JSONCError, load_jsonc
from .paths import Workspace, WorkspaceError, expand, resolve_workspace

__all__ = ["main", "build", "build_digest", "canonical", "SenseError"]

GIT_TIMEOUT = 30
TOOL_TIMEOUT = 60

# Where the coding agent keeps its per-project session logs. Overridable via
# ``config.jsonc`` -> ``sessions.dir`` because a published package cannot assume
# which agent runtime the reader uses; the default matches Claude Code.
DEFAULT_SESSIONS_DIR = "~/.claude/projects"

# Non-goal tables are looked up by heading text, which is language-specific;
# registries written in another language set ``non_goals_heading`` explicitly.
DEFAULT_NON_GOALS_HEADING = "Non-goals"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_STALE = 3
EXIT_PRIVACY = 4

# Subprocess accounting for --timing. Always collected (a perf_counter pair
# around a fork is free relative to the fork) and only printed on request.
_CMD_STATS = {"count": 0, "seconds": 0.0}


class SenseError(RuntimeError):
    """A configuration problem the run must not continue past.

    Reserved for conditions where continuing would produce a *plausible but
    wrong* snapshot -- an unreadable registry, a project root that does not
    exist. Everything else fails open into ``parse_failed``.
    """


# ---------------------------------------------------------------------------
# Path filtering
# ---------------------------------------------------------------------------

class PathFilter:
    """Compile a glob list into "directories to prune" plus "patterns to match".

    - ``**/X/**``   -> prune any directory named X. The fast path: it stops the
                       walk before it descends into node_modules at all, rather
                       than matching a million paths on the way out.
    - ``PREFIX/**`` -> prune the directory at that project-relative path.
    - anything else -> fnmatch against the project-relative path and basename.
    """

    __slots__ = ("prune_names", "prune_rel", "patterns")

    def __init__(self, globs: Optional[Sequence[str]] = None):
        self.prune_names = set()
        self.prune_rel = set()
        self.patterns: List[str] = []
        for g in globs or []:
            g = (g or "").strip()
            if not g:
                continue
            m = re.match(r"^\*\*/([^*/]+)/\*\*$", g)
            if m:
                self.prune_names.add(m.group(1))
                continue
            if g.endswith("/**") and "*" not in g[:-3]:
                self.prune_rel.add(g[:-3].strip("/"))
                continue
            if g.startswith("**/"):
                self.patterns.append(g[3:])
            else:
                self.patterns.append(g)

    def __bool__(self) -> bool:
        return bool(self.prune_names or self.prune_rel or self.patterns)

    def prune_dir(self, rel_dir: str, name: str) -> bool:
        if name in self.prune_names:
            return True
        rel = (rel_dir + "/" + name).strip("/") if rel_dir else name
        return rel in self.prune_rel

    def match_file(self, rel_path: str) -> bool:
        base = rel_path.rsplit("/", 1)[-1]
        for p in self.patterns:
            if fnmatch.fnmatch(rel_path, p) or fnmatch.fnmatch(base, p):
                return True
        return False

    def match_path(self, rel_path: str) -> bool:
        """Would the walk have skipped this path -- by pruning or by pattern?

        For callers that get paths handed to them instead of walking to them.
        ``match_file`` alone is not enough there: the pruning forms (``**/x/**``,
        ``PREFIX/**``) never reach ``patterns``, so a filter that stops a
        vendored tree during a walk would let every one of its files through when
        git names them.
        """
        parts = rel_path.strip("/").split("/")
        for i, part in enumerate(parts[:-1]):
            if part in self.prune_names:
                return True
            if "/".join(parts[:i + 1]) in self.prune_rel:
                return True
        return self.match_file(rel_path)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def run_cmd(args: Sequence[str], cwd=None, timeout: int = GIT_TIMEOUT) -> Tuple[bool, str]:
    """Return ``(ok, stdout)``. Never raises -- fail open."""
    started = time.perf_counter()
    try:
        p = subprocess.run(
            list(args), cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            return False, ""
        return True, p.stdout.decode("utf-8", "replace")
    except Exception:
        return False, ""
    finally:
        _CMD_STATS["count"] += 1
        _CMD_STATS["seconds"] += time.perf_counter() - started


def which(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        c = Path(d) / name
        if c.is_file() and os.access(str(c), os.X_OK):
            return str(c)
    # A process started by a GUI scheduler inherits a minimal PATH, so fall back
    # to the usual install prefixes rather than reporting the tool as missing.
    for d in ("/usr/local/bin", "/opt/homebrew/bin", str(Path.home() / ".local" / "bin")):
        c = Path(d) / name
        if c.is_file() and os.access(str(c), os.X_OK):
            return str(c)
    return None


def slugify_path(p) -> str:
    """Reproduce the agent's ``projects/`` directory naming: non-alphanumerics -> '-'."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(p))


def days_between(older: Optional[dt.date], newer: dt.date) -> Optional[int]:
    if older is None:
        return None
    return (newer - older).days


def iso(d) -> Optional[str]:
    return d.isoformat() if d else None


class Timing:
    """``--timing`` instrumentation, printed to stderr so stdout stays parseable."""

    __slots__ = ("enabled", "order", "spans")

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self.order: List[str] = []
        self.spans: Dict[str, float] = {}

    @contextlib.contextmanager
    def phase(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            if name not in self.spans:
                self.order.append(name)
                self.spans[name] = 0.0
            self.spans[name] += time.perf_counter() - started

    def report(self, stream) -> None:
        if not self.enabled:
            return
        stream.write("timing:\n")
        for name in self.order:
            stream.write("  %-28s %7.3fs\n" % (name, self.spans[name]))
        stream.write("  %-28s %7.3fs (%d processes)\n"
                     % ("subprocesses", _CMD_STATS["seconds"], _CMD_STATS["count"]))


# ---------------------------------------------------------------------------
# Privacy plumbing
# ---------------------------------------------------------------------------

def _glob_regex(glob: str) -> str:
    """Translate one path glob into a regex source string.

    ``fnmatch`` is not usable here. It compiles ``*`` to ``.*``, so ``**/x/**``
    never matches ``x/f`` (the ``**`` cannot collapse to nothing) and ``x/*``
    matches a path it should not. Both errors are silent, and one of them loses a
    privacy guarantee, so the translation is done here where the rules are stated:

      ``**/``  zero or more leading directories
      ``/**``  everything strictly inside that directory
      ``*``    any run of characters within one path segment
      ``?``    one character within one path segment
    """
    out: List[str] = []
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            j = i
            while j < n and glob[j] == "*":
                j += 1
            double = (j - i) > 1
            if double and glob[j:j + 1] == "/":
                out.append("(?:[^/]*/)*")
                i = j + 1
                continue
            if double and i > 0 and glob[i - 1] == "/":
                out.append(".+")          # trailing /** -- strictly inside
                i = j
                continue
            out.append(".*" if double else "[^/]*")
            i = j
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        if c == "[":
            j = i + 1
            if j < n and glob[j] in "!^":
                j += 1
            if j < n and glob[j] == "]":
                j += 1
            while j < n and glob[j] != "]":
                j += 1
            if j >= n:                    # unterminated class: a literal bracket
                out.append("\\[")
                i += 1
                continue
            inner = glob[i + 1:j].replace("\\", "\\\\")
            if inner[:1] in ("!", "^"):
                inner = "^" + inner[1:]
            out.append("[" + inner + "]")
            i = j + 1
            continue
        out.append(re.escape(c))
        i += 1
    return "".join(out)


class GlobSet:
    """A compiled set of path globs with the two questions the walk needs answered.

    ``matches`` is the privacy predicate: a path is covered when it matches a
    glob *or* lives under a directory that does. ``covers_dir`` is the pruning
    predicate: everything under this directory is covered, so the walk can stop
    here and record the directory itself -- which is what the git ``:(exclude)``
    pathspec and the private file count both need, and what reducing a glob to a
    literal prefix could not give them.

    Patterns are anchored at the base they are written against, the same way the
    registry's own ``ignore_globs`` examples are: ``*.key`` covers the ``.key``
    files beside it, ``**/*.key`` covers them at any depth. One rule for all four
    call sites matters more than which rule it is -- the count, the walk, the git
    exclusion and the pre-write scan disagreeing is how a name escapes.
    """

    __slots__ = ("_self", "_cover")

    def __init__(self, globs: Optional[Sequence[str]] = None):
        self._self: List[Any] = []
        self._cover: List[Any] = []
        for raw in globs or []:
            g = (raw or "").strip().strip("/")
            if not g:
                continue
            self._self.append(re.compile("(?s:" + _glob_regex(g) + ")\\Z"))
            # A glob whose last segment is `*` or `**` covers its parent
            # directory entirely; `*.key` covers nothing but the files it names.
            head = g.rsplit("/", 1)[0] if "/" in g else None
            if head and g.rsplit("/", 1)[1] in ("*", "**"):
                self._cover.append(re.compile("(?s:" + _glob_regex(head) + ")\\Z"))

    def __bool__(self) -> bool:
        return bool(self._self)

    def _hit(self, pats: Sequence[Any], rel: str) -> bool:
        return any(p.match(rel) for p in pats)

    def matches(self, rel: str) -> bool:
        rel = (rel or "").strip("/")
        parts = rel.split("/") if rel else [""]
        for i in range(1, len(parts) + 1):
            if self._hit(self._self, "/".join(parts[:i])):
                return True
        return False

    def covers_dir(self, rel: str) -> bool:
        rel = (rel or "").strip("/")
        parts = rel.split("/") if rel else [""]
        for i in range(1, len(parts) + 1):
            cand = "/".join(parts[:i])
            if self._hit(self._self, cand) or self._hit(self._cover, cand):
                return True
        return False


def privacy_globs(reg: Dict[str, Any]) -> List[str]:
    """Every ``privacy.never_read`` declaration, re-expressed relative to the root.

    Declarations are written relative to the project path (same convention as
    ``ignore_globs``), but privacy has to be enforced globally: a private
    directory nested inside another project's tree must be pruned when *that*
    project is walked too. Rebasing onto the shared root is what makes that
    possible.
    """
    out = set()
    for pr in reg.get("projects", []) or []:
        never = (pr.get("privacy") or {}).get("never_read") or []
        for rel in pr.get("paths", []) or []:
            for g in never:
                if not g:
                    continue
                out.add((rel.strip("/") + "/" + str(g).strip("/")).strip("/"))
    return sorted(out)


def rebase_globs(globs: Sequence[str], base_rel: str) -> List[str]:
    """Re-express root-relative globs against ``base_rel``; drop those outside it."""
    base = (base_rel or "").strip("/")
    out = []
    for g in globs:
        if not base:
            out.append(g)
        elif g == base:
            out.append("**")          # the whole subtree is private
        elif g.startswith(base + "/"):
            out.append(g[len(base) + 1:])
    return out


_GLOBSET_CACHE: Dict[Tuple[str, ...], GlobSet] = {}


def globset(globs: Sequence[str]) -> GlobSet:
    """Compile once per distinct glob list. ``find_private_leaks`` asks the same
    question of every string in the snapshot, so the regexes must not be rebuilt
    per call."""
    key = tuple(globs or ())
    gs = _GLOBSET_CACHE.get(key)
    if gs is None:
        gs = GlobSet(key)
        _GLOBSET_CACHE[key] = gs
    return gs


def _matches_private(value: str, globs: Sequence[str], root_str: str = "") -> bool:
    """True if ``value`` looks like a path under a never_read declaration.

    Matching is structural (the whole string is matched as a path), not
    substring, so human prose that merely *mentions* a private directory is not
    flagged. Only a string that actually is such a path trips it -- including one
    that merely *lives under* a declared directory, which is the case a
    literal-prefix reduction used to miss entirely.
    """
    if not value:
        return False
    gs = globset(globs)
    candidates = [value]
    if root_str and value.startswith(root_str + "/"):
        candidates.append(value[len(root_str) + 1:])
    return any(gs.matches(cand) for cand in candidates)


def find_private_leaks(obj: Any, globs: Sequence[str], root_str: str = "",
                       where: str = "$") -> List[Tuple[str, str]]:
    """Locate any private path inside a structure about to be written or printed.

    The safety net for the privacy rule. The two upstream defences (walk pruning,
    git pathspec exclusion) are the ones that are supposed to work; this one
    exists because they are spread across code that a future change could
    plausibly touch without realising what it is for. It costs one traversal of a
    structure we are already serializing, and it turns a silent privacy
    regression into a failed run.
    """
    leaks: List[Tuple[str, str]] = []
    if not globs:
        return leaks
    if isinstance(obj, dict):
        for k in sorted(obj, key=str):
            leaks.extend(find_private_leaks(obj[k], globs, root_str, "%s.%s" % (where, k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            leaks.extend(find_private_leaks(v, globs, root_str, "%s[%d]" % (where, i)))
    elif isinstance(obj, str):
        if _matches_private(obj, globs, root_str):
            leaks.append((where, obj))
    return leaks


# ---------------------------------------------------------------------------
# Filesystem sensing
# ---------------------------------------------------------------------------

def find_private_paths(base, private: GlobSet) -> List[str]:
    """Base-relative paths a never_read declaration actually covers, outermost first.

    Walking is the point. The globs are patterns, but the git ``:(exclude)``
    pathspec and the file count both need *real* paths, and a wildcard pattern
    cannot be reduced to one: ``**/private/**`` names no directory until you look
    at the tree. Reducing it instead of walking it is how a declared privacy rule
    used to end up enforcing nothing at all, silently.

    Returns ``[""]`` when the whole base is covered.
    """
    base = Path(base)
    if not private or not base.exists():
        return []
    if private.covers_dir(""):
        return [""]
    if base.is_file():
        return [""] if private.matches(base.name) else []

    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(str(base), topdown=True):
        rel_dir = os.path.relpath(dirpath, str(base))
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        keep = []
        for d in sorted(dirnames):
            rel = (rel_dir + "/" + d).strip("/") if rel_dir else d
            if private.covers_dir(rel):
                out.append(rel)      # record the directory, stop descending
            else:
                keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            rel = (rel_dir + "/" + fn).strip("/") if rel_dir else fn
            if private.matches(rel):
                out.append(rel)
    return sorted(out)


def walk_project(root, pfilter: PathFilter, as_of: dt.date,
                 windows: Sequence[int],
                 private: Optional[GlobSet] = None) -> Optional[Dict[str, Any]]:
    """Stat every file under ``root`` once and derive every window from that pass.

    Only ``os.stat`` -- contents are never opened. Returns None if ``root`` is
    absent, which the caller reports as a missing path rather than as zero
    activity. The private file count is deliberately *not* computed here: this
    walk has already pruned the private directories, which is the point.
    """
    counts = dict.fromkeys(windows, 0)
    active_days = set()
    newest_mtime = None
    total_files = 0
    per_file: List[Tuple[float, str]] = []   # only feeds top_changed_paths

    cutoffs = {w: (as_of - dt.timedelta(days=w)) for w in windows}
    max_w = max(windows)

    root = Path(root)
    if not root.exists():
        return None

    for dirpath, dirnames, filenames in os.walk(str(root), topdown=True):
        rel_dir = os.path.relpath(dirpath, str(root))
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        # ``pfilter`` handles the prune-by-name fast path; ``private`` is asked
        # separately because it must understand every glob form a human may have
        # written, and a name it lets through is a leaked name (defence 1 of 3).
        kept = []
        for d in sorted(dirnames):
            if pfilter.prune_dir(rel_dir, d):
                continue
            rel = (rel_dir + "/" + d).strip("/") if rel_dir else d
            if private and private.covers_dir(rel):
                continue
            kept.append(d)
        dirnames[:] = kept
        for fn in sorted(filenames):
            rel = (rel_dir + "/" + fn).strip("/") if rel_dir else fn
            if pfilter.match_file(rel):
                continue
            if private and private.matches(rel):
                continue
            try:
                st = os.stat(os.path.join(dirpath, fn))
            except OSError:
                continue
            total_files += 1
            mday = dt.date.fromtimestamp(st.st_mtime)
            if newest_mtime is None or st.st_mtime > newest_mtime[0]:
                newest_mtime = (st.st_mtime, rel, mday)
            for w in windows:
                if mday > cutoffs[w]:
                    counts[w] += 1
            if mday > cutoffs[max_w]:
                active_days.add(mday.isoformat())
                per_file.append((st.st_mtime, rel))

    per_file.sort(key=lambda t: (-t[0], t[1]))
    return {
        "total_files": total_files,
        "changed": {str(w): counts[w] for w in windows},
        # The caller unions active days across paths, so hand back the set itself
        # rather than a count that cannot be combined.
        "_active_days": sorted(active_days),
        "distinct_active_days_30d": len(active_days),
        "newest_file_mtime": (
            dt.datetime.fromtimestamp(newest_mtime[0]).replace(microsecond=0).isoformat()
            if newest_mtime else None
        ),
        "newest_file_path": newest_mtime[1] if newest_mtime else None,
        "newest_file_date": newest_mtime[2].isoformat() if newest_mtime else None,
        "top_changed_paths": [rel for _, rel in per_file[:8]],
    }


def count_private(root, never_read: Optional[Sequence[str]]) -> int:
    """Return an integer. Never returns, logs, or retains a single file name.

    This walk is intentionally *unfiltered*: it visits exactly the directories
    the main walk now prunes, and that separation is the whole design. Activity
    under a private path stays visible as a number so the brief can say "three
    files changed there" without ever being able to say which.

    The declarations are matched as globs, not reduced to a literal prefix. A
    reduction returned 0 for every wildcard form -- ``**/private/**``, ``x/*``,
    ``*.key`` -- and a count of 0 is indistinguishable from "nothing private
    here", which is precisely the sentence the rule exists to prevent.
    """
    root = Path(root)
    n = 0
    for rel in find_private_paths(root, globset(list(never_read or []))):
        target = (root / rel) if rel else root
        if target.is_file():
            n += 1
            continue
        for _dirpath, _dirnames, filenames in os.walk(str(target)):
            n += len(filenames)
    return n


# ---------------------------------------------------------------------------
# git sensing
# ---------------------------------------------------------------------------

def git_args(cwd, *args: str) -> List[str]:
    """A git command line that cannot write to the repository it is reading.

    Without ``--no-optional-locks`` a plain ``git status`` refreshes and rewrites
    ``.git/index`` -- and takes ``index.lock`` while doing it -- in every
    repository sensing touches. That breaks the "writes nowhere outside the
    workspace" invariant this file opens with, and it can collide with an editor
    or a build running in the same repository at the same time.
    """
    return ["git", "--no-optional-locks", "-C", str(cwd)] + list(args)


def git_toplevel(path, cache: Optional[Dict[str, Optional[str]]] = None) -> Optional[str]:
    """Repository root containing ``path``, or None.

    PERF: memoized per directory, shared across projects for the whole run. The
    win is modest -- registries usually list each path once -- and it cannot be
    made larger by inferring a parent's answer, because a directory under a known
    repository may itself be a nested repository and git must be asked.
    Risk to byte-identical output: none. The repository layout cannot change
    mid-run, so the cache only suppresses duplicate questions.
    """
    key = str(path)
    if cache is not None and key in cache:
        return cache[key]
    ok, out = run_cmd(git_args(key, "rev-parse", "--show-toplevel"))
    top = out.strip() if ok and out.strip() else None
    if cache is not None:
        cache[key] = top
    return top


def repo_subpath(abs_path, top) -> Optional[str]:
    """``abs_path`` expressed inside repository ``top``, in the casing git uses.

    Two everyday macOS layouts used to make this return nonsense, and a nonsense
    pathspec matches nothing while git still exits 0 -- so the project reported
    no branch, no commits and an empty ``parse_failed``: a live project rendered
    as dead.

      * a project reached through a symlink: ``rev-parse --show-toplevel``
        answers with the physical path, so a relpath from the unresolved one
        escapes the repository entirely.
      * a registry path whose case differs from the directory on disk, which a
        case-insensitive filesystem accepts everywhere except in a git pathspec.

    Returns None when the path genuinely is not inside ``top``; the caller must
    treat that as a failure to record, never as silence to report.
    """
    try:
        a = os.path.realpath(str(abs_path))
        t = os.path.realpath(str(top)).rstrip(os.sep)
    except OSError:
        return None
    try:
        rel = os.path.relpath(a, t)
    except ValueError:                       # different drives on Windows
        return None
    if rel.startswith(".."):
        # A case difference *above* the toplevel: realpath does not normalise
        # case, so the two spellings look like two unrelated places.
        if a.lower() == t.lower():
            return "."
        if not a.lower().startswith(t.lower() + os.sep):
            return None
        rel = a[len(t) + 1:]
    if rel == ".":
        return "."

    # Rebuild the tail from the names on disk. A case difference *below* the
    # toplevel produces a perfectly ordinary-looking relative path that every
    # filesystem call accepts and no git pathspec matches.
    cur, out = t, []
    for part in rel.split(os.sep):
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        if part not in names:
            matches = sorted(n for n in names if n.lower() == part.lower())
            if not matches:
                return None
            part = matches[0]
        out.append(part)
        cur = os.path.join(cur, part)
    return "/".join(out)


def repo_private(root, top, private_globs: Sequence[str]) -> Optional[GlobSet]:
    """Root-relative privacy globs, re-expressed against a repository toplevel.

    git reports paths relative to the repository, which is rarely the portfolio
    root. Without this translation a privacy check against git output compares
    two different coordinate systems and passes everything.
    """
    if not private_globs:
        return None
    inside = repo_subpath(top, root)      # the repository sits under the root
    if inside is not None and not inside.startswith(".."):
        return globset(rebase_globs(private_globs, "" if inside == "." else inside))
    outside = repo_subpath(root, top)     # the root sits under the repository
    if outside is not None and not outside.startswith(".."):
        return globset(private_globs if outside == "." else
                       [outside + "/" + g for g in private_globs])
    return None


def git_facts(root, paths: Sequence[str], as_of: dt.date,
              exclude_subpaths: Optional[Sequence[str]] = None,
              toplevel_cache: Optional[Dict[str, Optional[str]]] = None,
              problems: Optional[List[Dict[str, Any]]] = None,
              label: Optional[str] = None):
    """Resolve the toplevel per registered path and group by what git actually says.

    Never assume "project root == repository root". Repositories nest, and one
    repository routinely hosts several registered projects as subdirectories.
    Getting this wrong silently credits one project's commits to another, which
    is the single most damaging kind of error this file can make: it is invisible
    and it reads as fact.

    ``problems`` receives ``parse_failed`` entries. Anything that leaves a
    registered path unrepresented in the answer has to be recorded there: a
    pathspec that resolves to nothing produces exactly the same output as a
    repository with no commits, and only one of those is a fact.
    """
    def fail(path: str, code: str, why: str) -> None:
        if problems is not None:
            problems.append({"path": path, "code": code, "why": why})

    groups: Dict[str, List[str]] = {}
    for rel in paths:
        ap = Path(root) / rel
        if not ap.exists():
            continue
        top = git_toplevel(ap, toplevel_cache)
        if not top:
            continue
        sub = repo_subpath(ap, top)
        if sub is None:
            fail(label or rel, "git_path_unresolved",
                 "%s exists but could not be located inside the repository at %s, "
                 "so its git history is not reported" % (rel, top))
            continue
        groups.setdefault(top, []).append(sub)

    if not groups:
        return None

    repos = []
    for top in sorted(groups):
        specs = sorted(set(groups[top]))
        whole_repo = specs == ["."]
        pathspec: List[str] = []
        if not whole_repo:
            pathspec = list(specs)
        elif exclude_subpaths:
            pathspec = ["."]
        seen_ex = set()
        for ex in exclude_subpaths or []:
            # Caller-supplied order is already deterministic and meaningful
            # (registry order first, privacy exclusions appended); dedupe only.
            if ex in seen_ex:
                continue
            seen_ex.add(ex)
            exa = Path(root) / ex
            exs = repo_subpath(exa, top) if exa.exists() else None
            if exs is None:
                # An exclusion may name a path that no longer exists in the
                # working tree but still exists in history, and dropping it
                # would change the commit counts it was written to correct.
                try:
                    exs = os.path.relpath(str(exa), top).replace(os.sep, "/")
                except ValueError:
                    continue
            if not exs.startswith(".."):
                pathspec.append(":(exclude)" + exs)

        sep = ["--"] + pathspec if pathspec else []

        ok, out = run_cmd(git_args(top, "rev-parse", "--abbrev-ref", "HEAD"))
        branch = out.strip() if ok else None

        # PERF: one `git log -20` serves both the last commit and the recent-sha
        # list; the original spent a separate `git log -1` on what is by
        # definition the first line of this output.
        # Risk to byte-identical output: none -- same traversal, same pathspec,
        # and the split uses maxsplit so a separator inside a subject cannot
        # shift the two sha fields.
        last = None
        recent_shas: List[str] = []
        ok, out = run_cmd(
            git_args(top, "log", "-20",
                     "--format=%H%x1f%h%x1f%ad%x1f%s", "--date=short") + sep
        )
        if ok:
            for ln in out.splitlines():
                p = ln.split("\x1f", 3)
                if len(p) != 4:
                    continue
                recent_shas.extend(p[:2])
                if last is None:
                    last = {"sha": p[0], "short": p[1], "date": p[2], "subject": p[3]}

        # No commits for this pathspec looks exactly like a repository nobody has
        # committed to, and only one of those is a fact. Ask git which it is --
        # but only here, where the answer is already in doubt, because
        # `ls-files` over a large monorepo is not free.
        includes = [s for s in pathspec if not s.startswith(":")]
        if last is None and includes and includes != ["."]:
            has_head, _ = run_cmd(git_args(top, "rev-parse", "--verify", "-q", "HEAD"))
            tracked_ok, tracked = run_cmd(git_args(top, "ls-files", "--") + includes)
            if has_head and not (tracked_ok and tracked.strip()):
                fail(label or top, "git_pathspec_unmatched",
                     "git tracks no file under %s in the repository at %s, so its "
                     "commit history reads as silence rather than as no data"
                     % (", ".join(includes), top))

        # PERF (rejected): these three could be derived from a single
        # `git log --since=<90d> --format=%ct` pass. Not done -- `--since` prunes
        # traversal rather than filtering it, so a repository with skewed commit
        # dates (rebases, imports, cherry-picks) can legitimately give a
        # different count for `--since=7d` than for a 7-day filter over the
        # 90-day list. Risk to byte-identical output: medium, so it stays.
        commits = {}
        for w in (7, 30, 90):
            since = (as_of - dt.timedelta(days=w)).isoformat()
            ok, out = run_cmd(
                git_args(top, "rev-list", "--count", "--since=" + since, "HEAD") + sep
            )
            commits[str(w)] = int(out.strip()) if ok and out.strip().isdigit() else None

        ok, out = run_cmd(git_args(top, "status", "--porcelain") + sep)
        dirty = len([ln for ln in out.splitlines() if ln.strip()]) if ok else None

        ok, out = run_cmd(git_args(top, "remote"))
        has_remote = bool(ok and out.strip())

        repos.append({
            "toplevel": top,
            "pathspec": pathspec,
            "whole_repo": whole_repo and not exclude_subpaths,
            "branch": branch,
            "no_commits": last is None,   # freshly `git init`ed, nothing committed yet
            "last_commit": last,
            "commits_since": commits,
            "uncommitted": dirty,
            "has_remote": has_remote,
            "recent_shas": recent_shas,
        })
    return repos


def scc_complexity(scc_bin: Optional[str], root,
                   cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None):
    """``{relative path: {complexity, lines}}``. None unless ``scc`` is installed.

    PERF: memoized per repository. Several projects can share one repository and
    scanning a large tree is by far the most expensive optional step in the run.
    Risk to byte-identical output: none -- pure function of a directory that is
    not modified during the run.
    """
    if not scc_bin:
        return None
    key = str(root)
    if cache is not None and key in cache:
        return cache[key]

    result = None
    ok, out = run_cmd([scc_bin, "--by-file", "--format", "json", "--no-cocomo", key],
                      timeout=TOOL_TIMEOUT)
    if ok and out.strip():
        try:
            data = json.loads(out)
        except ValueError:
            data = None
        if data:
            m = {}
            for lang in data:
                for f in lang.get("Files", []) or []:
                    loc = f.get("Location") or f.get("Filename")
                    if not loc:
                        continue
                    rel = os.path.relpath(loc, key).replace(os.sep, "/")
                    m[rel] = {"complexity": f.get("Complexity", 0), "lines": f.get("Lines", 0)}
            result = m or None
    if cache is not None:
        cache[key] = result
    return result


def git_hotspots(top, pathspec: Sequence[str], as_of: dt.date, limit: int = 5,
                 scc_map: Optional[Dict[str, Any]] = None,
                 pfilter: Optional[PathFilter] = None,
                 private: Optional[GlobSet] = None):
    """churn x size hotspots. Churn comes from one `git log --numstat` pass.

    The time dimension is the part a code-reading agent cannot get at: it can see
    that a file is complex, but not that it has been rewritten nine times this
    quarter. Real cyclomatic complexity needs ``scc``; without it we use line
    count and say so in the field name rather than pretending.

    ``pfilter`` is the project's own ignore list, and passing it is not cosmetic:
    git reports vendored trees the walk never sees, so without it a committed
    ``node_modules`` or ``vendor`` takes every slot in the list and the one signal
    this function exists to produce is gone.
    """
    since = (as_of - dt.timedelta(days=90)).isoformat()
    sep = ["--"] + list(pathspec) if pathspec else []
    ok, out = run_cmd(
        git_args(top, "log", "--since=" + since, "--numstat", "--format=") + sep,
        timeout=TOOL_TIMEOUT,
    )
    if not ok:
        return None
    churn: Dict[str, int] = {}
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) != 3:
            continue
        a, d, path = parts
        if a == "-" or d == "-":     # binary file
            continue
        if pfilter and pfilter.match_path(path):
            continue
        if private and private.matches(path):
            continue
        try:
            churn[path] = churn.get(path, 0) + int(a) + int(d)
        except ValueError:
            continue
    if not churn:
        return []
    scored = []
    for path in sorted(churn):
        ch = churn[path]
        fp = Path(top) / path
        if not fp.is_file():
            continue
        info = (scc_map or {}).get(path)
        if info:
            weight, lines, cx = info["complexity"] or 1, info["lines"], info["complexity"]
        else:
            try:
                if fp.stat().st_size > 2_000_000:
                    continue
                with open(fp, "rb") as fh:
                    lines = sum(1 for _ in fh)
            except OSError:
                continue
            weight, cx = lines, None
        if lines < 40:
            continue
        scored.append({"path": path, "churn_90d": ch, "lines": lines,
                       "complexity": cx, "score": ch * weight})
    scored.sort(key=lambda r: (-r["score"], r["path"]))
    return scored[:limit]


# ---------------------------------------------------------------------------
# Agent session activity -- stat only, never parsed
# (a single session log runs to tens of megabytes, and its mtime already is the
#  last-activity time we want)
# ---------------------------------------------------------------------------

def scan_sessions(root, projects: Sequence[Dict[str, Any]],
                  sessions_dir=None) -> Dict[str, Dict[str, Any]]:
    slug_to_pid: Dict[str, str] = {}
    for pr in projects:
        for rel in pr.get("paths", []) or []:
            slug_to_pid[slugify_path(Path(root) / rel)] = pr["id"]

    per_project: Dict[str, Dict[str, Any]] = {}
    base = expand(sessions_dir or DEFAULT_SESSIONS_DIR)
    if not base.is_dir():
        return per_project

    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        # Longest matching slug wins, so a project nested inside another one is
        # credited to itself rather than to its parent.
        best = None
        for slug, pid in sorted(slug_to_pid.items()):
            if name == slug or name.startswith(slug + "-"):
                if best is None or len(slug) > len(best[0]):
                    best = (slug, pid)
        if not best:
            continue
        pid = best[1]
        acc = per_project.setdefault(pid, {"session_files": 0, "last_active": None, "days": set()})
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for f in entries:
            if f.suffix != ".jsonl":
                continue
            try:
                st = f.stat()        # stat only. Never open.
            except OSError:
                continue
            acc["session_files"] += 1
            ts = st.st_mtime
            if acc["last_active"] is None or ts > acc["last_active"]:
                acc["last_active"] = ts
            acc["days"].add(dt.date.fromtimestamp(ts).isoformat())

    out = {}
    for pid, acc in per_project.items():
        out[pid] = {
            "session_files": acc["session_files"],
            "last_active": (
                dt.datetime.fromtimestamp(acc["last_active"]).replace(microsecond=0).isoformat()
                if acc["last_active"] else None
            ),
            "last_active_date": (
                dt.date.fromtimestamp(acc["last_active"]).isoformat()
                if acc["last_active"] else None
            ),
            "distinct_session_days": len(acc["days"]),
        }
    return out


# ---------------------------------------------------------------------------
# Status documents: staleness and non-goals
# ---------------------------------------------------------------------------

DATE_RE = r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
DECLARED_PATTERNS = [
    r"(?:>\s*)?\*{0,2}Last updated\*{0,2}\s*[:：]\s*" + DATE_RE,
    r"last_reviewed\s*[:：]\s*['\"]?" + DATE_RE,
    # Same convention in CJK documents. Both alphabets are first-class inputs;
    # a registry is routinely a mix.
    r"(?:最后)?更新(?:日期|时间|于)?\s*[:：]\s*" + DATE_RE,
    r"^\s*(?:created|date)\s*[:：]\s*['\"]?" + DATE_RE,
]


def read_head(path, max_bytes: int = 8000) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(max_bytes)
    except OSError:
        return None


def parse_declared_date(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    for pat in DECLARED_PATTERNS:
        m = re.search(pat, text, re.I | re.M)
        if m:
            try:
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                continue
    return None


def parse_declared_status(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(
        r"^\s*\*{0,2}status\*{0,2}\s*[:：]\s*['\"]?([A-Za-z_][A-Za-z_ -]{1,30})",
        text, re.I | re.M,
    )
    return m.group(1).strip().rstrip("'\"") if m else None


def extract_non_goals(path, heading_hint: str) -> Optional[List[str]]:
    """Lift the first column of a "non-goals" table verbatim.

    Verbatim matters: these are decisions a human made about what *not* to build,
    and a model that paraphrases them will eventually propose one of them back.
    """
    txt = read_head(path, 60000)
    if not txt:
        return None
    lines = txt.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#") and heading_hint in ln:
            start = i
            break
    if start is None:
        return None
    items = []
    seen_header = False
    for ln in lines[start + 1:]:
        s = ln.strip()
        if s.startswith("#"):
            break
        if not s.startswith("|"):
            if items:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if set(first) <= set("-: "):
            seen_header = True
            continue
        if not seen_header:
            continue
        first = re.sub(r"[*`]", "", first).strip()
        if first:
            items.append(first)
    return items or None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def classify_signal(days: Optional[int], cfg: Dict[str, Any]) -> str:
    """Bucket "days since the freshest evidence". A pure threshold function.

    Deliberately not a model's job: if the same facts produced a different word
    on a different day, you would stop trusting the word.
    """
    if days is None:
        return "unknown"
    s = cfg["signal"]
    if days <= s["hot_days"]:
        return "hot"
    if days <= s["warm_days"]:
        return "warm"
    if days <= s["cold_days"]:
        return "cold"
    return "dormant"


def _rank(order: Sequence[str], kind: Optional[str]) -> int:
    """Confidence rank, with unknown kinds sorting last instead of raising."""
    try:
        return list(order).index(kind)
    except ValueError:
        return len(order)


_JSON_KIND = {dict: "object", list: "array", str: "string", bool: "boolean",
              int: "number", float: "number", type(None): "null"}


def _kind(value: Any) -> str:
    if isinstance(value, str) and not value.strip():
        return "an empty string"
    return _JSON_KIND.get(type(value), type(value).__name__)


def check_shapes(cfg: Any, reg: Any) -> None:
    """Reject a malformed-but-parseable registry or config with a sentence.

    Parsing only proves the file is JSON. A registry whose ``projects`` is an
    object, or whose project entry is a bare string, parses cleanly and then dies
    several hundred lines later with an AttributeError and a traceback -- which
    reads as a bug in the tool rather than as a typo in a file the reader owns
    and can fix. The standard here is the one already set by the missing
    ``defaults.root`` message: name the key, say what it must be.
    """
    def want(cond: bool, key: str, expected: str, got: Any) -> None:
        if not cond:
            raise SenseError("%s must be %s, not %s" % (key, expected, _kind(got)))

    want(isinstance(cfg, dict), "config.jsonc", "a JSON object", cfg)
    want(isinstance(reg, dict), "registry.jsonc", "a JSON object", reg)

    for key in ("defaults", "meta"):
        if reg.get(key) is not None:
            want(isinstance(reg[key], dict), "registry %s" % key, "an object", reg[key])

    projects = reg.get("projects")
    if projects is not None:
        want(isinstance(projects, list), "registry projects", "an array", projects)
        for i, pr in enumerate(projects):
            at = "registry projects[%d]" % i
            want(isinstance(pr, dict), at, "an object", pr)
            want(isinstance(pr.get("id"), str) and pr["id"].strip(),
                 "%s.id" % at, "a non-empty string", pr.get("id"))
            for key in ("paths", "ignore_globs", "exclude_subpaths", "status_docs",
                        "deadlines", "hard_rules", "conflicts"):
                if pr.get(key) is not None:
                    want(isinstance(pr[key], list), "%s.%s" % (at, key), "an array", pr[key])
            for key in ("privacy", "ice"):
                if pr.get(key) is not None:
                    want(isinstance(pr[key], dict), "%s.%s" % (at, key), "an object", pr[key])
            never = (pr.get("privacy") or {}).get("never_read")
            if never is not None:
                want(isinstance(never, list), "%s.privacy.never_read" % at,
                     "an array of globs", never)
            for j, sd in enumerate(pr.get("status_docs") or []):
                want(isinstance(sd, dict) and isinstance(sd.get("path"), str),
                     "%s.status_docs[%d]" % (at, j), "an object with a path", sd)

    watch = reg.get("watch")
    if watch is not None:
        want(isinstance(watch, list), "registry watch", "an array", watch)
        for i, w in enumerate(watch):
            want(isinstance(w, dict) and isinstance(w.get("path"), str),
                 "registry watch[%d]" % i, "an object with a path", w)


def resolve_root(ws: Workspace, reg: Dict[str, Any]) -> Path:
    """Resolve ``defaults.root`` once, at the entry point.

    The value is human-authored, so it may contain ``~`` or ``$HOME`` and may be
    relative. It used to be handed straight to ``Path()`` at half a dozen call
    sites, which turned a portable registry into one that silently sensed
    nothing. Raising here is the point: a missing root must never fail open into
    an empty-but-plausible snapshot.
    """
    raw = (reg.get("defaults") or {}).get("root")
    if not raw:
        raise SenseError("registry defaults.root is missing; set it to the directory "
                         "that holds your projects")
    root = expand(raw)
    if not root.is_absolute():
        root = (ws.root / root)
    root = Path(os.path.normpath(str(root)))
    if not root.is_dir():
        raise SenseError("registry defaults.root %r resolves to %s, which does not exist"
                         % (raw, root))
    return root


def build(ws: Workspace, cfg: Dict[str, Any], reg: Dict[str, Any],
          as_of: dt.date, now: dt.datetime, timer: Optional[Timing] = None) -> Dict[str, Any]:
    """Sense everything the registry declares and return the snapshot structure."""
    timer = timer or Timing(False)
    check_shapes(cfg, reg)
    root = resolve_root(ws, reg)
    default_globs = list((reg.get("defaults") or {}).get("ignore_globs") or [])
    windows = cfg["signal"]["windows"]
    stale_days = cfg["signal"]["stale_threshold_days"]
    self_id = (reg.get("defaults") or {}).get("self_project_id")

    # Adopt whatever is in the root that the registry has not spoken for, and do
    # it here -- before anything reads reg["projects"] -- so that privacy globs,
    # the session scan and the main walk all see one list. Merging later would
    # produce a project that is sensed but whose privacy declarations were
    # collected from a registry that had never heard of it.
    #
    # check_shapes has already run against the declared registry above. These
    # entries are synthesised by us and are well-formed by construction, so they
    # are deliberately not re-validated: a failure here would be our bug, and
    # reporting it as the user's malformed registry would send them editing a
    # file that is fine.
    discovered = discover(root, reg, ws)
    if discovered:
        reg = dict(reg)
        reg["projects"] = list(reg.get("projects") or []) + discovered

    # Root-relative privacy globs, applied to every walk regardless of which
    # project declared them (FIX-1a: a private directory nested in another
    # project's tree used to be walked by that project).
    private_globs = privacy_globs(reg)

    parse_failed: List[Dict[str, Any]] = []
    no_declared_date: List[str] = []      # a doc that states no date is a fact, not a fault
    tool_missing: List[Dict[str, str]] = []
    scc_bin = which((cfg.get("external_tools") or {}).get("scc", "scc"))
    if not scc_bin:
        tool_missing.append({
            "tool": "scc",
            "why": "cyclomatic complexity is unavailable; line count is used as the proxy",
        })

    toplevel_cache: Dict[str, Optional[str]] = {}
    scc_cache: Dict[str, Optional[Dict[str, Any]]] = {}

    with timer.phase("sessions"):
        sessions = scan_sessions(root, reg.get("projects", []),
                                 (cfg.get("sessions") or {}).get("dir"))
    evidence_index: Dict[str, Dict[str, Any]] = {}

    def add_ev(source, kind, value=None):
        """One source can satisfy several kinds at once -- a status document is
        both "a doc that declares a date" and "a file with an mtime".

        An earlier version recorded one kind per source and then dropped
        legitimate citations for citing the "wrong" kind. The evidence gate
        exists to stop invention, not to police phrasing.
        """
        if not source:
            return
        e = evidence_index.setdefault(source, {"kinds": [], "value": value})
        if kind not in e["kinds"]:
            e["kinds"].append(kind)
        if value is not None and e.get("value") is None:
            e["value"] = value

    projects_out = []
    for pr in reg.get("projects", []) or []:
        pid = pr["id"]
        paths = list(pr.get("paths", []) or [])
        own_globs = list(pr.get("ignore_globs", []) or [])

        # ---- filesystem ----
        fs_agg: Dict[str, Any] = {
            "total_files": 0, "changed": {str(w): 0 for w in windows},
            "distinct_active_days_30d": 0, "newest_file_mtime": None,
            "newest_file_path": None, "newest_file_date": None,
            "top_changed_paths": [], "missing_paths": [],
        }
        active_days_union = set()
        newest = None
        # Privacy globs rebased onto each declared path, compiled once: the walk
        # prunes with them, and the git exclusions below are derived from the
        # directories they actually match.
        private_by_rel = {rel: globset(rebase_globs(private_globs, rel)) for rel in paths}
        with timer.phase("walk"):
            for rel in paths:
                ap = Path(root) / rel
                if not ap.exists():
                    fs_agg["missing_paths"].append(rel)
                    continue
                # Privacy globs are rebased onto this path so the walk itself can
                # never collect a private file name (defence 1 of 3).
                pfilter = PathFilter(default_globs + own_globs + rebase_globs(private_globs, rel))
                f = walk_project(ap, pfilter, as_of, windows, private=private_by_rel[rel])
                if f is None:
                    parse_failed.append({"path": rel, "code": "walk_failed",
                                         "why": "directory could not be walked"})
                    continue
                fs_agg["total_files"] += f["total_files"]
                for w in windows:
                    fs_agg["changed"][str(w)] += f["changed"][str(w)]
                active_days_union.update(f["_active_days"])   # union, not max
                if f["newest_file_mtime"] and (newest is None or f["newest_file_mtime"] > newest[0]):
                    newest = (f["newest_file_mtime"],
                              "/".join(x for x in (rel, f["newest_file_path"]) if x),
                              f["newest_file_date"])
                fs_agg["top_changed_paths"].extend(
                    ["/".join(x for x in (rel, p) if x) for p in f["top_changed_paths"]]
                )
        fs_agg["distinct_active_days_30d"] = len(active_days_union)
        if newest:
            fs_agg["newest_file_mtime"], fs_agg["newest_file_path"], fs_agg["newest_file_date"] = newest
        fs_agg["top_changed_paths"] = sorted(set(fs_agg["top_changed_paths"]))[:8]

        # ---- privacy: a number, and nothing else ----
        # FIX-1b: its own unfiltered scan, because the walk above deliberately
        # pruned exactly these directories.
        priv = pr.get("privacy") or {}
        private_count = 0
        if priv.get("never_read"):
            with timer.phase("private_count"):
                for rel in paths:
                    private_count += count_private(Path(root) / rel, priv["never_read"])

        # ---- git ----
        git_out = None
        hotspots = None
        if pr.get("git") != "none":
            # Private directories are excluded from the pathspec too, so no file
            # name can arrive through git log/status either (defence 2 of 3).
            #
            # Scoped to this project's own paths, and appended after the author's
            # own exclusions rather than merged and re-sorted. Both details are
            # load-bearing: handing git a pathspec at all switches on history
            # simplification, so giving a whole-repo project an irrelevant
            # exclusion silently changes its commit counts.
            #
            # The exclusions are the directories and files a never_read glob
            # actually matched on disk, not the globs themselves: `:(exclude)`
            # takes a pathspec, and a pattern reduced to a literal prefix
            # produced an empty one -- an exclusion that excluded nothing and
            # said nothing about it.
            own_ex = list(pr.get("exclude_subpaths") or [])
            private_here: List[str] = []
            if private_globs:
                with timer.phase("private_paths"):
                    for rel in paths:
                        gs = private_by_rel.get(rel)
                        if not gs:
                            continue
                        base = rel.strip("/")
                        for m in find_private_paths(Path(root) / rel, gs):
                            private_here.append((base + "/" + m).strip("/") if m else base)
            excludes = own_ex + [d for d in sorted(set(private_here)) if d not in own_ex]
            with timer.phase("git_facts"):
                repos = git_facts(root, paths, as_of, exclude_subpaths=excludes,
                                  toplevel_cache=toplevel_cache,
                                  problems=parse_failed, label=pid)
            if repos:
                git_out = repos
                for r in repos:
                    for sha in r.get("recent_shas", []):
                        add_ev(sha, "commit", r["toplevel"])
                    if r.get("last_commit"):
                        add_ev(r["last_commit"]["sha"], "commit", r["toplevel"])
                        add_ev(r["last_commit"]["short"], "commit", r["toplevel"])
                main_repo = repos[0]
                with timer.phase("scc"):
                    scc_map = scc_complexity(scc_bin, main_repo["toplevel"], scc_cache)
                # git names paths the walk never sees, so the project's own
                # ignore list has to be applied here as well or a vendored tree
                # owns the hotspot list. Prune-by-name globs (`**/vendor/**`)
                # carry across; a path-anchored one only lines up when the
                # project is the repository root, which is the common case.
                with timer.phase("git_hotspots"):
                    hs = git_hotspots(main_repo["toplevel"], main_repo["pathspec"], as_of,
                                      scc_map=scc_map,
                                      pfilter=PathFilter(default_globs + own_globs),
                                      private=repo_private(root, main_repo["toplevel"],
                                                           private_globs))
                if hs is not None:
                    hotspots = hs
            elif paths:
                parse_failed.append({"path": pid, "code": "no_git_toplevel",
                                     "why": "declared as a git project but no repository "
                                            "root could be resolved"})

        # ---- status documents ----
        docs = []
        for sd in pr.get("status_docs", []) or []:
            dp = Path(root) / sd["path"]
            entry: Dict[str, Any] = {"path": sd["path"], "kind": sd.get("kind"),
                                     "authority": sd.get("authority"), "exists": dp.exists()}
            # A registry that declares the same file both "read it" and "never
            # read it" is contradicting itself. Do not open it: the pre-write
            # guard refuses the run over the path a moment later, and it should
            # refuse without this file ever having been read.
            if _matches_private(sd["path"], private_globs):
                docs.append(entry)
                continue
            if dp.exists():
                try:
                    entry["mtime_date"] = dt.date.fromtimestamp(dp.stat().st_mtime).isoformat()
                except OSError:
                    entry["mtime_date"] = None
                head = read_head(dp)
                declared = parse_declared_date(head)
                entry["declared_date"] = declared
                entry["declared_status"] = parse_declared_status(head)
                if declared:
                    try:
                        d = dt.date.fromisoformat(declared)
                        age = (as_of - d).days
                        entry["declared_age_days"] = age
                        entry["stale"] = age > stale_days
                    except ValueError:
                        entry["declared_age_days"] = None
                        entry["stale"] = None
                else:
                    entry["declared_age_days"] = None
                    entry["stale"] = None
                    # "The document states no date" is a fact about the document,
                    # not a broken parser. parse_failed is reserved for real
                    # failures; mixing the two drowns the signal.
                    if sd.get("kind") in ("status", "sprint"):
                        no_declared_date.append(sd["path"])
                if sd.get("known_stale"):
                    entry["registry_known_stale"] = True
                add_ev(sd["path"], "doc_declared", entry.get("declared_date"))
                add_ev(sd["path"], "file_mtime", entry.get("mtime_date"))   # it is also a file
            else:
                parse_failed.append({"path": sd["path"], "code": "status_doc_missing",
                                     "why": "registry points at a status document "
                                            "that does not exist"})
            docs.append(entry)

        # ---- non-goals ----
        non_goals = None
        if pr.get("non_goals_doc"):
            ngp = Path(root) / pr["non_goals_doc"]
            non_goals = extract_non_goals(
                ngp, pr.get("non_goals_heading", DEFAULT_NON_GOALS_HEADING))
            if non_goals is None:
                parse_failed.append({"path": pr["non_goals_doc"], "code": "non_goals_not_found",
                                     "why": "no non-goals table found under that heading"})
            else:
                add_ev(pr["non_goals_doc"], "doc_declared", "non_goals")

        # ---- deadlines: only what a human wrote in the registry ----
        deadlines = []
        for dl in pr.get("deadlines", []) or []:
            try:
                d = dt.date.fromisoformat(dl["date"])
            except (ValueError, KeyError, TypeError):
                parse_failed.append({"path": pid, "code": "bad_deadline",
                                     "why": "deadline date is not a valid ISO date: %s" % (dl,)})
                continue
            days_until = (d - as_of).days
            lead = dl.get("lead_days", 21)
            deadlines.append({
                "date": dl["date"], "label": dl.get("label", ""),
                "days_until": days_until, "lead_days": lead, "hard": dl.get("hard", False),
                "in_lead_window": 0 <= days_until <= lead,
                "overdue": days_until < 0,
            })
            add_ev("deadline:" + dl["date"], "human", dl.get("label"))
        # Stable sort: same-day deadlines keep the order the human wrote them in.
        deadlines.sort(key=lambda x: x["date"])

        # ---- freshest evidence ----
        sess = sessions.get(pid) or {}
        cands: List[Tuple[str, str]] = []
        if git_out:
            for r in git_out:
                if r.get("last_commit"):
                    cands.append(("commit", r["last_commit"]["date"]))
        if fs_agg["newest_file_date"]:
            cands.append(("file_mtime", fs_agg["newest_file_date"]))
        if sess.get("last_active_date"):
            cands.append(("session", sess["last_active_date"]))

        best_kind, best_date, days_since = None, None, None
        conf = cfg["evidence"]["confidence_order"]
        for kind, ds in cands:
            try:
                d = dt.date.fromisoformat(ds)
            except ValueError:
                continue
            age = (as_of - d).days
            if (days_since is None or age < days_since
                    or (age == days_since and _rank(conf, kind) < _rank(conf, best_kind))):
                days_since, best_kind, best_date = age, kind, ds

        for p in fs_agg["top_changed_paths"]:
            add_ev(p, "file_mtime", None)
        for rel in paths:
            add_ev(rel, "file_mtime", None)
        if sess:
            add_ev("session:" + pid, "session", sess.get("last_active_date"))

        no_git = pr.get("git") == "none"
        # Name the measure this list was actually ranked by. `scc` being
        # installed is not the same as `scc` having reported on these files -- it
        # skips languages it does not know, and each such file silently falls
        # back to line count. A label claiming complexity over a list ranked by
        # lines is a fact the reader cannot check and cannot see is wrong. With
        # nothing ranked there is nothing to misdescribe, so the label states the
        # measure that was available.
        used_complexity = (all(h.get("complexity") is not None for h in hotspots)
                           if hotspots else bool(scc_bin))
        if used_complexity:
            hotspot_metric = "churn_90d x cyclomatic complexity (scc)"
        elif scc_bin:
            hotspot_metric = ("churn_90d x line count (scc reported no complexity for "
                              "at least one of these files; lines stand in)")
        else:
            hotspot_metric = ("churn_90d x line count (scc not installed; "
                              "lines stand in for complexity)")
        projects_out.append({
            "id": pid,
            "name": pr.get("name", pid),
            "paths": paths,
            "is_self": bool(self_id) and pid == self_id,
            # False when the entry was synthesised by discovery rather than
            # written by a human: its tier and ICE are placeholders, not choices.
            "declared": not pr.get("discovered", False),
            "tier": pr.get("tier"),
            "goal_one_line": pr.get("goal_one_line"),
            "horizon": pr.get("horizon"),
            "ice": pr.get("ice"),
            "git_declared": pr.get("git", "none"),
            "has_git": bool(git_out),
            "git": git_out,
            "hotspots": hotspots,
            "hotspot_metric": hotspot_metric,
            "hotspot_metric_kind": "complexity" if used_complexity else "lines",
            "fs": fs_agg,
            "private_file_count": private_count,
            "sessions": sess or None,
            "status_docs": docs,
            "non_goals": non_goals,
            "deadlines": deadlines,
            "conflicts": pr.get("conflicts"),
            "hard_rules": pr.get("hard_rules"),
            "has_own_daily_entry": pr.get("has_own_daily_entry"),
            "blocked_by": pr.get("blocked_by"),
            "open_decision": pr.get("open_decision"),
            "external_dependency": pr.get("external_dependency"),
            "automation_surface": pr.get("automation_surface"),
            "neglect_days": pr.get("neglect_days", cfg["neglect"]["default_days"]),
            "live_url": pr.get("live_url"),
            "registry_notes": pr.get("notes"),
            "evidence": {
                "best_kind": best_kind,
                "best_date": best_date,
                "days_since": days_since,
                "signal": classify_signal(days_since, cfg),
                # The brief must name the kind of signal: "76 files changed
                # (file timestamps; this tree has no git)" and "178 commits"
                # should not read the same.
                "caveat_code": "no_git" if (best_kind != "commit" and no_git) else None,
                "caveat": ("no git here, so progress can only be inferred from file "
                           "timestamps and sessions") if (best_kind != "commit" and no_git) else None,
            },
        })

    # ---- watch / infra ----
    watch_out = []
    with timer.phase("watch"):
        for w in reg.get("watch", []) or []:
            ap = Path(root) / w["path"]
            rebased = rebase_globs(private_globs, w["path"])
            pf = PathFilter(default_globs + rebased)
            f = (walk_project(ap, pf, as_of, windows, private=globset(rebased))
                 if ap.exists() else None)
            watch_out.append({
                "path": w["path"], "reason": w.get("reason"), "exists": ap.exists(),
                "newest_file_date": f["newest_file_date"] if f else None,
                "changed_7d": f["changed"].get("7") if f else None,
                "days_since": days_between(
                    dt.date.fromisoformat(f["newest_file_date"]), as_of
                ) if f and f["newest_file_date"] else None,
            })

    slot = cfg["schedule"]["slot"]
    try:
        hh, mm = [int(x) for x in slot.split(":")]
        planned = dt.datetime.combine(as_of, dt.time(hh, mm))
        lateness = int((now - planned).total_seconds() // 60)
    except Exception:
        lateness = None
        parse_failed.append({"path": "config.jsonc", "code": "bad_slot",
                             "why": "schedule.slot could not be parsed as HH:MM"})

    for e in evidence_index.values():
        e["kinds"] = sorted(e["kinds"])   # idempotence: never depend on visit order

    return {
        "schema_version": 2,
        "run": {
            "generated_at": now.replace(microsecond=0).isoformat(),
            "as_of_date": as_of.isoformat(),
            "planned_slot": slot,
            "lateness_minutes": lateness,
            "late": (lateness is not None and lateness > cfg["schedule"]["late_warn_minutes"]),
            "generator": "nextbrief.sense",
            "generator_version": __version__,
        },
        "registry_meta": reg.get("meta"),
        "projects": projects_out,
        "watch": watch_out,
        "infra": reg.get("infra"),
        "archived": reg.get("archived"),
        "evidence_index": evidence_index,
        "parse_failed": parse_failed,
        "docs_without_declared_date": sorted(no_declared_date),
        "tool_missing": tool_missing,
    }


def load_backlog_summary(ws: Workspace) -> List[Dict[str, Any]]:
    """Fold every backlog entry's frontmatter into one compact list.

    This exists for cost, not tidiness. Measured on the first real run: the model
    issued a separate Read for each of fourteen backlog files plus two reads of a
    100 KB snapshot, taking 36 turns. Cached-input cost is roughly turns x context
    size, so that came to millions of tokens for a single nightly brief. Folded
    into one file the model reads once and the turn count drops to single digits.
    """
    out: List[Dict[str, Any]] = []
    bl = ws.backlog
    if not bl.is_dir():
        return out
    for f in sorted(bl.glob("*.md")):
        if f.name.startswith("_"):
            continue
        try:
            fm, _body = parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not fm:
            continue
        a = fm.get("automation") or {}
        src = fm.get("source") or {}
        out.append({
            "id": fm.get("id"), "file": f.name, "title": fm.get("title"),
            "project": fm.get("project"), "status": fm.get("status"),
            "priority": fm.get("priority"), "blocked_by": fm.get("blocked_by"),
            "is_next_action": fm.get("is_next_action"),
            "automation_tier": a.get("tier") if isinstance(a, dict) else None,
            "what_needs_human": a.get("what_needs_human") if isinstance(a, dict) else None,
            "next_probe": a.get("next_probe") if isinstance(a, dict) else None,
            "human_confirmed": fm.get("human_confirmed"),
            "source_doc": src.get("doc") if isinstance(src, dict) else None,
            "estimate_min": fm.get("estimate_min"),
        })
    return out


def build_digest(ws: Workspace, snap: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The model-facing input. The full snapshot stays behind for the evidence gate.

    Each project block carries its own list of legal citation handles (``cite``),
    so the model cites things it can actually see and every citation resolves in
    ``evidence_index`` without shipping that whole index into the context.
    """
    projs = []
    for p in snap["projects"]:
        # The workspace's own project is sensed like any other -- it earns its
        # keep or it does not -- but the model is not asked to plan maintenance
        # on its own tooling every night.
        if p.get("is_self"):
            continue
        cite = list(p.get("paths") or [])
        for r in p.get("git") or []:
            if r.get("last_commit"):
                cite.append(r["last_commit"]["short"])
        if p.get("sessions"):
            cite.append("session:" + p["id"])
        for d in p.get("status_docs") or []:
            if d.get("exists"):
                cite.append(d["path"])
        cite.extend((p["fs"].get("top_changed_paths") or [])[:3])
        for dl in p.get("deadlines") or []:
            cite.append("deadline:" + dl["date"])

        g0 = (p.get("git") or [{}])[0]
        projs.append({
            "id": p["id"], "name": p["name"], "tier": p["tier"],
            # Whether a human wrote this entry. Without it a null tier and a
            # stated one are indistinguishable to stage 2, which is exactly the
            # confusion that makes a model fill the gap with something plausible.
            "declared": p.get("declared", True),
            "goal": p.get("goal_one_line"),
            "signal": p["evidence"]["signal"],
            "days_since_evidence": p["evidence"]["days_since"],
            "evidence_kind": p["evidence"]["best_kind"],
            "no_git": p.get("git_declared") == "none",
            "facts": {
                "commits_30d": (g0.get("commits_since") or {}).get("30"),
                "last_commit": (g0.get("last_commit") or {}).get("date"),
                "last_commit_subject": (g0.get("last_commit") or {}).get("subject"),
                "uncommitted": g0.get("uncommitted"),
                "files_changed_7d": p["fs"]["changed"].get("7"),
                "active_days_30d": p["fs"].get("distinct_active_days_30d"),
                "session_days": (p.get("sessions") or {}).get("distinct_session_days"),
                "newest_file": p["fs"].get("newest_file_path"),
            },
            "deadlines": [d for d in (p.get("deadlines") or [])
                          if d["in_lead_window"] or d["overdue"]],
            "all_deadlines": [{"date": d["date"], "label": d["label"],
                               "days_until": d["days_until"]}
                              for d in (p.get("deadlines") or [])],
            "blocked_by": p.get("blocked_by"),
            "open_decision": p.get("open_decision"),
            "external_dependency": p.get("external_dependency"),
            "has_own_daily_entry": p.get("has_own_daily_entry"),
            "hard_rules": p.get("hard_rules"),
            "non_goals": p.get("non_goals"),
            "stale_docs": [{"path": d["path"], "declared": d.get("declared_date"),
                            "age_days": d.get("declared_age_days")}
                           for d in (p.get("status_docs") or []) if d.get("stale")],
            "conflicts": p.get("conflicts"),
            "automation_surface": p.get("automation_surface"),
            "notes": p.get("registry_notes"),
            "cite": sorted({c for c in cite if c}),
        })

    return {
        "_readme": ("The only input to stage 2. Each project's `cite` list is the set of "
                    "evidence sources that project may be cited with -- cite what you can "
                    "see and nothing you write will be dropped by the evidence gate. The "
                    "full data lives in snapshot.json, which the renderer checks you against."),
        "run": snap["run"],
        "caps": cfg["caps"],
        "limits": cfg["limits"],
        "projects": projs,
        "backlog": load_backlog_summary(ws),
        "watch": snap.get("watch"),
        "health": {
            "parse_failed": snap.get("parse_failed"),
            "docs_without_declared_date": snap.get("docs_without_declared_date"),
            "tool_missing": snap.get("tool_missing"),
        },
    }


def canonical(snap: Dict[str, Any]) -> str:
    """A generated artifact minus the wall clock, for idempotence comparison.

    Used on both the snapshot and the digest: `run` is the only block either one
    carries that legitimately differs between two runs of the same inputs.
    """
    c = dict(snap)
    c.pop("run", None)
    return json.dumps(c, ensure_ascii=False, sort_keys=True, indent=2)


def _dump(obj: Any) -> str:
    # sort_keys is load-bearing, not cosmetic: it is what makes two runs
    # byte-comparable and therefore what makes --check mean anything.
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _parse_as_of(raw: Optional[str]) -> Tuple[dt.date, dt.datetime]:
    """Resolve ``(as_of, now)``. Both are injected so runs are reproducible.

    With ``--as-of`` the clock is pinned as well, otherwise a test comparing two
    runs would still differ in the ``run`` block. A bare date pins the clock to
    midday, which is arbitrary but stated rather than accidental.
    """
    if not raw:
        now = dt.datetime.now()
        return now.date(), now
    if "T" in raw:
        now = dt.datetime.fromisoformat(raw)
        return now.date(), now
    day = dt.date.fromisoformat(raw)
    return day, dt.datetime.combine(day, dt.time(12, 0))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="nextbrief sense",
        description="stage 1 -- deterministic sensing, no model",
    )
    ap.add_argument("--workspace", help="workspace directory (default: discovered)")
    ap.add_argument("--out", help="directory for generated files (default: the workspace)")
    ap.add_argument("--check", action="store_true",
                    help="exit 3 if the snapshot or the digest content would change")
    ap.add_argument("--stdout", action="store_true", help="print the snapshot, write nothing")
    ap.add_argument("--as-of", dest="as_of", metavar="ISO",
                    help="pin the run date (YYYY-MM-DD or a full ISO timestamp)")
    ap.add_argument("--timing", action="store_true", help="print phase timings to stderr")
    args = ap.parse_args(argv)

    timer = Timing(args.timing)
    # One handler per input. A shared try block reported an undecodable
    # config.jsonc as an "--as-of" problem and a broken registry as a problem
    # with config.jsonc, which sends the reader to edit a file that is fine.
    try:
        ws = resolve_workspace(args.workspace, out=args.out)
    except WorkspaceError as exc:
        print("sense: %s" % exc, file=sys.stderr)
        return EXIT_ERROR

    loaded = {}
    with timer.phase("load_config"):
        for name, path in (("config", ws.config_path), ("registry", ws.registry_path)):
            try:
                loaded[name] = load_jsonc(path)
            except JSONCError as exc:
                print("sense: %s" % exc, file=sys.stderr)
                return EXIT_ERROR
            except (OSError, ValueError) as exc:
                # UnicodeDecodeError is a ValueError, and it arrives from
                # read_text rather than from the parser, so JSONCError does not
                # cover it. Name the file either way.
                print("sense: cannot read %s: %s" % (path, exc), file=sys.stderr)
                return EXIT_ERROR
    cfg, reg = loaded["config"], loaded["registry"]

    try:
        as_of, now = _parse_as_of(args.as_of)
    except ValueError as exc:
        print("sense: --as-of is not a valid ISO date: %s" % exc, file=sys.stderr)
        return EXIT_ERROR

    try:
        check_shapes(cfg, reg)
        root_str = str(resolve_root(ws, reg))
        with timer.phase("build"):
            snap = build(ws, cfg, reg, as_of, now, timer=timer)
        with timer.phase("digest"):
            digest = build_digest(ws, snap, cfg)
    except SenseError as exc:
        print("sense: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except KeyError as exc:
        # A missing config key is a configuration error, not a crash worth a
        # traceback; every other failure mode in this file fails open.
        print("sense: config.jsonc is missing required key %s" % exc, file=sys.stderr)
        return EXIT_ERROR

    text = _dump(snap)
    dtext = _dump(digest)

    # FIX-1c: refuse to emit anything containing a never_read path. This runs
    # before --stdout too, because printing a private file name leaks it just as
    # thoroughly as writing it. Kept as a separate, dumb, final check precisely
    # so that it keeps working after someone rewrites the walk.
    globs = privacy_globs(reg)
    leaks = (find_private_leaks(snap, globs, root_str, "snapshot")
             + find_private_leaks(digest, globs, root_str, "digest"))
    if leaks:
        print("sense: refusing to write -- %d path(s) covered by privacy.never_read "
              "reached the output:" % len(leaks), file=sys.stderr)
        for where, _value in leaks[:10]:
            # Report the location, never the leaked value itself.
            print("  at %s" % where, file=sys.stderr)
        print("  privacy.never_read paths may contribute a count and nothing else. "
              "If the registry itself points a status_doc or a project path inside "
              "a never_read tree, that contradiction is the cause; otherwise this "
              "is a bug in sense.", file=sys.stderr)
        return EXIT_PRIVACY

    if args.stdout:
        sys.stdout.write(text)
        timer.report(sys.stderr)
        return EXIT_OK

    if args.check:
        timer.report(sys.stderr)
        # Both artifacts, not just the snapshot. The backlog lives only in the
        # digest, so every `ok` / `done` / `drop` -- the commands the brief itself
        # tells you to run -- changes what the next render produces while leaving
        # the snapshot identical. Checking the snapshot alone reported "current"
        # for a brief that was already wrong, and a scheduler running
        # `nextbrief check || nextbrief run` therefore never re-ran.
        for label, path, fresh in (("snapshot", ws.snapshot, snap),
                                   ("digest", ws.digest, digest)):
            if not path.exists():
                print("sense: %s does not exist yet" % path, file=sys.stderr)
                return EXIT_STALE
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                print("sense: %s cannot be read back; treating it as out of date" % path,
                      file=sys.stderr)
                return EXIT_STALE
            if canonical(old) != canonical(fresh):
                print("sense: %s is out of date (non-timestamp fields changed)" % label,
                      file=sys.stderr)
                return EXIT_STALE
        print("sense: snapshot and digest are current")
        return EXIT_OK

    ws.ensure_dirs()
    if ws.snapshot.exists():
        # Yesterday's snapshot is what the renderer diffs against; losing it
        # costs a day of "what changed", so a failed rotation is not fatal.
        try:
            write_text(ws, ws.snapshot_prev, ws.snapshot.read_text(encoding="utf-8"),
                       skip_identical=False)
        except OSError:
            pass
    # skip_identical=False: the sense stage rewrites unconditionally, which is
    # what its own --check determinism test was written against.
    write_text(ws, ws.snapshot, text, skip_identical=False)
    write_text(ws, ws.digest, dtext, skip_identical=False)

    n_hot = sum(1 for p in snap["projects"] if p["evidence"]["signal"] == "hot")
    print("sense: %d projects | %d hot | %d parse failures | snapshot %.0fKB / digest %.0fKB"
          % (len(snap["projects"]), n_hot, len(snap["parse_failed"]),
             len(text) / 1024, len(dtext) / 1024))
    for t in snap["tool_missing"]:
        print("  optional tool missing: %s -- %s" % (t["tool"], t["why"]))
    timer.report(sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
