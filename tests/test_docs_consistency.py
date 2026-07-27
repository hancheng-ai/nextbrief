"""Claims in the docs that a machine can check, checked by a machine.

An adversarial read of 0.1.0rc1 found the README asserting that release assets
were not attached (they were), a CHANGELOG dating a version that was never cut,
a setup command that fails on the interpreter the same file calls the floor, and
a Homebrew formula pointing at a tarball that does not exist. Every one of those
was true prose once. Prose does not notice when it stops being true, so the
specific, checkable half of it is pinned here instead.

The version is read from the package rather than repeated, so bumping it fails
these tests until the docs are moved with it -- which is the whole point.
"""

from __future__ import annotations

import re
import unittest

from helpers import REPO_ROOT

from nextbrief import __version__

README = REPO_ROOT / "README.md"
README_ZH = REPO_ROOT / "README.zh.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
FORMULA = REPO_ROOT / "packaging" / "homebrew" / "nextbrief.rb"

TAG = "v%s" % __version__


def read(path) -> str:
    return path.read_text(encoding="utf-8")


class Changelog(unittest.TestCase):
    def test_newest_heading_is_the_version_that_exists(self):
        """A heading for a version nobody tagged is a promise the repo cannot keep."""
        headings = re.findall(r"^## \[([^\]]+)\]", read(CHANGELOG), re.MULTILINE)
        self.assertEqual(headings[0], "Unreleased")
        self.assertEqual(
            headings[1], __version__,
            "CHANGELOG's newest released heading is %r but the package is %r"
            % (headings[1], __version__),
        )

    def test_link_definitions_follow_the_heading(self):
        text = read(CHANGELOG)
        self.assertIn("[%s]: https://github.com/hancheng-ai/nextbrief/releases/tag/%s"
                      % (__version__, TAG), text)
        self.assertIn("compare/%s...HEAD" % TAG, text)


class Readme(unittest.TestCase):
    """`/releases/latest/` resolves through GitHub's "latest release" endpoint,
    which skips prereleases. While the newest release is an rc, every such URL in
    the docs is a 404 -- so the docs must use the tag."""

    def test_no_latest_download_urls(self):
        # Matched as a URL, not as a substring: both files discuss
        # `/releases/latest/` in prose precisely to explain why they avoid it.
        bad = re.compile(r"https://github\.com/\S*/releases/latest/download")
        for path in (README, README_ZH):
            self.assertIsNone(
                bad.search(read(path)),
                "%s links a /releases/latest/ asset, which 404s for a prerelease"
                % path.name,
            )

    def test_asset_urls_point_at_the_current_tag(self):
        pattern = re.compile(r"releases/download/(v[^/\s]+)/")
        for path in (README, README_ZH):
            tags = set(pattern.findall(read(path)))
            self.assertTrue(tags, "%s documents no release asset at all" % path.name)
            self.assertEqual(tags, {TAG}, "%s links assets from %s" % (path.name, sorted(tags)))

    def test_distribution_table_no_longer_denies_the_release(self):
        """The specific contradiction that was reported: the table said the
        zipapp is not built and the assets are pending, while the release
        workflow was already attaching all four."""
        text = read(README)
        table = text[text.index("### Distribution"):]
        table = table[:table.index("\n## ")]
        for stale in ("not built", "pending the first tag", "until a `v*` tag is pushed"):
            self.assertNotIn(stale, table)
        self.assertIn(TAG, table)


class Contributing(unittest.TestCase):
    def test_first_setup_block_does_not_use_a_bare_editable_install(self):
        """`pip install -e .` needs PEP 660, which pip learned in 21.3. The pip
        bundled with the 3.9 this file calls the floor is 21.2.4, so the first
        command a contributor runs must not be that one."""
        text = read(CONTRIBUTING)
        first = re.search(r"```bash\n(.*?)\n```", text, re.DOTALL).group(1)
        self.assertNotIn("pip install -e .", first)
        self.assertIn("unittest discover", first)

    def test_the_editable_path_upgrades_pip_first(self):
        text = read(CONTRIBUTING)
        upgrade = text.index("pip install --upgrade pip")
        editable = text.index("pip install -e .")
        self.assertLess(upgrade, editable, "the upgrade must precede the editable install")

    def test_no_bare_python_invocations(self):
        """macOS ships no `python`, only `python3`."""
        for block in re.findall(r"```bash\n(.*?)\n```", read(CONTRIBUTING), re.DOTALL):
            for line in block.splitlines():
                self.assertFalse(
                    line.strip().startswith("python -m"),
                    "`python` is not a command on macOS: %r" % line.strip(),
                )


class HomebrewFormula(unittest.TestCase):
    def test_url_and_version_match_the_package(self):
        text = read(FORMULA)
        self.assertIn(
            'url "https://github.com/hancheng-ai/nextbrief/releases/download/'
            '%s/nextbrief-%s.tar.gz"' % (TAG, __version__), text,
        )
        self.assertIn('version "%s"' % __version__, text)

    def test_checksum_is_filled_in(self):
        sha = re.search(r'^  sha256 "([0-9a-f]{64})"', read(FORMULA), re.MULTILINE)
        self.assertIsNotNone(sha, "sha256 is still a placeholder")

    def test_block_cannot_touch_the_users_real_workspace(self):
        """`nextbrief init` writes a pointer at $XDG_CONFIG_HOME/nextbrief/
        workspace. Unredirected, `brew test` repoints the user's daily brief at
        Homebrew's scratch directory -- silently."""
        block = read(FORMULA)
        block = block[block.index("\n  test do"):]
        self.assertIn('ENV["HOME"] = testpath', block)
        self.assertIn('ENV["XDG_CONFIG_HOME"] = testpath', block)
        init = block.index('"init"')
        for var in ('ENV["HOME"]', 'ENV["XDG_CONFIG_HOME"]'):
            self.assertLess(block.index(var), init, "%s is redirected too late" % var)

    def test_block_does_not_notify_a_human(self):
        block = read(FORMULA)
        block = block[block.index("\n  test do"):]
        v0 = re.search(r'system bin/"nextbrief".*"v0".*', block)
        self.assertIsNotNone(v0)
        self.assertIn('"--no-notify"', v0.group(0))


if __name__ == "__main__":
    unittest.main()
