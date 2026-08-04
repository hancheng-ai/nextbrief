"""The check that stands between an obvious secret and a public repository.

Every assertion here is about the scan *failing*. A check that has only ever
been watched to pass is indistinguishable from one that cannot fail, and the two
look identical in a green build (CONTRIBUTING rule 7).

So: plant each class of finding, watch the scan reject it, and only then assert
it is quiet on a clean tree.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TempCase, requires_git

REPO = Path(__file__).resolve().parent.parent
SCAN = REPO / "scripts" / "leak-shapes.py"

# Assembled at run time rather than written out, so this file does not itself
# contain a string the scan is looking for. The alternative is to exclude the
# test from the scan, and an unscanned file is a place to hide something -- which
# is the whole objection to a guard that skips its own definition.
HOME_PATH_BAIT = "/" + "Users" + "/someone/Projects/thing"
KEY_BAIT = "-----BEGIN" + " RSA PRIVATE KEY-----"
TOKEN_BAIT = "ghp_" + "a" * 24
ZEROS = "0" * 40


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args),
                          capture_output=True, text=True)


class LeakShapesCase(TempCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "Test")
        # The scan resolves its own path from the repo it runs in, so the script
        # has to exist there too.
        (self.repo / "scripts").mkdir(exist_ok=True)
        (self.repo / "scripts" / "leak-shapes.py").write_text(
            SCAN.read_text(encoding="utf-8"), encoding="utf-8")

    def commit(self, name: str, text: str) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "add %s" % name)

    def scan(self, *args):
        argv = [sys.executable, "scripts/leak-shapes.py"] + list(args)
        return subprocess.run(argv, cwd=str(self.repo), capture_output=True,
                              text=True)


@requires_git
class WhatItRejects(LeakShapesCase):
    def test_an_absolute_home_path_fails_the_scan(self):
        self.commit("doc.md", "See %s for details.\n" % HOME_PATH_BAIT)
        got = self.scan("--all")
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertIn("absolute macOS home path", got.stdout)

    def test_a_private_key_header_fails_the_scan(self):
        self.commit("id.pem", "%s\nAAAA\n" % KEY_BAIT)
        got = self.scan("--all")
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertIn("a private key", got.stdout)

    def test_a_token_fails_the_scan(self):
        self.commit("ci.env", "TOKEN=%s\n" % TOKEN_BAIT)
        got = self.scan("--all")
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertIn("GitHub token", got.stdout)

    def test_a_leak_in_history_fails_even_when_the_tip_is_clean(self):
        """The push publishes the history, not the tip.

        Removing the line in a later commit is exactly the fix that does not
        work, so it is the one worth a test.
        """
        self.commit("doc.md", "Contact %s for the details.\n" % HOME_PATH_BAIT)
        self.commit("doc.md", "Contact the maintainer for the details.\n")
        got = self.scan("--all")
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertIn("absolute macOS home path", got.stdout)

    def test_the_worktree_scope_sees_an_uncommitted_leak(self):
        (self.repo / "draft.md").write_text("path: %s\n" % HOME_PATH_BAIT,
                                            encoding="utf-8")
        got = self.scan("--worktree")
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertIn("absolute macOS home path", got.stdout)

    def test_a_new_branch_range_scans_its_commits(self):
        """An all-zero "before" is a branch the remote has never seen.

        An earlier version of this scope produced an empty commit list here and
        reported success having compared nothing -- a guard that could not fail.
        """
        self.commit("doc.md", "See %s.\n" % HOME_PATH_BAIT)
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        got = self.scan("--range", "%s..%s" % (ZEROS, head))
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertIn("absolute macOS home path", got.stdout)


@requires_git
class WhatItAccepts(LeakShapesCase):
    def test_a_clean_repository_passes(self):
        self.commit("doc.md", "Nothing private here.\n")
        got = self.scan("--all")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)

    def test_the_scans_own_pattern_definitions_are_not_a_finding(self):
        """The scanner is committed to the repository it scans.

        Its patterns are written as character classes precisely so that the
        pattern text is not itself an instance of what it matches. If that ever
        stops holding, every run reports the scanner -- and a warning that fires
        every time for a harmless reason is a defect (rule 8).
        """
        self.commit("doc.md", "Nothing private here.\n")
        got = self.scan("--all")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        self.assertNotIn("leak-shapes.py", got.stdout)


@requires_git
class WhatItSaysWhenItCannotCheck(LeakShapesCase):
    def test_it_says_the_pass_is_a_floor_rather_than_a_fence(self):
        """A green line that reads like a guarantee is worse than no line.

        The one thing this scan structurally cannot catch is a relabelled
        example, so the clean message has to decline the credit.
        """
        self.commit("doc.md", "Nothing private here.\n")
        got = self.scan("--all")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        self.assertIn("floor", got.stdout)

    def test_an_empty_range_is_reported_rather_than_called_clean(self):
        self.commit("doc.md", "Nothing private here.\n")
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        got = self.scan("--range", "%s..%s" % (head, head))
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        self.assertIn("nothing new to scan", got.stdout)

    def test_outside_a_git_repository_it_errors_rather_than_passing(self):
        outside = self.tmp / "not-a-repo"
        outside.mkdir()
        got = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "leak-shapes.py"), "--all"],
            cwd=str(outside), capture_output=True, text=True)
        self.assertEqual(got.returncode, 2, got.stdout + got.stderr)


if __name__ == "__main__":
    unittest.main()
