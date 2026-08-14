"""What `--version` says, and every way it must refuse to guess.

The failure behind this: an editable install, the wheel on `PATH`, and the source
tree all printed the same three digits, so an install that had quietly stopped
being the code its owner was reading could not be told from one that had not.
It went unnoticed for nine days.

Three contracts here are load-bearing and are what most of this file is about.

**It never raises and never hangs.** `--version` is on the argument parser, so it
is built on every invocation, and `sense` stamps the same string into every
snapshot it writes. A version probe that can throw would take the nightly run
down with it, which is a far worse outcome than a missing suffix.

**It never guesses.** Every branch that cannot establish the answer returns an
empty segment rather than something plausible -- and the dirty check has its own
case here, because reporting a modified tree as clean is the reading that sends
somebody looking for a bug in a commit that does not match what is on disk.

**The release version has exactly one reader.** `_local_version` answers a
narrower question -- what local segment, if any -- and never sees `__version__`;
`build_version` is the only thing in the package that does. A constant rewritten
by a regex in `bump-version.sh`, which three files then have to agree about, is
a constant that several readers can be several kinds of wrong about.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import unittest

from helpers import REPO_ROOT, TempCase, git, git_init, requires_posix_dev_env

import nextbrief
from nextbrief import __version__, build_version

# The whole grammar of the segment, anchored at both ends. PEP 440 local versions
# are what makes this a latch and not a label -- PyPI rejects them -- so a shape
# that drifted out of the accepted form would take that property with it
# silently.
SEGMENT = re.compile(r"^\+dev\.g[0-9a-f]{7}(\.dirty)?$")


def commit_one(root):
    """A repository with exactly one commit, owing nothing to the machine's git."""
    git_init(root)
    (root / "f.txt").write_text("x", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "one")


def readers_of(name):
    """Every place in the package that *reads* `name`, as (file, line).

    Through the AST rather than a grep, and that distinction is the whole point.
    A grep for a symbol answers "where does this string appear", which conflates
    the definition, the `__all__` entry that exports it, the docstrings that
    explain it and the comments that say why something else is used instead --
    none of which is a reader. The question worth guarding is narrower: who
    loads this value. `ast.Load` answers exactly that and cannot be fooled by
    prose, which a grep-shaped rule has to be talked out of one wording at a
    time.
    """
    found = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and node.id == name
                    and isinstance(node.ctx, ast.Load)):
                found.append((path.relative_to(REPO_ROOT), node.lineno))
            # `from . import __version__` is a read too, and the one that used
            # to put this symbol in three files at once.
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == name:
                        found.append((path.relative_to(REPO_ROOT), node.lineno))
    return found


class TheReleaseVersionHasExactlyOneReader(unittest.TestCase):
    def test_exactly_one_thing_reads_it(self):
        # The criterion, as code rather than as a sentence somebody has to
        # re-apply by eye. It was a grep once; a grep counts the definition, the
        # export and every docstring that names the symbol, so it could be
        # satisfied by rewording prose and violated by adding a comment. This
        # counts loads.
        readers = readers_of("__version__")
        self.assertEqual(
            len(readers), 1,
            "the release constant should have exactly one reader; found %d: %s"
            % (len(readers), ", ".join("%s:%d" % r for r in readers)))
        where, _line = readers[0]
        self.assertEqual(pathlib.Path("src/nextbrief/__init__.py"), where,
                         "the one reader moved out of __init__.py, to %s" % where)

    def test_dunder_version_stays_plain(self):
        # bump-version.sh rewrites this line with a regex, three files have to
        # agree, and the release workflow compares the tag against pyproject.toml
        # byte for byte. A suffix leaking into the constant breaks all three at
        # once, and only at tag time.
        self.assertNotIn("+", __version__)
        build_version()
        self.assertNotIn("+", __version__)

    def test_the_probe_never_reads_it(self):
        # The separation this file's docstring describes, asserted rather than
        # claimed: everything `_local_version` returns is either empty or begins
        # with the local separator, so it cannot be handing back a version string.
        for got in ("", nextbrief._local_version()):
            if got:
                self.assertTrue(got.startswith("+"),
                                "the probe returned %r, which is not a segment" % got)
                self.assertNotIn(__version__, got)

    def test_what_is_printed_is_the_two_halves_joined(self):
        self.assertEqual(build_version(),
                         __version__ + nextbrief._local_version())


class ACheckoutSaysWhichCommitItIs(TempCase):
    def test_a_repository_gets_a_local_segment(self):
        root = self.tmp / "repo"
        (root / "src" / "nextbrief").mkdir(parents=True)
        commit_one(root)

        got = nextbrief._local_version(str(root / "src" / "nextbrief"))
        self.assertRegex(got, SEGMENT)
        self.assertNotIn(".dirty", got)

    def test_the_sha_is_the_one_git_reports(self):
        root = self.tmp / "sha"
        (root / "src" / "nextbrief").mkdir(parents=True)
        commit_one(root)
        want = git(root, "rev-parse", "--short=7", "HEAD").stdout.decode().strip()

        got = nextbrief._local_version(str(root / "src" / "nextbrief"))
        self.assertEqual(got, "+dev.g%s" % want)

    def test_an_uncommitted_change_is_said_out_loud(self):
        root = self.tmp / "dirty"
        (root / "src" / "nextbrief").mkdir(parents=True)
        commit_one(root)
        (root / "f.txt").write_text("y", encoding="utf-8")

        got = nextbrief._local_version(str(root / "src" / "nextbrief"))
        self.assertRegex(got, SEGMENT)
        self.assertTrue(got.endswith(".dirty"),
                        "a modified tree reported itself as %r" % got)


class EverythingElseGetsNoSegmentAtAll(TempCase):
    def test_a_tree_with_no_repository_above_it(self):
        here = self.tmp / "src" / "nextbrief"
        here.mkdir(parents=True)
        self.assertEqual(nextbrief._local_version(str(here)), "")

    def test_the_walk_stops_before_it_finds_an_unrelated_repository(self):
        # The trap: a venv several directories inside a home directory that is
        # itself a repository. Three levels is already too far, and a segment
        # here would stamp this build with a commit from someone's dotfiles.
        root = self.tmp / "home"
        deep = root / "venv" / "lib" / "site-packages" / "nextbrief"
        deep.mkdir(parents=True)
        commit_one(root)

        self.assertEqual(nextbrief._local_version(str(deep)), "")

    def test_a_path_through_an_archive_is_not_a_directory(self):
        # The zipapp, and it is settled before any path walking: a `.pyz` sitting
        # in the root of its own checkout has a `.git` well within two levels.
        root = self.tmp / "checkout"
        root.mkdir()
        commit_one(root)
        pyz = root / "nextbrief.pyz"
        pyz.write_bytes(b"PK\x03\x04 not really an archive")

        self.assertEqual(nextbrief._local_version(str(pyz / "nextbrief")), "")

    def test_a_repository_with_no_commits_yet(self):
        # `rev-parse HEAD` fails on an unborn HEAD. Detached HEAD, by contrast,
        # resolves fine and is deliberately not a failure case.
        root = self.tmp / "unborn"
        (root / "src" / "nextbrief").mkdir(parents=True)
        git(root, "init", "-q")
        self.assertEqual(
            nextbrief._local_version(str(root / "src" / "nextbrief")), "")

    def _with_path(self, value, here):
        saved = os.environ.get("PATH")
        os.environ["PATH"] = value
        try:
            return nextbrief._local_version(here)
        finally:
            if saved is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = saved

    def test_no_git_binary_at_all(self):
        root = self.tmp / "nogit"
        (root / "src" / "nextbrief").mkdir(parents=True)
        (root / ".git").mkdir()
        got = self._with_path(str(self.tmp / "empty"),
                              str(root / "src" / "nextbrief"))
        self.assertEqual(got, "")

    @requires_posix_dev_env
    def test_a_dirtiness_it_could_not_determine_is_not_reported_as_clean(self):
        # This one needs a git that answers one question and fails the other,
        # because the obvious test -- take git off PATH -- never reaches the
        # branch: `rev-parse` fails first and the empty segment is already
        # returned. The mutation harness is what said so; the test passed for
        # the wrong reason until it did.
        #
        # Reporting a modified tree as clean is the reading that sends somebody
        # looking for a bug in the commit named here, in a tree that does not
        # match it -- so an unanswerable dirty check costs the whole segment.
        root = self.tmp / "halfgit"
        (root / "src" / "nextbrief").mkdir(parents=True)
        (root / ".git").mkdir()

        fake = self.tmp / "fakebin"
        fake.mkdir()
        shim = fake / "git"
        shim.write_text(
            "#!/bin/sh\n"
            "for a in \"$@\"; do\n"
            "  case \"$a\" in\n"
            "    rev-parse) echo abc1234; exit 0 ;;\n"
            "    status) exit 1 ;;\n"
            "  esac\n"
            "done\n"
            "exit 1\n",
            encoding="utf-8")
        shim.chmod(0o755)

        got = self._with_path(str(fake), str(root / "src" / "nextbrief"))
        self.assertEqual(got, "")


class TheAnswerIsRememberedNotRecomputed(unittest.TestCase):
    def test_the_probe_runs_at_most_once(self):
        # `--version` is on the parser, which is built on every invocation, and
        # `sense` asks on every run. Neither should pay for a subprocess twice.
        saved = nextbrief._BUILD_VERSION
        calls = []
        original = nextbrief._local_version

        def counting(here=None):
            calls.append(here)
            return original(here)

        nextbrief._BUILD_VERSION = None
        nextbrief._local_version = counting
        try:
            first = build_version()
            second = build_version()
        finally:
            nextbrief._local_version = original
            nextbrief._BUILD_VERSION = saved

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1,
                         "the probe ran %d times, not once" % len(calls))


if __name__ == "__main__":
    unittest.main()
