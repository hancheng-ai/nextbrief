"""`do` leaves a trace, and `new` takes a number nothing else can also take.

Three failures were reported together and only one of them is a race. Keeping
them apart is the whole design, so the tests are grouped the same way:

* **the race** -- two sessions read the same directory, both concluded NA-0043,
  and both were right. :class:`ExclusiveAllocation`.
* **no claim record** -- nothing anywhere said an item had been started, so the
  same one could be opened twice. :class:`DoRecordsAClaim`, :class:`ASecondDo`.
* **no completion signal** -- a session went idle carrying the work and it took
  two days and a transcript to find out. :class:`AClaimWithNothingBehindIt`.

And one test class for the thing that must stay true after all of it:
:class:`AClaimIsNotALock`. The failure actually observed three times was
abandonment, and a lock's punishment for abandonment is to seal the item shut.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from helpers import (
    TempCase,
    capture,
    git,
    git_commit_all,
    git_init,
    requires_git,
    write_backlog_item,
)

from nextbrief import cli
from nextbrief.frontmatter import parse_frontmatter, rewrite_block
from nextbrief.items import claim_lines, claim_of


def _fm(path):
    return parse_frontmatter(Path(path).read_text(encoding="utf-8"))[0]


class DoBase(TempCase):
    """A workspace, an item, and a session that is recorded rather than exec'd."""

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        self.item = write_backlog_item(self.ws, "NA-0001", title="An open item")
        self.opened = []

    def _do(self, *args, answers=None):
        """Run `do`, capturing where it would have opened a session."""

        def record(cfg, target, prompt):
            self.opened.append(target)
            return 0

        replies = list(answers or [])

        def ask(_prompt=""):
            if not replies:
                raise EOFError
            return replies.pop(0)

        with mock.patch.object(cli, "_exec_session", record), \
                mock.patch("builtins.input", ask):
            return capture(cli.main,
                           ["--workspace", str(self.ws), "do", "NA-0001"] + list(args))

    def _claim(self):
        return claim_of(_fm(self.item) or {})


class DoRecordsAClaim(DoBase):
    """`status: in_progress` had a reader in the engine and no writer anywhere.

    It is in `OPEN_STATUSES` and in the schema's list of legal values, and a
    grep for it across the package found exactly one line -- the one that reads
    it. This is the producer that was missing, and the reason the two commands
    below it can exist at all.
    """

    def test_it_writes_in_progress_and_the_claim(self):
        code, out, err = self._do("--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.opened, [cli.build_context(
            cli.resolve_workspace(str(self.ws), None), self.item).cwd])

        fm = _fm(self.item)
        self.assertEqual(fm["status"], "in_progress")
        claim = self._claim()
        self.assertIsNotNone(claim, "nothing recorded that this item was started")
        self.assertEqual(claim["at"], dt.date.today().isoformat())
        self.assertEqual(claim["where"], self.opened[0])
        self.assertTrue(str(claim["by"]).strip(), "a claim by nobody names nobody")
        self.assertIn("NA-0001", out)

    def test_the_block_is_readable_by_the_parser_that_wrote_it(self):
        # A nested mapping flattened onto one line parses back as a string, and
        # every reader downstream would see a claim with no fields in it.
        self._do("--yes")
        text = self.item.read_text(encoding="utf-8")
        self.assertIn("\nclaim:\n", text)
        self.assertEqual(claim_lines(text)[0], "claim:")
        self.assertTrue(all(ln.startswith("  ") for ln in claim_lines(text)[1:]))

    @requires_git
    def test_the_branch_the_session_opens_on_is_part_of_the_record(self):
        # Without it `check` has no question to ask: a claim naming only a
        # directory cannot be compared against anything that happened.
        target = self.ws / "projects" / "orchard"
        git_init(target)
        git(target, "checkout", "-q", "-b", "fix/duplicate-ids")
        self._do("--yes")
        claim = self._claim()
        self.assertEqual(claim["where"], str(target))
        self.assertEqual(claim["branch"], "fix/duplicate-ids")

    def test_a_directory_that_is_not_a_repository_records_no_branch(self):
        # `null`, not the literal string HEAD and not the directory again --
        # `check` asks git about this value and must not be handed something
        # that merely looks like a branch name.
        self._do("--yes")
        self.assertIsNone(self._claim()["branch"])

    @requires_git
    def test_the_claim_is_committed(self):
        git_init(self.ws)
        git_commit_all(self.ws, "backlog: the item before anyone started on it")
        code, _out, err = self._do("--yes")
        self.assertEqual(code, 0, err)
        proc = subprocess.run(
            ["git", "-C", str(self.ws), "status", "--porcelain", "--", "backlog"],
            capture_output=True)
        self.assertEqual(proc.stdout.decode("utf-8", "replace").strip(), "")

    def test_cancelling_records_nothing(self):
        # The claim is written at the moment a session is definitely being
        # opened. Written any earlier, `do` used to look at an item would leave
        # a claim behind, and every one of those is a false report of work.
        code, out, _err = self._do(answers=["q"])
        self.assertEqual(code, 0)
        self.assertEqual(self.opened, [])
        self.assertIsNone(self._claim())
        self.assertEqual(_fm(self.item)["status"], "open")

    def test_end_of_input_records_nothing_either(self):
        code, _out, _err = self._do()
        self.assertEqual(code, 0)
        self.assertEqual(self.opened, [])
        self.assertIsNone(self._claim())


class ASecondDo(DoBase):
    """Running `do` twice on one item is the failure this makes visible."""

    def setUp(self):
        super().setUp()
        self._do("--yes")
        self.opened.clear()
        self.first = self._claim()

    def test_the_claim_already_there_is_printed_verbatim(self):
        _code, out, _err = self._do(answers=[""])
        # The lines out of the file, not a re-rendering of parsed values: a
        # paraphrase is exactly where a hand-edited oddity would go missing, and
        # the oddity is the thing that tells you this claim is not what you
        # assumed.
        for line in claim_lines(self.item.read_text(encoding="utf-8")):
            self.assertIn(line.strip(), out,
                          "the claim already on the item was not shown")
        self.assertIn(str(self.first["by"]), out)
        self.assertIn(str(self.first["at"]), out)

    def test_it_asks_and_stopping_opens_nothing(self):
        code, out, _err = self._do(answers=["q"])
        self.assertEqual(code, 0)
        self.assertEqual(self.opened, [])
        self.assertIn("cancelled", out.lower())

    def test_carrying_on_is_allowed_and_is_the_default(self):
        code, _out, err = self._do(answers=["", ""])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.opened), 1,
                         "an existing claim refused instead of asking")

    def test_taking_it_over_replaces_the_claim_rather_than_stacking_one(self):
        # Two `claim:` keys in one block and the parser takes the last, so the
        # file would read correctly to a machine and wrongly to a person.
        self._do(answers=["", ""])
        text = self.item.read_text(encoding="utf-8")
        self.assertEqual(text.count("\nclaim:\n"), 1)
        self.assertIsNotNone(self._claim())

    def test_the_notice_comes_before_the_directory_picker(self):
        # After it, the question is asked at the moment its answer has stopped
        # being useful.
        _code, out, _err = self._do(answers=["q"])
        self.assertNotIn("Where should this happen?", out)


class AClaimIsNotALock(DoBase):
    """The one property the whole posture rests on.

    Deadlock needs hold-and-wait over several resources and cannot arise here.
    What a lock would really introduce is the *stale* lock -- and the failure
    that has actually happened three times is abandonment, so a lock's
    contribution would be to seal shut precisely the items somebody needs to
    pick up.
    """

    def test_an_abandoned_claim_still_lets_the_work_go_on(self):
        rewrite_block(self.item, "claim", {
            "by": "a session that went idle",
            "at": "2026-08-09",
            "where": str(self.ws / "projects" / "orchard"),
            "branch": "fix/duplicate-ids",
        })
        cli.rewrite_fields(cli.resolve_workspace(str(self.ws), None),
                           self.item, {"status": "in_progress"})

        code, out, err = self._do(answers=["", ""])
        # Asserted before the exit code, because this is the property and the
        # exit code is a consequence of it. The other way round, a version that
        # refuses outright fails on the exit code first and the failure says
        # nothing about what was actually lost.
        self.assertEqual(len(self.opened), 1,
                         "a claim nobody is behind stopped somebody working")
        self.assertEqual(code, 0, err)
        self.assertIn("2026-08-09", out)
        self.assertEqual(self._claim()["at"], dt.date.today().isoformat(),
                         "the stale claim outlived the person who took it over")

    def test_yes_shows_the_claim_and_does_not_stop(self):
        # `--yes` is the non-interactive path. It must still print what it found
        # -- silence there would make the record useless in the one mode a
        # script runs in -- and it must not block, because a question nobody can
        # answer is a refusal wearing a prompt.
        self._do("--yes")
        self.opened.clear()
        code, out, err = self._do("--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.opened), 1)
        self.assertIn("claim:", out)

    def test_a_claim_that_cannot_be_written_does_not_stop_the_session(self):
        def refuse(*_a, **_k):
            raise OSError(13, "Permission denied")

        with mock.patch.object(cli, "rewrite_fields", refuse):
            code, _out, err = self._do("--yes")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.opened), 1,
                         "failing to write the note became a way of blocking work")
        self.assertIn("warning", err.lower())


@requires_git
class AClaimWithNothingBehindIt(TempCase):
    """`check` answers the question it took two days and a transcript to answer.

    The claim date and the branch are the only two facts needed, and neither
    existed anywhere in the workspace before `do` started writing them down.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        self.project = self.ws / "projects" / "orchard"
        git(self.project, "checkout", "-q", "-b", "fix/duplicate-ids")

    def _claimed(self, at, branch="fix/duplicate-ids", where=None):
        path = write_backlog_item(self.ws, "NA-0045", title="Duplicate ids are silent",
                                  status="in_progress")
        rewrite_block(path, "claim", {
            "by": "the session that had the work in it",
            "at": at,
            "where": str(self.project) if where is None else where,
            "branch": branch,
        })
        return path

    def _warnings(self):
        ws = cli.resolve_workspace(str(self.ws), None)
        return cli._abandoned_claims(ws, None)

    def _commit_on(self, branch, when):
        git(self.project, "checkout", "-q", branch)
        (self.project / "src" / "main.py").write_text("LINE = 1\n", encoding="utf-8")
        git_commit_all(self.project, "orchard: work on the claimed item", when=when)

    def test_a_claim_with_no_commits_on_its_branch_is_reported(self):
        self._claimed(at=str(dt.date.today() - dt.timedelta(days=2)))
        warnings = self._warnings()
        self.assertEqual(len(warnings), 1,
                         "NA-0045 was claimed and its branch is empty, and check "
                         "said %r" % (warnings,))
        self.assertIn("NA-0045", warnings[0])
        self.assertIn("fix/duplicate-ids", warnings[0])

    def test_a_branch_that_was_never_made_is_the_loudest_case_not_a_gap(self):
        self._claimed(at=str(dt.date.today() - dt.timedelta(days=2)),
                      branch="a-branch-nobody-created")
        self.assertEqual(len(self._warnings()), 1)

    def test_a_branch_with_commits_since_the_claim_says_nothing(self):
        # The narrowing that keeps this from becoming an alarm that always
        # rings. Something is happening; this warning has nothing to add to it.
        at = dt.date.today() - dt.timedelta(days=2)
        self._claimed(at=str(at))
        self._commit_on("fix/duplicate-ids",
                        when="%sT09:00:00+00:00" % (at + dt.timedelta(days=1)))
        self.assertEqual(self._warnings(), [])

    def test_a_claim_taken_today_says_nothing(self):
        self._claimed(at=dt.date.today().isoformat())
        self.assertEqual(self._warnings(), [],
                         "a claim taken today was reported as gone quiet")

    def test_a_claim_it_cannot_check_says_nothing(self):
        # No branch recorded, so there is no question to put to git. A warning
        # fired on absent evidence teaches the reader that the warning does not
        # mean anything.
        path = self._claimed(at=str(dt.date.today() - dt.timedelta(days=9)))
        rewrite_block(path, "claim", {
            "by": "somebody", "at": str(dt.date.today() - dt.timedelta(days=9)),
            "where": str(self.project), "branch": None,
        })
        self.assertEqual(self._warnings(), [])

    def test_a_directory_that_has_gone_says_nothing(self):
        self._claimed(at=str(dt.date.today() - dt.timedelta(days=9)),
                      where=str(self.ws / "projects" / "a-worktree-since-removed"))
        self.assertEqual(self._warnings(), [])

    def test_an_open_item_with_no_claim_is_not_a_candidate(self):
        write_backlog_item(self.ws, "NA-0046", title="Nobody has started this")
        self.assertEqual(self._warnings(), [])

    def test_it_is_a_warning_and_not_the_exit_code(self):
        # Exit 3 is a signal to a scheduler, and re-running the pipeline cannot
        # make a forgotten session commit anything.
        self._claimed(at=str(dt.date.today() - dt.timedelta(days=2)))
        ws = cli.resolve_workspace(str(self.ws), None)
        with mock.patch.object(cli, "_run_sense", lambda _a: 0), \
                mock.patch.object(cli, "_run_render", lambda _a: 0):
            code, _out, err = capture(cli.cmd_check, ws, mock.Mock(), None)
        self.assertEqual(code, 0)
        self.assertIn("NA-0045", err)
        self.assertIn("warning", err)


@requires_git
class TheTrunkIsNotEvidence(TempCase):
    """On a shared branch, somebody else answers the question for you.

    Measured before it was changed (NA-0050): replaying pm's 53 items against
    the real history of the repositories they were worked in put 92% of claims
    on the trunk, and 51% of all claims were silenced by commits that never
    touched the item -- 61% among the repositories actually being worked at the
    time. The old criterion's answer was decided by whether the *repository* was
    busy, not by whether the *item* was, which made it quiet exactly where work
    happens and loud exactly where none does.

    So the trunk is out of scope, and these tests hold both halves of that: it
    stops speaking about the trunk, and it does not start speaking anywhere it
    used to be right to stay quiet.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        self.project = self.ws / "projects" / "orchard"

    def _claimed(self, branch, days_ago=2, where=None):
        at = dt.date.today() - dt.timedelta(days=days_ago)
        path = write_backlog_item(self.ws, "NA-0045",
                                  title="Duplicate ids are silent",
                                  status="in_progress")
        rewrite_block(path, "claim", {
            "by": "the session that had the work in it",
            "at": str(at),
            "where": str(self.project) if where is None else where,
            "branch": branch,
        })
        return at

    def _warnings(self):
        ws = cli.resolve_workspace(str(self.ws), None)
        return cli._abandoned_claims(ws, None)

    def _commit_on(self, branch, when, message="orchard: unrelated work"):
        git(self.project, "checkout", "-q", branch)
        note = self.project / "src" / ("%s.py" % when.isoformat().replace("-", "_"))
        note.write_text("LINE = 1\n", encoding="utf-8")
        git_commit_all(self.project, message,
                       when="%sT09:00:00+00:00" % when)

    # -- it stops speaking about the trunk ---------------------------------

    def test_a_claim_on_the_trunk_is_not_judged(self):
        # The branch is empty since the claim, which is the one thing the old
        # criterion looked for -- and on the trunk it means nothing, because
        # the next person to push anything would have silenced it.
        self._claimed(branch="main")
        self.assertEqual(self._warnings(), [],
                         "a claim on the trunk was judged on a question the "
                         "trunk cannot answer")

    def test_the_trunk_is_quiet_whether_or_not_anybody_else_commits(self):
        # The defect itself: the old answer flipped on traffic belonging to
        # other work. Same claim, same emptiness of *its* work, and the only
        # difference is somebody else's commit. Both must read the same now.
        at = self._claimed(branch="main")
        before = self._warnings()
        self._commit_on("main", at + dt.timedelta(days=1),
                        message="orchard: something else entirely")
        self.assertEqual(before, self._warnings())
        self.assertEqual(before, [])

    def test_the_repository_decides_which_branch_is_its_trunk(self):
        # origin/HEAD is the repository saying so. Where it says the trunk is
        # `release`, a claim on `main` is a claim on a branch somebody made on
        # purpose, and is judged like one.
        git(self.project, "branch", "release")
        git(self.project, "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/release")
        self._claimed(branch="main")
        warnings = self._warnings()
        self.assertEqual(len(warnings), 1,
                         "origin/HEAD called `release` the trunk, so `main` is "
                         "not one, and check said %r" % (warnings,))
        self.assertIn("NA-0045", warnings[0])

    def test_a_repository_with_no_recognisable_trunk_says_nothing(self):
        # Neither origin/HEAD nor a branch by either name git itself uses. A
        # guess about which branch is shared is not evidence, so it stays quiet
        # rather than warning on the strength of one.
        git(self.project, "branch", "-m", "main", "steading")
        self._claimed(branch="fix/duplicate-ids")
        self.assertEqual(self._warnings(), [])

    # -- and it does not start speaking anywhere it should not -------------

    def test_the_same_claim_one_branch_over_is_still_reported(self):
        # NA-0046 measured exactly this pair: one claim, only claim.branch
        # differs. The dedicated branch is where the question still has an
        # answer, and NA-0045 -- the abandonment actually on record -- is this
        # case.
        git(self.project, "checkout", "-q", "-b", "fix/duplicate-ids")
        git(self.project, "checkout", "-q", "main")
        self._claimed(branch="fix/duplicate-ids")
        warnings = self._warnings()
        self.assertEqual(len(warnings), 1,
                         "the branch made for this item is empty and check "
                         "said %r" % (warnings,))
        self.assertIn("fix/duplicate-ids", warnings[0])

    def test_a_busy_dedicated_branch_says_nothing(self):
        # The half that matters more than the catch: somebody is working, on a
        # branch of their own, and has been all week. An alarm that rings
        # through that is one nobody reads by Friday.
        at = self._claimed(branch="feat/tenancy")
        git(self.project, "checkout", "-q", "-b", "feat/tenancy")
        for day in range(1, 4):
            self._commit_on("feat/tenancy", at + dt.timedelta(days=day),
                            message="orchard: still going")
        self.assertEqual(self._warnings(), [],
                         "an item being actively worked on its own branch was "
                         "reported as abandoned")

    def test_a_claim_taken_today_on_a_dedicated_branch_still_says_nothing(self):
        # The trunk rule is an extra narrowing, not a replacement for the ones
        # already there.
        git(self.project, "checkout", "-q", "-b", "feat/tenancy")
        git(self.project, "checkout", "-q", "main")
        self._claimed(branch="feat/tenancy", days_ago=0)
        self.assertEqual(self._warnings(), [])


class ExclusiveAllocation(TempCase):
    """Two writers that read the same directory must not leave with one number.

    Both sessions were right about what they had seen: the highest id on disk,
    plus one. Neither had written anything down yet, so there was nothing for
    the other to have seen. No amount of care in *reading* closes that.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _ids(self):
        return sorted(parse_frontmatter(p.read_text(encoding="utf-8"))[0]["id"]
                      for p in (self.ws / "backlog").glob("*.md"))

    def _second_writer(self, title):
        """`new`, run as a writer whose scan happened before the first one wrote.

        The interleaving that actually occurred, made deterministic: the
        directory listing this call sees is the one from *before* the other
        writer created its file, so it computes the same id -- and everything
        after that is the code under test.
        """
        stale = []
        with mock.patch.object(cli, "_all_entries", lambda _ws: stale):
            return self._run("new", title, "--project", "orchard")

    def test_the_loser_of_the_race_retries_instead_of_colliding(self):
        self.assertEqual(self._run("new", "First", "--project", "orchard")[0], 0)
        code, out, err = self._second_writer("Second")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._ids(), ["NA-0001", "NA-0002"],
                         "both writers left with the same number")
        self.assertIn("NA-0002", out)

    def test_two_different_titles_cannot_share_one_id(self):
        """The shape the collision actually had.

        Exclusive creation of the *item file* would not have caught this: the
        titles differ, so the filenames differ, and both creations succeed. The
        number has to be what is taken, not the name it ends up in.
        """
        self.assertEqual(
            self._run("new", "Video narration should state the theme",
                      "--project", "orchard")[0], 0)
        code, _out, err = self._second_writer(
            "Duplicate backlog ids are silent")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._ids(), ["NA-0001", "NA-0002"],
                         "both writers left with the same number")
        self.assertEqual(len(cli._duplicate_ids(
            cli.resolve_workspace(str(self.ws), None))), 0)

    def test_the_number_is_taken_before_the_file_is_written(self):
        # What makes the retry above possible: the marker exists for every id
        # handed out, whether or not anything came of it.
        self._run("new", "First", "--project", "orchard")
        ws = cli.resolve_workspace(str(self.ws), None)
        self.assertTrue((ws.ids / "NA-0001").exists())

    def test_an_id_burned_by_a_run_that_died_is_not_handed_out_again(self):
        # A gap in the numbering costs nothing -- ids are names, not a count.
        # Reusing one puts a second file under a name already announced.
        ws = cli.resolve_workspace(str(self.ws), None)
        cli.claim_exclusively(ws, ws.ids / "NA-0001")
        code, out, err = self._run("new", "The one after the crash",
                                   "--project", "orchard")
        self.assertEqual(code, 0, err)
        self.assertIn("NA-0002", out)

    def test_a_workspace_with_no_ledger_still_writes_the_item_down(self):
        # The ledger narrows a window; it is not the guarantee. Losing it must
        # degrade to the directory scan, never to a refusal to record a task.
        def refuse(*_a, **_k):
            raise OSError(30, "Read-only file system")

        with mock.patch.object(cli, "claim_exclusively", refuse):
            code, out, err = self._run("new", "Written anyway", "--project", "orchard")
        self.assertEqual(code, 0, err)
        self.assertIn("NA-0001", out)


class NestedBlockWriter(TempCase):
    """The frontmatter writer knew how to write scalars and nothing else."""

    def _file(self, text):
        path = self.tmp / "item.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_block_is_written_where_the_parser_can_read_it_back(self):
        path = self._file("---\nid: NA-0001\nstatus: open\n---\n\nbody\n")
        self.assertTrue(rewrite_block(path, "claim", {"by": "someone", "at": "2026-08-10"}))
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fm["claim"], {"by": "someone", "at": "2026-08-10"})
        self.assertEqual(body, "body\n")

    def test_rewriting_replaces_the_block_in_place(self):
        path = self._file("---\nid: NA-0001\nclaim:\n  by: first\nupdated_date: 2026-08-10\n---\n\nbody\n")
        rewrite_block(path, "claim", {"by": "second"})
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("claim:"), 1)
        self.assertNotIn("first", text)
        # The key that followed it is still after it, and still top-level.
        self.assertLess(text.index("claim:"), text.index("updated_date:"))
        self.assertEqual(parse_frontmatter(text)[0]["updated_date"], "2026-08-10")

    def test_removing_takes_the_indented_lines_with_it(self):
        path = self._file("---\nid: NA-0001\nclaim:\n  by: first\n  at: 2026-08-10\nstatus: open\n---\n\nbody\n")
        rewrite_block(path, "claim", None)
        fm, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertNotIn("claim", fm)
        self.assertEqual(fm["status"], "open")
        self.assertNotIn("at: 2026-08-10", path.read_text(encoding="utf-8"))

    def test_a_neighbouring_block_is_left_alone(self):
        path = self._file("---\nid: NA-0001\nautomation:\n  tier: skill\nclaim:\n  by: first\n---\n\nbody\n")
        rewrite_block(path, "claim", {"by": "second"})
        fm, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fm["automation"], {"tier": "skill"})
        self.assertEqual(fm["claim"], {"by": "second"})

    def test_a_file_with_no_frontmatter_is_left_alone(self):
        path = self._file("no frontmatter here\n")
        self.assertFalse(rewrite_block(path, "claim", {"by": "someone"}))
        self.assertEqual(path.read_text(encoding="utf-8"), "no frontmatter here\n")


if __name__ == "__main__":
    unittest.main()
