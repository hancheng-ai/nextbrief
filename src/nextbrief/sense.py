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

Usage:
  sense                write state/snapshot.json and state/digest.json
  sense --check        exit 3 if the snapshot content would change
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
from .frontmatter import parse_frontmatter
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

def _literal_dir(glob: str) -> Optional[str]:
    """The literal directory a glob names, or None if it is a wildcard pattern.

    ``member/**`` -> ``member``; ``**/*.wav`` -> None. Used where we need a real
    path (a git pathspec, a directory to count) rather than a matcher.
    """
    g = (glob or "").strip("/")
    if g.endswith("/**"):
        g = g[:-3]
    if not g or any(ch in g for ch in "*?["):
        return None
    return g


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


def _matches_private(value: str, globs: Sequence[str], root_str: str = "") -> bool:
    """True if ``value`` looks like a path under a never_read declaration.

    Matching is structural (fnmatch against the whole string), not substring, so
    human prose that merely *mentions* a private directory is not flagged. Only a
    string that actually is such a path trips it.
    """
    if not value:
        return False
    candidates = [value]
    if root_str and value.startswith(root_str + "/"):
        candidates.append(value[len(root_str) + 1:])
    for cand in candidates:
        cand = cand.strip("/")
        for pat in globs:
            if fnmatch.fnmatch(cand, pat):
                return True
    return False


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

def walk_project(root, pfilter: PathFilter, as_of: dt.date,
                 windows: Sequence[int]) -> Optional[Dict[str, Any]]:
    """Stat every file under ``root`` once and derive every window from that pass.

    Only ``os.stat`` -- contents are never opened. Returns None if ``root`` is
    absent, which the caller reports as a missing path rather than as zero
    activity. The private file count is deliberately *not* computed here: this
    walk has already pruned the private directories, which is the point.
    """
    counts = {w: 0 for w in windows}
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
        dirnames[:] = sorted(d for d in dirnames if not pfilter.prune_dir(rel_dir, d))
        for fn in sorted(filenames):
            rel = (rel_dir + "/" + fn).strip("/") if rel_dir else fn
            if pfilter.match_file(rel):
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
    """
    n = 0
    for pat in never_read or []:
        base = _literal_dir(pat)
        if not base:
            continue
        d = Path(root) / base
        if not d.exists():
            continue
        if d.is_file():
            n += 1
            continue
        for _dirpath, _dirnames, filenames in os.walk(str(d)):
            n += len(filenames)
    return n


# ---------------------------------------------------------------------------
# git sensing
# ---------------------------------------------------------------------------

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
    ok, out = run_cmd(["git", "-C", key, "rev-parse", "--show-toplevel"])
    top = out.strip() if ok and out.strip() else None
    if cache is not None:
        cache[key] = top
    return top


def git_facts(root, paths: Sequence[str], as_of: dt.date,
              exclude_subpaths: Optional[Sequence[str]] = None,
              toplevel_cache: Optional[Dict[str, Optional[str]]] = None):
    """Resolve the toplevel per registered path and group by what git actually says.

    Never assume "project root == repository root". Repositories nest, and one
    repository routinely hosts several registered projects as subdirectories.
    Getting this wrong silently credits one project's commits to another, which
    is the single most damaging kind of error this file can make: it is invisible
    and it reads as fact.
    """
    groups: Dict[str, List[str]] = {}
    for rel in paths:
        ap = Path(root) / rel
        if not ap.exists():
            continue
        top = git_toplevel(ap, toplevel_cache)
        if not top:
            continue
        try:
            sub = os.path.relpath(str(ap), top).replace(os.sep, "/")
        except ValueError:
            continue
        groups.setdefault(top, []).append("." if sub == "." else sub)

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
            try:
                exs = os.path.relpath(str(exa), top).replace(os.sep, "/")
            except ValueError:
                continue
            if not exs.startswith(".."):
                pathspec.append(":(exclude)" + exs)

        sep = ["--"] + pathspec if pathspec else []

        ok, out = run_cmd(["git", "-C", top, "rev-parse", "--abbrev-ref", "HEAD"])
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
            ["git", "-C", top, "log", "-20",
             "--format=%H%x1f%h%x1f%ad%x1f%s", "--date=short"] + sep
        )
        if ok:
            for ln in out.splitlines():
                p = ln.split("\x1f", 3)
                if len(p) != 4:
                    continue
                recent_shas.extend(p[:2])
                if last is None:
                    last = {"sha": p[0], "short": p[1], "date": p[2], "subject": p[3]}

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
                ["git", "-C", top, "rev-list", "--count", "--since=" + since, "HEAD"] + sep
            )
            commits[str(w)] = int(out.strip()) if ok and out.strip().isdigit() else None

        ok, out = run_cmd(["git", "-C", top, "status", "--porcelain"] + sep)
        dirty = len([ln for ln in out.splitlines() if ln.strip()]) if ok else None

        ok, out = run_cmd(["git", "-C", top, "remote"])
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
                 scc_map: Optional[Dict[str, Any]] = None):
    """churn x size hotspots. Churn comes from one `git log --numstat` pass.

    The time dimension is the part a code-reading agent cannot get at: it can see
    that a file is complex, but not that it has been rewritten nine times this
    quarter. Real cyclomatic complexity needs ``scc``; without it we use line
    count and say so in the field name rather than pretending.
    """
    since = (as_of - dt.timedelta(days=90)).isoformat()
    sep = ["--"] + list(pathspec) if pathspec else []
    ok, out = run_cmd(
        ["git", "-C", top, "log", "--since=" + since, "--numstat", "--format="] + sep,
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
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
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
    root = resolve_root(ws, reg)
    default_globs = list((reg.get("defaults") or {}).get("ignore_globs") or [])
    windows = cfg["signal"]["windows"]
    stale_days = cfg["signal"]["stale_threshold_days"]
    self_id = (reg.get("defaults") or {}).get("self_project_id")

    # Root-relative privacy globs, applied to every walk regardless of which
    # project declared them (FIX-1a: a private directory nested in another
    # project's tree used to be walked by that project).
    private_globs = privacy_globs(reg)
    private_dirs = sorted({d for d in (_literal_dir(g) for g in private_globs) if d})

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
        with timer.phase("walk"):
            for rel in paths:
                ap = Path(root) / rel
                if not ap.exists():
                    fs_agg["missing_paths"].append(rel)
                    continue
                # Privacy globs are rebased onto this path so the walk itself can
                # never collect a private file name (defence 1 of 3).
                pfilter = PathFilter(default_globs + own_globs + rebase_globs(private_globs, rel))
                f = walk_project(ap, pfilter, as_of, windows)
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
            own_ex = list(pr.get("exclude_subpaths") or [])
            bases = [p.strip("/") for p in paths]
            excludes = own_ex + [
                d for d in private_dirs
                if d not in own_ex and any(d == b or d.startswith(b + "/") for b in bases)
            ]
            with timer.phase("git_facts"):
                repos = git_facts(root, paths, as_of, exclude_subpaths=excludes,
                                  toplevel_cache=toplevel_cache)
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
                with timer.phase("git_hotspots"):
                    hs = git_hotspots(main_repo["toplevel"], main_repo["pathspec"], as_of,
                                      scc_map=scc_map)
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
        projects_out.append({
            "id": pid,
            "name": pr.get("name", pid),
            "paths": paths,
            "is_self": bool(self_id) and pid == self_id,
            "tier": pr.get("tier"),
            "goal_one_line": pr.get("goal_one_line"),
            "horizon": pr.get("horizon"),
            "ice": pr.get("ice"),
            "git_declared": pr.get("git", "none"),
            "has_git": bool(git_out),
            "git": git_out,
            "hotspots": hotspots,
            "hotspot_metric": ("churn_90d x cyclomatic complexity (scc)" if scc_bin
                               else "churn_90d x line count (scc not installed; "
                                    "lines stand in for complexity)"),
            "hotspot_metric_kind": "complexity" if scc_bin else "lines",
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
            pf = PathFilter(default_globs + rebase_globs(private_globs, w["path"]))
            f = walk_project(ap, pf, as_of, windows) if ap.exists() else None
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
            "cite": sorted(set(c for c in cite if c)),
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
    """The snapshot minus the wall clock, for idempotence comparison."""
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
                    help="exit 3 if the snapshot content would change")
    ap.add_argument("--stdout", action="store_true", help="print the snapshot, write nothing")
    ap.add_argument("--as-of", dest="as_of", metavar="ISO",
                    help="pin the run date (YYYY-MM-DD or a full ISO timestamp)")
    ap.add_argument("--timing", action="store_true", help="print phase timings to stderr")
    args = ap.parse_args(argv)

    timer = Timing(args.timing)
    try:
        ws = resolve_workspace(args.workspace, out=args.out)
        with timer.phase("load_config"):
            cfg = load_jsonc(ws.config_path)
            reg = load_jsonc(ws.registry_path)
        as_of, now = _parse_as_of(args.as_of)
    except (WorkspaceError, JSONCError) as exc:
        print("sense: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print("sense: --as-of is not a valid ISO date: %s" % exc, file=sys.stderr)
        return EXIT_ERROR

    try:
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
        print("  privacy.never_read paths may contribute a count and nothing else; "
              "this is a bug in sense, not in your registry.", file=sys.stderr)
        return EXIT_PRIVACY

    if args.stdout:
        sys.stdout.write(text)
        timer.report(sys.stderr)
        return EXIT_OK

    if args.check:
        timer.report(sys.stderr)
        if not ws.snapshot.exists():
            print("sense: %s does not exist yet" % ws.snapshot, file=sys.stderr)
            return EXIT_STALE
        try:
            old = json.loads(ws.snapshot.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return EXIT_STALE
        if canonical(old) != canonical(snap):
            print("sense: snapshot is out of date (non-timestamp fields changed)",
                  file=sys.stderr)
            return EXIT_STALE
        print("sense: snapshot is current")
        return EXIT_OK

    ws.ensure_dirs()
    if ws.snapshot.exists():
        # Yesterday's snapshot is what the renderer diffs against; losing it
        # costs a day of "what changed", so a failed rotation is not fatal.
        try:
            ws.snapshot_prev.write_text(ws.snapshot.read_text(encoding="utf-8"),
                                        encoding="utf-8")
        except OSError:
            pass
    ws.snapshot.write_text(text, encoding="utf-8")
    ws.digest.write_text(dtext, encoding="utf-8")

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
