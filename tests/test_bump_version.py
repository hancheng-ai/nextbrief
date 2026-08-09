"""`scripts/bump-version.sh`, run twice, against a table it is not allowed to rewrite.

The script sweeps the new version through README.md, README.zh.md and the
Homebrew formula, because the version appears there in badges, install commands
and download URLs and updating only the three machine-readable literals leaves
those pointing at the previous release.

The sweep was an unbounded ``text.replace(previous, new)``. README.md then grew
an append-only release-history table, and every bump rewrote the newest row's
version into the release being cut -- while leaving that row's CHANGELOG anchor
and publication date pointing at the release it used to describe. The row still
parsed, the table still rendered, CI stayed green, and a release disappeared from
the public record. It shipped twice and was corrected by hand twice.

So this file drives the real script, twice, over two release cycles, and asserts
both halves:

* the row written during the first cycle is still byte-for-byte what it was --
  version string, anchor and date -- after the second, and
* the live references *did* move, because a "fix" that simply stopped sweeping
  would satisfy the first half completely and reintroduce the bug the sweep
  exists for.

Driven as a subprocess rather than reimplemented: the defect lives in the shell
script maintainers actually run, and a Python transcription of it is a second
thing that can drift.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest

from helpers import REPO_ROOT, TempCase, requires_posix_dev_env

SCRIPT = REPO_ROOT / "scripts" / "bump-version.sh"

# The boundary markers, spelled out here rather than parsed out of the script.
# They are a contract between the script and the documents it edits, and a
# contract both sides can rename together is not one -- so the literals live in
# the test and `test_the_script_still_speaks_these_markers` pins them.
SKIP_BEGIN = "<!-- bump-version:skip:begin -->"
SKIP_END = "<!-- bump-version:skip:end -->"

# An invented package. Nothing here is borrowed from a real project, and the
# strings are chosen to be unmistakable in a grep if one ever escapes the
# temporary directory.
PYPROJECT = """\
[build-system]
requires = ["hatchling>=1.21"]

[project]
name = "driftwood"
version = "3.1.0rc1"
"""

# The script hard-codes `src/nextbrief/__init__.py`, so the fixture keeps that
# path even though the fixture package is called driftwood everywhere else.
INIT_PY = '__version__ = "3.1.0rc1"\n'

CITATION = """\
cff-version: 1.2.0
title: "driftwood"
version: 3.1.0rc1
date-released: "2026-02-03"
"""

CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- Something that has not shipped yet.

## [3.1.0rc1] - 2026-02-03

### Added

- The first cut.

[Unreleased]: https://github.com/example-owner/driftwood/compare/v3.1.0rc1...HEAD
[3.1.0rc1]: https://github.com/example-owner/driftwood/releases/tag/v3.1.0rc1
"""

# The row the first cycle leaves behind. Its date is deliberately not today, so
# "the date survived" cannot be satisfied by a bug that rewrites every date to
# the day the bump runs -- which is what the script stamps on a new section.
ROW_RC1 = "| [3.1.0rc1](CHANGELOG.md#310rc1---2026-02-03) | 2026-02-03 | The first cut. |"

README = """\
# driftwood

[![Release](https://img.shields.io/badge/release-v3.1.0rc1-blue)](https://github.com/example-owner/driftwood/releases/tag/v3.1.0rc1)

A fictional tool, here so the release script has something to edit.

## Install

```
pipx install "driftwood==3.1.0rc1"
curl -fsSLO https://github.com/example-owner/driftwood/releases/download/v3.1.0rc1/driftwood.pyz
```

## Release history

Newest first. Every entry links to CHANGELOG.md, which is the record.

{begin}
| Version | Published | What it brought |
|---|---|---|
| [Unreleased](CHANGELOG.md#unreleased) | — | — |
{row}
{end}

## License

Apache-2.0
""".format(begin=SKIP_BEGIN, end=SKIP_END, row=ROW_RC1)

# No table today, and that is the point: this file is swept as well, so the
# boundary has to be written per file rather than "the file that has the table".
README_ZH = """\
# driftwood

The current release is `3.1.0rc1`.

    pipx install "driftwood==3.1.0rc1"
"""

# Written to packaging/homebrew/nextbrief.rb, the path SWEEP names, for the same
# reason as src/nextbrief/__init__.py above: the script skips a file it cannot
# find, so a fixture that files this under the invented name is a fixture the
# sweep never reaches. The first run of this test did exactly that and the
# live-reference assertion caught it.
#
# The fenced `sha256-of:` line is the second thing in this repository that is
# append-only inside a swept file, and it arrived the same way the release
# table did: added to a file the sweep already covered, without re-reading what
# the sweep would do to it. It names the release a digest was taken from, so it
# is a statement about the past in exactly the sense a history row is -- and
# unlike a history row, sweeping it produces a comment that agrees with
# `version` and is wrong, which is the one state the guard in
# tests/test_docs_consistency.py cannot see.
FORMULA = """\
class Driftwood < Formula
  url "https://github.com/example-owner/driftwood/releases/download/v3.1.0rc1/driftwood-3.1.0rc1.tar.gz"
  version "3.1.0rc1"
  # <!-- bump-version:skip:begin -->
  # sha256-of: 3.1.0rc1
  # <!-- bump-version:skip:end -->
  sha256 "%s"
end
""" % ("d0" * 32)


def unfenced(text: str) -> str:
    """Everything outside the skip markers, concatenated.

    Used to ask "did the sweep reach the live references", separately from "did
    it leave the history alone" -- one string cannot answer both.
    """
    out, pos = [], 0
    while True:
        begin = text.find(SKIP_BEGIN, pos)
        if begin < 0:
            break
        end = text.find(SKIP_END, begin)
        if end < 0:
            break
        out.append(text[pos:begin])
        pos = end + len(SKIP_END)
    out.append(text[pos:])
    return "".join(out)


@requires_posix_dev_env
class BumpTwice(TempCase):
    """Two consecutive release cycles in a throwaway tree.

    The script derives its own ROOT from BASH_SOURCE, so a copy of it beside a
    copy of the files it edits redirects the whole run -- the repository's own
    pyproject.toml, CHANGELOG and READMEs are never opened.
    """

    def setUp(self):
        super().setUp()
        self.work = self.tmp / "release"
        (self.work / "scripts").mkdir(parents=True)
        (self.work / "src" / "nextbrief").mkdir(parents=True)
        (self.work / "packaging" / "homebrew").mkdir(parents=True)
        shutil.copy2(str(SCRIPT), str(self.work / "scripts" / SCRIPT.name))
        self._write("pyproject.toml", PYPROJECT)
        self._write("src/nextbrief/__init__.py", INIT_PY)
        self._write("CITATION.cff", CITATION)
        self._write("CHANGELOG.md", CHANGELOG)
        self._write("README.md", README)
        self._write("README.zh.md", README_ZH)
        self._write("packaging/homebrew/nextbrief.rb", FORMULA)

    # -- plumbing -----------------------------------------------------------

    def _write(self, rel, text):
        (self.work / rel).write_text(text, encoding="utf-8")

    def _read(self, rel):
        return (self.work / rel).read_text(encoding="utf-8")

    def bump(self, version):
        proc = subprocess.run(
            ["bash", str(self.work / "scripts" / SCRIPT.name), version],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
        log = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, log)
        return log

    def _changelog_date(self, version):
        """The date the script stamped on a section, read back rather than guessed.

        The script uses `date -u`, and a test that recomputes it is a test that
        can disagree with the script across a UTC midnight.
        """
        match = re.search(r"(?m)^## \[%s\] - (\d{4}-\d{2}-\d{2})$" % re.escape(version),
                          self._read("CHANGELOG.md"))
        self.assertIsNotNone(match, "no [%s] section in the CHANGELOG" % version)
        return match.group(1)

    def _add_history_row(self, version, blurb):
        """What a maintainer does by hand: file the release just cut, newest first.

        The script does not write these rows; it only sweeps over them. So the
        second cycle has to be handed a row from the first, or there is nothing
        for the second bump to damage and the test proves nothing.
        """
        date = self._changelog_date(version)
        row = "| [%s](CHANGELOG.md#%s---%s) | %s | %s |" % (
            version, version.replace(".", ""), date, date, blurb)
        text = self._read("README.md")
        marker = "| [Unreleased](CHANGELOG.md#unreleased) | — | — |\n"
        self.assertIn(marker, text)
        self._write("README.md", text.replace(marker, marker + row + "\n", 1))
        return row

    # -- the guard ----------------------------------------------------------

    def test_two_bumps_leave_every_earlier_history_row_untouched(self):
        self.bump("3.1.0rc2")

        # Cycle one. rc1 was `previous`, so this is the row the unbounded
        # replace rewrote: version moved to rc2, anchor and date left behind.
        self.assertIn(ROW_RC1, self._read("README.md"),
                      "the first bump rewrote the 3.1.0rc1 release-history row")

        row_rc2 = self._add_history_row("3.1.0rc2", "The second cut.")

        self.bump("3.1.0rc3")

        # Cycle two. Both rows have to survive: rc2 is now `previous` and is the
        # row this bump would rewrite, and rc1 proves the damage is not merely
        # deferred by one release.
        readme = self._read("README.md")
        self.assertIn(ROW_RC1, readme,
                      "the second bump rewrote the 3.1.0rc1 release-history row")
        self.assertIn(row_rc2, readme,
                      "the second bump rewrote the 3.1.0rc2 release-history row")

    def test_two_bumps_leave_the_formulas_digest_provenance_alone(self):
        """The same invariant, on the line whose corruption is invisible.

        A rewritten history row at least looks wrong to a reader who checks the
        date beside it. A rewritten `sha256-of:` looks *right*: it agrees with
        `version`, which is precisely the condition
        tests/test_docs_consistency.py reads as "the digest is current" before
        it allows the READMEs to print `brew install --build-from-source`. So
        the sweep reaching this line does not merely damage a record -- it
        disarms the check that exists to catch a stale digest, and re-documents
        the failing install command that check was written for.

        Two cycles rather than one, for the reason the row test gives: rc1 is
        `previous` on the first bump and rc2 on the second, and a fence that
        only holds for one release is not a fence.
        """
        self.bump("3.1.0rc2")
        self.bump("3.1.0rc3")

        formula = self._read("packaging/homebrew/nextbrief.rb")
        self.assertIn(
            "# sha256-of: 3.1.0rc1", formula,
            "a bump rewrote the formula's `sha256-of:` line, so it now names a "
            "release the digest below it did not come from")
        # The digest itself is hex and has nothing for a version sweep to match,
        # so this asserts the fence did not eat something else on its way past.
        self.assertIn('sha256 "%s"' % ("d0" * 32), formula)

    def test_the_sweep_still_moves_every_live_reference(self):
        """The other half, and the reason the first half is not enough.

        Deleting the sweep outright would make every assertion above pass, and
        would put the badges, the install commands and the download URLs back to
        pointing at the previous release -- which is the defect the sweep was
        added to fix, one release later.
        """
        self.bump("3.1.0rc2")
        self._add_history_row("3.1.0rc2", "The second cut.")
        self.bump("3.1.0rc3")

        live = unfenced(self._read("README.md"))
        for moved in (
            "badge/release-v3.1.0rc3-blue",
            "releases/tag/v3.1.0rc3",
            'pipx install "driftwood==3.1.0rc3"',
            "releases/download/v3.1.0rc3/driftwood.pyz",
        ):
            self.assertIn(moved, live, "README.md still points at an older release")
        for stale in ("3.1.0rc1", "3.1.0rc2"):
            self.assertNotIn(stale, live,
                             "README.md keeps a live %s reference outside the table" % stale)

        # Every swept file, not just the one with a table: the boundary is not
        # allowed to cost the other two their sweep.
        self.assertIn("3.1.0rc3", self._read("README.zh.md"))
        self.assertNotIn("3.1.0rc1", self._read("README.zh.md"))
        formula = self._read("packaging/homebrew/nextbrief.rb")
        self.assertIn('version "3.1.0rc3"', formula)
        self.assertIn("driftwood-3.1.0rc3.tar.gz", formula)

        # And the three machine-readable literals, so a run that swept nothing
        # at all cannot look like a run that swept correctly.
        self.assertIn('version = "3.1.0rc3"', self._read("pyproject.toml"))
        self.assertIn('__version__ = "3.1.0rc3"', self._read("src/nextbrief/__init__.py"))

    def test_the_changelog_keeps_its_older_sections(self):
        """The same append-only invariant one file over, across two cycles.

        Deliberately *not* phrased as "CHANGELOG.md stays out of SWEEP", which is
        what it was first written to say. Checking that claim by mutation showed
        it is not what this asserts: `changelog_text` is read into memory before
        the sweep loop runs and written back after it, so putting CHANGELOG.md
        into SWEEP is silently a no-op rather than a corruption. What this does
        check is the outcome that matters -- older sections, their link
        definitions and the newest-first ordering all still there afterwards --
        and it goes red for any code that reaches those, whichever route it took.
        """
        self.bump("3.1.0rc2")
        self.bump("3.1.0rc3")
        text = self._read("CHANGELOG.md")
        self.assertIn("## [3.1.0rc1] - 2026-02-03", text)
        self.assertIn(
            "[3.1.0rc1]: https://github.com/example-owner/driftwood/releases/tag/v3.1.0rc1",
            text)
        headings = re.findall(r"(?m)^## \[([^\]]+)\]", text)
        self.assertEqual(headings, ["Unreleased", "3.1.0rc3", "3.1.0rc2", "3.1.0rc1"])

    def test_an_unclosed_marker_stops_the_release(self):
        """Guessing is what caused this. An opened fence with no close is an
        author whose intent cannot be read, and the script says so instead of
        picking one of the two possible meanings silently."""
        self._write("README.md", README.replace(SKIP_END, ""))
        proc = subprocess.run(
            ["bash", str(self.work / "scripts" / SCRIPT.name), "3.1.0rc2"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
        log = proc.stdout.decode("utf-8", "replace")
        self.assertNotEqual(proc.returncode, 0, log)
        self.assertIn("README.md", log)


class TheBoundaryIsHonouredByThisRepository(unittest.TestCase):
    """The fixture above proves the mechanism. This proves it is switched on here.

    The next occurrence of this bug is not a change to the script -- it is
    somebody adding append-only content to a file that is already swept, exactly
    as the README table was added without anyone re-reading the sweep's
    assumptions. README.zh.md is in SWEEP and has no table today; the day it
    gains one, this goes red rather than a release going missing.
    """

    def _script(self):
        return SCRIPT.read_text(encoding="utf-8")

    def _sweep_list(self):
        # Read out of the script, so a fourth file added to SWEEP is covered
        # without anyone remembering to add it here too.
        match = re.search(r"(?m)^SWEEP = \[(.*?)\]", self._script(), re.DOTALL)
        self.assertIsNotNone(match, "SWEEP is no longer a literal list in the script")
        names = re.findall(r'"([^"]+)"', match.group(1))
        self.assertTrue(names, "SWEEP parsed as empty")
        return names

    def test_the_script_still_speaks_these_markers(self):
        """Pinned to the assignment, not to the file.

        Written first as `marker in script`, which stayed green while the script
        was mutated to use a different marker -- because the explanatory comment
        above the assignment quotes the old one, and a substring match cannot
        tell prose from code. Caught by running that mutation, and it is the
        same shape as the docs test that once matched a date in a URL instead of
        in the column it meant.
        """
        script = self._script()
        for var, marker in (("SKIP_BEGIN", SKIP_BEGIN), ("SKIP_END", SKIP_END)):
            # assertTrue rather than assertRegex: the haystack is the whole
            # script, and dumping it buries the line of the message that matters.
            self.assertTrue(
                re.search(r'(?m)^%s = "%s"$' % (var, re.escape(marker)), script),
                "the script no longer assigns %s the marker the docs carry: %s"
                % (var, marker))

    def test_no_swept_file_has_release_history_outside_the_markers(self):
        for name in self._sweep_list():
            path = REPO_ROOT / name
            if not path.is_file():
                continue
            live = unfenced(path.read_text(encoding="utf-8"))
            # A link to a *versioned* CHANGELOG anchor is a statement about a
            # release that already happened; nothing live ever needs one. The
            # unversioned `#unreleased` anchor is not such a statement.
            stranded = re.findall(r"CHANGELOG\.md#\d[0-9a-z.-]*", live)
            self.assertEqual(
                stranded, [],
                "%s cites past releases outside %s / %s, so the next bump will "
                "rewrite them: %s" % (name, SKIP_BEGIN, SKIP_END, stranded))

    def test_no_swept_file_names_a_digests_release_outside_the_markers(self):
        """The fence around `sha256-of:` is load-bearing, so its absence is red.

        Deleting the two marker lines from the formula is a one-line tidy-up
        that looks like removing noise -- an HTML comment in a Ruby file, twice,
        for no visible reason. Nothing else would notice: the formula still
        parses, `brew install` still works today, and the guard in
        tests/test_docs_consistency.py stays green right up until the next bump
        quietly moves the line and takes that guard down with it.
        """
        for name in self._sweep_list():
            path = REPO_ROOT / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            named = re.findall(r"(?m)^\s*# sha256-of: \S+$", text)
            if not named:
                continue
            self.assertEqual(
                re.findall(r"(?m)^\s*# sha256-of: \S+$", unfenced(text)), [],
                "%s records which release a digest came from outside %s / %s, "
                "so the next bump will rewrite that line to the new version "
                "while the digest beside it stays where it is -- and a digest "
                "whose provenance line agrees with `version` is one no test "
                "can tell from a current one" % (name, SKIP_BEGIN, SKIP_END))


if __name__ == "__main__":
    unittest.main()
