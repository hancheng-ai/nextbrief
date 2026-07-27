"""The command line.

This replaced a zsh script, and the tests below are mostly about the properties
that rewrite was for: it must work with no arguments, refuse a typo instead of
ignoring it, forward stage flags verbatim, and be safe to run twice.
"""

from __future__ import annotations

import json
import os
import unittest

from helpers import AS_OF, TempCase, capture, requires_git, write_backlog_item

from nextbrief import cli
from nextbrief.frontmatter import parse_frontmatter
from nextbrief.paths import pointer_file


class Parser(TempCase):
    def test_help_exits_zero_and_lists_the_commands(self):
        code, out, _ = capture(cli.main, ["--help"])
        self.assertEqual(code, 0)
        for command in ("run", "v0", "sense", "render", "check", "init", "do", "ls"):
            self.assertIn(command, out)

    def test_no_arguments_prints_help_rather_than_failing(self):
        code, out, _ = capture(cli.main, [])
        self.assertEqual(code, 0)
        self.assertIn("commands:", out)

    def test_unknown_subcommand_exits_non_zero(self):
        code, _, err = capture(cli.main, ["definitely-not-a-command"])
        self.assertNotEqual(code, 0)
        self.assertIn("invalid choice", err)

    def test_unknown_flag_on_a_normal_command_is_an_error(self):
        # A typo that is silently ignored becomes an option that never took
        # effect, discovered weeks later.
        ws = self.workspace(with_git=False)
        code, _, err = capture(cli.main, ["--workspace", str(ws), "ls", "--not-a-flag"])
        self.assertNotEqual(code, 0)
        self.assertIn("unrecognized", err)

    def test_version(self):
        code, out, _ = capture(cli.main, ["--version"])
        self.assertEqual(code, 0)
        self.assertIn("nextbrief", out)

    def test_an_unresolved_workspace_is_a_usage_error(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        os.chdir(str(bare))
        code, _, err = capture(cli.main, ["ls"])
        self.assertEqual(code, 2)
        self.assertIn("nextbrief init", err)


class StageArguments(unittest.TestCase):
    """Stage flags are taken from the raw argv, because argparse's leftovers lose
    the adjacency between an option and its value."""

    def test_global_flags_are_stripped_and_the_rest_is_verbatim(self):
        argv = ["--workspace", "/ws", "sense", "--as-of", "2026-03-16", "--stdout"]
        self.assertEqual(
            cli._stage_args(argv, "sense"), ["--as-of", "2026-03-16", "--stdout"]
        )

    def test_equals_form_of_a_global_flag(self):
        argv = ["--locale=zh", "render", "--dry-run"]
        self.assertEqual(cli._stage_args(argv, "render"), ["--dry-run"])

    def test_a_global_flag_after_the_subcommand_is_still_consumed(self):
        argv = ["run", "--workspace", "/ws", "--no-notify"]
        self.assertEqual(cli._stage_args(argv, "run"), ["--no-notify"])


class Pipeline(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def test_check_reports_a_missing_snapshot_with_exit_code_three(self):
        # The contract a scheduler branches on without parsing output.
        code, _, _ = capture(cli.main, ["--workspace", str(self.ws), "check"])
        self.assertEqual(code, 3)

    def test_sense_forwards_its_flags(self):
        # Two flags, one of which takes a value: the pair has to survive the trip
        # through argparse with its adjacency intact.
        code, out, err = capture(
            cli.main, ["--workspace", str(self.ws), "sense", "--as-of", AS_OF, "--stdout"]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["run"]["as_of_date"], AS_OF)
        self.assertFalse((self.ws / "state").exists())

    def test_check_agrees_with_a_snapshot_it_just_wrote(self):
        code, _, err = capture(cli.main, ["--workspace", str(self.ws), "sense"])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.ws / "state" / "snapshot.json").is_file())
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "check"])[0], 0)

    def test_v0_runs_the_whole_deterministic_pipeline(self):
        code, _, err = capture(
            cli.main, ["--workspace", str(self.ws), "v0", "--no-notify"]
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((self.ws / "BRIEF.md").is_file())
        self.assertTrue((self.ws / "BRIEF.html").is_file())

    def test_brief_and_log_explain_themselves_before_there_is_anything_to_show(self):
        code, _, err = capture(cli.main, ["--workspace", str(self.ws), "brief"])
        self.assertEqual(code, 1)
        self.assertIn("nextbrief v0", err)
        code, _, err = capture(cli.main, ["--workspace", str(self.ws), "log"])
        self.assertEqual(code, 1)
        self.assertIn("nextbrief v0", err)


class BacklogCommands(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        self.item = write_backlog_item(self.ws, "NA-0001", title="An open item")

    def _fields(self):
        return parse_frontmatter(self.item.read_text(encoding="utf-8"))[0]

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def test_ls_lists_open_items(self):
        code, out, err = self._run("ls")
        self.assertEqual(code, 0, err)
        self.assertIn("NA-0001", out)
        self.assertIn("An open item", out)

    def test_ls_on_an_empty_backlog_says_so(self):
        os.remove(str(self.item))
        code, out, _ = self._run("ls")
        self.assertEqual(code, 0)
        self.assertIn("Nothing open", out)

    def test_show_prints_the_whole_file(self):
        code, out, _ = self._run("show", "NA-0001")
        self.assertEqual(code, 0)
        self.assertIn("- [ ] It is done", out)

    def test_an_unknown_id_is_reported_not_guessed(self):
        code, _, err = self._run("show", "NA-9999")
        self.assertEqual(code, 1)
        self.assertIn("NA-9999", err)

    def test_ok_confirms(self):
        self.assertEqual(self._run("ok", "NA-0001")[0], 0)
        self.assertIs(self._fields()["human_confirmed"], True)
        self.assertEqual(self._fields()["status"], "open")

    def test_done_closes_and_confirms(self):
        # Closing an item is the strongest possible statement that it was real and
        # worded the way you meant, so human_confirmed rides along.
        self.assertEqual(self._run("done", "NA-0001")[0], 0)
        self.assertEqual(self._fields()["status"], "done")
        self.assertIs(self._fields()["human_confirmed"], True)

    def test_drop_keeps_the_file(self):
        self.assertEqual(self._run("drop", "NA-0001")[0], 0)
        self.assertEqual(self._fields()["status"], "dropped")
        self.assertTrue(self.item.is_file())

    def test_closing_outside_a_repository_warns_about_the_missing_baseline(self):
        # Without a baseline the write-permission gate cannot tell your `done`
        # from an agent's, so the CLI says so instead of failing quietly.
        _code, _out, err = self._run("done", "NA-0001")
        self.assertIn("write-permission gate", err)


class Init(TempCase):
    def setUp(self):
        super().setUp()
        self.target = self.tmp / "new-vault"
        # A global identity, so the documented "one commit to diff against"
        # invariant can actually be checked here.
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = Example User\n\temail = example@example.invalid\n",
            encoding="utf-8",
        )

    def _init(self):
        return capture(cli.main, ["init", str(self.target), "-y", "--no-scan"])

    def test_creates_a_usable_workspace(self):
        code, out, err = self._init()
        self.assertEqual(code, 0, err)
        for name in ("registry.jsonc", "config.jsonc", ".gitignore"):
            self.assertTrue((self.target / name).is_file(), name)
        for name in ("backlog", "state", "log", "prompts"):
            self.assertTrue((self.target / name).is_dir(), name)
        self.assertIn("Workspace ready", out)

    def test_writes_the_pointer_so_later_commands_need_no_flags(self):
        self.assertEqual(self._init()[0], 0)
        self.assertEqual(
            pointer_file().read_text(encoding="utf-8").strip(), str(self.target)
        )

    def test_the_generated_registry_parses(self):
        from nextbrief.jsonc import load_jsonc

        self.assertEqual(self._init()[0], 0)
        registry = load_jsonc(self.target / "registry.jsonc")
        self.assertIn("defaults", registry)
        self.assertIn("projects", registry)

    def test_rerunning_is_idempotent(self):
        self.assertEqual(self._init()[0], 0)
        # Edit the registry the way a human would, then re-run: the most valuable
        # file in the workspace must survive.
        registry = self.target / "registry.jsonc"
        edited = registry.read_text(encoding="utf-8") + "\n// a human was here\n"
        registry.write_text(edited, encoding="utf-8")
        config_before = (self.target / "config.jsonc").read_bytes()

        code, out, err = self._init()
        self.assertEqual(code, 0, err)
        self.assertEqual(registry.read_text(encoding="utf-8"), edited)
        self.assertEqual((self.target / "config.jsonc").read_bytes(), config_before)
        self.assertIn("already a workspace", out)

    @requires_git
    def test_leaves_a_git_baseline_for_the_write_permission_gate(self):
        self.assertEqual(self._init()[0], 0)
        self.assertTrue((self.target / ".git").is_dir())
        from helpers import git

        proc = git(self.target, "rev-parse", "--verify", "HEAD")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))

    def test_state_is_gitignored_because_it_is_a_view_not_a_record(self):
        self.assertEqual(self._init()[0], 0)
        ignored = (self.target / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/state/", ignored)
        self.assertIn("/BRIEF.md", ignored)


class ConfigTolerance(TempCase):
    def test_a_broken_config_warns_but_the_cli_still_works(self):
        # Only the pipeline stages truly need config; every CLI command has a
        # working default, and a JSONC typo must not lock you out of `ls`.
        ws = self.workspace(with_git=False)
        write_backlog_item(ws, "NA-0001", title="Still listable")
        (ws / "config.jsonc").write_text("{ broken", encoding="utf-8")
        code, out, _ = capture(cli.main, ["--workspace", str(ws), "ls"])
        self.assertEqual(code, 0)
        self.assertIn("Still listable", out)


if __name__ == "__main__":
    unittest.main()
