"""The command line.

This replaced a zsh script, and the tests below are mostly about the properties
that rewrite was for: it must work with no arguments, refuse a typo instead of
ignoring it, forward stage flags verbatim, and be safe to run twice.
"""

from __future__ import annotations

import datetime as dt
import errno
import json
import os
import subprocess
import unittest
from unittest import mock

from helpers import (
    AS_OF,
    BASE_CONFIG,
    TempCase,
    capture,
    git_init,
    make_project_entry,
    make_snapshot,
    requires_git,
    write_backlog_item,
    write_snapshot,
)

from nextbrief import cli
from nextbrief.frontmatter import parse_frontmatter
from nextbrief.paths import pointer_file


def days_ago(n: int) -> str:
    """An ``updated_date`` relative to the real today.

    Item age is the one thing in this suite that cannot be pinned with
    ``--as-of``: the backlog commands read the wall clock, because a human runs
    them now. Deriving the fixture dates from today keeps the age fixed anyway.
    """
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


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

    def test_a_long_id_is_printed_in_full(self):
        # An id is meant to be pasted into `nextbrief ok <id>`. Truncated to the
        # column width it becomes an id that does not exist.
        write_backlog_item(self.ws, "HUMAN-CONF", title="A wide identifier")
        code, out, err = self._run("ls")
        self.assertEqual(code, 0, err)
        self.assertIn("HUMAN-CONF", out)

    def test_an_operational_failure_is_one_line_naming_the_path(self):
        # A traceback buries the only fact the reader can act on: which path the
        # OS refused.
        target = str(self.ws / "state" / "snapshot.json")

        def boom(ws, args, cat):
            raise OSError(errno.EACCES, "Permission denied", target)

        original = cli._HANDLERS["ls"]
        cli._HANDLERS["ls"] = boom
        self.addCleanup(lambda: cli._HANDLERS.__setitem__("ls", original))

        code, _out, err = self._run("ls")
        self.assertNotEqual(code, 0)
        self.assertEqual(err.strip().splitlines(), ["error: %s: Permission denied" % target])


class Durability(TempCase):
    """`ok` / `done` / `drop` may only report success when the change survives.

    The write-permission gate reverts any backlog field that differs from git
    HEAD, so an edit that was written but not committed is an edit the next run
    destroys -- while the user has already been told it worked.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        self.item = write_backlog_item(self.ws, "NA-0001", title="An open item")
        # No system-wide git identity may leak in; the point of these tests is a
        # machine where nobody has configured one.
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

    def _fields(self):
        return parse_frontmatter(self.item.read_text(encoding="utf-8"))[0]

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _repo_without_identity(self):
        subprocess.run(
            ["git", "-C", str(self.ws), "init", "-q"],
            env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1"),
            capture_output=True,
        )

    @requires_git
    def test_done_writes_nothing_when_git_has_no_identity(self):
        self._repo_without_identity()
        code, out, err = self._run("done", "NA-0001")
        self.assertNotEqual(code, 0)
        self.assertEqual(self._fields()["status"], "open")
        self.assertIsNot(self._fields()["human_confirmed"], True)
        self.assertNotIn("-> done", out)
        self.assertIn('git config --global user.email "you@example.com"', err)
        self.assertIn('git config --global user.name "Your Name"', err)

    @requires_git
    def test_ok_does_not_promise_a_guarantee_it_cannot_establish(self):
        # "automatic decay will never touch it again" is only true once the
        # confirmation is committed.
        self._repo_without_identity()
        code, out, _err = self._run("ok", "NA-0001")
        self.assertNotEqual(code, 0)
        self.assertNotIn("confirmed", out)
        self.assertIsNot(self._fields()["human_confirmed"], True)

    @requires_git
    def test_a_failed_commit_is_a_failed_command(self):
        # Identity is fine here; git refuses for some other reason (a hook, a
        # locked index, a read-only .git). The user must not be told it worked.
        git_init(self.ws)
        real_git = cli._git

        def flaky(root, *args):
            if args and args[0] == "commit":
                return 1, "", "fatal: cannot lock ref"
            return real_git(root, *args)

        cli._git = flaky
        self.addCleanup(lambda: setattr(cli, "_git", real_git))

        code, out, err = self._run("done", "NA-0001")
        self.assertNotEqual(code, 0)
        self.assertNotIn("-> done", out)
        self.assertIn("cannot lock ref", err)

    def test_a_machine_without_git_can_still_close_an_item(self):
        # No git means no write-permission gate either, so nothing will revert the
        # edit: this is a note, not a refusal.
        with mock.patch("shutil.which", return_value=None):
            code, out, err = self._run("done", "NA-0001")
        self.assertEqual(code, 0, err)
        self.assertIn("-> done", out)
        self.assertIn("git is not installed", err)
        self.assertEqual(self._fields()["status"], "done")

    @requires_git
    def test_a_successful_close_leaves_nothing_uncommitted(self):
        # The success line means exactly this: the gate now sees your edit as the
        # baseline rather than as an agent's write.
        git_init(self.ws)
        code, out, err = self._run("done", "NA-0001")
        self.assertEqual(code, 0, err)
        self.assertIn("-> done", out)
        proc = subprocess.run(
            ["git", "-C", str(self.ws), "status", "--porcelain", "--", str(self.item)],
            capture_output=True,
        )
        self.assertEqual(proc.stdout.decode("utf-8", "replace").strip(), "")


class Prune(TempCase):
    """`prune` selects; it is not `ls` with different closing prose.

    Selection follows the `decay` block of config.jsonc, and every selected item
    has to carry the reasons that put it there -- selection a reader cannot audit
    is selection they have to trust.
    """

    def setUp(self):
        super().setUp()
        config = json.loads(json.dumps(BASE_CONFIG))
        config["decay"] = {
            "auto_drop_after_days": 60,
            "auto_drop_requires": {
                "created_by_prefix": "nextbrief-",
                "human_confirmed": False,
                "zero_project_evidence": True,
            },
        }
        self.ws = self.workspace(with_git=False, config=config)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _dormant_project(self, days_since=120):
        write_snapshot(
            self.ws,
            make_snapshot(
                projects=[
                    make_project_entry(
                        "orchard",
                        evidence={
                            "best_kind": None,
                            "best_date": None,
                            "days_since": days_since,
                            "signal": "dormant",
                            "caveat_code": None,
                            "caveat": None,
                        },
                    )
                ]
            ),
        )

    def test_an_aged_agent_proposal_is_selected_and_says_why(self):
        write_backlog_item(
            self.ws, "NB-0007", title="A stale proposal",
            created_by="nextbrief-sense", human_confirmed=False,
            updated_date=days_ago(90),
        )
        self._dormant_project()
        code, out, err = self._run("prune")
        self.assertEqual(code, 0, err)
        self.assertIn("NB-0007", out)
        self.assertIn("untouched for 90 days", out)
        self.assertIn("created by nextbrief-sense", out)
        self.assertIn("orchard has shown no evidence for 120 days", out)

    def test_what_you_wrote_or_confirmed_is_never_selected(self):
        # The documented promise, and the only reason the feature is safe to have.
        write_backlog_item(
            self.ws, "NB-HUMAN", title="Yours", created_by="human",
            human_confirmed=False, updated_date=days_ago(400),
        )
        write_backlog_item(
            self.ws, "NB-CONF", title="Confirmed", created_by="nextbrief-sense",
            human_confirmed=True, updated_date=days_ago(400),
        )
        self._dormant_project()
        code, out, err = self._run("prune")
        self.assertEqual(code, 0, err)
        self.assertNotIn("NB-HUMAN", out)
        self.assertNotIn("NB-CONF", out)
        self.assertIn("Nothing matches", out)

    def test_a_live_project_keeps_its_items(self):
        write_backlog_item(
            self.ws, "NB-0007", title="A stale proposal",
            created_by="nextbrief-sense", human_confirmed=False,
            updated_date=days_ago(90),
        )
        self._dormant_project(days_since=2)
        code, out, err = self._run("prune")
        self.assertEqual(code, 0, err)
        self.assertNotIn("NB-0007", out)

    def test_a_young_item_is_not_selected(self):
        write_backlog_item(
            self.ws, "NB-0008", title="Recent proposal",
            created_by="nextbrief-sense", human_confirmed=False,
            updated_date=days_ago(3),
        )
        self._dormant_project()
        code, out, err = self._run("prune")
        self.assertEqual(code, 0, err)
        self.assertNotIn("NB-0008", out)

    def test_a_missing_snapshot_is_reported_rather_than_guessed_around(self):
        write_backlog_item(
            self.ws, "NB-0007", title="A stale proposal",
            created_by="nextbrief-sense", human_confirmed=False,
            updated_date=days_ago(90),
        )
        code, out, err = self._run("prune")
        self.assertEqual(code, 0, err)
        self.assertIn("state/snapshot.json", out)
        self.assertIn("NB-0007", out)
        self.assertIn("project evidence unknown", out)

    def test_the_configured_window_is_the_one_that_applies(self):
        config = json.loads(json.dumps(BASE_CONFIG))
        config["decay"] = {"auto_drop_after_days": 10, "auto_drop_requires": {
            "created_by_prefix": "nextbrief-", "zero_project_evidence": False}}
        (self.ws / "config.jsonc").write_text(json.dumps(config), encoding="utf-8")
        write_backlog_item(
            self.ws, "NB-0009", title="Two weeks old",
            created_by="nextbrief-sense", human_confirmed=False,
            updated_date=days_ago(14),
        )
        code, out, err = self._run("prune")
        self.assertEqual(code, 0, err)
        self.assertIn("NB-0009", out)
        self.assertIn("rule: 10 or more", out)

    def test_a_long_id_survives_the_id_column(self):
        write_backlog_item(
            self.ws, "HUMAN-CONF", title="A wide identifier",
            created_by="nextbrief-sense", human_confirmed=False,
            updated_date=days_ago(90),
        )
        self._dormant_project()
        code, out, _err = self._run("prune")
        self.assertEqual(code, 0)
        self.assertIn("HUMAN-CONF", out)
        self.assertNotIn("HUMAN-CON ", out)


class DoPicker(TempCase):
    """The one guarantee `do` exists for: it never chooses a directory for you.

    Kept under test because the picker is the only interactive code here, and an
    exec into the wrong tree is not something a user can undo.
    """

    def test_end_of_input_cancels_and_opens_nothing(self):
        ws = self.workspace(with_git=False)
        write_backlog_item(ws, "NA-0001", title="An open item")
        opened = []

        def record(cfg, target, prompt):
            opened.append(target)
            return 0

        with mock.patch.object(cli, "_exec_session", record), \
                mock.patch("builtins.input", side_effect=EOFError):
            code, out, err = capture(cli.main, ["--workspace", str(ws), "do", "NA-0001"])
        self.assertEqual(code, 0, err)
        self.assertEqual(opened, [])
        self.assertIn("cancelled", out.lower())


class SenseHelp(TempCase):
    def test_the_documented_stage_flags_are_in_the_help(self):
        # README documents them and they work; a flag missing from --help reads as
        # a flag that was removed.
        code, out, _err = capture(cli.main, ["sense", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--as-of", out)
        self.assertIn("--timing", out)


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

class Permissions(TempCase):
    """`permissions --merge-into` edits a file holding someone's entire agent
    configuration. The only acceptable behaviour is additive: a tool that
    rewrites more than it was asked to is a tool nobody runs twice."""

    OTHERS = {
        "model": "some-model",
        "hooks": {"PreToolUse": [{"matcher": "Bash",
                                  "hooks": [{"type": "command", "command": "guard.py"}]}]},
        "statusLine": {"type": "command", "command": "line.py"},
        "permissions": {"allow": ["Bash(git status:*)"], "deny": ["Bash(rm -rf:*)"]},
        "autoCompactEnabled": True,
    }

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        self.target = self.tmp / "settings.json"

    def _run(self, *extra):
        return capture(cli.main, ["--workspace", str(self.ws), "permissions"] + list(extra))

    def _written(self):
        return json.loads(self.target.read_text(encoding="utf-8"))

    def test_printing_writes_nothing(self):
        code, out, err = self._run()
        self.assertEqual(code, 0, err)
        self.assertIn("permissions", out)
        self.assertFalse(self.target.exists())

    def test_merging_preserves_every_unrelated_key(self):
        self.target.write_text(json.dumps(self.OTHERS), encoding="utf-8")
        self.assertEqual(self._run("--merge-into", str(self.target))[0], 0)
        got = self._written()
        for key in ("model", "hooks", "statusLine", "autoCompactEnabled"):
            self.assertEqual(got[key], self.OTHERS[key], key)

    def test_merging_preserves_existing_rules(self):
        self.target.write_text(json.dumps(self.OTHERS), encoding="utf-8")
        self.assertEqual(self._run("--merge-into", str(self.target))[0], 0)
        perms = self._written()["permissions"]
        self.assertIn("Bash(git status:*)", perms["allow"])
        self.assertEqual(perms["deny"], ["Bash(rm -rf:*)"])

    def test_merging_is_idempotent(self):
        self.target.write_text(json.dumps(self.OTHERS), encoding="utf-8")
        self._run("--merge-into", str(self.target))
        first = self.target.read_text(encoding="utf-8")
        code, out, _ = self._run("--merge-into", str(self.target))
        self.assertEqual(code, 0)
        self.assertEqual(self.target.read_text(encoding="utf-8"), first)

    def test_a_backup_is_left_behind(self):
        self.target.write_text(json.dumps(self.OTHERS), encoding="utf-8")
        self._run("--merge-into", str(self.target))
        backup = self.target.with_suffix(self.target.suffix + ".nextbrief-backup")
        self.assertTrue(backup.is_file())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), self.OTHERS)

    def test_unreadable_json_is_refused_rather_than_replaced(self):
        self.target.write_text("{ this is not json", encoding="utf-8")
        code, _, err = self._run("--merge-into", str(self.target))
        self.assertEqual(code, 1)
        self.assertIn("refusing", err.lower())
        self.assertEqual(self.target.read_text(encoding="utf-8"), "{ this is not json")

    def test_a_missing_file_is_created(self):
        self.assertEqual(self._run("--merge-into", str(self.target))[0], 0)
        self.assertIn("allow", self._written()["permissions"])
