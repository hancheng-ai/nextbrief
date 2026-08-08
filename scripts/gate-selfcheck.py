#!/usr/bin/env python3
"""Make the *absence* of the pre-push fence look different from its success.

    scripts/gate-selfcheck.py         on a clone: is the fence actually on?
    scripts/gate-selfcheck.py --ci    on a runner: is the fence intact and red-capable?

The failure this exists for. `.githooks/pre-push` was written, documented in
CONTRIBUTING.md, and referenced from CI -- and on the machine that mattered it
had never been activated, because `core.hooksPath` needs setting once per clone
and nobody set it. It stayed off for weeks. Nothing anywhere said so. A borrowed
project name went out in two releases underneath that silence.

The shape of that failure is the point: **a gate that was never installed and a
gate that passed produce exactly the same log -- nothing.** Every other check in
this repository can be watched failing (see tests/test_leak_shapes.py, and rule 7
in CONTRIBUTING.md). This one could not, because there was nothing to watch.

So this script asks three questions, and is deliberately answered differently
depending on where it runs.

1. **Is the fence wired?** `.githooks/pre-push` present, executable, and actually
   invoking the scanner; `scripts/leak-shapes.py` present. A renamed or deleted
   scanner turns the hook into a no-op that still exits 0. Failure everywhere,
   CI included -- this one is a property of the repository, not of a clone.

2. **Can the fence go red?** A throwaway repository is built, scanned clean, then
   baited with each publishable shape and scanned again. Clean must be 0 and
   baited must be 1. One half alone proves nothing: a scanner that always exits 0
   passes the first, and one that always exits 1 passes the second. Failure
   everywhere.

3. **Is the fence switched on here?** `core.hooksPath` resolving to a directory
   that holds an executable `pre-push`.

   Question 3 is the one that caught nobody, and it is also the one a CI runner
   cannot honestly answer. A runner clones fresh and has no hook by construction,
   so asserting it there would make the job permanently red -- and a gate that is
   always red is a gate people route around, which is the same as no gate. On a
   runner it is therefore a warning that says plainly what CI does not know. On a
   working clone it is a hard failure with the command that fixes it.

That asymmetry is the whole design, and it is why "at least warn" is the honest
ceiling in CI rather than a weaker choice. What CI *does* enforce is 1 and 2:
that the fence a contributor is told to switch on is still there and still able
to fail. What no runner can tell you is whether the person pushing had it on.

Exit codes: 0 ok, 1 the gate is absent or broken, 2 the check could not run --
the last a failure on purpose, because a check that cannot run must never be
mistaken for one that passed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK, EXIT_ABSENT, EXIT_ERROR = 0, 1, 2

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / ".githooks" / "pre-push"
SCANNER = REPO / "scripts" / "leak-shapes.py"

ACTIVATE = "git config core.hooksPath .githooks"

# Assembled at run time rather than written out, so this file does not itself
# contain the strings it plants -- the same reason tests/test_leak_shapes.py does
# it. Excluding this file from the scan instead would create an unscanned file,
# which is a place to hide something.
BAIT = "\n".join([
    "/" + "Users" + "/someone/Projects/thing",
    "-----BEGIN" + " RSA PRIVATE KEY-----",
    "ghp_" + "a" * 24,
])


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def note(kind: str, message: str, ci: bool) -> None:
    """Print a finding, as a GitHub annotation when a runner is watching."""
    if ci and kind in ("warning", "error"):
        # Annotations are single-line; the detail lines are printed separately.
        print("::%s::%s" % (kind, message.replace("\n", " ")))
    print("  %s: %s" % (kind.upper(), message))


def check_wiring(ci: bool) -> bool:
    """The fence exists in the repository and points at something real."""
    ok = True
    if not HOOK.is_file():
        note("error", "%s is missing -- the fence is not in the repository."
             % HOOK.relative_to(REPO), ci)
        return False
    if not os.access(HOOK, os.X_OK):
        note("error", "%s is not executable, so git will not run it."
             % HOOK.relative_to(REPO), ci)
        ok = False
    if not SCANNER.is_file():
        note("error", "%s is missing -- the hook would run and find nothing to"
             " call, which exits 0 and reads as a pass."
             % SCANNER.relative_to(REPO), ci)
        return False
    if SCANNER.name not in HOOK.read_text(encoding="utf-8"):
        note("error", "%s does not mention %s. The hook and the scanner have"
             " drifted apart, and a hook that calls nothing still exits 0."
             % (HOOK.relative_to(REPO), SCANNER.name), ci)
        ok = False
    if ok:
        print("  ok: %s is executable and calls %s"
              % (HOOK.relative_to(REPO), SCANNER.name))
    return ok


def check_can_go_red(ci: bool) -> bool:
    """Watch the scanner pass on a clean tree and fail on a baited one.

    Both halves, because either alone is satisfied by a scanner stuck on one
    answer. This is the only part of the fence a runner can genuinely exercise.
    """
    if not shutil.which("git"):
        note("error", "git is not installed, so the fence could not be"
             " exercised. Not a pass.", ci)
        return False

    tmp = tempfile.mkdtemp(prefix="gate-selfcheck-")
    try:
        init = run(["git", "-c", "init.defaultBranch=main", "init", "-q", tmp])
        if init.returncode != 0:
            note("error", "could not create a scratch repository: %s"
                 % init.stderr.strip(), ci)
            return False

        clean = Path(tmp) / "clean.txt"
        clean.write_text("nothing to see here\n", encoding="utf-8")
        first = run([sys.executable, str(SCANNER), "--worktree"], cwd=tmp)
        if first.returncode != EXIT_OK:
            note("error", "the scanner reports findings on a clean tree"
                 " (exit %d). A guard that is always red is one people route"
                 " around." % first.returncode, ci)
            return False

        (Path(tmp) / "baited.txt").write_text(BAIT + "\n", encoding="utf-8")
        second = run([sys.executable, str(SCANNER), "--worktree"], cwd=tmp)
        if second.returncode != EXIT_ABSENT:
            note("error", "the scanner did NOT reject planted shapes (exit %d)."
                 " The fence cannot fail, so its silence means nothing."
                 % second.returncode, ci)
            return False

        print("  ok: the scanner is quiet on a clean tree and rejects planted"
              " shapes")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def hooks_dir() -> Path | None:
    """The directory git will actually take hooks from, if any."""
    out = run(["git", "config", "--get", "core.hooksPath"], cwd=REPO)
    if out.returncode != 0 or not out.stdout.strip():
        return None
    raw = Path(out.stdout.strip()).expanduser()
    # git does not expand `~`, and a relative path is taken from the top level.
    return raw if raw.is_absolute() else (REPO / raw)


def check_activated(ci: bool) -> bool:
    """Is a pre-push hook actually going to run on this clone?

    In CI this can only ever be "no", so it is reported as what it is: a thing
    this job does not know. Anywhere else, "no" is the bug.
    """
    path = hooks_dir()
    hook = path / "pre-push" if path else None
    live = bool(hook and hook.is_file() and os.access(hook, os.X_OK))

    if live:
        print("  ok: core.hooksPath -> %s, which holds an executable pre-push"
              % path)
        return True

    if ci:
        note("warning",
             "CI cannot tell whether the author's clone had the pre-push fence"
             " switched on. A green build is not the privacy gate -- the fence"
             " runs locally, before objects leave the machine, and this job runs"
             " after they have already landed.", ci)
        print("        Passes 2 and 3 of the full scan read files that exist on"
              " one machine.")
        print("        CI green means the fence is INTACT, never that it RAN.")
        return True

    if path is None:
        note("error", "core.hooksPath is unset: this clone has no pre-push"
             " fence at all.", ci)
    else:
        note("error", "core.hooksPath is %s, which has no executable pre-push."
             % path, ci)
    print("        Nothing has been checking your pushes. Switch it on:")
    print()
    print("            %s" % ACTIVATE)
    print()
    print("        Hooks are not cloned, so this is once per clone. Maintainers")
    print("        with the fuller local scanner point it at that instead.")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ci", action="store_true",
                    help="a runner has no hook by construction: warn rather"
                         " than fail on activation, and say what is unknown")
    args = ap.parse_args()

    ci = args.ci or bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))

    if run(["git", "rev-parse", "--git-dir"], cwd=REPO).returncode != 0:
        sys.stderr.write("gate-selfcheck: not a git repository\n")
        return EXIT_ERROR

    print("gate-selfcheck: %s" % ("CI mode -- the fence itself cannot run here"
                                  if ci else "checking this clone"))

    results = [
        check_wiring(ci),
        check_can_go_red(ci),
        check_activated(ci),
    ]

    if all(results):
        print("gate-selfcheck: ok")
        return EXIT_OK
    print("\nThe pre-push fence is what keeps unpublishable content off a public"
          "\nserver. See the \"No personal data, ever\" section of CONTRIBUTING.md.")
    return EXIT_ABSENT


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_ERROR)
