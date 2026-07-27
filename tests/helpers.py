"""Fixtures shared by the test suite.

Three properties every test here depends on, all of them established in this file
rather than in each test:

* **No wall clock.** Every workspace is sensed with a pinned ``--as-of`` and every
  file and commit carries a fixed timestamp, so "hot / warm / cold" is a function
  of the fixture and not of the day the suite runs.
* **No machine state.** ``HOME`` and ``XDG_CONFIG_HOME`` are redirected into the
  temporary directory, so a test can never read the developer's real workspace
  pointer, agent session logs or global git identity -- and, just as importantly,
  can never write to them.
* **No network and no model.** Nothing below shells out to anything except git.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

# Importable without PYTHONPATH so `python3 -m unittest discover -s tests` works
# from a plain checkout, which is what CONTRIBUTING tells a first-time reader to run.
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

EXAMPLE_WORKSPACE = REPO_ROOT / "examples" / "workspace"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_EXAMPLE_BUILT = False


def ensure_example_built() -> None:
    """Generate examples/workspace/projects/ if it is not there yet.

    The project tree is generated rather than committed -- it contains real git
    repositories, and its file mtimes have to be pinned for the snapshot to be
    reproducible. That means a fresh checkout has no fixture at all, and the
    whole sense/render suite fails with 'defaults.root ... does not exist',
    which reads as a code bug rather than a missing build step.

    Building on demand keeps `python -m unittest discover -s tests` working as
    the first thing a contributor types, with no README lookup in between.
    """
    global _EXAMPLE_BUILT
    if _EXAMPLE_BUILT or (EXAMPLE_WORKSPACE / "projects").is_dir():
        _EXAMPLE_BUILT = True
        return
    script = EXAMPLE_WORKSPACE / "scripts" / "build-example.sh"
    if not script.is_file():
        raise unittest.SkipTest("example workspace build script is missing: %s" % script)
    proc = subprocess.run(
        ["bash", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "could not build the example workspace (%s):\n%s"
            % (script, proc.stdout.decode("utf-8", "replace"))
        )
    _EXAMPLE_BUILT = True


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

# The date the fixtures are written to be read on. Deadlines, staleness windows
# and commit dates below are all calibrated against it.
AS_OF = "2026-03-16"
AS_OF_DATE = dt.date(2026, 3, 16)

# Two days before AS_OF: recent enough to count as "hot" under any sane config.
RECENT_MTIME = dt.datetime(2026, 3, 14, 9, 0).timestamp()
# Well outside every window, for a project that is supposed to look dormant.
OLD_MTIME = dt.datetime(2025, 11, 2, 9, 0).timestamp()

GIT_DATE = "2026-03-10T09:00:00+00:00"
GIT_NAME = "Example User"
GIT_EMAIL = "example@example.invalid"

HAS_GIT = shutil.which("git") is not None

requires_git = unittest.skipUnless(HAS_GIT, "git is not installed")


# ---------------------------------------------------------------------------
# process / environment plumbing
# ---------------------------------------------------------------------------


def git(cwd, *args, **kwargs):
    """Run git with a pinned identity and pinned dates.

    Author *and* committer dates are set: a repository built with only the author
    date pinned still hashes differently on every run, which would defeat the
    byte-identical assertions further down.
    """
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": GIT_NAME,
            "GIT_AUTHOR_EMAIL": GIT_EMAIL,
            "GIT_COMMITTER_NAME": GIT_NAME,
            "GIT_COMMITTER_EMAIL": GIT_EMAIL,
            "GIT_AUTHOR_DATE": kwargs.get("when", GIT_DATE),
            "GIT_COMMITTER_DATE": kwargs.get("when", GIT_DATE),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        env=env,
        capture_output=True,
    )


def git_init(root) -> None:
    """A repository that owes nothing to the machine's global git config."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    git(root, "config", "user.name", GIT_NAME)
    git(root, "config", "user.email", GIT_EMAIL)
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "core.autocrlf", "false")


def git_commit_all(root, message="fixture", when=GIT_DATE) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message, when=when)


def set_mtime(path, when=RECENT_MTIME) -> None:
    """Pin one file's timestamps. Sensing reads mtime, so this is what decides
    every activity count in a fixture."""
    os.utime(str(path), (when, when))


def set_tree_mtime(root, when=RECENT_MTIME) -> None:
    """Pin a whole tree, skipping ``.git`` -- git rewrites those files as it works
    and their timestamps are never sensed anyway."""
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            with contextlib.suppress(OSError):
                set_mtime(os.path.join(dirpath, name), when)


def capture(func, *args, **kwargs):
    """Call ``func`` and return ``(exit_code, stdout, stderr)``.

    ``SystemExit`` is caught because argparse raises it for ``--help`` and for a
    bad subcommand, and both are outcomes the CLI tests assert on.
    """
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = func(*args, **kwargs)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return int(code or 0), out.getvalue(), err.getvalue()


def read_jsonl(path):
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def tree_state(root):
    """``{relative path: (size, mtime_ns)}`` for a whole tree.

    The containment test compares this before and after a full run. Size *and*
    mtime, because a rewrite with identical length would otherwise slip through.
    """
    root = Path(root)
    state = {}
    for path in sorted(root.rglob("*")):
        try:
            st = path.stat()
        except OSError:
            continue
        rel = str(path.relative_to(root))
        state[rel] = (st.st_size, st.st_mtime_ns) if path.is_file() else ("dir", None)
    return state


# ---------------------------------------------------------------------------
# workspace fixtures
# ---------------------------------------------------------------------------

# Everything the pipeline stages read out of config. Kept complete rather than
# minimal: sense raises on a missing key by design, and a test that discovers
# that one key at a time teaches nothing.
BASE_CONFIG = {
    "locale": "en",
    "schedule": {"slot": "21:30", "late_warn_minutes": 360},
    "signal": {
        "hot_days": 3,
        "warm_days": 10,
        "cold_days": 30,
        "stale_threshold_days": 21,
        "windows": [1, 7, 30],
    },
    "evidence": {
        "confidence_order": ["commit", "session", "file_mtime", "doc_declared", "human", "none"],
        "none_allowed_pattern": "no signal",
    },
    "scoring": {
        "half_life_days": 21,
        "decay_floor": 0.3,
        "tier_weight": {"flagship": 1.3, "active": 1.0, "maintenance": 0.6, "dormant": 0.4},
        "deadline_boost_max": 3.0,
    },
    "neglect": {"default_days": 30},
    "caps": {
        "max_next_actions": 3,
        "max_waiting_for": 5,
        "max_agent_queue": 3,
        "max_decision_pending": 3,
        "per_project_line_chars": 140,
        # Deliberately far above the shipped default: several tests assert on the
        # reminders block, which the 60-line ceiling would legitimately cut.
        "brief_max_lines": 400,
    },
    "limits": {"max_open_items_total": 40, "max_open_per_project": 5},
    "notify": {"enabled": False, "backend": "none", "only_if": []},
    "model": {"provider": "none"},
    # Pointed at a directory the fixture never creates, so no test can be
    # perturbed by the agent session logs of whoever is running the suite.
    "sessions": {"dir": "./no-such-sessions"},
}


def base_registry():
    """A two-project portfolio: one git project with a non-goals table, one
    without version control that declares a private directory."""
    return {
        "meta": {"owner": "Example User", "last_reviewed": AS_OF},
        "defaults": {
            "root": "./projects",
            "ignore_globs": ["**/.git/**", "**/__pycache__/**", "**/.DS_Store"],
        },
        "projects": [
            {
                "id": "orchard",
                "name": "Orchard",
                "paths": ["orchard"],
                "git": "auto",
                "tier": "flagship",
                "goal_one_line": "Decide whether the tenancy rewrite ships this quarter",
                "ice": {"impact": 5, "confidence": 3, "effort": 4},
                "status_docs": [
                    {"path": "orchard/PROJECT_STATUS.md", "kind": "status", "authority": "high"}
                ],
                "non_goals_doc": "orchard/README.md",
                "non_goals_heading": "Explicitly not doing",
                "neglect_days": 21,
            },
            {
                "id": "kiln",
                "name": "Kiln",
                "paths": ["kiln"],
                "git": "none",
                "tier": "maintenance",
                "goal_one_line": "Keep the batch runner alive; no new features",
                "ice": {"impact": 2, "confidence": 4, "effort": 2},
                "privacy": {
                    "never_read": ["fixtures/private/**"],
                    "reason": "Captured payloads kept as regression fixtures. Count them, never read them.",
                },
                "status_docs": [
                    {"path": "kiln/README.md", "kind": "status", "authority": "medium"}
                ],
            },
        ],
        "watch": [],
    }


# File names chosen to be unmistakable in a grep: if any of these strings turns up
# in a snapshot, the privacy rule has been broken and nothing else looks like this.
PRIVATE_FILES = (
    "ledger-capture-0001.json",
    "ledger-capture-0002.json",
    "ledger-capture-0003.json",
)

ORCHARD_README = """\
# Orchard

> Last updated: 2026-03-10

A fictional service used to exercise the sensing stage.

## Explicitly not doing

| Not doing | Why |
|---|---|
| Build a mobile app | The audience is on desktop; a second client doubles the surface |
| Add a plugin system | Nobody has asked twice for the same extension |
"""

ORCHARD_STATUS = """\
# Project status

> Last updated: 2026-03-10

Status: active

The tenancy benchmark harness records per-tenant timings.
"""


def make_workspace(root, registry=None, config=None, with_git=True):
    """Create a complete, sensible workspace at ``root`` and return it as a Path.

    ``with_git`` controls only whether the *project* is a repository; whether the
    workspace itself is one is left to the caller, because the write-permission
    gate's behaviour with and without a baseline is itself under test.
    """
    root = Path(root)
    (root / "backlog").mkdir(parents=True, exist_ok=True)
    (root / "prompts").mkdir(parents=True, exist_ok=True)

    reg = base_registry() if registry is None else registry
    cfg = json.loads(json.dumps(BASE_CONFIG)) if config is None else config
    (root / "registry.jsonc").write_text(
        "// fixture registry\n" + json.dumps(reg, indent=2) + "\n", encoding="utf-8"
    )
    (root / "config.jsonc").write_text(
        "// fixture config\n" + json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
    )

    orchard = root / "projects" / "orchard"
    (orchard / "src").mkdir(parents=True, exist_ok=True)
    (orchard / "README.md").write_text(ORCHARD_README, encoding="utf-8")
    (orchard / "PROJECT_STATUS.md").write_text(ORCHARD_STATUS, encoding="utf-8")
    (orchard / "src" / "main.py").write_text(
        "\n".join("LINE = %d" % i for i in range(80)) + "\n", encoding="utf-8"
    )

    kiln = root / "projects" / "kiln"
    (kiln / "fixtures" / "private").mkdir(parents=True, exist_ok=True)
    (kiln / "README.md").write_text(
        "# Kiln\n\n> Last updated: 2026-02-01\n\nBatch runner.\n", encoding="utf-8"
    )
    for name in PRIVATE_FILES:
        (kiln / "fixtures" / "private" / name).write_text('{"payload": 1}\n', encoding="utf-8")

    if with_git and HAS_GIT:
        git_init(orchard)
        git_commit_all(orchard, "orchard: fixture history")

    set_tree_mtime(root / "projects", RECENT_MTIME)
    return root


def write_backlog_item(ws_root, item_id, **fields):
    """One backlog entry, written in the frontmatter subset the schema documents."""
    data = {
        "id": item_id,
        "title": "Do the thing",
        "project": "orchard",
        "status": "open",
        "priority": 2,
        "is_next_action": True,
        "human_confirmed": False,
        "created_by": "human",
        "updated_date": AS_OF,
    }
    data.update(fields)
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, list):
            rendered = "[%s]" % ", ".join(str(v) for v in value)
        else:
            rendered = str(value)
        lines.append("%s: %s" % (key, rendered))
    lines.append("---")
    lines.append("")
    lines.append("## Acceptance")
    lines.append("")
    lines.append("- [ ] It is done")
    lines.append("")
    path = Path(ws_root) / "backlog" / ("%s.md" % item_id)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# handcrafted snapshots, for the gate tests
# ---------------------------------------------------------------------------


def make_project_entry(pid="orchard", **over):
    """A snapshot project entry with every field the renderer reads.

    Handcrafted rather than sensed: the gate tests are about what the renderer
    does with a given fact, and deriving the fact from a filesystem would make
    each of them depend on the sensing stage as well.
    """
    entry = {
        "id": pid,
        "name": pid.title(),
        "paths": [pid],
        "is_self": False,
        "tier": "flagship",
        "goal_one_line": "A fictional goal",
        "ice": {"impact": 5, "confidence": 3, "effort": 4},
        "git_declared": "auto",
        "has_git": True,
        "git": [
            {
                "toplevel": "/example/%s" % pid,
                "pathspec": [],
                "whole_repo": True,
                "branch": "main",
                "no_commits": False,
                "last_commit": {
                    "sha": "0" * 40,
                    "short": "0000000",
                    "date": "2026-03-10",
                    "subject": "fixture",
                },
                "commits_since": {"7": 2, "30": 9, "90": 20},
                "uncommitted": 0,
                "has_remote": False,
                "recent_shas": ["0" * 40, "0000000"],
            }
        ],
        "hotspots": [],
        "hotspot_metric_kind": "lines",
        "fs": {
            "total_files": 12,
            "changed": {"1": 1, "7": 4, "30": 9},
            "distinct_active_days_30d": 5,
            "newest_file_mtime": "2026-03-14T09:00:00",
            "newest_file_path": "%s/README.md" % pid,
            "newest_file_date": "2026-03-14",
            "top_changed_paths": ["%s/README.md" % pid],
            "missing_paths": [],
        },
        "private_file_count": 0,
        "sessions": None,
        "status_docs": [
            {
                "path": "%s/PROJECT_STATUS.md" % pid,
                "kind": "status",
                "authority": "high",
                "exists": True,
                "mtime_date": "2026-03-14",
                "declared_date": "2026-03-10",
                "declared_status": "active",
                "declared_age_days": 6,
                "stale": False,
            }
        ],
        "non_goals": None,
        "deadlines": [],
        "conflicts": None,
        "hard_rules": None,
        "has_own_daily_entry": None,
        "blocked_by": None,
        "open_decision": None,
        "external_dependency": None,
        "automation_surface": [],
        "neglect_days": 30,
        "registry_notes": None,
        "evidence": {
            "best_kind": "commit",
            "best_date": "2026-03-14",
            "days_since": 2,
            "signal": "hot",
            "caveat_code": None,
            "caveat": None,
        },
    }
    entry.update(over)
    return entry


def make_snapshot(projects=None, evidence_index=None, **over):
    snap = {
        "schema_version": 2,
        "run": {
            "generated_at": "2026-03-16T12:00:00",
            "as_of_date": AS_OF,
            "planned_slot": "21:30",
            "lateness_minutes": -570,
            "late": False,
            "generator": "nextbrief.sense",
            "generator_version": "test",
        },
        "registry_meta": {"owner": "Example User"},
        "projects": [make_project_entry()] if projects is None else projects,
        "watch": [],
        "infra": None,
        "archived": None,
        "evidence_index": {
            "orchard/PROJECT_STATUS.md": {
                "kinds": ["doc_declared", "file_mtime"],
                "value": "2026-03-10",
            },
            "orchard/README.md": {"kinds": ["file_mtime"], "value": None},
            "0000000": {"kinds": ["commit"], "value": "/example/orchard"},
        }
        if evidence_index is None
        else evidence_index,
        "parse_failed": [],
        "docs_without_declared_date": [],
        "tool_missing": [],
    }
    snap.update(over)
    return snap


def write_snapshot(ws_root, snapshot):
    state = Path(ws_root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "snapshot.json"
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_brief_json(ws_root, brief):
    state = Path(ws_root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "brief.json"
    path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# base test case
# ---------------------------------------------------------------------------


class TempCase(unittest.TestCase):
    """A temporary directory plus an environment that cannot reach the real machine.

    ``HOME`` and ``XDG_CONFIG_HOME`` are redirected because ``init`` writes a
    workspace pointer under the config home and sensing looks for agent sessions
    under ``~``. A suite that leaves either pointing at the developer's account
    can silently overwrite the workspace they use every day.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="nextbrief-test-")
        # Resolved: on macOS the temp root is reached through a symlink, and git
        # reports the physical path. Comparing one against the other turns every
        # repository-relative pathspec into nonsense.
        self.tmp = Path(self._tmp).resolve()
        self.home = self.tmp / "home"
        self.xdg = self.home / ".config"
        self.xdg.mkdir(parents=True, exist_ok=True)

        self._env = dict(os.environ)
        for name in ("NEXTBRIEF_WORKSPACE", "NEXTBRIEF_OUT", "NEXTBRIEF_LOCALE", "NEXTBRIEF_AGENT"):
            os.environ.pop(name, None)
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.xdg)
        self._cwd = os.getcwd()
        self.addCleanup(self._restore)

    def _restore(self):
        with contextlib.suppress(OSError):
            os.chdir(self._cwd)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- convenience --------------------------------------------------------

    def workspace(self, name="ws", **kwargs):
        return make_workspace(self.tmp / name, **kwargs)

    def copy_example(self, name="example"):
        """A private copy of ``examples/workspace``, inputs only.

        Copied rather than used in place for two reasons: the run writes
        ``state/`` and ``BRIEF.md`` into the workspace, and the repository's own
        example must not become test output.

        Generated artifacts are excluded rather than copied. A contributor who
        has run the example once would otherwise inherit its ``state/`` here, and
        every test asserting on a first run -- that ``--stdout`` writes nothing,
        that a missing snapshot reports itself missing -- would fail for a reason
        that has nothing to do with the code. CI never sees it, because a fresh
        checkout has no artifacts; only the person who actually used the example
        does. The list mirrors examples/workspace/.gitignore.
        """
        ensure_example_built()
        dest = self.tmp / name
        shutil.copytree(
            str(EXAMPLE_WORKSPACE),
            str(dest),
            symlinks=True,
            ignore=shutil.ignore_patterns("state", "log", "BRIEF.md", "BRIEF.html"),
        )
        return dest
