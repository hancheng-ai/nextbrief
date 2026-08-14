"""The shared git fixture, checked directly rather than through its users.

``git_init`` is the most-called fixture in this suite -- nine test modules and
several hundred repositories per run -- and it stopped spawning ``git`` to
configure what it had just created. ``init`` writes ``.git/config`` and
``.git/HEAD``; four ``git config`` calls and one ``symbolic-ref`` were five more
processes to add six lines of INI and one line of text, and processes are what
this suite is made of: ~13,500 git spawns, ~11 per test. The same suite takes 40
seconds on ubuntu-latest and 7m29s on windows-latest, where a spawn costs roughly
ten times as much, and that job alone is CI's wall clock because the matrix runs
in parallel.

Writing the files is only equivalent while it stays equivalent, and every way it
could stop is quiet:

- **The identity has to live in the repository**, not only in ``git()``'s
  environment. ``done``, ``drop`` and ``defer`` all commit, and those git
  processes are spawned by the code under test, which never sees this module's
  environment. A runner with no global ``user.email`` fails them -- and fails
  them somewhere else entirely, in whichever test happened to close an item.
- **``init``'s own keys must survive.** The config is appended to, never
  replaced: ``repositoryformatversion`` is written by ``init``, and on Windows
  so are ``ignorecase`` and ``symlinks``. Losing those is not a faster fixture,
  it is a different repository, and one that reads differently on one platform.
- **``core.autocrlf = false`` is what keeps the Windows runner honest.** The
  runners set it globally; the frontmatter parser's line-ending contract is
  tested against a tree checked out with CRLF, and a fixture that quietly
  inherited the global would move that ground without failing anything here.

So this asserts the fixture's *shape*, once, at the cost of one repository. The
alternative is discovering a malformed fixture through several hundred confusing
failures in modules that have nothing to do with git.
"""

import subprocess
import unittest

from helpers import GIT_EMAIL, GIT_NAME, TempCase, git_commit_all, git_init


class TheGitFixture(TempCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        git_init(self.repo)

    def _cfg(self, *args):
        out = subprocess.run(["git", "-C", str(self.repo)] + list(args),
                             capture_output=True, text=True)
        return out.stdout.strip()

    def test_the_repository_is_shaped_the_way_the_suite_assumes(self):
        # `symbolic-ref`, not `rev-parse --abbrev-ref`: this repository has no
        # commits yet, and on an unborn branch `rev-parse` answers "HEAD".
        self.assertEqual(self._cfg("symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(self._cfg("config", "user.name"), GIT_NAME)
        self.assertEqual(self._cfg("config", "user.email"), GIT_EMAIL)
        self.assertEqual(self._cfg("config", "commit.gpgsign"), "false")
        self.assertEqual(self._cfg("config", "core.autocrlf"), "false")
        # `init` wrote this one. Appending kept it; replacing the file would not.
        self.assertEqual(self._cfg("config", "core.repositoryformatversion"), "0")

    def test_a_commit_works_without_any_git_environment(self):
        """★ The half the fixture exists for, and the one a faster fixture breaks. ★

        The engine commits on `done`, `drop` and `defer`, in a subprocess it
        spawns itself. Stripping every `GIT_*` variable is what that subprocess
        actually sees, so this is the identity coming from the repository or from
        nowhere.
        """
        (self.repo / "a.txt").write_text("x\n", encoding="utf-8")
        git_commit_all(self.repo)
        (self.repo / "b.txt").write_text("y\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], capture_output=True)
        import os
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        done = subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "-m", "as the engine does"],
            capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._cfg("log", "-1", "--format=%an <%ae>"),
                         "%s <%s>" % (GIT_NAME, GIT_EMAIL))


if __name__ == "__main__":
    unittest.main()
