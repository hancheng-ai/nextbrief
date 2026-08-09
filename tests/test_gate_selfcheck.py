"""The check that notices when the pre-push fence is not there.

Every other guard in this repository can be watched failing. This one could not,
and that is the failure it exists for: `.githooks/pre-push` was written and
documented and never activated on the machine that mattered, because
`core.hooksPath` needs setting once per clone. **A gate that was never installed
and a gate that passed produce the same log -- nothing.** Two releases went out
carrying a borrowed project name underneath that silence.

So, in the idiom of tests/test_leak_shapes.py: break the fence five ways, watch
the self-check reject each one, and only then assert it is quiet on a fence that
is whole (CONTRIBUTING rule 7).

The last class is the one that actually would have caught it -- it points the
self-check at *this* clone rather than a scratch one. It is skipped on a runner,
which has no hook by construction, and the skip reason says so rather than
letting a silent skip read as a pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TempCase, requires_git, requires_posix_dev_env

REPO = Path(__file__).resolve().parent.parent
SELFCHECK = REPO / "scripts" / "gate-selfcheck.py"

EXIT_OK, EXIT_ABSENT = 0, 1

ON_CI = bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))


class ScratchClone(TempCase):
    """A throwaway repository carrying a copy of the fence, breakable at will.

    A copy rather than the real tree: the point is to remove the scanner and
    chmod the hook, and doing that to the working clone would leave the author
    without a fence if the test ever failed halfway.
    """

    def setUp(self):
        super().setUp()
        self.clone = self.tmp / "clone"
        (self.clone / "scripts").mkdir(parents=True)
        (self.clone / ".githooks").mkdir(parents=True)
        for name in ("gate-selfcheck.py", "leak-shapes.py"):
            shutil.copy2(REPO / "scripts" / name, self.clone / "scripts" / name)
        shutil.copy2(REPO / ".githooks" / "pre-push", self.clone / ".githooks" / "pre-push")
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q",
                        str(self.clone)], check=True)

    def check(self, *args, ci=False):
        """Run the copied self-check inside the copied clone.

        The environment is PINNED, not inherited. `gate-selfcheck` decides
        between failing and warning by reading `CI` / `GITHUB_ACTIONS`, so a
        test that inherits them asserts one thing on a laptop and the opposite
        on a runner -- which is exactly what happened: the local-case tests
        below passed here and failed on every CI leg from 2026-08-08, and the
        red went unnoticed for three pushes because the suite was green in
        front of the person running it.

        Pass `ci=True` to assert the runner behaviour deliberately. The point of
        the split is that both halves get tested wherever the suite runs.
        """
        env = dict(os.environ)
        env.pop("CI", None)
        env.pop("GITHUB_ACTIONS", None)
        if ci:
            env["CI"] = "true"
        proc = subprocess.run(
            [sys.executable, "scripts/gate-selfcheck.py", *args],
            cwd=str(self.clone), capture_output=True, text=True, env=env)
        return proc.returncode, proc.stdout + proc.stderr

    # -- the activation question, which is answered differently per context ----

    @requires_git
    def test_an_unactivated_clone_fails(self):
        """The exact NA-0030 condition: the hook exists, nothing points at it."""
        code, out = self.check()
        self.assertEqual(code, EXIT_ABSENT, out)
        self.assertIn("core.hooksPath is unset", out)
        # ...and it must say what to type. A failure that leaves you to go
        # looking is one people learn to ignore.
        self.assertIn("git config core.hooksPath .githooks", out)

    @requires_git
    def test_the_same_clone_only_warns_on_a_runner(self):
        """A runner clones fresh and has no hook, so failing there would make the
        job permanently red -- and a gate that is always red is routed around,
        which is the same as no gate. It has to say what it does not know."""
        code, out = self.check("--ci")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("::warning::", out)
        self.assertIn("green build is not the privacy gate", out)

    @requires_git
    def test_a_runner_is_also_recognised_without_the_flag(self):
        """`--ci` was covered; the environment variables were not, and they are
        the half that actually fires. A real runner sets `GITHUB_ACTIONS`, and
        nothing passes `--ci` for it -- so the flag was tested and the live path
        never was. That gap is why every CI leg from 2026-08-08 was red while
        the suite was green locally: the same inheritance made the local-case
        tests take this branch by accident."""
        code, out = self.check(ci=True)
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("::warning::", out)

    @requires_git
    def test_hooks_path_pointing_somewhere_empty_still_fails(self):
        """Set, but at a directory with no pre-push in it. Configured and inert
        looks exactly like configured and working from the outside."""
        empty = self.tmp / "nowhere"
        empty.mkdir()
        subprocess.run(["git", "config", "core.hooksPath", str(empty)],
                       cwd=str(self.clone), check=True)
        code, out = self.check()
        self.assertEqual(code, EXIT_ABSENT, out)
        self.assertIn("no executable pre-push", out)

    @requires_git
    def test_an_activated_clone_passes(self):
        """Only meaningful after the four failures above."""
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"],
                       cwd=str(self.clone), check=True)
        code, out = self.check()
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("gate-selfcheck: ok", out)

    # -- the wiring and red-capability questions, which fail everywhere --------
    #
    # These are properties of the repository rather than of one clone, so CI mode
    # must not soften them. Each is asserted with --ci for exactly that reason.

    @requires_git
    def test_a_missing_scanner_fails_even_on_a_runner(self):
        (self.clone / "scripts" / "leak-shapes.py").unlink()
        code, out = self.check("--ci")
        self.assertEqual(code, EXIT_ABSENT, out)
        self.assertIn("missing", out)

    @requires_git
    def test_a_hook_that_calls_nothing_fails_even_on_a_runner(self):
        """The quietest breakage of all: `exit 0` is a hook, and it passes."""
        hook = self.clone / ".githooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        code, out = self.check("--ci")
        self.assertEqual(code, EXIT_ABSENT, out)
        self.assertIn("drifted apart", out)

    # The execute bit is a POSIX concept and Windows has not got one: NTFS
    # carries no mode bits, chmod(0o644) is very nearly a no-op there, and
    # os.access(X_OK) answers True for any file that exists. So this cannot be
    # made to fail on Windows, and a check that cannot fail is not a check --
    # skipping says that out loud rather than asserting something vacuous.
    @requires_posix_dev_env
    @requires_git
    def test_a_non_executable_hook_fails_even_on_a_runner(self):
        hook = self.clone / ".githooks" / "pre-push"
        hook.chmod(0o644)
        code, out = self.check("--ci")
        self.assertEqual(code, EXIT_ABSENT, out)
        self.assertIn("not executable", out)

    @requires_git
    def test_a_scanner_that_cannot_fail_is_not_a_fence(self):
        """The failure mode the whole file is named for, one level down: a
        scanner stuck on 0 passes every clean-tree check ever written."""
        (self.clone / "scripts" / "leak-shapes.py").write_text(
            "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
        code, out = self.check("--ci")
        self.assertEqual(code, EXIT_ABSENT, out)
        self.assertIn("did NOT reject planted shapes", out)

    @requires_git
    def test_a_scanner_stuck_on_red_is_not_a_fence_either(self):
        """The other half. Asserting only that a guard rejects bad input is
        satisfied by one that rejects everything, and one that rejects
        everything gets switched off within a week."""
        (self.clone / "scripts" / "leak-shapes.py").write_text(
            "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n", encoding="utf-8")
        code, out = self.check("--ci")
        self.assertEqual(code, EXIT_ABSENT, out)
        self.assertIn("always red", out)


class ThisClone(unittest.TestCase):
    """The alarm itself, pointed at the repository you are actually working in.

    This is the assertion that would have caught NA-0030 on the day it happened
    rather than three releases later, and it is why the self-check is wired into
    the suite instead of living only in CI: a runner cannot see your git config,
    and the suite is the thing that runs constantly.

    Fresh clone, red test, one documented command, green. That is the intended
    experience, not an accident of it.
    """

    @unittest.skipIf(
        ON_CI,
        "a CI runner clones fresh and has no hook by construction -- the "
        "gate-selfcheck job asserts the fence is intact and red-capable, which "
        "is the part a runner can honestly answer")
    @requires_git
    def test_the_fence_is_switched_on_in_this_clone(self):
        proc = subprocess.run([sys.executable, str(SELFCHECK)],
                              cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(
            proc.returncode, EXIT_OK,
            "the pre-push fence is not active in this clone:\n\n%s\n%s"
            % (proc.stdout, proc.stderr))


if __name__ == "__main__":
    unittest.main()
