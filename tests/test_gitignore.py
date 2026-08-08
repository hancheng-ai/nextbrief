"""This repository's own ``.gitignore``, checked from both sides.

``nextbrief init`` writes a workspace into the directory it is pointed at, and a
bare ``nextbrief init`` still defaults to the current one -- so an agent that runs
it from this repository's root scaffolds a workspace *here*. Half of what that
leaves behind was already held back; ``prompts/`` and ``schema/`` were not, and
their contents are copies of packaged data that look plausible enough at the root
of this repository to be committed by somebody who did not notice where they came
from.

The two tests pull in opposite directions on purpose, because the obvious fix for
one breaks the other.

Widening the ignore file is the fix for the first, and the trap is that a pattern
with no leading slash matches at *every* depth. This package ships
``src/nextbrief/prompts/`` and ``src/nextbrief/schema/`` as tracked data, so a
bare ``schema/`` -- the pattern anyone would reach for first -- silently untracks
four files it was never aimed at. Nothing about that is visible in ``git status``:
the files stay on disk, stay unmodified, and simply stop being watched. The near
miss is the same one ``.claude/`` versus ``.claude-plugin/`` already survived, and
it is why every workspace rule in that file carries a leading slash.

``--no-index`` is load-bearing in the first test and cannot be dropped. Without
it ``git check-ignore`` skips tracked paths by definition, and a check whose input
is exactly the tracked paths then reports nothing however wrong the rules are --
green for the one reason that proves nothing (CONTRIBUTING rule 7).

A negation is not a swallowing. ``examples/workspace/.gitignore`` re-admits
``state/brief.json`` after ignoring ``state/``, and ``check-ignore -v`` reports
that match like any other, pattern prefixed with ``!``. Matching on the pattern
rather than on the presence of a line is what keeps the guard from reading its own
baseline as a failure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import REPO_ROOT, TempCase, capture, git, requires_git

from nextbrief import cli

# Reported by `check-ignore -z -v` as four NUL-terminated fields per match.
FIELDS = 4


def _check_ignore(repo, paths, extra_config=()):
    """Every path in `paths` that some ignore rule matches, as
    (source, lineno, pattern, path).

    `--no-index` is what makes the answer meaningful: the paths handed in here are
    the tracked ones, and without the flag git declines to consider a tracked path
    at all -- so the command succeeds, prints nothing, and cannot fail. `-z` is
    used on both sides so that a pathname needing quotes cannot shift the columns.
    """
    cmd = ["git", "-C", str(repo)]
    for key, value in extra_config:
        cmd += ["-c", "%s=%s" % (key, value)]
    cmd += ["check-ignore", "-z", "-v", "--no-index", "--stdin"]
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    proc = subprocess.run(
        cmd,
        input=b"".join(str(p).encode("utf-8") + b"\0" for p in paths),
        capture_output=True,
        env=env,
    )
    # 0 = something matched, 1 = nothing did. Anything else is git failing, and a
    # guard that reads a crash as "clean" is the failure mode this file is about.
    if proc.returncode not in (0, 1):
        raise AssertionError(
            "git check-ignore failed (%d): %s"
            % (proc.returncode, proc.stderr.decode("utf-8", "replace").strip())
        )
    parts = proc.stdout.decode("utf-8", "replace").split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    return [tuple(parts[i:i + FIELDS]) for i in range(0, len(parts), FIELDS)]


def _swallowed(matches):
    """The matches that actually hold a path back. A pattern beginning with `!`
    re-admits it, which is the opposite outcome from the same-shaped line."""
    return [m for m in matches if not m[2].startswith("!")]


def _tracked_paths():
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise unittest.SkipTest("not a git checkout: %s" % proc.stderr.decode("utf-8", "replace"))
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


@requires_git
class NoRuleSwallowsATrackedFile(unittest.TestCase):
    def setUp(self):
        self.tracked = _tracked_paths()
        # A whole-tree assertion over an empty list passes for free. This one has
        # bitten here before, so the input is checked before it is trusted.
        self.assertGreater(len(self.tracked), 50,
                           "git ls-files returned %d paths; the check below would "
                           "be asserting over nothing" % len(self.tracked))

    def test_no_tracked_file_is_held_back_by_an_ignore_rule(self):
        """The rule that pays for itself.

        A tracked file that becomes ignored keeps working -- it is still in the
        index, still checked out, still committed on the next `git commit -a`.
        What stops is `git add` noticing when it changes, and `git status`
        mentioning it, so the divergence surfaces as a mystery weeks later.
        """
        bad = _swallowed(_check_ignore(REPO_ROOT, self.tracked))
        self.assertEqual(
            [], bad,
            "these tracked files are ignored:\n%s"
            % "\n".join("  %s  <-  %s:%s  %s" % (m[3], m[0], m[1], m[2]) for m in bad))

    def test_the_check_reports_one_when_there_is_one(self):
        """The same detector, given a rule that does swallow tracked files.

        `core.excludesFile` is used rather than editing `.gitignore`, so the
        ability to fail is asserted on every run instead of being watched once by
        whoever wrote this. The pattern is the exact near miss: a bare `prompts/`
        aimed at the workspace directory `init` creates, landing on the packaged
        one four levels down.
        """
        box = tempfile.mkdtemp(prefix="nextbrief-ignore-")
        self.addCleanup(shutil.rmtree, box, True)
        extra = Path(box) / "excludes"
        extra.write_text("prompts/\n", encoding="utf-8")

        bad = _swallowed(_check_ignore(
            REPO_ROOT, self.tracked, [("core.excludesFile", str(extra))]))
        caught = sorted(m[3] for m in bad)
        self.assertIn("src/nextbrief/prompts/daily.en.md", caught,
                      "a bare `prompts/` did not reach the packaged prompts, so "
                      "the test above is green for an unknown reason")
        self.assertTrue(all(m[0] == str(extra) for m in bad),
                        "something other than the planted rule matched: %s" % (bad,))


@requires_git
class WhatInitLeavesInThisRepository(TempCase):
    """`init` refuses `--workspace`, but a bare `nextbrief init` still writes to
    the current directory, so the ignore file is the layer that catches it here."""

    def setUp(self):
        super().setUp()
        self.target = self.tmp / "box" / "ws"
        code, _out, err = capture(cli.main, ["init", str(self.target), "-y", "--no-scan"])
        self.assertEqual(0, code, err)

        # The ignore rules as a clone receives them, and nothing else. Asking the
        # real checkout would fold in `.git/info/exclude` and a global
        # `core.excludesFile`, neither of which travels -- and on the machine this
        # was written on the former does carry `.claude` rules, so the coverage
        # below would have passed for a reason a contributor never gets.
        self.box = self.tmp / "shipped-rules"
        self.box.mkdir()
        git(self.box, "init", "-q")
        shutil.copyfile(str(REPO_ROOT / ".gitignore"), str(self.box / ".gitignore"))

    def _created(self):
        """What init put in the workspace, workspace-relative, directories marked.

        The trailing slash is not cosmetic: `git check-ignore` decides whether a
        directory-only pattern applies from the pathname alone once `--no-index`
        is in play, so `/state/` does not match the string `state`.
        """
        out = []
        for dirpath, dirnames, filenames in os.walk(str(self.target)):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            here = Path(dirpath)
            for name in dirnames:
                out.append((here / name).relative_to(self.target).as_posix() + "/")
            for name in filenames:
                out.append((here / name).relative_to(self.target).as_posix())
        return sorted(out)

    def test_init_does_not_overwrite_a_gitignore_that_is_already_there(self):
        """What buys `.gitignore` its exemption below.

        It is the one path `init` writes that this repository cannot hold back --
        the file would have to ignore itself, and it is tracked, so the rule would
        do nothing anyway. The exemption is only safe because `init` leaves an
        existing one alone, which is a fact about init and belongs in an
        assertion rather than in a comment.
        """
        keep = self.tmp / "box" / "other"
        keep.mkdir(parents=True)
        (keep / ".gitignore").write_text("# mine\nscratch/\n", encoding="utf-8")
        code, _out, err = capture(cli.main, ["init", str(keep), "-y", "--no-scan"])
        self.assertEqual(0, code, err)
        self.assertEqual("# mine\nscratch/\n",
                         (keep / ".gitignore").read_text(encoding="utf-8"))

    def test_everything_init_writes_here_is_already_ignored(self):
        created = self._created()
        self.assertIn("schema/brief.schema.json", created,
                      "init wrote nothing under schema/, so the check below is "
                      "asserting over a tree it never reached")
        uncovered = [
            p for p in created
            if p != ".gitignore" and not _check_ignore(self.box, [p])
        ]
        self.assertEqual(
            [], uncovered,
            "`nextbrief init` in this repository's root would leave these "
            "untracked and unignored:\n%s" % "\n".join("  " + p for p in uncovered))


if __name__ == "__main__":
    unittest.main()
