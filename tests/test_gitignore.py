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
from nextbrief.paths import Workspace

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
    # This machine's own ignore rules, switched off -- the single line that
    # makes "does the coverage travel?" a question this file can answer.
    #
    # `GIT_CONFIG_NOSYSTEM` suppresses /etc/gitconfig and `GIT_CONFIG_GLOBAL`
    # suppresses ~/.gitconfig, and *neither reaches the default excludes file*.
    # When `core.excludesFile` is unset git falls back to
    # $XDG_CONFIG_HOME/git/ignore, or ~/.config/git/ignore -- and that fallback
    # is a path lookup rather than a configured value, so no environment
    # variable turns it off. Setting the key explicitly is the only thing that
    # does. Measured, because the two obvious variables both look like they
    # should be enough and both leave the file in play.
    #
    # On the machine this was written on that file exists and carries a
    # `.claude` rule, so until this line the coverage check below was partly
    # answered by a file no clone receives. That is precisely the substitution
    # the `.claude/` entry in .gitignore was added to end, reappearing inside
    # the guard written to prove it had ended.
    cmd += ["-c", "core.excludesFile=%s" % os.devnull]
    # After the pin, so a deliberately planted rule still wins: git takes the
    # last `-c` for a given key.
    for key, value in extra_config:
        cmd += ["-c", "%s=%s" % (key, value)]
    cmd += ["check-ignore", "-z", "-v", "--no-index", "--stdin"]
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
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
        # The third source of rules that does not travel, and the only one no
        # flag can suppress: `git init` copies `info/exclude` out of the init
        # template, and a machine with `init.templateDir` set has real rules in
        # it. Emptied rather than trusted.
        self.local_exclude = self.box / ".git" / "info" / "exclude"
        self.local_exclude.parent.mkdir(parents=True, exist_ok=True)
        self.local_exclude.write_text("", encoding="utf-8")

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

    def test_every_artifact_the_engine_can_write_here_is_ignored(self):
        """What `init` scaffolds is not what a *run* leaves behind.

        The check above walks the tree `init --no-scan` produced, and the two
        files with the most in them are not in it: `BRIEF.md` and `BRIEF.html`
        appear at the end of a render, and `state/snapshot.json` -- which carries
        a filename out of every project the registry tracks, including the
        directories marked `never_read` because the filenames are the sensitive
        part -- appears at the end of a sense. Their rules were in `.gitignore`
        and nothing exercised them.

        Enumerated from `Workspace` rather than listed, so a property added later
        is covered without anyone remembering to come back here.
        """
        ws = Workspace(root=self.target, out=self.target, source="test")
        named = {name: getattr(ws, name)
                 for name in dir(Workspace)
                 if not name.startswith("_")
                 and isinstance(getattr(Workspace, name, None), property)}
        self.assertGreaterEqual(len(named), 10, named)
        for expected in ("brief_md", "brief_html", "snapshot"):
            self.assertIn(expected, named,
                          "Workspace no longer names %s, so this test is not "
                          "checking what it says it checks" % expected)

        # `git check-ignore --no-index` decides a directory-only pattern from
        # the pathname alone, so a directory has to arrive with its slash.
        rels = []
        for path in named.values():
            rel = Path(path).relative_to(self.target).as_posix()
            rels.append(rel + "/" if Path(path).is_dir() else rel)

        uncovered = sorted(r for r in set(rels) if not _check_ignore(self.box, [r]))
        self.assertEqual(
            [], uncovered,
            "the engine can write these into this repository and nothing holds "
            "them back:\n%s" % "\n".join("  " + p for p in uncovered))

    def test_the_box_carries_no_local_exclude_rules_of_its_own(self):
        """`.git/info/exclude` is the one ignore source with no off switch, so
        the only defence is that the box's copy is empty. Asserted rather than
        assumed, because emptying it in setUp is a line that can be deleted
        without anything else changing colour."""
        self.assertEqual("", self.local_exclude.read_text(encoding="utf-8"))


@requires_git
class CoverageThisMachineProvidesIsNotCoverage(TempCase):
    """A rule that does not travel with a clone is the failure this file exists
    to prevent, and the check above was open to it from the inside.

    `.claude/` was held back for a while by a global excludes file and by
    `.git/info/exclude`, on one machine, and looked completely fine there --
    which is why it is now a line in the tracked `.gitignore` with a comment
    saying so. The guard added at the same time copied that tracked file into a
    fresh box to measure coverage "as a clone receives it", and the box was not
    isolated: `git check-ignore` still reads `~/.config/git/ignore`, whatever
    the environment says, because that path is a fallback rather than a setting.

    So the substitution the fix was about could have happened again *inside the
    test for it*, and nothing would have been a different colour. The two tests
    below are the pair that can tell: one plants a rule only this machine could
    have and requires it to count for nothing, the other requires the shipped
    file to still be read -- because a checker that has stopped seeing anything
    at all would pass the first one perfectly.
    """

    # A workspace path init really does create, so the test is about the rule
    # rather than about a name nothing would ever match.
    PLANTED = "prompts/daily.en.md"

    def setUp(self):
        super().setUp()
        self.box = self.tmp / "box"
        self.box.mkdir()
        git(self.box, "init", "-q")
        (self.box / ".git" / "info" / "exclude").write_text("", encoding="utf-8")

        # A whole fake machine: `$XDG_CONFIG_HOME/git/ignore` is where git looks
        # first, `$HOME/.config/git/ignore` where it looks next, and both are
        # planted so the test cannot pass merely because this platform prefers
        # the other one.
        home = self.tmp / "elsewhere"
        for base in (home / ".config" / "git", home / "xdg" / "git"):
            base.mkdir(parents=True)
            (base / "ignore").write_text("prompts/\n", encoding="utf-8")
        for name, value in (("HOME", str(home)),
                            ("XDG_CONFIG_HOME", str(home / "xdg"))):
            before = os.environ.get(name)
            os.environ[name] = value
            self.addCleanup(self._restore_env, name, before)

    @staticmethod
    def _restore_env(name, before):
        if before is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before

    def test_a_rule_only_this_machine_has_does_not_count_as_coverage(self):
        """The box has no `.gitignore` at all, so every match is a leak."""
        matches = _check_ignore(self.box, [self.PLANTED])
        self.assertEqual(
            [], matches,
            "%s was reported as ignored by a repository with no .gitignore, so "
            "the coverage check is reading rules a clone never gets: %s"
            % (self.PLANTED, matches))

    def test_the_shipped_file_is_still_read(self):
        """The half that stops the one above passing by seeing nothing.

        Same box, same planted machine, plus the tracked `.gitignore` -- and now
        the path must be held back, by a rule sourced from that file.
        """
        shutil.copyfile(str(REPO_ROOT / ".gitignore"), str(self.box / ".gitignore"))
        matches = _swallowed(_check_ignore(self.box, [self.PLANTED]))
        self.assertTrue(matches, "%s is not covered by the tracked .gitignore" % self.PLANTED)
        sources = {m[0] for m in matches}
        self.assertEqual(
            {".gitignore"}, sources,
            "the match came from somewhere other than the file that ships: %s" % (matches,))


if __name__ == "__main__":
    unittest.main()
