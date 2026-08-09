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
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

TAG = "v%s" % __version__

REPO_URL = "https://github.com/hancheng-ai/nextbrief"


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
    which skips prereleases.

    Before 0.2.0 that endpoint 404'd outright, because every release was an rc.
    It resolves now, and the rule survives the reason it was written for: it
    resolves to the newest *non-prerelease*, while every other version string on
    the page is swept to whatever was last tagged. Tag a candidate and the two
    disagree -- the page names one version and its download links serve another,
    with nothing in the response to say so. A URL that points at the version the
    surrounding prose names is the only kind that cannot drift."""

    def test_no_latest_download_urls(self):
        # Matched as a URL, not as a substring: both files discuss
        # `/releases/latest/` in prose precisely to explain why they avoid it.
        bad = re.compile(r"https://github\.com/\S*/releases/latest/download")
        for path in (README, README_ZH):
            self.assertIsNone(
                bad.search(read(path)),
                "%s links a /releases/latest/ asset, which serves the newest "
                "non-prerelease rather than the version this page documents"
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

    # Sixty-four hex characters is all the check above can see, and a digest
    # four releases old is sixty-four hex characters. `version` is swept by
    # scripts/bump-version.sh on every bump; the digest cannot be, because it
    # belongs to an asset that does not exist until the tag is pushed. So they
    # separate on every release and are meant to be rejoined by a second, manual
    # commit -- which was skipped for 0.2.0rc1 through rc4, leaving the formula
    # pointing at a `0.2.0rc*` tarball with the 0.1.0rc14 digest and the README
    # printing a `brew install` that fails its checksum. Nothing in the repo
    # could say so, because nothing recorded which release the digest came from.
    DIGEST_PROVENANCE = re.compile(r"^  # sha256-of: (\S+)$", re.MULTILINE)

    # The pinned build, as documented. `--HEAD` builds from main and checks no
    # digest, so it is always safe to offer; this is the other one.
    PINNED_INSTALL = "brew install --build-from-source"

    def _digest_version(self):
        named = self.DIGEST_PROVENANCE.search(read(FORMULA))
        self.assertIsNotNone(
            named,
            "the formula does not record which release its sha256 was taken "
            "from. Add a `  # sha256-of: <version>` line above it: the digest "
            "is the one version literal in this repository that no script can "
            "sweep, so the only way to see it go stale is to write down what it "
            "belongs to.")
        return named.group(1)

    def test_the_readmes_do_not_offer_a_pinned_build_with_a_stale_digest(self):
        digest_version = self._digest_version()
        if digest_version == __version__:
            return          # rejoined; the pinned command is honest again
        for path in (README, README_ZH):
            self.assertNotIn(
                self.PINNED_INSTALL, read(path),
                "%s offers `%s ...`, which downloads the %s sdist and checks it "
                "against a digest taken from %s. That command fails. Either "
                "update the formula's sha256 and its `sha256-of:` line to %s, "
                "or keep documenting `--HEAD` until you can."
                % (path.name, self.PINNED_INSTALL, __version__, digest_version,
                   __version__))

    def test_exactly_one_line_records_where_the_digest_came_from(self):
        """`_digest_version` reads the first match and so does the release job.

        Two matching lines is not a conflict either of them would report -- they
        would agree, on the wrong one, the moment a second was added above the
        real one. The formula's surrounding prose discusses `sha256-of:` at
        length, which is exactly the material a reformat turns into a line that
        starts in the wrong column.
        """
        found = self.DIGEST_PROVENANCE.findall(read(FORMULA))
        self.assertEqual(
            len(found), 1,
            "the formula has %d `  # sha256-of:` lines; the guard and the "
            "release workflow both take the first and would silently agree on "
            "it: %s" % (len(found), found))

    # Both quote styles: the release job writes one of these patterns with
    # single quotes (it contains a double quote) and the other with double.
    RAW_STRING = re.compile(r"""r(['"])((?:(?!\1).)*)\1""")

    def test_the_release_workflow_rewrites_the_lines_this_class_reads(self):
        """The automation and the guard have to be talking about the same lines.

        `.github/workflows/release.yml` opens a pull request setting the digest
        and the `sha256-of:` line from the release's SHA256SUMS -- it is why the
        manual rejoining commit is no longer something to forget, having been
        forgotten for 0.2.0rc1 through rc4. It finds those two lines with its own
        regexes, in another language, in a file nothing here imports.

        So reindent the stanza, or rename the marker, and the plausible outcome
        is not a loud failure: it is a release whose job cannot find the line,
        or -- worse -- a guard that has stopped reading the line the job still
        writes. Compiled and run against the real formula rather than compared
        as text, because the two patterns are spelled differently on purpose and
        agreeing on the spelling is not what matters.
        """
        patterns = [body for _, body in self.RAW_STRING.findall(read(RELEASE_WORKFLOW))
                    if "sha256" in body]
        self.assertEqual(
            len(patterns), 2,
            "expected the release workflow to target exactly the sha256 line "
            "and the sha256-of line; found %d patterns: %s"
            % (len(patterns), patterns))

        formula = read(FORMULA)
        for pattern in patterns:
            hits = re.findall(pattern, formula)
            self.assertEqual(
                len(hits), 1,
                "the release workflow rewrites %r, which matches %d lines in "
                "the formula. It rewrites the first one it finds."
                % (pattern, len(hits)))

        # And specifically: the line the job rewrites is the line
        # `_digest_version` reads. Nothing above pins them to each other.
        provenance = [p for p in patterns if "sha256-of" in p]
        self.assertEqual(len(provenance), 1, patterns)
        theirs = re.search(provenance[0], formula)
        mine = self.DIGEST_PROVENANCE.search(formula)
        self.assertIsNotNone(mine)
        self.assertEqual(
            theirs.group(0), mine.group(0),
            "the release workflow updates one line and this test reads another")

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

    # The link target used to be the bare `CHANGELOG.md`, and this pattern was
    # anchored on `](CHANGELOG`. Absolutising the links for PyPI put a
    # `https://github.com/.../blob/vX.Y.Z/` in front of every one of them, and
    # the pattern stopped matching -- leaving a loop with no body and a test
    # that could not fail. `checked` below is why that cannot happen quietly a
    # second time.
    CITES = re.compile(r"\[([0-9][^\]]*)\]\((?:\S*/)?CHANGELOG\.md#")

    def test_it_does_not_invent_a_version(self):
        # The other direction. A row for a version nobody tagged is the same
        # promise-the-repo-cannot-keep this file already checks for in the
        # CHANGELOG itself.
        known = {v for v, _ in self._versions_in_changelog()}
        checked = 0
        for row in self._table().splitlines():
            for cited in self.CITES.findall(row):
                self.assertIn(cited, known,
                              "README release table cites %s, which the CHANGELOG "
                              "does not have" % cited)
                checked += 1
        self.assertGreater(checked, 5,
                           "only %d rows in the release table cite a CHANGELOG "
                           "anchor at all; this test is reading nothing" % checked)


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

    # The only host allowed to serve it, and the reason a relative path is not
    # an option. `pyproject.toml` sets `readme = "README.md"`, so that file is
    # PyPI's long description, and a relative `src` has no meaning on a PyPI
    # page: it renders as a broken image, above the fold, on the page most new
    # users land on. A broken image is louder than no image.
    #
    # This test used to forbid any `http` on the mark's line, which sounds like
    # the safe rule and is not one that can be satisfied -- it forced the broken
    # render and offered nothing in exchange. The hazard actually worth refusing
    # is a *third-party* host: somebody else's uptime, and somebody else's view
    # of who reads this page. This repository's own file served from
    # raw.githubusercontent is neither, and both GitHub and PyPI proxy images
    # through camo, so no reader's address reaches the origin regardless.
    MARK_ORIGIN = "https://raw.githubusercontent.com/hancheng-ai/nextbrief/"

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

    def _image_sources(self, head):
        return re.findall(r'<img[^>]*?\ssrc="([^"]*)"', head)

    def test_every_image_above_the_fold_comes_from_this_repository(self):
        """A logo served from somewhere else is a logo that can change, expire,
        or report who read the page. A logo served from nowhere -- a relative
        path -- is a broken image on PyPI. See MARK_ORIGIN for why this is a
        host allowlist rather than a ban on `http`."""
        for doc, _text, head in self._heads():
            srcs = self._image_sources(head)
            self.assertTrue(srcs, "%s shows no image in its first 12 lines" % doc)
            for src in srcs:
                self.assertTrue(
                    src.startswith(self.MARK_ORIGIN),
                    "%s serves an image from %r. It must come from %s -- a "
                    "relative path renders broken on PyPI, where README.md is "
                    "the long description, and any other host is somebody "
                    "else's uptime and somebody else's log."
                    % (doc, src, self.MARK_ORIGIN))

    def test_the_mark_is_pinned_to_this_release_rather_than_to_a_branch(self):
        """PyPI keeps every version's long description forever.

        A `main`-pinned URL breaks the rendered page of every release ever
        published the day that file is moved or renamed; a tag-pinned one keeps
        working, because the tag keeps pointing at the tree that shipped. The
        tag is swept by `scripts/bump-version.sh` along with every other version
        string in the file, so this costs nothing per release -- and goes red if
        a bump ever stops reaching it, which is the failure that would otherwise
        leave the URL quietly on an old tag.
        """
        for doc, _text, head in self._heads():
            for src in self._image_sources(head):
                ref = src[len(self.MARK_ORIGIN):].split("/")[0]
                # assertTrue rather than assertEqual: on two strings the latter
                # renders a multi-line diff and pushes the sentence that says
                # what to do below it, where it reads as a footnote.
                self.assertTrue(
                    ref == TAG,
                    "%s serves the mark from ref %r, and this release is %s. It "
                    "must be a tag: PyPI renders every version's long "
                    "description from whatever the URL resolves to at read "
                    "time, so a branch breaks the page of every release ever "
                    "published the day that file moves. "
                    "`scripts/bump-version.sh` sweeps this ref along with the "
                    "rest of the file." % (doc, ref, TAG))


class EveryLinkSurvivesLeavingGitHub(unittest.TestCase):
    """The mark's problem one layer out: the fifty links printed around it.

    `pyproject.toml` sets `readme = "README.md"`, so that file is PyPI's long
    description, and PyPI renders it with no base URL. A relative link therefore
    resolves against the *project page* rather than against this repository:
    `[example workspace](examples/workspace)` arrives as
    `https://pypi.org/project/nextbrief/examples/workspace` and 404s. Reported
    from the live page, with 33 links in README.md and 17 in README.zh.md doing
    it.

    The half-fix is why this is a guard rather than just a commit. The mark was
    moved to an absolute, tag-pinned URL for precisely this reason; the links
    two lines below it were noticed in the same breath and left relative, and
    nothing here could tell the difference -- because on GitHub, which is where
    the file gets reviewed, every one of them works.

    Anchors are untouched and stay allowed. `](#privacy)` resolves inside the
    rendered page wherever that page is served.

    README.zh.md is not a long description today and is held to the same rule
    anyway: the two files are kept in step by the rest of this module, and a
    rule that covers one of a translated pair is a rule that gets translated
    away.
    """

    DOCS = ("README.md", "README.zh.md")

    # `](target)`, plus whatever an inline HTML fragment carries -- the mark is
    # an <img>, and a relative <a href> further down the page would sail past a
    # Markdown-only pattern.
    LINK = re.compile(r"\]\(([^)\s]+)\)")
    ATTR = re.compile(r"<[a-zA-Z]+[^>]*?\s(?:href|src)=\"([^\"]*)\"")

    # Removed before scanning: a link inside a fence is sample text that renders
    # as characters, not as a link, so it cannot 404 anywhere.
    FENCE = re.compile(r"(?ms)^```.*?^```")

    # A scheme, a protocol-relative host, or an anchor -- the three shapes that
    # do not need a base URL to mean something.
    RESOLVES_ANYWHERE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//|#)")

    IN_REPO = re.compile(
        re.escape(REPO_URL) + r"/(?P<kind>blob|tree)/(?P<ref>[^/]+)/(?P<path>[^#\s]+)")

    def _targets(self, doc):
        text = self.FENCE.sub("", (REPO_ROOT / doc).read_text(encoding="utf-8"))
        found = self.LINK.findall(text) + self.ATTR.findall(text)
        # Asserted, not assumed. A pattern that quietly stops matching turns
        # every loop below into one with no body, which is the first of the four
        # ways rule 7 of CONTRIBUTING.md lists for a test to be green while
        # checking nothing. Both files carry dozens of links and always will.
        self.assertGreater(
            len(found), 25,
            "%s: the link scan found %d targets, so the assertions below are "
            "not reading this file any more" % (doc, len(found)))
        return found

    def test_neither_readme_uses_a_relative_path_link(self):
        for doc in self.DOCS:
            for target in self._targets(doc):
                if self.RESOLVES_ANYWHERE.match(target):
                    continue
                path, _, anchor = target.partition("#")
                kind = "tree" if (REPO_ROOT / path).is_dir() else "blob"
                self.fail(
                    "%s links %r as a relative path. README.md is PyPI's long "
                    "description (`readme = \"README.md\"` in pyproject.toml) "
                    "and PyPI renders it with no base URL, so that link is "
                    "resolved against the project page -- "
                    "https://pypi.org/project/nextbrief/%s -- and 404s. Write "
                    "%s/%s/%s/%s instead, keeping any #anchor as it is. An "
                    "anchor-only link such as `](#privacy)` needs no base URL "
                    "and is what this guard allows."
                    % (doc, target, target, REPO_URL, kind, TAG,
                       path + ("#" + anchor if anchor else "")))

    def test_every_link_into_this_release_names_a_path_that_exists(self):
        """A rewritten link with a typo in it is a 404 that reads as a fix.

        Only the links pinned to this release are resolved: this checkout *is*
        that tree, so it can answer for them. A link on an older tag points at a
        tree this working copy is not, and nothing here can check it -- which is
        the point of pinning, not a gap in it.
        """
        checked = 0
        for doc in self.DOCS:
            for target in self._targets(doc):
                found = self.IN_REPO.match(target)
                if not found or found.group("ref") != TAG:
                    continue
                path = REPO_ROOT / found.group("path")
                if found.group("kind") == "tree":
                    self.assertTrue(
                        path.is_dir(),
                        "%s links %s as a directory, and %s is not one in this "
                        "checkout" % (doc, target, found.group("path")))
                else:
                    self.assertTrue(
                        path.is_file(),
                        "%s links %s, and %s is not a file in this checkout. "
                        "The tag is swept forward on every release, so a path "
                        "renamed here without the README following it becomes a "
                        "404 on the next one."
                        % (doc, target, found.group("path")))
                checked += 1
        self.assertGreater(checked, 25,
                           "only %d links into this release were resolved; the "
                           "URL pattern has stopped matching" % checked)

    def test_no_link_is_pinned_to_a_branch(self):
        """Deliberately "a tag" rather than "this tag", and the difference is
        the release-history table.

        Everything outside `<!-- bump-version:skip:begin -->` is swept forward
        by `scripts/bump-version.sh` on every release and is therefore on the
        current tag. The table inside those markers is fenced off from the sweep
        on purpose -- each row is a statement about a release that already
        happened -- so its links keep whatever tag was current when the row was
        written, and go on resolving, because that tag still points at a tree
        that has that anchor in its CHANGELOG. Asserting TAG everywhere would go
        red on the first bump after this one and blame the table for the fence
        working as designed.

        What is never right is a branch. PyPI keeps every version's long
        description forever, so a `main`-pinned URL breaks the page of every
        release ever published the day that file moves.
        """
        for doc in self.DOCS:
            for target in self._targets(doc):
                found = self.IN_REPO.match(target)
                if not found:
                    continue
                ref = found.group("ref")
                self.assertTrue(
                    re.match(r"^v\d", ref),
                    "%s links %s, whose ref %r is a branch rather than a "
                    "release tag. Copying a URL out of the address bar hands "
                    "you /blob/main/, which is how this happens. PyPI keeps "
                    "every version's long description forever, so that page "
                    "breaks the day the file moves; write /%s/%s/ instead, "
                    "which `scripts/bump-version.sh` sweeps forward every "
                    "release." % (doc, target, ref, found.group("kind"), TAG))


class TheBadgesAtTheTop(unittest.TestCase):
    """Two badges that look alike and answer different questions.

    `release` states which version *this page* documents -- the same version its
    download URLs, its Homebrew formula and its mark URL are all pinned to. That
    is a fact about the file, so it lives in the file, is swept by
    `scripts/bump-version.sh`, and is checked here so a hand-edited release
    cannot leave it behind while everything around it moves.

    A dynamic "newest release on GitHub" badge would not need the sweep, and was
    rejected anyway: it would disagree with every pinned URL on the page from
    the moment a candidate is tagged, which is the hazard the install section
    explains two paragraphs down about `/releases/latest/`.

    `pypi` answers a different question -- what `pip install nextbrief` hands
    you today -- and this file cannot know the answer. The tag may not be
    pushed; the publish job may have refused a red build. So it is not asserted
    here, and the badge reads the index instead of this file. The two disagreeing
    while a candidate is out is the badges working, not drifting.
    """

    def _head(self, doc):
        return "\n".join(
            (REPO_ROOT / doc).read_text(encoding="utf-8").splitlines()[:12])

    def test_the_release_badge_names_the_version_this_page_documents(self):
        for doc in ("README.md", "README.zh.md"):
            head = self._head(doc)
            for want, what in (
                ("img.shields.io/badge/release-%s-" % TAG, "the release badge"),
                ("/releases/tag/%s)" % TAG, "the link under it"),
            ):
                self.assertIn(want, head,
                              "%s: %s does not say %s, while the rest of the "
                              "page is pinned to it" % (doc, what, TAG))

    def test_the_pypi_badge_reads_the_index_rather_than_this_file(self):
        """A version literal here would be a claim about a system this
        repository does not control, held in a file nothing checks against it --
        wrong for a whole release cycle if a publish ever fails."""
        for doc in ("README.md", "README.zh.md"):
            self.assertIn(
                "img.shields.io/pypi/v/nextbrief", self._head(doc),
                "%s no longer carries a PyPI badge that reads the index" % doc)


class ThePrivacyPolicy(unittest.TestCase):
    """`PRIVACY.md` exists for a form field, which is exactly why it needs a test.

    A policy written once for a submission and never read again is the kind of
    document that goes quietly wrong: the commands it names get renamed, and
    somebody eventually softens it into the reassuring version. Both are cheap
    to catch and expensive to publish.
    """

    PRIVACY = REPO_ROOT / "PRIVACY.md"

    # Claims that would be false the moment they were written. There is no
    # server, so there is nothing to retain, nothing to delete on request, and
    # no third party to share with. A policy that says otherwise is not
    # reassuring, it is the first untrue thing this repository has published.
    #
    # A blunt substring match, and a negated mention trips it on purpose. It
    # already caught a sentence in the first draft that said there was nothing
    # to *request deletion* from -- true, and still the wrong sentence, because
    # "we do not retain your data beyond 30 days" reads to almost everyone as a
    # promise about a system that exists. The way past this guard is to not
    # raise the subject.
    UNTRUE_IF_WRITTEN = (
        "retention period",
        "we retain",
        "we store your",
        "request deletion",
        "delete your data",
        "data controller",
        "third parties",
        "we collect",
    )

    def _text(self):
        self.assertTrue(self.PRIVACY.is_file(),
                        "PRIVACY.md is missing; the plugin directory's privacy "
                        "field has nowhere to point")
        return self.PRIVACY.read_text(encoding="utf-8")

    def test_it_is_linked_from_the_documents_that_would_send_someone_to_it(self):
        for doc in ("README.md", "README.zh.md", "SECURITY.md"):
            self.assertIn("PRIVACY.md",
                          (REPO_ROOT / doc).read_text(encoding="utf-8"),
                          "%s never links PRIVACY.md" % doc)

    def test_it_names_both_paths_out_and_the_control_over_them(self):
        """"Nothing leaves your machine" is the sentence a privacy page for a
        local tool writes itself, and here it is false twice over."""
        text = self._text()
        for claim, why in (
            ("digest.json", "the file stage 2 sends to a model"),
            ("probe", "the one command that fetches, and only when run"),
            ("v0", "the command that sends nothing, which is what makes the "
                   "other two a choice"),
            ("privacy.never_read", "the reader's own control over what is opened"),
        ):
            self.assertIn(claim, text,
                          "PRIVACY.md never mentions %r -- %s" % (claim, why))

    def test_the_commands_it_names_are_commands_that_exist(self):
        """A policy naming a command that was renamed is a policy describing a
        program nobody is running."""
        from nextbrief.cli import build_parser

        known = set()
        for action in build_parser()._subparsers._group_actions:
            known |= set(action.choices)
        for named in ("v0", "probe", "run"):
            self.assertIn(named, known,
                          "PRIVACY.md describes `nextbrief %s`, which the CLI no "
                          "longer accepts" % named)

    def test_it_promises_nothing_it_would_need_a_server_to_keep(self):
        text = self._text().lower()
        found = [p for p in self.UNTRUE_IF_WRITTEN if p in text]
        self.assertEqual(
            [], found,
            "PRIVACY.md makes claims that require an operator on the other end: "
            "%s. There is no server, so each of these is either meaningless or "
            "false." % found)


def _release_jobs():
    """`{name: {"needs": [...], "if": "..."}}` read out of release.yml as text.

    Text, not `yaml`, because the pinned interpreter this suite runs on --
    /usr/bin/python3, 3.9.6 -- has no PyYAML, and adding a test-only dependency
    to check a zero-dependency project is a poor trade. Jobs sit at two spaces
    and their keys at four, which is enough structure for this.
    """
    jobs, cur = {}, None
    inside = False
    for line in RELEASE_WORKFLOW.read_text(encoding="utf-8").splitlines():
        if re.match(r"^jobs:\s*$", line):
            inside = True
            continue
        if not inside or not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            cur = m.group(1)
            jobs[cur] = {"needs": [], "if": ""}
            continue
        if cur is None:
            continue
        m = re.match(r"^    needs:\s*\[(.*)\]\s*$", line)
        if m:
            jobs[cur]["needs"] = [n.strip() for n in m.group(1).split(",") if n.strip()]
            continue
        if re.match(r"^    if:", line):
            jobs[cur]["if"] = line.split(":", 1)[1].strip()
            continue
        if jobs.get(cur) and jobs[cur]["if"] and re.match(r"^      \S", line):
            jobs[cur]["if"] += " " + line.strip()
    return jobs


class AJobDownstreamOfAlwaysMustCarryAlways(unittest.TestCase):
    """A skip propagates through the graph; `always()` rescues only its own job.

    Found the expensive way on v0.2.0. The `homebrew` job needed `[build,
    github-release]`, both of which SUCCEEDED, and it was skipped anyway --
    because `github-release` had itself been rescued from a skipped `testpypi`
    by `always()`, and that rescue does not reach downstream.

    It could never have run. Exactly one publish job executes per release, so
    something upstream is always skipped. The job was written, fixture-tested and
    reviewed for whether it could break the publish; nobody asked whether it
    could execute, and a job that never runs looks exactly like one with nothing
    to do.
    """

    def test_every_such_job_has_it(self):
        jobs = _release_jobs()
        self.assertIn("homebrew", jobs, "release.yml no longer parses as expected")
        rescued = {n for n, j in jobs.items() if "always()" in j["if"]}
        missing = [
            "%s needs %s" % (name, sorted(set(job["needs"]) & rescued))
            for name, job in jobs.items()
            if set(job["needs"]) & rescued and "always()" not in job["if"]
        ]
        self.assertEqual(
            [], missing,
            "these jobs depend on a job that uses always() but do not use it "
            "themselves, so they are skipped whenever that rescue fires -- which "
            "for a release is every time: %s" % missing)

    def test_the_reader_can_actually_see_the_conditions(self):
        """The guard above passes trivially if the parser returns nothing.

        Asserted separately because a silent parse failure and a clean repo are
        the same green -- and this test file was itself briefly deleted by a
        `git checkout --` during the work that added it, which reported
        `Ran 0 tests ... OK`.
        """
        jobs = _release_jobs()
        self.assertGreaterEqual(len(jobs), 5, jobs)
        self.assertIn("always()", jobs["github-release"]["if"])
        self.assertIn("always()", jobs["homebrew"]["if"])
        self.assertEqual(["build", "github-release"], jobs["homebrew"]["needs"])
