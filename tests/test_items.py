"""Closing an item without losing what it knew, and parking one so it comes back.

Two things are asserted here that nothing else can assert cheaply:

* the **whole defer round trip** -- parked, invisible, and back in the brief on
  the morning it comes due -- driven entirely by pinned `--as-of` dates, so it is
  a property of the mechanism rather than of the day the suite runs;
* the closing record's **round trip through a file a person may have edited**,
  because the record is prose in Markdown and its parser is the only thing
  standing between "what was actually done" and a summary view that finds
  nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import unittest
from unittest import mock

from helpers import (
    AS_OF,
    AS_OF_DATE,
    TempCase,
    capture,
    git_commit_all,
    git_init,
    requires_git,
    write_backlog_item,
)

from nextbrief import cli, items
from nextbrief.frontmatter import parse_frontmatter


class Liveness(unittest.TestCase):
    """`is_live` is the whole of the defer mechanism: nothing writes an item back
    open, so every question about "is it back yet" is answered from the file."""

    def test_open_statuses_are_live(self):
        for status in ("open", "in_progress", "waiting"):
            self.assertTrue(items.is_live({"status": status}, AS_OF_DATE), status)

    def test_terminal_statuses_are_not(self):
        for status in ("done", "dropped"):
            self.assertFalse(items.is_live({"status": status}, AS_OF_DATE), status)

    def test_a_deferral_hides_the_item_until_its_date(self):
        fm = {"status": "deferred", "deferred_until": "2026-03-20"}
        self.assertFalse(items.is_live(fm, dt.date(2026, 3, 19)))
        self.assertTrue(items.is_live(fm, dt.date(2026, 3, 20)))
        self.assertTrue(items.is_live(fm, dt.date(2026, 4, 1)))

    def test_it_returns_without_anything_having_run_in_between(self):
        """The property that makes a deferral safe to trust. A workspace nobody
        sensed for a fortnight still shows everything that came due during it --
        there is no "the run that was supposed to wake it up" to miss."""
        fm = {"status": "deferred", "deferred_until": "2026-03-20"}
        self.assertTrue(items.is_live(fm, dt.date(2026, 6, 1)))

    def test_an_unreadable_date_fails_towards_the_item_coming_back(self):
        """A typo must not park something forever. That is the exact outcome
        `defer` exists to prevent, so the missing-date case is live, loudly, on
        the next run rather than silent for good."""
        for bad in (None, "", "next tuesday", "2026-13-99", 12345):
            self.assertTrue(items.is_live({"status": "deferred", "deferred_until": bad},
                                          AS_OF_DATE), repr(bad))

    def test_deferred_is_a_status_only_a_human_may_write(self):
        # Parking an item takes it off the page exactly as closing it does, so an
        # agent able to write it could hide work nobody would ask about again.
        self.assertIn("deferred", items.HUMAN_ONLY_STATUSES)
        for status in items.TERMINAL_STATUSES:
            self.assertIn(status, items.HUMAN_ONLY_STATUSES)


class ClosingRecord(unittest.TestCase):
    def test_round_trip(self):
        record = items.Closing(
            "2026-03-16",
            "Migrated all 47 posts, not the 3 the item asked for.\nThe hotlink "
            "protection needed a referer header.",
            [items.FutureWork("Write down the hotlink fix", None),
             items.FutureWork("Do the same for sjtuaa", "NA-0042")])
        text = items.upsert_closing("---\nid: NA-0005\n---\n\nNotes.\n", record)
        got = items.parse_closing(text)
        self.assertEqual(got.closed_on, record.closed_on)
        self.assertEqual(got.summary, record.summary)
        self.assertEqual(list(got.future_work), list(record.future_work))

    def test_it_does_not_disturb_what_was_already_in_the_file(self):
        original = "---\nid: NA-0005\n---\n\n<!-- AC:BEGIN -->\n- [ ] #1 x\n<!-- AC:END -->\n"
        text = items.upsert_closing(original, items.Closing("2026-03-16", "done it", []))
        self.assertIn("<!-- AC:BEGIN -->\n- [ ] #1 x\n<!-- AC:END -->", text)

    def test_closing_twice_replaces_rather_than_stacks(self):
        text = items.upsert_closing("body\n", items.Closing("2026-03-16", "first", []))
        text = items.upsert_closing(text, items.Closing("2026-03-17", "second", []))
        self.assertEqual(text.count(items.CLOSING_BEGIN), 1)
        self.assertEqual(items.parse_closing(text).summary, "second")

    def test_a_sentence_containing_an_arrow_is_not_read_as_a_promotion(self):
        # `-> NA-0042` is only a promotion at the END of the line. Prose about
        # "A -> B" is ordinary future work, and misreading it would attach a
        # follow-up to an item that does not exist.
        record = items.Closing("2026-03-16", "", [
            items.FutureWork("Document the A -> B migration path", None)])
        got = items.parse_closing(items.upsert_closing("x\n", record))
        self.assertIsNone(got.future_work[0].promoted_to)
        self.assertEqual(got.future_work[0].text, "Document the A -> B migration path")

    def test_no_block_is_not_an_error(self):
        self.assertIsNone(items.parse_closing("---\nid: NA-1\n---\n\nnothing here\n"))

    def test_a_mangled_block_yields_empty_fields_rather_than_raising(self):
        text = "%s\nwho knows what a person typed here\n%s\n" % (
            items.CLOSING_BEGIN, items.CLOSING_END)
        got = items.parse_closing(text)
        self.assertEqual(got.summary, "")
        self.assertEqual(list(got.future_work), [])

    def test_recording_a_promotion_touches_only_that_entry(self):
        record = items.Closing("2026-03-16", "s", [
            items.FutureWork("one", None), items.FutureWork("two", None)])
        text = items.record_promotion(items.upsert_closing("x\n", record), 1, "NA-0042")
        got = items.parse_closing(text)
        self.assertIsNone(got.future_work[0].promoted_to)
        self.assertEqual(got.future_work[1].promoted_to, "NA-0042")


class MintingIds(unittest.TestCase):
    def test_the_prefix_and_padding_come_from_the_backlog_not_from_a_constant(self):
        # A workspace's id convention is its own. Hard-coding "NA-" would mint
        # follow-ups into a namespace the rest of the backlog does not use.
        self.assertEqual(items.next_item_id(["P-007", "P-012"], "P-007"), "P-013")
        self.assertEqual(items.next_item_id(["NA-0003", "NA-0017"], "NA-0017"), "NA-0018")

    def test_ids_of_another_prefix_do_not_move_the_counter(self):
        self.assertEqual(items.next_item_id(["NA-0003", "P-9999"], "NA-0003"), "NA-0004")

    def test_a_cjk_title_still_produces_a_filename(self):
        self.assertTrue(items.slug("把防盗链的解法写下来"))


class DeferRoundTrip(TempCase):
    """AC: one item goes the whole way -- parked, hidden, and back in the brief on
    the day it comes due."""

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        self.item = write_backlog_item(self.ws, "NA-0001", title="Still true, later")

    def _fields(self):
        return parse_frontmatter(self.item.read_text(encoding="utf-8"))[0]

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def test_defer_without_until_is_refused(self):
        # The safety property, stated as a test: a deferral that never returns is
        # a drop nobody recorded.
        code, _out, err = self._run("defer", "NA-0001")
        self.assertEqual(code, 2)
        self.assertIn("--until", err)
        self.assertEqual(self._fields()["status"], "open")

    def test_a_date_is_recorded_and_the_item_leaves_the_list(self):
        due = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        code, out, err = self._run("defer", "NA-0001", "--until", due,
                                   "--reason", "downstream is not ready")
        self.assertEqual(code, 0, err)
        self.assertIn(due, out)
        fm = self._fields()
        self.assertEqual(fm["status"], "deferred")
        self.assertEqual(fm["deferred_until"], due)
        self.assertEqual(fm["deferred_because"], "downstream is not ready")

        code, out, _ = self._run("ls")
        self.assertNotIn("Still true, later", out)
        self.assertIn("1 item(s) deferred", out)

    def test_a_condition_still_gets_a_date(self):
        """"After VirtualTutor ships" is a good reason and a useless trigger.
        Both are kept: the condition is what a person reads, the date is the only
        part anything can act on."""
        code, out, err = self._run("defer", "NA-0001", "--until", "after VirtualTutor ships")
        self.assertEqual(code, 0, err)
        fm = self._fields()
        self.assertEqual(fm["deferred_when"], "after VirtualTutor ships")
        self.assertEqual(
            fm["deferred_until"],
            (dt.date.today() + dt.timedelta(days=30)).isoformat())

    def test_the_review_window_is_configurable(self):
        cfg = json.loads((self.ws / "config.jsonc").read_text(encoding="utf-8")
                         .split("\n", 1)[1])
        cfg["defer"] = {"review_after_days": 7}
        (self.ws / "config.jsonc").write_text(
            "// fixture\n" + json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        self._run("defer", "NA-0001", "--until", "when the API settles")
        self.assertEqual(
            self._fields()["deferred_until"],
            (dt.date.today() + dt.timedelta(days=7)).isoformat())

    def test_it_comes_back_on_its_own_and_the_deferred_view_says_when(self):
        due = (dt.date.today() + dt.timedelta(days=3)).isoformat()
        self._run("defer", "NA-0001", "--until", due)
        code, out, err = self._run("ls", "--deferred")
        self.assertEqual(code, 0, err)
        self.assertIn(due, out)
        self.assertIn("back in 3 day(s)", out)

        # Nothing is run to wake it. The date arrives and the item is live.
        self.assertTrue(items.is_live(self._fields(), dt.date.fromisoformat(due)))

    def test_cancel_brings_it_back_now(self):
        self._run("defer", "NA-0001", "--until", "2099-01-01")
        self.assertEqual(self._run("defer", "NA-0001", "--cancel")[0], 0)
        self.assertEqual(self._fields()["status"], "open")
        self.assertIn("Still true, later", self._run("ls")[1])

    def test_prune_does_not_reap_something_that_is_merely_parked(self):
        """A parked item is scheduled, not forgotten -- and decay must not read
        "nobody touched this" off a date somebody deliberately set."""
        self._run("defer", "NA-0001", "--until", "2099-01-01")
        code, out, err = self._run("prune")
        self.assertEqual(code, 0, err)
        self.assertNotIn("NA-0001", out)

    @requires_git
    def test_it_reappears_in_the_brief_on_the_morning_it_is_due(self):
        """The whole round trip, on pinned dates.

        Rendered twice against the same workspace: once with `--as-of` a day
        before the deferral runs out and once on the day itself. The first brief
        must not mention it; the second must name it as having come back.
        """
        ws = self.ws
        git_init(ws)
        git_commit_all(ws, "workspace")
        due = dt.date(2026, 3, 20)
        self._run("defer", "NA-0001", "--until", due.isoformat())
        git_commit_all(ws, "defer")

        def brief_on(day):
            capture(cli.main, ["--workspace", str(ws), "sense", "--as-of", day.isoformat()])
            code, _out, err = capture(
                cli.main, ["--workspace", str(ws), "render", "--no-notify"])
            self.assertEqual(code, 0, err)
            return (ws / "BRIEF.md").read_text(encoding="utf-8")

        before = brief_on(due - dt.timedelta(days=1))
        self.assertNotIn("NA-0001", before)
        self.assertIn("1 deferred", before)

        after = brief_on(due)
        self.assertIn("Back today", after)
        self.assertIn("NA-0001", after)


class ProposedStatusIsRead(TempCase):
    """The field was written by the prompt and read by nothing.

    That is worse than either half alone: the safe action an agent could take --
    decline to close the item, and say so -- was also the silent one. These tests
    hold the loop closed at both ends: the suggestion reaches the page, and
    answering it in either direction stops it being asked again.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        self.item = write_backlog_item(
            self.ws, "NA-0001", title="Write the getting-started page",
            proposed_status="done")

    def _fields(self):
        return parse_frontmatter(self.item.read_text(encoding="utf-8"))[0]

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _brief(self):
        capture(cli.main, ["--workspace", str(self.ws), "sense", "--as-of", AS_OF])
        code, _out, err = capture(
            cli.main, ["--workspace", str(self.ws), "render", "--no-notify"])
        self.assertEqual(code, 0, err)
        return (self.ws / "BRIEF.md").read_text(encoding="utf-8")

    def test_the_brief_lists_it_with_the_commands_that_answer_it(self):
        brief = self._brief()
        self.assertIn("Waiting for your confirmation", brief)
        self.assertIn("NA-0001", brief)
        self.assertIn("nextbrief done NA-0001", brief)
        self.assertIn("nextbrief ok NA-0001", brief)

    def test_it_reaches_the_html_as_well(self):
        # `nextbrief open` shows the HTML. A question asked only in the Markdown
        # goes unasked for anyone who reads the brief in a browser -- which is
        # the original failure of this field, one layer up.
        self._brief()
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertIn("Waiting for your confirmation", html)
        self.assertIn("nextbrief done NA-0001", html)

    def test_agreeing_clears_it(self):
        self.assertEqual(self._run("done", "NA-0001")[0], 0)
        self.assertIsNone(self._fields()["proposed_status"])

    def test_disagreeing_clears_it_too(self):
        """`ok` says "this is real and worded right" -- which, on an item somebody
        proposed closing and you did not close, is a refusal. Leaving the field
        standing would re-ask the same question every morning, and a question
        that survives its own answer teaches people to skim the section."""
        self.assertEqual(self._run("ok", "NA-0001")[0], 0)
        self.assertIsNone(self._fields()["proposed_status"])
        self.assertEqual(self._fields()["status"], "open")
        self.assertNotIn("Waiting for your confirmation", self._brief())

    def test_an_item_with_no_proposal_gains_no_null_field(self):
        # The common path must not accumulate a `proposed_status: null` line on
        # every item anybody ever confirms.
        write_backlog_item(self.ws, "NA-0002", title="Ordinary")
        self._run("ok", "NA-0002")
        text = (self.ws / "backlog" / "NA-0002.md").read_text(encoding="utf-8")
        self.assertNotIn("proposed_status", text)


class ClosingThroughTheCli(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        self.item = write_backlog_item(self.ws, "NA-0005", title="Run 3 probes")

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _text(self):
        return self.item.read_text(encoding="utf-8")

    def test_done_records_what_actually_happened(self):
        code, out, err = self._run(
            "done", "NA-0005",
            "--summary", "Migrated all 47 posts, not 3.",
            "--future-work", "Write down the hotlink fix",
            "--future-work", "Do the same for sjtuaa")
        self.assertEqual(code, 0, err)
        closing = items.parse_closing(self._text())
        self.assertEqual(closing.summary, "Migrated all 47 posts, not 3.")
        self.assertEqual(len(closing.future_work), 2)
        self.assertIn("followup", out)

    def test_done_with_nothing_to_say_writes_no_block(self):
        """Both fields are skippable, and skipping must leave no trace. An empty
        record in the file would read as "asked, and there was nothing", which is
        a different and stronger claim than "not asked"."""
        self.assertEqual(self._run("done", "NA-0005")[0], 0)
        self.assertIsNone(items.parse_closing(self._text()))

    def test_closed_reads_the_records_back_by_project(self):
        self._run("done", "NA-0005", "--summary", "Migrated all 47 posts, not 3.",
                  "--future-work", "Write down the hotlink fix")
        code, out, err = self._run("closed")
        self.assertEqual(code, 0, err)
        self.assertIn("orchard", out)
        self.assertIn("Migrated all 47 posts", out)
        self.assertIn("Write down the hotlink fix", out)

    def test_closed_counts_the_items_that_left_no_record(self):
        # The honest measure of whether the habit is sticking, and the reason the
        # view counts rather than only lists.
        self._run("done", "NA-0005")
        code, out, _ = self._run("closed")
        self.assertIn("1 with no record", out)

    def test_followup_lists_before_it_creates(self):
        self._run("done", "NA-0005", "--future-work", "Write down the hotlink fix")
        code, out, err = self._run("followup", "NA-0005")
        self.assertEqual(code, 0, err)
        self.assertIn("Write down the hotlink fix", out)
        self.assertEqual(len(list(self.ws.glob("backlog/*.md"))), 1)

    def test_promoting_mints_an_item_that_points_back(self):
        self._run("done", "NA-0005", "--future-work", "Write down the hotlink fix")
        code, out, err = self._run("followup", "NA-0005", "--all")
        self.assertEqual(code, 0, err)
        self.assertIn("NA-0006", out)

        made = next(p for p in self.ws.glob("backlog/NA-0006-*.md"))
        fm, _body = parse_frontmatter(made.read_text(encoding="utf-8"))
        self.assertEqual(fm["discovered_from"], "NA-0005")
        self.assertEqual(fm["project"], "orchard")
        self.assertEqual(fm["title"], "Write down the hotlink fix")
        # Written by a person, in the only sense that matters to decay: they
        # typed the sentence and typed the command.
        self.assertIs(fm["human_confirmed"], True)

        # And the edge is recorded on this side too, so a follow-up nobody picked
        # up stays visibly unpicked.
        self.assertEqual(
            items.parse_closing(self._text()).future_work[0].promoted_to, "NA-0006")

    def test_promoting_twice_does_not_mint_a_duplicate(self):
        self._run("done", "NA-0005", "--future-work", "Write down the hotlink fix")
        self._run("followup", "NA-0005", "--all")
        code, out, err = self._run("followup", "NA-0005", "--all")
        self.assertEqual(code, 0, err)
        self.assertIn("already", out)
        self.assertEqual(len(list(self.ws.glob("backlog/NA-0006-*.md"))), 1)

    def _interactively(self, answers, *args):
        """Run `done` as a person at a terminal would, with `answers` typed in."""
        typed = iter(answers)
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            return self._run(*args)

    def test_the_prompt_asks_two_questions_and_takes_both(self):
        code, _out, err = self._interactively(
            ["Migrated all 47 posts, not 3.", "Write down the hotlink fix", ""],
            "done", "NA-0005")
        self.assertEqual(code, 0, err)
        closing = items.parse_closing(self._text())
        self.assertEqual(closing.summary, "Migrated all 47 posts, not 3.")
        self.assertEqual([f.text for f in closing.future_work],
                         ["Write down the hotlink fix"])

    def test_enter_through_both_questions_records_nothing(self):
        """A prompt you cannot escape is trained into a reflex within a
        fortnight, and an empty record reads as "asked, and there was nothing" --
        a stronger claim than "not asked"."""
        code, _out, err = self._interactively(["", ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        self.assertIsNone(items.parse_closing(self._text()))
        self.assertEqual(
            parse_frontmatter(self._text())[0]["status"], "done")

    def test_ctrl_c_at_the_prompt_still_closes_the_item(self):
        # The person asked for `done`. Declining to answer the questions is not
        # declining the command, and treating it as a failure would leave them
        # unsure whether the close happened.
        def interrupt(_prompt=""):
            raise KeyboardInterrupt

        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", interrupt):
            code, _out, err = self._run("done", "NA-0005")
        self.assertEqual(code, 0, err)
        self.assertEqual(parse_frontmatter(self._text())[0]["status"], "done")

    def test_flags_skip_the_prompt_entirely(self):
        def never(_prompt=""):
            raise AssertionError("asked a question it was already given the answer to")

        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", never):
            code, _out, err = self._run("done", "NA-0005", "--summary", "did it")
        self.assertEqual(code, 0, err)
        self.assertEqual(items.parse_closing(self._text()).summary, "did it")

    def test_an_out_of_range_selection_is_refused(self):
        self._run("done", "NA-0005", "--future-work", "one")
        code, _out, err = self._run("followup", "NA-0005", "--promote", "4")
        self.assertEqual(code, 2)
        self.assertIn("no #4", err)


if __name__ == "__main__":
    unittest.main()
