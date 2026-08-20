"""The command line.

This replaced a zsh script, and the tests below are mostly about the properties
that rewrite was for: it must work with no arguments, refuse a typo instead of
ignoring it, forward stage flags verbatim, and be safe to run twice.
"""

from __future__ import annotations

import datetime as dt
import errno
import io
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
    git_commit_all,
    git_init,
    make_project_entry,
    make_snapshot,
    requires_git,
    tree_state,
    write_backlog_item,
    write_brief_json,
    write_snapshot,
)

from nextbrief import cli, sense
from nextbrief.frontmatter import parse_frontmatter
from nextbrief.jsonc import load_jsonc
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

    def test_a_fresh_snapshot_with_no_brief_yet_is_out_of_date(self):
        """`check` covers both deterministic stages, not just the first.

        It used to run `sense --check` and nothing else, so a workspace whose
        snapshot was current reported current -- however old BRIEF.md was, and
        even when there was no BRIEF.md at all. A scheduler running
        `check || run` therefore never re-ran, which is the single outcome the
        exit code exists to prevent.
        """
        code, _, err = capture(cli.main, ["--workspace", str(self.ws), "sense"])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.ws / "state" / "snapshot.json").is_file())
        self.assertFalse((self.ws / "BRIEF.md").exists())
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "check"])[0], 3)

    def test_check_agrees_with_a_brief_it_just_wrote(self):
        code, _, err = capture(cli.main, ["--workspace", str(self.ws), "v0", "--no-notify"])
        self.assertEqual(code, 0, err)
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "check"])[0], 0)

    def test_a_stale_brief_is_reported_even_when_the_snapshot_is_current(self):
        # The gap that made the contract incomplete, stated as a test: the
        # snapshot is untouched and only the artifact has drifted.
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "v0", "--no-notify"])[0], 0)
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "check"])[0], 0)
        (self.ws / "BRIEF.md").write_text("something else entirely\n", encoding="utf-8")
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "check"])[0], 3)

    def test_a_stale_brief_html_is_reported_too(self):
        """BRIEF.html is what `nextbrief open` shows, and its write is fail-open.

        A markdown-only check calls a workspace current however wrong the HTML
        is, and `cmd_open` re-renders only when the file is absent -- never when
        it is merely stale. This half of the check had no test, so dropping the
        HTML comparison left all 623 green.
        """
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "v0", "--no-notify"])[0], 0)
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "check"])[0], 0)
        (self.ws / "BRIEF.html").write_text("<h1>arbitrarily stale</h1>", encoding="utf-8")
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "check"])[0], 3,
                         "a stale BRIEF.html was reported as current")

    def test_check_writes_nothing(self):
        """A check that mutates what it is checking is not a check.

        Compared over the whole tree. Checking `runs.jsonl` and `BRIEF.md` alone
        — which is what this did — missed `log/deferred.jsonl`, and would equally
        have missed the write gate reverting a backlog file on disk. A mutation
        audit reverted both halves of that fix and every one of the 623 tests
        stayed green.
        """
        # Over the cap on purpose. The write this missed is `log/deferred.jsonl`,
        # which only gets appended to when a section overflows -- so a fixture
        # that never overflows cannot see the defect however much of the tree it
        # compares. A mutation audit re-enabled the write and this test stayed
        # green until the fixture below was added.
        caps = load_jsonc(str(self.ws / "config.jsonc"))
        over = caps.get("caps", {}).get("max_next_actions", 3) + 2
        write_brief_json(self.ws, {"next_actions": [
            {"title": "Item %d" % i, "project": "orchard",
             "evidence": [{"kind": "doc_declared", "source": "orchard/PROJECT_STATUS.md"}]}
            for i in range(over)]})
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "v0", "--no-notify"])[0], 0)
        self.assertTrue((self.ws / "log" / "deferred.jsonl").is_file(),
                        "the fixture did not overflow a cap, so nothing was deferred")
        before = tree_state(self.ws)
        self.assertEqual(capture(cli.main, ["--workspace", str(self.ws), "check"])[0], 0)
        self.assertEqual(tree_state(self.ws), before, "check modified the workspace")


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


class DuplicateIds(TempCase):
    """Two files claiming one id, which is not hypothetical.

    Two sessions nine hours apart each took "the highest id, plus one" off the
    same directory, and both were right about what they had seen. The result was
    two files with `id: NA-0043`, one of them a P0. `ls` printed both rows, `show`
    silently picked one, `check` said nothing at all -- so `done NA-0043` would
    have closed whichever file the directory listing reached first, with no
    output distinguishing that from having closed the right one.

    That is the false-completion failure the design contract's rule 4 is about,
    arriving through the door that rule does not watch: not an agent writing
    `done`, but the tool resolving a person's `done` onto the wrong object.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        # Same id, two files, exactly as it happened. `write_backlog_item` names
        # the file after the id, so the rename is what makes them two files
        # rather than one overwriting the other.
        self.first = self._claim("NA-0043-windows-support-measured.md",
                                 "Windows support, measured")
        self.second = self._claim("NA-0043-video-narration-theme.md",
                                  "The narration should state the theme")

    def _claim(self, filename, title):
        path = write_backlog_item(self.ws, "NA-0043", title=title)
        target = path.with_name(filename)
        path.rename(target)
        return target

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _status(self, path):
        return parse_frontmatter(path.read_text(encoding="utf-8"))[0]["status"]

    def test_the_fixture_really_does_claim_one_id_twice(self):
        # The trigger, asserted. Every other test in this class says "nothing
        # was closed" or "this failed", and a fixture that had quietly become
        # one file would satisfy all of them for the wrong reason.
        ids = [parse_frontmatter(p.read_text(encoding="utf-8"))[0]["id"]
               for p in (self.first, self.second)]
        self.assertEqual(ids, ["NA-0043", "NA-0043"])
        self.assertNotEqual(self.first.name, self.second.name)

    def test_check_fails_and_names_both_files(self):
        code, _out, err = self._run("check")
        self.assertEqual(code, 1,
                         "a duplicated id has to fail, and with 1 rather than 3: "
                         "3 is the code a scheduler answers by re-running the "
                         "pipeline, which cannot fix this")
        self.assertIn("NA-0043", err)
        self.assertIn(self.first.name, err)
        self.assertIn(self.second.name, err)

    def test_check_says_error_rather_than_warning(self):
        # The distinction the item was filed about. A warning is read the way
        # warnings are read -- which is not at all -- and the thing being warned
        # about closes the wrong item.
        _code, _out, err = self._run("check")
        line = next(ln for ln in err.splitlines() if "NA-0043" in ln)
        self.assertTrue(line.startswith("error: "), line)

    def test_done_on_a_duplicated_id_closes_nothing(self):
        code, out, err = self._run("done", "NA-0043")
        self.assertEqual(code, 1,
                         "done resolved a duplicated id onto one of the files")
        self.assertNotIn("-> done", out)
        # Both files, because closing "only one of them" is the exact defect.
        self.assertEqual(self._status(self.first), "open")
        self.assertEqual(self._status(self.second), "open")
        self.assertIn(self.first.name, err)
        self.assertIn(self.second.name, err)

    def test_every_command_that_resolves_an_id_refuses(self):
        # One resolution path, so this is really asking whether each command
        # goes through it. `do` is included because it opens an agent session in
        # a directory chosen from the item, and the wrong item is the wrong
        # directory.
        for args in (("show", "NA-0043"),
                     ("ok", "NA-0043"),
                     ("done", "NA-0043"),
                     ("drop", "NA-0043"),
                     ("defer", "NA-0043", "--until", "2099-01-01"),
                     ("do", "NA-0043"),
                     ("followup", "NA-0043")):
            code, _out, err = self._run(*args)
            self.assertEqual(code, 1, "%s did not refuse" % (args,))
            self.assertIn(self.first.name, err, "%s did not name both files" % (args,))
            self.assertIn(self.second.name, err, "%s did not name both files" % (args,))
        self.assertEqual(self._status(self.first), "open")
        self.assertEqual(self._status(self.second), "open")

    def test_an_id_only_one_file_claims_still_resolves(self):
        # The other half. A refusal that fired on every id would pass every
        # assertion above and break the tool.
        write_backlog_item(self.ws, "NA-0044", title="Only one of me")
        code, out, err = self._run("show", "NA-0044")
        self.assertEqual(code, 0, err)
        self.assertIn("Only one of me", out)

    def test_a_closed_file_still_counts_as_a_claimant(self):
        # `show`, `followup` and `closed` all reach a done item by id, so a
        # collision between an open item and a closed one is exactly as
        # ambiguous -- and much easier to miss.
        parse_frontmatter(self.first.read_text(encoding="utf-8"))
        self.first.write_text(
            self.first.read_text(encoding="utf-8").replace(
                "status: open", "status: done", 1),
            encoding="utf-8")
        code, _out, err = self._run("check")
        self.assertEqual(code, 1,
                         "a collision with a closed file went unreported")
        self.assertIn(self.first.name, err)


class NewItem(TempCase):
    """`new` assigns the id, because a person doing it by eye is what collided.

    The property under test is not "it counts correctly". It is *what it counts
    over*: the working tree, including entries that exist but are not committed
    yet -- which is precisely the entry the next person does not see.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _ids(self):
        return sorted(parse_frontmatter(p.read_text(encoding="utf-8"))[0]["id"]
                      for p in (self.ws / "backlog").glob("*.md"))

    def test_it_takes_the_next_id_and_keeps_the_backlog_s_own_shape(self):
        write_backlog_item(self.ws, "NA-0007", title="Something already here")
        code, out, err = self._run("new", "A thing to do", "--project", "orchard")
        self.assertEqual(code, 0, err)
        self.assertIn("NA-0008", out)
        self.assertEqual(self._ids(), ["NA-0007", "NA-0008"])

    def test_a_first_item_in_an_empty_backlog(self):
        code, out, err = self._run("new", "The very first one", "--project", "orchard")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._ids(), ["NA-0001"])
        self.assertIn("NA-0001", out)

    @requires_git
    def test_it_sees_an_item_that_is_not_committed_yet(self):
        """The criterion this command exists for.

        `git show HEAD:` cannot see a file that was created and not committed,
        and the same night this was filed, three backlog files were in exactly
        that state. An allocator that reads the committed history hands out
        NA-0002 here -- on top of the NA-0002 already sitting on disk.
        """
        git_init(self.ws)
        write_backlog_item(self.ws, "NA-0001", title="Committed")
        git_commit_all(self.ws, "backlog: the committed one")
        write_backlog_item(self.ws, "NA-0002", title="Written, never committed")
        proc = subprocess.run(
            ["git", "-C", str(self.ws), "show", "HEAD:backlog/NA-0002.md"],
            capture_output=True)
        self.assertNotEqual(proc.returncode, 0,
                            "the fixture committed NA-0002, so it cannot show "
                            "that uncommitted entries are counted")

        code, out, err = self._run("new", "The third one", "--project", "orchard")
        self.assertEqual(code, 0, err)
        self.assertIn("NA-0003", out)
        self.assertEqual(self._ids(), ["NA-0001", "NA-0002", "NA-0003"])

    def test_two_in_a_row_do_not_collide(self):
        # The whole point, end to end: the second call sees what the first
        # wrote, and `check` agrees afterwards.
        self.assertEqual(self._run("new", "First", "--project", "orchard")[0], 0)
        self.assertEqual(self._run("new", "Second", "--project", "orchard")[0], 0)
        self.assertEqual(self._ids(), ["NA-0001", "NA-0002"])
        self.assertEqual(len(cli._duplicate_ids(
            cli.resolve_workspace(str(self.ws), None))), 0)

    def test_an_unknown_project_is_refused_rather_than_filed_under_nothing(self):
        # An item filed under a project that does not exist never appears under
        # one in the brief, and nothing says so.
        code, _out, err = self._run("new", "Homeless", "--project", "no-such-project")
        self.assertEqual(code, 2)
        self.assertIn("orchard", err)
        self.assertEqual(self._ids(), [])

    def test_a_title_of_nothing_but_spaces_is_refused(self):
        code, _out, _err = self._run("new", "   ", "--project", "orchard")
        self.assertEqual(code, 2)
        self.assertEqual(self._ids(), [])

    @requires_git
    def test_the_item_it_writes_is_committed(self):
        # Same reasoning as `ok` / `done`: an uncommitted backlog file is what
        # the write-permission gate reverts, and the next `new` would then reuse
        # the id of an item that had been announced.
        git_init(self.ws)
        code, _out, err = self._run("new", "Committed on the way out", "--project", "orchard")
        self.assertEqual(code, 0, err)
        proc = subprocess.run(
            ["git", "-C", str(self.ws), "status", "--porcelain", "--", "backlog"],
            capture_output=True)
        self.assertEqual(proc.stdout.decode("utf-8", "replace").strip(), "")


class SettlingIsSomethingDoDoesAndDoneDoesNot(TempCase):
    """★ The check belongs where the evidence changes, not where the decision is
    made. ★

    Settling acceptance criteria -- running the check, reading the output,
    ticking what holds -- is slow and it belongs to `do`, which is opening a
    session anyway. Three reasons it must not migrate into `done`, and the third
    is a rule rather than a preference:

    * **Timing.** By the time `done` is typed the decision is made. A check that
      answers "0 of 4 hold" arrives *after* the moment it could have changed
      anything, and a check that is routinely overridden teaches people to ignore
      it -- which then costs the checks that were worth reading.
    * **Latency.** `done` is one interactive keystroke away from a commit. A
      check that shells out is either a wait or a timeout, and an unreliable
      check is worse than none.
    * **The engine may not run what it reads.** Acceptance criteria are prose in
      a file. Executing them would be exactly the "content is data, never a
      command" line this engine does not cross -- there is a fixture in the test
      suite instructing its reader to mark every task complete.

    So `done` keeps doing what it already did: it *shows* the tally somebody else
    settled. These are the guards that it did not quietly grow a second job.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        # Deliberately unsettled, and deliberately checkable: a criterion a
        # settlement pass would have to go and look at is the only fixture that
        # can prove `done` did not go and look at it.
        self.item = write_backlog_item(
            self.ws, "NA-0001", title="An open item",
            body="\n".join(["<!-- AC:BEGIN -->",
                             "- [ ] #1 (agent) `ruff check` is clean",
                             "- [ ] #2 (you) the tail is worth a schema",
                             "<!-- AC:END -->"]))
        # Nobody at the keyboard, whatever the terminal running the suite is.
        # `do`'s picker calls `input()` and `done` asks about ticks on a tty, so
        # a suite run from an interactive shell would otherwise stop here and
        # wait -- which reads as a hung test rather than as a missing redirect.
        stdin = mock.patch("sys.stdin", io.StringIO())
        stdin.start()
        self.addCleanup(stdin.stop)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _watch_launch(self):
        """Record every call to the one function that builds a settlement pass."""
        calls = []
        real = cli.build_context

        def watched(*args, **kwargs):
            calls.append(args[1] if len(args) > 1 else None)
            return real(*args, **kwargs)

        cli.build_context = watched
        self.addCleanup(lambda: setattr(cli, "build_context", real))
        return calls

    def test_do_is_where_the_pass_is_assembled(self):
        """★ The half that stops the guard below from passing vacuously. ★

        A test asserting `done` never calls something can be green because the
        seam it patched is not the one anybody calls. This asserts the same patch
        catches the caller that does.
        """
        calls = self._watch_launch()
        code, out, _err = self._run("do", "NA-0001")
        self.assertEqual(code, 0)          # no tty: the picker reads EOF and cancels
        self.assertEqual(len(calls), 1, "`do` no longer builds the opening message")
        self.assertIn("NA-0001", out)

    def test_done_assembles_no_pass_of_its_own(self):
        calls = self._watch_launch()
        code, out, err = self._run("done", "NA-0001", "--summary", "closed it")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [], "`done` grew a settlement pass")
        self.assertIn("-> done", out)

    def test_done_still_shows_the_tally_somebody_else_settled(self):
        # Not a check: a reading of marks already in the file. This is the whole
        # of `done`'s job here and it must keep doing it, or "no new checks"
        # would be satisfied by a `done` that says nothing at all.
        code, out, err = self._run("done", "NA-0001", "--summary", "closed it")
        self.assertEqual(code, 0, err)
        self.assertIn("0/2", out)

    def test_done_settles_nothing_on_its_own(self):
        # Both boxes were open before and both are open after. A pass that ran
        # here would have had one it could tick, which is why the fixture has one.
        self._run("done", "NA-0001", "--summary", "closed it")
        body = self.item.read_text(encoding="utf-8")
        self.assertIn("- [ ] #1 (agent) `ruff check` is clean", body)
        self.assertIn("- [ ] #2 (you) the tail is worth a schema", body)

    def test_done_spawns_nothing_but_git(self):
        """★ What "it did not get slower" is measured as. ★

        Wall-clock is not assertable on a shared machine, so the guard is on the
        thing that would make it slow: every settlement pass has to run something
        to have checked anything. `done` may spawn git -- it commits, and that is
        the durability promise -- and nothing else. A test harness, a probe or a
        second repository's log would all land here.
        """
        seen = []
        real = subprocess.run

        def recorded(cmd, *args, **kwargs):
            seen.append(cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd)
            return real(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", recorded):
            code, out, err = self._run("done", "NA-0001", "--summary", "closed it")
        self.assertEqual(code, 0, err)
        self.assertIn("-> done", out)
        self.assertEqual([c for c in seen if c != "git"], [],
                         "`done` now runs something other than git: %s" % seen)


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


class ProjectsCommand(TempCase):
    """`ls` lists backlog items; nothing listed projects.

    Tolerable while the registry *was* the project list. Once discovery started
    adopting directories on its own the set could change with nobody editing
    anything, and "what is the tool actually watching?" had no cheap answer --
    you had to render a whole brief and read the table inside it.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()

    def run_cmd(self, *extra):
        return capture(cli.main, ["--workspace", str(self.ws), "projects"] + list(extra))

    def test_it_lists_every_project_with_its_freshness(self):
        code, _, err = capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cmd()
        self.assertEqual(code, 0, err)
        self.assertIn("orchard", out)
        self.assertIn("kiln", out)
        self.assertIn("2 project", out)

    def test_a_discovered_project_is_marked_as_such(self):
        # The distinction the whole registry-as-annotation model turns on.
        (self.ws / "projects" / "latecomer").mkdir(parents=True)
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        code, out, err = self.run_cmd()
        self.assertEqual(code, 0, err)
        self.assertIn("latecomer", out)
        self.assertIn("registry", out, "a discovered project is not flagged as undeclared")
        self.assertIn("1 discovered", out)

    def test_it_says_what_to_run_when_there_is_no_snapshot(self):
        code, _, err = self.run_cmd()
        self.assertNotEqual(code, 0)
        self.assertIn("sense", err)

    def test_it_writes_nothing(self):
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        before = tree_state(self.ws)
        self.assertEqual(self.run_cmd()[0], 0)
        self.assertEqual(tree_state(self.ws), before)


class HelpIsNotPrintedTwice(unittest.TestCase):
    def test_the_command_list_appears_once(self):
        """argparse would print its own list of the same twenty subcommands
        under the hand-written one -- the same information twice, in two orders
        and two levels of detail."""
        code, out, _ = capture(cli.main, ["--help"])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("all three stages"), 1)
        # Keyed on text that is actually in the help. The phrase this used to
        # look for was reworded when `check` grew to cover the renderer, and a
        # count of 1 against a string that appears 0 times fails loudly -- which
        # is the right way round for a guard.
        self.assertEqual(out.count("self-check over both stages"), 1)

    def test_the_commands_come_before_the_flags(self):
        # The usage line names the flags first; that is argparse's own layout and
        # is not what this is about. What matters is that the reader meets the
        # command list before the option list, which is why it moved from the
        # epilog into the description.
        code, out, _ = capture(cli.main, ["--help"])
        self.assertEqual(code, 0)
        options = next(h for h in ("\noptions:", "\noptional arguments:") if h in out)
        self.assertLess(out.index("commands:"), out.index(options))

    def test_every_command_in_the_help_text_is_real(self):
        import re

        code, out, _ = capture(cli.main, ["--help"])
        listed = set(re.findall(r"(?m)^  ([a-z0-9]+)\s{2,}", out))
        real = set()
        for action in cli.build_parser()._subparsers._group_actions:
            real |= set(action.choices)
        self.assertTrue(listed)
        self.assertEqual(sorted(listed - real), [])
