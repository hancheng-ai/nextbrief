"""The plugin's skill body, checked as executable text rather than as prose.

A skill body is not documentation. It is a file that another person's agent
reads and then runs, on a machine and a workspace this repository will never
see. That makes every command in it a command this project is shipping, and it
puts the skill in exactly the category CLAUDE.md rule 3 is about: content read
out of a file, acted on by a model.

So the rule is enforced instead of asked for. `nextbrief do` opens a working
session; `done`, `drop` and `defer` write terminal state. None of them may
appear in a skill, and a five-line lint holds that better than a paragraph
telling a model to be careful -- an instruction is something a model can drift
from, and the whole design contract of this engine is built on not relying on
that.

Two directions, because they fail differently:

* **Allowlist over runnable text.** Anything inside a code fence or a backtick
  span is something an agent may copy and run, so every subcommand there must be
  one of the six read-only ones. Prose is excluded on purpose: "`nextbrief`
  keeps one workspace" is a sentence, not an invocation, and a scan that cannot
  tell the difference gets switched off.
* **Denylist over everything.** The gap that leaves is a state-changing command
  written in bare prose with no backticks, which an agent will still act on. So
  the whole file is also checked against the write commands by name.

Both sets are derived from the CLI's own dispatch table rather than typed out
here, so a command added to the engine tomorrow is covered tonight.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest

from helpers import REPO_ROOT, requires_posix_dev_env

from nextbrief import __version__

PLUGIN_DIR = REPO_ROOT / ".claude-plugin"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
MARKETPLACE_JSON = PLUGIN_DIR / "marketplace.json"
SKILLS_DIR = REPO_ROOT / "skills"

# The read-only six. `context --json` is the one the plugin exists for; the rest
# are the same data shaped for a person. Every one of them reads a file the
# engine already wrote and prints it.
ALLOWED = frozenset({"context", "projects", "brief", "show", "ls", "closed"})

# Global flags that swallow the token after them. Without this list the first
# non-flag token after `nextbrief --workspace /tmp/x` is the *path*, and
# `nextbrief --workspace /tmp/x do NA-0001` reads as a command called `/tmp/x`
# -- which is not in the allowlist either, so the test would still fail, but for
# the wrong reason and with an error message that sends the reader nowhere.
VALUED_GLOBAL_FLAGS = ("--workspace", "--out", "--locale")

_INVOCATION = re.compile(r"\b(?:nextbrief|nb)\b([^\n`]*)")
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")


def runnable_text(body: str) -> str:
    """The parts of a markdown body an agent may copy and run."""
    return "\n".join(_FENCE.findall(body) + _INLINE.findall(body))


def commands_in(text: str) -> list:
    """Every subcommand invoked in `text`, with global flags stepped over.

    One per invocation: the first token that is neither a flag nor a flag's
    value. `nextbrief --version` yields nothing, which is correct -- it names no
    subcommand.
    """
    found = []
    for tail in _INVOCATION.findall(text):
        tokens = tail.split()
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in VALUED_GLOBAL_FLAGS:
                i += 2
                continue
            if token.startswith("-"):
                i += 1
                continue
            found.append(token)
            break
    return found


def cli_commands() -> set:
    """Every command the CLI will accept, from both places it registers one.

    `_HANDLERS` is the dispatch table and covers almost all of them, but not
    quite all: `init` runs before a workspace can be resolved, so `main`
    special-cases it ahead of the table and it appears only as an argparse
    subparser. Deriving the denylist from the table alone left the one command
    that scaffolds a workspace and repoints the default-workspace pointer off
    the list -- which is the command most worth keeping out of a skill, and the
    one that has already written a stray workspace into this repository once.

    The union rather than either half, because the question this set answers is
    "what will the CLI do if an agent types it", and a subparser is enough for
    the answer to be yes. (`test_docs_consistency` reads the table alone on
    purpose; it is asking the different question of what is wired up.)
    """
    src = (REPO_ROOT / "src" / "nextbrief" / "cli.py").read_text(encoding="utf-8")
    names = set(re.findall(r'"([a-z0-9-]+)": cmd_', src))
    assert len(names) > 10, "the command table stopped parsing"

    from nextbrief.cli import build_parser

    for action in build_parser()._subparsers._group_actions:
        names |= set(action.choices)
    return names


def skill_files() -> list:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


class TheExtractorItself(unittest.TestCase):
    """Positive controls.

    The lint below is only worth its line count if it can actually see a banned
    command, and the cheapest way for it to be green forever is to see nothing
    at all. These are the samples that make the difference visible.
    """

    def test_it_sees_a_plain_invocation(self):
        self.assertEqual(commands_in("run `nb do NA-0001` next"), ["do"])

    def test_it_sees_one_hidden_behind_a_global_flag(self):
        # The evasion that matters: a flag with a value in front of the verb.
        self.assertEqual(
            commands_in("nextbrief --workspace /tmp/ws do NA-0001"), ["do"])
        self.assertEqual(
            commands_in("nextbrief --locale en --out /tmp/o done NA-0001"), ["done"])

    def test_it_does_not_mistake_a_bare_flag_for_a_command(self):
        self.assertEqual(commands_in("nextbrief --version"), [])

    def test_it_keeps_a_read_only_command_with_its_flags(self):
        self.assertEqual(
            commands_in("nextbrief --workspace /tmp/ws context --json"), ["context"])

    def test_it_does_not_run_past_the_end_of_a_line(self):
        # Two invocations, not one command called `projects\nnextbrief`.
        self.assertEqual(commands_in("nextbrief ls\nnextbrief projects"),
                         ["ls", "projects"])

    def test_runnable_text_keeps_code_and_drops_prose(self):
        body = "`nextbrief` keeps a workspace.\n\n```bash\nnextbrief ls\n```\n"
        self.assertNotIn("keeps", runnable_text(body))
        self.assertIn("nextbrief ls", runnable_text(body))


class TheAllowlist(unittest.TestCase):
    def test_the_allowlist_names_only_real_commands(self):
        """An allowlist entry the CLI has never heard of permits nothing and
        hides a typo -- `closed` misspelt is a command that cannot be run and a
        rule that cannot bite."""
        self.assertEqual(sorted(ALLOWED - cli_commands()), [])

    def test_the_session_and_terminal_state_commands_are_not_on_it(self):
        """Named one by one rather than derived, because these five are the
        reason the file exists and a derivation could quietly stop covering
        them.

        `init` is in the list for a reason that is not symmetry: it is the only
        one dispatched outside `_HANDLERS`, so it is the one a derived set drops
        silently, and it writes a whole workspace plus the pointer that decides
        which workspace every later bare command reads.
        """
        known = cli_commands()
        for banned in ("do", "done", "drop", "defer", "init"):
            self.assertIn(banned, known,
                          "%r is no longer a command the CLI accepts; this guard "
                          "is now vacuous" % banned)
            self.assertNotIn(banned, ALLOWED)

    def test_there_is_a_skill_to_check(self):
        """The empty-iteration guard. Every assertion below is a loop over the
        skills, and a loop over nothing is green."""
        self.assertTrue(skill_files(), "the plugin ships no SKILL.md at all")

    def test_each_skill_actually_invokes_something(self):
        """The other half of it: a skill whose commands the extractor cannot see
        passes the allowlist trivially. Assert the trigger happened."""
        for path in skill_files():
            found = commands_in(runnable_text(path.read_text(encoding="utf-8")))
            self.assertGreaterEqual(
                len(set(found)), 4,
                "%s: the extractor found %r -- either the skill runs almost "
                "nothing, or the scan is not reaching it" % (path.name, sorted(set(found))))
            self.assertIn("context", found,
                          "%s: the command the plugin exists for is missing" % path.name)

    def test_every_command_a_skill_runs_is_read_only(self):
        for path in skill_files():
            found = set(commands_in(runnable_text(path.read_text(encoding="utf-8"))))
            self.assertEqual(
                sorted(found - ALLOWED), [],
                "%s runs commands that are not read-only: %s"
                % (path.name, sorted(found - ALLOWED)))

    def test_no_skill_names_a_write_command_even_in_prose(self):
        """Backticks are a formatting choice, and an agent reading "then run
        nextbrief done NA-0001" will do it either way."""
        write_commands = cli_commands() - ALLOWED
        for path in skill_files():
            named = set(commands_in(path.read_text(encoding="utf-8")))
            self.assertEqual(
                sorted(named & write_commands), [],
                "%s names state-changing commands: %s"
                % (path.name, sorted(named & write_commands)))


class TheContractTheSkillQuotes(unittest.TestCase):
    """The skill tells another agent which `schema_version` it understands, and
    to refuse anything else. That instruction is only safe while the number in
    it is the number the engine writes.

    The failure is the quiet kind and it lands on somebody else: bump
    `INVENTORY_SCHEMA_VERSION` and every installed copy of this skill starts
    telling agents to stop on the version that is now correct, and to parse the
    one that no longer exists. Nothing in this repository would notice.
    """

    def test_the_version_it_names_is_the_version_sense_writes(self):
        from nextbrief.inventory import INVENTORY_SCHEMA_VERSION

        for path in skill_files():
            body = path.read_text(encoding="utf-8")
            quoted = re.findall(r"`?schema_version`?[:\s]+`?(\d+)`?", body)
            self.assertTrue(
                quoted,
                "%s never names a schema_version, so it tells a reader nothing "
                "about which shape it can handle" % path.name)
            self.assertEqual(
                sorted(set(quoted)), [str(INVENTORY_SCHEMA_VERSION)],
                "%s tells agents it understands schema_version %s; the engine "
                "writes %d" % (path.name, sorted(set(quoted)), INVENTORY_SCHEMA_VERSION))


class TheEngineMayNotBeInstalled(unittest.TestCase):
    """A plugin ships skills. It does not ship a Python package.

    So the complete path for somebody who finds this in a directory is
    `/plugin install` -> success -> `nextbrief: command not found`, and the one
    person who will never see it is the author, whose `PATH` has had the engine
    on it the whole time. Both halves of that were verified before this was
    written: the shell really does answer `command not found` when the binary is
    absent, and the skill really did not mention the possibility.

    Asserted here rather than trusted, because the failure is somebody else's
    first screen and this repository will never run on their machine.
    """

    def _body(self):
        bodies = [p.read_text(encoding="utf-8") for p in skill_files()]
        self.assertTrue(bodies, "the plugin ships no SKILL.md at all")
        return bodies

    def test_the_first_thing_each_skill_runs_is_the_check_that_it_can_run(self):
        """Position is the whole of it. Install guidance below three commands
        that already failed is guidance nobody reaches."""
        for path in skill_files():
            runnable = runnable_text(path.read_text(encoding="utf-8"))
            first = next((ln.strip() for ln in runnable.splitlines() if ln.strip()), "")
            self.assertEqual(
                "nextbrief --version", first,
                "%s starts by running %r. The first command has to be the one "
                "that establishes the engine exists." % (path.name, first))

    def test_each_skill_says_how_to_install_the_engine(self):
        for path, body in zip(skill_files(), self._body()):
            for line in ("pipx install nextbrief", "uv tool install nextbrief"):
                self.assertIn(line, body,
                              "%s does not tell the reader %r" % (path.name, line))
            self.assertIn("command not found", body,
                          "%s never names the failure it is guarding against, so "
                          "an agent cannot match what it sees to what it read"
                          % path.name)

    def test_no_skill_offers_to_install_the_engine_itself(self):
        """The posture, not just the words. This skill reads; putting a package
        on somebody's machine is their decision, and a skill that hedges on that
        is one an agent will read as permission."""
        for path, body in zip(skill_files(), self._body()):
            self.assertRegex(
                body, r"do not (run the install|install it) yourself",
                "%s gives install commands without saying who runs them" % path.name)

    # `/bin/sh` and a PATH of ("/usr/bin", "/bin") are the scaffolding, not the
    # subject: what is under test is the skill's wording against a real shell's
    # real "command not found". Windows has neither of those directories, so
    # the harness would be measuring its own absence.
    @requires_posix_dev_env
    def test_the_command_it_starts_with_really_does_fail_when_the_engine_is_gone(self):
        """The behavioural half, and the reason the two above are not enough.

        A skill can describe any failure it likes; what makes the description
        useful is that it matches what the shell actually produces. So the first
        command is run for real, on a `PATH` the engine is not on, and both the
        wording and the exit code it comes back with have to be ones the skill
        has already named.

        The exit code is the half worth having. "command not found" appears in
        the skill twice, once in passing, so requiring the phrase somewhere is a
        bar an accidental mention clears -- which is how the first version of
        this test survived the mutation that deleted the sentence that mattered.
        The number is written down exactly once, and it is checked against the
        number the shell really returned rather than against a constant here.
        """
        bare = os.pathsep.join(("/usr/bin", "/bin"))
        if shutil.which("nextbrief", path=bare) is not None:
            raise unittest.SkipTest(
                "nextbrief is installed in %s, so no PATH here is without it" % bare)

        first = next(ln.strip() for ln in runnable_text(
            skill_files()[0].read_text(encoding="utf-8")).splitlines() if ln.strip())
        proc = subprocess.run(["/bin/sh", "-c", first], env={"PATH": bare},
                              capture_output=True, text=True)
        output = (proc.stdout + proc.stderr).lower()

        self.assertNotEqual(0, proc.returncode,
                            "%r succeeded with the engine off the PATH, so this "
                            "test is not measuring what it thinks" % first)
        self.assertIn("not found", output,
                      "the shell reported %r instead; the skill's guidance is "
                      "keyed to wording that does not happen" % output.strip())
        for path, body in zip(skill_files(), self._body()):
            self.assertIn(
                "`%d`" % proc.returncode, body,
                "the shell exits %d when the engine is absent, and %s never "
                "names that number -- so an agent cannot tell this failure from "
                "any other non-zero exit" % (proc.returncode, path.name))


class TheManifests(unittest.TestCase):
    def _plugin(self):
        return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))

    def _marketplace(self):
        return json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))

    def test_both_manifests_exist_and_parse(self):
        for path in (PLUGIN_JSON, MARKETPLACE_JSON):
            self.assertTrue(path.is_file(), "%s is missing" % path.name)
            json.loads(path.read_text(encoding="utf-8"))

    def test_the_marketplace_lists_this_plugin_under_the_name_it_has(self):
        listed = {p.get("name") for p in self._marketplace().get("plugins") or []}
        self.assertIn(self._plugin()["name"], listed,
                      "marketplace.json lists %s; plugin.json is called %r"
                      % (sorted(listed), self._plugin()["name"]))

    def test_every_listed_source_resolves_to_a_plugin(self):
        """`source: "./"` says the plugin is this repository. If that path has no
        `.claude-plugin/plugin.json` under it, the entry installs nothing."""
        for entry in self._marketplace().get("plugins") or []:
            source = entry.get("source")
            self.assertIsInstance(source, str,
                                  "%s has no local source" % entry.get("name"))
            root = (REPO_ROOT / source).resolve()
            self.assertTrue(
                (root / ".claude-plugin" / "plugin.json").is_file(),
                "%s points at %s, which is not a plugin" % (entry.get("name"), source))
            self.assertTrue((root / "skills").is_dir(),
                            "%s points at %s, which ships no skills" % (entry.get("name"), source))

    def test_each_manifest_points_at_the_schema_for_its_own_shape(self):
        """A `$schema` that 404s is worse than no `$schema` at all.

        `marketplace.json` shipped `https://anthropic.com/claude-code/
        marketplace.schema.json` from the day it was written. That URL has never
        resolved -- Anthropic's own template carried it for eighteen months and
        replaced it for the same reason. So the manifest had never been checked
        against anything, while carrying the one field whose presence says it
        had. `plugin.json` never claimed a schema at all, which was the more
        honest of the two states.

        The real ones are hosted by SchemaStore, generated from Claude Code's
        own definitions. Verified on 2026-08-08 rather than assumed: both return
        200 with a draft-07 schema whose `$id` is the URL it was fetched from;
        the SchemaStore catalog maps them to `**/.claude-plugin/plugin.json` and
        `**/.claude-plugin/marketplace.json` respectively; the plugins reference
        prints the first as the example value for this very field; and
        `anthropics/claude-code` ships the second in its own marketplace file.

        Pinned as an exact pair rather than a pattern, and pinned per file,
        because the two URLs differ by one word and the copy-paste that swaps
        them still resolves, still validates, and validates against the wrong
        shape -- which is the same failure as the dead link, minus the tell.
        A test cannot fetch a URL without making the suite depend on somebody
        else's uptime, so what it can do is refuse anything nobody has checked.
        """
        expected = {
            "plugin.json": "https://json.schemastore.org/claude-code-plugin-manifest.json",
            "marketplace.json": "https://json.schemastore.org/claude-code-marketplace.json",
        }
        for path, doc in ((PLUGIN_JSON, self._plugin()),
                          (MARKETPLACE_JSON, self._marketplace())):
            want = expected[path.name]
            got = doc.get("$schema")
            self.assertEqual(
                want, got,
                "%s cites %r. Only the two SchemaStore URLs have been checked to "
                "exist and to describe the right file; anything else is a claim "
                "of validation nobody has verified." % (path.name, got))

    def test_the_manifests_hold_what_those_schemas_require(self):
        """The half of the schema that can be checked without the network.

        Both schemas are `required`-driven and short about it: a plugin manifest
        needs `name`, a marketplace needs `name`, `owner` and `plugins`, and each
        listed plugin needs `name` and `source`. Repeated here so that pointing
        at a schema is not the whole of the claim -- `$schema` is ignored at load
        time, so nothing at runtime would notice a manifest that failed it.
        """
        plugin, market = self._plugin(), self._marketplace()
        self.assertTrue(str(plugin.get("name") or "").strip(),
                        "plugin.json has no name, the one field the schema requires")
        for key in ("name", "owner", "plugins"):
            self.assertIn(key, market, "marketplace.json is missing required %r" % key)
        self.assertTrue(str((market["owner"] or {}).get("name") or "").strip(),
                        "marketplace.json owner has no name")
        self.assertTrue(market["plugins"], "marketplace.json lists no plugins")
        for entry in market["plugins"]:
            for key in ("name", "source"):
                self.assertIn(key, entry,
                              "marketplace entry %r is missing required %r"
                              % (entry.get("name"), key))

    def test_a_version_here_must_be_one_the_bump_script_moves(self):
        """Neither manifest carries a version today, and there are two reasons.

        The second one is the one that would change somebody's mind, so it goes
        first. `source` here is `./` in a git-hosted marketplace, and with no
        `version` set the plugin's version resolves to the source's commit SHA --
        which means users get an update whenever this repository moves. Setting
        an explicit `version` swaps that for "users get updates only when you
        bump this field", and the thing being shipped is a skill body that
        changes between releases. Pinned to the package version, a skill fix
        landing between tags would reach nobody, and `/plugin update` would tell
        them they were already current. That is a worse default than the warning
        it silences: `claude plugin validate` passes with a warning about the
        missing version, and only `--strict` turns that into an exit 1.

        And the older reason, which still holds: `scripts/bump-version.sh`
        rewrites three literals and sweeps three more files. A fourth version
        string outside both lists is the one nothing moves, and it goes stale at
        the next release while looking exactly like the others. This does not
        forbid versioning the plugin -- it requires that whoever does it teaches
        the bump script first, and decides about the paragraph above.
        """
        bump = (REPO_ROOT / "scripts" / "bump-version.sh").read_text(encoding="utf-8")
        for path, doc in ((PLUGIN_JSON, self._plugin()),
                          (MARKETPLACE_JSON, self._marketplace())):
            rel = path.relative_to(REPO_ROOT).as_posix()
            for obj in [doc] + list(doc.get("plugins") or []):
                if "version" not in obj:
                    continue
                self.assertIn(
                    rel, bump,
                    "%s carries a version and scripts/bump-version.sh has never "
                    "heard of the file" % rel)
                self.assertEqual(
                    obj["version"], __version__,
                    "%s says %r; the package is %r" % (rel, obj["version"], __version__))

    def test_the_first_sentence_is_the_one_the_inventory_will_print(self):
        """`inventory.py` reads this exact filename out of *other* people's
        projects and keeps only `_one_sentence(description)`.

        Its fallback when a first sentence runs past `MAX_DESCRIPTION` is a hard
        slice, so an opening sentence that never ends is published as a fragment
        cut mid-word. That is invisible from inside this file, which is the only
        reason it is worth a test.

        An earlier version of this also asserted the description named the tool,
        by searching `homepage + description`. The homepage is a github.com URL
        with `nextbrief` in the path, so the search always succeeded and the
        assertion could not fail in either direction. It was removed rather than
        repaired: it was checking a URL for something it meant to require of the
        prose.
        """
        from nextbrief.inventory import MAX_DESCRIPTION, _one_sentence

        for doc, label in ((self._plugin(), "plugin.json"),
                           (self._marketplace()["plugins"][0], "the marketplace entry")):
            first = _one_sentence(doc["description"])
            self.assertTrue(first.strip(), "%s has an empty description" % label)
            self.assertLessEqual(len(first), MAX_DESCRIPTION)
            self.assertRegex(
                first, r"[.!?]$",
                "%s: the first sentence does not end within %d characters, so "
                "the inventory will publish it hard-cut: ...%r"
                % (label, MAX_DESCRIPTION, first[-48:]))


if __name__ == "__main__":
    unittest.main()
