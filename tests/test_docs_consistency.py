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
ARCHITECTURE = REPO_ROOT / "docs" / "ARCHITECTURE.md"

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


class Architecture(unittest.TestCase):
    """The design document, checked against the code it describes.

    This file was unguarded until a release shipped with it asserting that a
    discovered project "carries neutral placeholders instead of judgements" --
    the exact behaviour that release had been cut to remove -- while
    contradicting itself a hundred lines further down. Nothing noticed, because
    every other doc here is checked and this one was not.
    """

    def _text(self):
        return ARCHITECTURE.read_text(encoding="utf-8")

    def test_every_command_it_names_exists(self):
        from nextbrief.cli import build_parser

        real = set()
        for action in build_parser()._subparsers._group_actions:
            real |= set(action.choices)
        named = set(re.findall(r"`nextbrief ([a-z0-9-]+)", self._text()))
        self.assertTrue(named, "the architecture doc names no command at all")
        self.assertEqual(sorted(named - real), [],
                         "named in ARCHITECTURE.md but not a real command")

    def test_it_does_not_claim_discovery_invents_a_status(self):
        """Tied to the code, not to a phrase.

        While `DISCOVERED_STATUS` is None the doc must not describe a discovered
        project as carrying a placeholder, neutral or otherwise. If someone
        deliberately reintroduces a default tier, this assertion stops applying
        on its own rather than having to be remembered.
        """
        from nextbrief import discovery

        if discovery.DISCOVERED_STATUS is not None:
            self.skipTest("a default tier exists again; the prose may describe it")
        text = self._text().lower()
        for phrase in ("neutral placeholder", "placeholder tier",
                       "carries neutral", "neutral values instead"):
            self.assertNotIn(phrase, text,
                             "ARCHITECTURE.md still describes a tier discovery "
                             "no longer invents (%r)" % phrase)

    def test_the_registry_keys_it_documents_are_real(self):
        # Keys named in prose that `check_shapes` has never heard of are the
        # other direction of the same drift.
        documented = set(re.findall(r"`(outcomes|serves|ignored|watch|infra|archived|"
                                    r"annotations\.jsonc|declared)`", self._text()))
        self.assertTrue(documented, "the doc names no registry vocabulary")
        # `ignored` is consumed by discovery, the rest by sense -- so look at the
        # whole engine rather than guessing which module owns a key.
        src = "".join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO_ROOT / "src" / "nextbrief").glob("*.py")))
        for key in documented - {"annotations.jsonc"}:
            self.assertIn('"%s"' % key, src,
                          "ARCHITECTURE.md documents %r but no module reads it" % key)


class ShippedConfigTemplate(unittest.TestCase):
    """Every key in the config a new user copies must be one the engine reads.

    `nextbrief init` writes this file into the workspace, so each key in it is a
    promise that turning the number changes something. Nine of them did not: a
    `renotify_days` in two sections that no line of code has ever read, a
    `recheck_budget_per_run`, a `cost.alert_usd_7d` for cost sensing that was
    never built -- and a `notify.sink` where the sink layer reads
    `notify.backend`, so setting it to "none" to stop notifications left them
    switched on.

    That last one is the shape that makes this worth a test rather than a
    cleanup. A dead key is inert; a *misspelt* key is a setting that silently
    does the opposite of what its owner asked, and nothing else in the system
    would ever contradict the file.

    Two keys are legitimately read by the prompt rather than by Python, and are
    accepted here on that basis -- but note that a cap enforced only by asking
    the model politely is not a cap. `docs/ARCHITECTURE.md` says so, and gate 4
    exists because of it.
    """

    # Both of them. Phase 0 cleaned the template and this test only looked at the
    # template, so the example workspace kept eight dead keys and a `notify.sink`
    # that made `make render` push a real desktop notification -- in the one
    # workspace whose entire purpose is to be safe to try.
    CONFIGS = (
        ("src/nextbrief/templates/config.example.jsonc", "the shipped template"),
        ("examples/workspace/config.jsonc", "the example workspace"),
    )

    def _template(self, rel="src/nextbrief/templates/config.example.jsonc"):
        from nextbrief.jsonc import load_jsonc
        return load_jsonc(str(REPO_ROOT / rel))

    @staticmethod
    def _leaves(obj, path=""):
        for key, value in obj.items():
            full = (path + "." + key) if path else key
            if isinstance(value, dict):
                yield from ShippedConfigTemplate._leaves(value, full)
            else:
                yield full, key

    def _string_constants(self):
        # String *constants*, parsed, not grepped. `ccusage` appeared in a
        # comment while being read by nothing, and a grep would have called that
        # a reference.
        import ast

        found = set()
        for path in sorted((REPO_ROOT / "src" / "nextbrief").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    found.add(node.value)
        return found

    def _prompt_text(self):
        return "".join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO_ROOT / "src" / "nextbrief" / "prompts").rglob("*.md")))

    def test_every_key_it_ships_is_read_by_something(self):
        constants = self._string_constants()
        prompts = self._prompt_text()
        for rel, label in self.CONFIGS:
            orphans = [full for full, leaf in self._leaves(self._template(rel))
                       if leaf not in constants and leaf not in prompts]
            self.assertEqual(orphans, [],
                             "%s ships keys nothing reads: %s" % (label, orphans))

    def test_the_notify_reasons_it_lists_are_ones_should_notify_implements(self):
        # The same defect one level down: `only_if` is a list of names, and a name
        # `should_notify` does not know is never true. Asking for a notification
        # in a vocabulary the code does not speak produces silence, which is
        # indistinguishable from nothing having happened.
        import inspect

        from nextbrief import render

        source = inspect.getsource(render.should_notify)
        for rel, label in self.CONFIGS:
            for reason in self._template(rel)["notify"]["only_if"]:
                self.assertIn('"%s"' % reason, source,
                              "%s lists notify reason %r that should_notify "
                              "never tests" % (label, reason))

    def test_the_example_workspace_cannot_push_a_real_notification(self):
        """Its own comment promises this, and the key it used could not keep it.

        `notify.sink` is read by nothing -- the sink layer reads `notify.backend`
        -- so the example resolved to the platform default and pushed a real
        desktop banner on every `make render`. An example whose whole purpose is
        to be safe to try must be safe to try.
        """
        from nextbrief.sinks import resolve_backend

        self.assertEqual(
            resolve_backend(self._template("examples/workspace/config.jsonc")),
            "none")


class ReleaseHistory(unittest.TestCase):
    """The README's release table, against the CHANGELOG it indexes.

    A hand-maintained list of releases is a list that stops being true the first
    time somebody cuts a tag in a hurry. The failure is quiet — a table missing
    its newest row looks exactly like a table — so the correspondence is checked
    rather than trusted.
    """

    def _versions_in_changelog(self):
        return re.findall(r"^## \[([^\]]+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?",
                          read(CHANGELOG), re.MULTILINE)

    def _table(self):
        text = read(README)
        start = text.index("## Release history")
        return text[start:text.index("\n## ", start + 1)]

    def test_every_released_version_appears(self):
        table = self._table()
        for version, _date in self._versions_in_changelog():
            self.assertIn(version, table,
                          "CHANGELOG has %s and the README release table does not" % version)

    def test_the_dates_agree_with_the_changelog(self):
        table = self._table()
        for version, date in self._versions_in_changelog():
            if not date:
                continue          # Unreleased carries no date, by definition
            row = [ln for ln in table.splitlines() if ("[%s]" % version) in ln]
            self.assertTrue(row, "no row for %s" % version)
            # The DATE CELL, not the row. The anchor link in the first cell ends
            # in the same date, so `assertIn(date, row)` matched there and passed
            # while the published column said something else entirely -- caught
            # by mutating the date and watching this stay green.
            cells = [c.strip() for c in row[0].strip().strip("|").split("|")]
            self.assertEqual(cells[1], date,
                             "README dates %s as %r; the CHANGELOG says %s"
                             % (version, cells[1], date))

    def test_it_is_ordered_newest_first(self):
        table = self._table()
        dates = re.findall(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|", table)
        self.assertTrue(dates, "the table carries no dates at all")
        self.assertEqual(dates, sorted(dates, reverse=True),
                         "release rows are not newest-first: %s" % dates)

    def test_it_does_not_invent_a_version(self):
        # The other direction. A row for a version nobody tagged is the same
        # promise-the-repo-cannot-keep this file already checks for in the
        # CHANGELOG itself.
        known = {v for v, _ in self._versions_in_changelog()}
        for row in self._table().splitlines():
            for cited in re.findall(r"\[([0-9][^\]]*)\]\(CHANGELOG", row):
                self.assertIn(cited, known,
                              "README release table cites %s, which the CHANGELOG "
                              "does not have" % cited)


class EveryCommandIsDocumented(unittest.TestCase):
    """The reverse direction: the docs already promise nothing the code lacks,
    and this checks the code offers nothing the docs never mention.

    A subcommand nobody can find is a subcommand nobody uses. The failure is
    silent by construction -- `--help` lists it, so it looks documented to anyone
    who already knows it is there, and invisible to everyone else.

    Both languages, because a reader of one is not served by the other. The
    translations diverge under exactly this pressure: an English section gets
    added and its counterpart does not, and nothing notices until somebody reads
    the shorter file looking for a command that is not in it.
    """

    def _commands(self):
        src = (REPO_ROOT / "src" / "nextbrief" / "cli.py").read_text(encoding="utf-8")
        # From the dispatch table, which is what `main` actually resolves. Read
        # from the source rather than by importing and walking argparse, so that
        # a command registered but never wired up still counts as missing.
        names = sorted(set(re.findall(r'"([a-z0-9-]+)": cmd_', src)))
        self.assertGreater(len(names), 10, "the command table stopped parsing")
        return names

    def _documents(self, doc, name):
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        # Mentioned as a command, not merely as an English word. `open`, `do`,
        # `show` and `log` are all ordinary prose, and matching them bare would
        # make this test pass on any README containing a sentence.
        return re.search(r"(nextbrief|nb)[^\n`]{0,40}\b%s\b" % re.escape(name), text)

    def test_readme_documents_every_command(self):
        missing = [n for n in self._commands() if not self._documents("README.md", n)]
        self.assertEqual(missing, [], "README.md never mentions: %s" % " ".join(missing))

    def test_the_chinese_readme_documents_every_command(self):
        missing = [n for n in self._commands()
                   if not self._documents("README.zh.md", n)]
        self.assertEqual(missing, [],
                         "README.zh.md never mentions: %s" % " ".join(missing))


class TheTrustAnswerIsAboveTheFold(unittest.TestCase):
    """A tool that reads every directory you own has to say so early.

    It used to say so at line 589 of 626 -- after the install instructions, the
    cost table and the command reference. Anyone deciding whether to run it had
    already decided by then, which makes the honesty decorative.

    "Above the fold" is measured as a line number rather than a section order,
    because a section can be moved down by anything inserted above it and nobody
    would notice. 120 lines is roughly two screens: past the tagline and the
    three-stage diagram, and before the first thing that asks for a decision.
    """

    FOLD = 120

    def _first_line_matching(self, doc, pattern):
        for i, line in enumerate((REPO_ROOT / doc).read_text(encoding="utf-8")
                                 .splitlines(), 1):
            if re.search(pattern, line):
                return i
        return None

    def test_both_readmes_answer_what_it_reads_early(self):
        for doc, pattern in (("README.md", r"^## What it reads"),
                             ("README.zh.md", r"^## 它读什么")):
            at = self._first_line_matching(doc, pattern)
            self.assertIsNotNone(at, "%s has no trust section at all" % doc)
            self.assertLessEqual(
                at, self.FOLD,
                "%s answers 'what does this read' at line %d; anyone deciding "
                "whether to run it has decided before then" % (doc, at))

    def test_the_trust_section_names_what_leaves_the_machine(self):
        """The question people actually have. A section that describes what is
        read and stops there answers the easier half."""
        for doc, sends in (("README.md", "digest.json"), ("README.zh.md", "digest.json")):
            head = "\n".join((REPO_ROOT / doc).read_text(encoding="utf-8")
                             .splitlines()[:self.FOLD])
            self.assertIn(sends, head,
                          "%s does not say what leaves the machine, above the fold" % doc)
            self.assertIn("v0", head,
                          "%s does not say which command sends nothing" % doc)

    def test_security_md_exists_and_is_linked(self):
        self.assertTrue((REPO_ROOT / "SECURITY.md").is_file(),
                        "a tool with this reach ships no SECURITY.md")
        for doc in ("README.md", "README.zh.md"):
            self.assertIn("SECURITY.md",
                          (REPO_ROOT / doc).read_text(encoding="utf-8"),
                          "%s never links SECURITY.md" % doc)


class TheMarkAtTheTop(unittest.TestCase):
    """The artwork has existed since the icon was built and appeared nowhere.

    Two things about putting it in a README are easy to get wrong in ways that
    only show on somebody else's screen, so both are pinned rather than eyeballed
    once.
    """

    # Written out rather than derived from the file: the point of the test is
    # that the READMEs agree with each other on a specific path, and deriving it
    # from whichever path they happen to contain would agree with anything.
    MARK = "packaging/icon/nextbrief.svg"

    def _heads(self):
        for doc in ("README.md", "README.zh.md"):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            yield doc, text, "\n".join(text.splitlines()[:12])

    def test_both_readmes_open_with_the_same_mark(self):
        self.assertTrue((REPO_ROOT / self.MARK).is_file(),
                        "%s does not exist, so both READMEs link to nothing" % self.MARK)
        for doc, _text, head in self._heads():
            self.assertIn(self.MARK, head,
                          "%s does not show the mark in its first 12 lines" % doc)

    def test_the_mark_carries_its_own_colour(self):
        """The trap, and the reason `nextbrief-mono.svg` is not the file above.

        The monochrome mark is drawn in `currentColor`, which is what makes it
        useful for checking the silhouette and useless here: an SVG loaded
        through `<img>` inherits nothing from the page, so `currentColor`
        resolves to its own initial value -- black -- and the mark is black on
        `#0d1117` for every reader with GitHub's dark theme on. Rendered both
        ways to check rather than reasoned about: the colour mark reads on both
        backgrounds because it carries its own ivory sheet and a rim that holds
        it against white, and the mono one disappears into the dark.
        """
        svg = (REPO_ROOT / self.MARK).read_text(encoding="utf-8")
        self.assertNotIn(
            "currentColor", svg,
            "%s is drawn in currentColor. Through an <img> that resolves to "
            "black, so it vanishes on a dark background -- which is most of the "
            "readers who will ever see it." % self.MARK)

    def test_the_mark_does_not_reach_outside_the_repository(self):
        """A logo served from somewhere else is a logo that can change, expire,
        or report who read the page."""
        for doc, _text, head in self._heads():
            for line in head.splitlines():
                if self.MARK not in line:
                    continue
                self.assertNotIn("http", line,
                                 "%s loads the mark over the network: %s"
                                 % (doc, line.strip()))
