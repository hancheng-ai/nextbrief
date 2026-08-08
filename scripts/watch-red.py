#!/usr/bin/env python3
"""Watch each guard fail, one mutation at a time, without the loop lying to you.

    scripts/watch-red.py                       every mutation in the manifest
    scripts/watch-red.py --only schema         just the ones whose label matches
    scripts/watch-red.py --quick               skip the closing full-suite run
    scripts/watch-red.py --list                what is in the manifest

Rule 7 of CONTRIBUTING.md asks you to break the line a test covers, watch the
test go red, and put the line back. Done by hand that is three steps, and on
macOS two of them can silently not happen.

The pinned `/usr/bin/python3` caches bytecode *outside* the checkout, under
`~/Library/Caches/com.apple.python/<absolute path of the source>/`, and those
`.pyc` files use timestamp invalidation: they are reused whenever they agree
with the source on whole-second mtime and byte size. A mutation like
`SCHEMA_VERSION = 1` -> `= 2` changes neither.

So the hand-run loop has two failure modes that point opposite ways:

  * revert inside the mutation's second and the cached mutant outlives the
    revert -- the guard looked red, and is not;
  * mutate inside the warm run's second and the mutant is never compiled --
    the guard looked unable to fail, and is not.

`git diff` is clean through both. Hence four defences, all of them here:

  1. purge that cache for this checkout before starting and after finishing,
     along with any in-tree `__pycache__` (Linux has the same (mtime, size)
     rule, just in a different directory);
  2. `-B` on the interpreter and `PYTHONDONTWRITEBYTECODE=1` in the
     environment, so neither this process nor anything it spawns writes one;
  3. a distinct mtime stamped after every write, so a size-preserving edit
     invalidates any cache that survived 1 and 2 anyway;
  4. a per-mutation sentinel: after the revert the same test must go GREEN
     again. A poisoned cache then surfaces on the mutation that caused it
     instead of as a baffling result fifteen steps later.

Two further rules about what counts as watched, both learned the hard way:

  * the red run must *name* the manifest's `expect` string. A test that goes
    red for an unrelated reason has not been watched failing, it has just been
    broken.
  * an anchor that does not appear in its file exactly once is an error, not a
    skip. A mutation that cannot be applied proves nothing, and a harness that
    shrugs at it reports a pass it did not earn.

Exit codes: 0 every guard was watched failing and recovered; 1 at least one was
not; 2 the run could not be made trustworthy -- a bad manifest, an unresolvable
anchor, a revert that did not restore the file. The last is a failure on
purpose, because a check that could not run must never read as one that passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

EXIT_OK, EXIT_NOT_WATCHED, EXIT_ERROR = 0, 1, 2

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "tests" / "mutations.json"

REQUIRED = ("label", "file", "old", "new", "select", "expect")

# Where the suite imports from, and the only places this script will delete a
# cache. Deliberately not a walk from the repository root: a checkout here can
# hold git worktrees under `.claude/`, and those are somebody else's tree.
CODE_DIRS = ("src", "tests", "scripts")

# Apple's framework build redirects bytecode here rather than to an in-tree
# __pycache__. The path is the source directory's absolute path appended whole.
APPLE_CACHE = Path.home() / "Library" / "Caches" / "com.apple.python"

ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

# Whole seconds, stepped well clear of any real mtime the tree already has, so
# every write in a run lands in a second of its own. Defence 3.
STEP = 10


def purge_cache() -> None:
    """Remove every bytecode cache that could speak for this checkout.

    Both locations, because the trap is not macOS-specific: an in-tree
    `__pycache__` on Linux is invalidated by the same (mtime, size) pair and
    lies in exactly the same way.
    """
    mirror = APPLE_CACHE / str(REPO).lstrip("/")
    for rel in CODE_DIRS:
        shutil.rmtree(mirror / rel, ignore_errors=True)
        for stale in (REPO / rel).rglob("__pycache__"):
            shutil.rmtree(stale, ignore_errors=True)
    for stray in mirror.glob("*.pyc"):              # modules at the repo root
        stray.unlink()


def test_command(select: str) -> list[str]:
    """The one place the runner is named. `-B` is defence 2."""
    return [sys.executable, "-B", "-m", "unittest", "discover",
            "-s", "tests", "-k", select]


def run_tests(select: str) -> subprocess.CompletedProcess:
    return subprocess.run(test_command(select), cwd=str(REPO),
                          capture_output=True, text=True, env=ENV)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> list[dict]:
    """Read the manifest and refuse anything that would weaken the run."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        sys.stderr.write("watch-red: cannot read %s: %s\n" % (path, exc))
        raise SystemExit(EXIT_ERROR) from None
    except json.JSONDecodeError as exc:
        sys.stderr.write("watch-red: %s is not valid JSON: %s\n" % (path, exc))
        raise SystemExit(EXIT_ERROR) from None

    mutations = raw.get("mutations") if isinstance(raw, dict) else raw
    if not isinstance(mutations, list) or not mutations:
        sys.stderr.write("watch-red: %s lists no mutations.\n" % path)
        raise SystemExit(EXIT_ERROR)

    problems = []
    for i, m in enumerate(mutations):
        if not isinstance(m, dict):
            problems.append("#%d is not an object" % i)
            continue
        missing = [k for k in REQUIRED if not m.get(k)]
        if missing:
            problems.append("#%d (%s) is missing %s"
                            % (i, m.get("label", "unlabelled"), ", ".join(missing)))
        elif m["old"] == m["new"]:
            problems.append("#%d (%s) mutates nothing" % (i, m["label"]))
        elif not (REPO / m["file"]).is_file():
            problems.append("#%d (%s) targets %s, which does not exist"
                            % (i, m["label"], m["file"]))
    if problems:
        sys.stderr.write("watch-red: the manifest cannot be trusted:\n")
        for p in problems:
            sys.stderr.write("  - %s\n" % p)
        raise SystemExit(EXIT_ERROR)
    return mutations


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=Path, default=MANIFEST,
                    help="mutation list (default: tests/mutations.json)")
    ap.add_argument("--only", metavar="TEXT",
                    help="run only mutations whose label contains TEXT")
    ap.add_argument("--list", action="store_true",
                    help="print the manifest and exit without touching anything")
    ap.add_argument("--quick", action="store_true",
                    help="skip the closing full-suite run (minutes). Implied by"
                         " --only; either way the skip is reported, never silent")
    args = ap.parse_args()

    # A full manifest takes minutes. Redirected to a file or a pipe, stdout is
    # block-buffered and shows nothing at all until the run ends, which reads
    # exactly like a hang.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:                          # pragma: no cover
        pass

    mutations = load(args.manifest)
    if args.only:
        mutations = [m for m in mutations if args.only.lower() in m["label"].lower()]
        if not mutations:
            sys.stderr.write("watch-red: no mutation label contains %r\n" % args.only)
            return EXIT_ERROR

    if args.list:
        for m in mutations:
            print("  %-58s %s :: %s" % (m["label"], m["file"], m["select"]))
        print("\n%d mutations" % len(mutations))
        return EXIT_OK

    purge_cache()                                   # defence 1
    clock = _base_stamp()
    watched, unwatched = 0, []
    # Every file this run stamps, with the mtime it had when we found it, so the
    # tree is handed back exactly as borrowed. Restored last, after the closing
    # purge, where it can no longer make a cache look current.
    borrowed: dict[Path, float] = {}

    for m in mutations:
        path = REPO / m["file"]
        original = path.read_bytes()
        before = digest(path)
        text = original.decode("utf-8")
        borrowed.setdefault(path, path.stat().st_mtime)

        seen = text.count(m["old"])
        if seen != 1:
            sys.stderr.write(
                "\nwatch-red: %s -- the anchor appears %d times in %s, so the"
                " mutation is ambiguous.\n         A mutation that cannot be"
                " applied proves nothing. Fix the manifest.\n"
                % (m["label"], seen, m["file"]))
            purge_cache()
            return EXIT_ERROR

        clock += STEP
        _write(path, text.replace(m["old"], m["new"]).encode("utf-8"), clock)
        try:
            red = run_tests(m["select"])
        finally:
            clock += STEP
            _write(path, original, clock)

        if digest(path) != before:
            sys.stderr.write("\nwatch-red: the revert did not restore %s. Stopping"
                             " with the tree dirty -- check `git diff`.\n" % m["file"])
            return EXIT_ERROR

        out = red.stdout + red.stderr
        failed = red.returncode != 0
        named = m["expect"] in out
        green_again = run_tests(m["select"]).returncode == 0   # defence 4

        ran = next((ln for ln in out.splitlines() if ln.startswith("Ran ")), "?")
        print("%-5s %-58s %-16s %s"
              % ("RED" if failed else "GREEN", m["label"], ran,
                 "green again" if green_again else "*** STILL RED AFTER REVERT"))

        if failed and named and green_again:
            watched += 1
            continue

        unwatched.append(m["label"])
        if not failed:
            print("      !!! did not fail -- this guard is not watching anything")
        elif not named:
            print("      !!! failed without mentioning %r -- red for the wrong"
                  " reason" % m["expect"])
            for ln in out.splitlines():
                if "Error" in ln or "assert" in ln.lower():
                    print("        | " + ln[:150])
        if not green_again:
            print("      !!! the file is back but the test is not. A stale"
                  " bytecode cache does exactly this.")

    skip_final = args.quick or bool(args.only)
    final = None
    if not skip_final:
        final = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover",
                                "-s", "tests"], cwd=str(REPO), capture_output=True,
                               text=True, env=ENV)
    purge_cache()
    for path, mtime in borrowed.items():            # hand the tree back as found
        os.utime(path, (mtime, mtime))

    print("\n%d/%d guards watched failing and recovering."
          % (watched, watched + len(unwatched)))
    if unwatched:
        print("Not watched:")
        for label in unwatched:
            print("  - %s" % label)

    if skip_final:
        print("\nThe closing full-suite run was SKIPPED (%s), so this run has not"
              " shown\nthat the tree is whole -- only that each mutation reverted"
              " byte-for-byte.\nRun without it before you believe the number"
              " above."
              % ("--quick" if args.quick else "--only"))
        return EXIT_OK if not unwatched else EXIT_NOT_WATCHED
    if final.returncode != 0:
        print("\nwatch-red: the suite does NOT pass with every mutation reverted."
              "\nSomething did not go back. Check `git status` before trusting"
              " anything above.")
        return EXIT_ERROR
    print("suite green with the tree restored.")
    return EXIT_OK if not unwatched else EXIT_NOT_WATCHED


def _base_stamp() -> int:
    """Start the clock above every source mtime the interpreter may have cached.

    Not `time.time()`: on a checkout whose files were written this second, the
    first mutation would land in the same second as whatever compiled them --
    which is the second of the two failures in this script's docstring.
    """
    newest = 0
    for rel in CODE_DIRS:
        for p in (REPO / rel).rglob("*.py"):
            newest = max(newest, int(p.stat().st_mtime))
    return newest + STEP


def _write(path: Path, data: bytes, stamp: int) -> None:
    path.write_bytes(data)
    os.utime(path, (stamp, stamp))                  # defence 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\nwatch-red: interrupted -- check `git status`, a"
                         " mutation may still be applied.\n")
        sys.exit(EXIT_ERROR)
