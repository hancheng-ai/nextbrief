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
from nextbrief.paths import Workspace


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

    def test_ctrl_c_at_the_prompt_closes_nothing(self):
        """Ctrl-C stops the command. It is not a way to skip the questions.

        This reverses a deliberate earlier decision, whose reasoning was that
        "declining to answer the questions is not declining the command" and that
        failing would leave the reader unsure whether the close happened. The
        first half does not survive contact: `done` writes
        `human_confirmed: true` and commits, so treating an interrupt as consent
        records the reader confirming something they were trying to back out of.
        The second half is a real concern and is answered by SAYING nothing
        happened -- see the message asserted below -- rather than by closing the
        item anyway. Enter already skips, and the prompt says so.
        """
        def interrupt(_prompt=""):
            raise KeyboardInterrupt

        before = self._text()
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", interrupt):
            code, _out, err = self._run("done", "NA-0005")
        self.assertNotEqual(code, 0, "an interrupted command reported success")
        self.assertNotEqual(parse_frontmatter(self._text())[0].get("status"), "done")
        self.assertEqual(self._text(), before,
                         "an interrupted `done` still modified the item file")
        self.assertIn("not closed", err)

    def test_eof_still_skips_the_questions_and_closes(self):
        """The other half, and the reason the two cannot share a branch. EOF is a
        pipe running dry or a non-tty run -- nobody is there to answer, which is
        not the same as somebody stopping the command."""
        def eof(_prompt=""):
            raise EOFError

        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", eof):
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


def _acceptance(*criteria):
    """A body whose acceptance criteria are exactly ``criteria``.

    Each entry is ``(ticked, text)``. ``write_backlog_item`` appends one unticked
    criterion of its own, so anything asserting on a count passes this instead.
    """
    lines = ["<!-- AC:BEGIN -->"]
    lines += ["- [%s] #%d %s" % ("x" if ok else " ", i, text)
              for i, (ok, text) in enumerate(criteria, 1)]
    lines.append("<!-- AC:END -->")
    return "\n".join(lines)


class SayingWhatIsAboutToClose(TempCase):
    """The three terminal verbs name the item before they touch it.

    `done`, `drop` and `defer` each write `human_confirmed: true` and commit, so
    a mistyped id permanently confirms an item nobody has read -- and `NA-0017`
    and `NA-0019` differ by one keystroke. `do`, which merely opens a session,
    already printed a header; the irreversible three printed nothing.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        self.item = write_backlog_item(
            self.ws, "NA-0005", title="Run 3 probes",
            body=_acceptance((True, "one"), (False, "two")))

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def test_done_names_the_item_before_it_asks_anything(self):
        out = self._run("done", "NA-0005", "--summary", "x")[1]
        self.assertIn("NA-0005 · Run 3 probes", out)
        self.assertIn("Orchard", out)          # the registry's name, not the id
        self.assertIn("1/2", out)              # the number that stops you

    def test_the_header_comes_before_the_first_question(self):
        typed = iter(["", ""])
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            out = self._run("done", "NA-0005")[1]
        self.assertLess(out.index("Run 3 probes"), out.index("What actually happened"))

    def test_drop_names_it_too(self):
        out = self._run("drop", "NA-0005")[1]
        self.assertIn("NA-0005 · Run 3 probes", out)

    def test_defer_names_it_too(self):
        # The quietest of the three: a wrongly deferred item leaves the brief on
        # the spot and says nothing again until its date.
        out = self._run("defer", "NA-0005", "--until", "2026-04-01")[1]
        self.assertIn("NA-0005 · Run 3 probes", out)

    def test_an_item_with_no_criteria_says_nothing_about_them(self):
        write_backlog_item(self.ws, "NA-0006", title="Bare", body="No criteria here.")
        out = self._run("drop", "NA-0006")[1]
        self.assertIn("NA-0006 · Bare", out)
        self.assertNotIn("0/0", out)

    def test_a_mistyped_id_still_refuses_and_names_nothing(self):
        code, out, err = self._run("done", "NA-9999")
        self.assertEqual(code, 1)
        self.assertIn("No item NA-9999", err)
        self.assertNotIn("Run 3 probes", out)


class DraftsAreOfferedNeverAssumed(TempCase):
    """A draft is shown above the question and is never what Enter means.

    ★ This class exists for one assertion. ★

    Drafts make the two closing questions cheaper to answer, which is the cure
    for the "answered with Enter within a fortnight" problem the prompt was
    designed around. Pointed one degree differently, the same mechanism is the
    worst thing here: if Enter took the draft, the reflex that already answers
    every form would start producing machine sentences signed by a person. An
    empty field says "nobody knows"; a wrong summary in your own name is a
    fabricated finding, and that is what the evidence gate exists to prevent.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def _item(self, item_id="NA-0005", **fields):
        return write_backlog_item(self.ws, item_id, title="Run 3 probes", **fields)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _closing(self, item_id="NA-0005"):
        return items.parse_closing(
            (self.ws / "backlog" / ("%s.md" % item_id)).read_text(encoding="utf-8"))

    def _interactively(self, answers, *args):
        typed = iter(answers)
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            return self._run(*args)

    # -- the red one --------------------------------------------------------

    def test_enter_skips_even_when_a_draft_is_on_offer(self):
        """Plant "Enter accepts the draft" and this must fail.

        Two halves, and both are needed: the draft really was offered (so the
        test cannot pass by there being nothing to accept), and pressing Enter
        recorded nothing at all.
        """
        self._item(body=_acceptance((True, "one"), (False, "two")))
        code, out, err = self._interactively(["", ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        self.assertIn("draft:", out)                       # it was on offer
        self.assertIn("AC 1/2", out)                       # and this is what it said
        self.assertIsNone(self._closing())                 # and Enter took none of it

    def test_enter_leaves_no_summary_even_when_follow_ups_were_typed(self):
        # The sharper form: a record does get written, so "no block at all" is
        # not what is doing the work -- the summary field itself stays empty and
        # says so.
        self._item(body=_acceptance((True, "one"), (False, "two")))
        code, _out, err = self._interactively(
            ["", "something I noticed", ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        closing = self._closing()
        self.assertEqual(closing.summary, "")
        self.assertEqual(closing.summary_source, "none")

    # -- accepting on purpose ----------------------------------------------

    def test_the_accept_key_takes_the_draft_verbatim(self):
        self._item(body=_acceptance((True, "one"), (False, "two")))
        code, out, err = self._interactively([cli.ACCEPT_DRAFT, ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        closing = self._closing()
        self.assertIn("AC 1/2", closing.summary)
        self.assertEqual(closing.summary_source, "accepted_draft")
        # And the offer said which key does it, rather than leaving it to be guessed.
        self.assertIn(cli.ACCEPT_DRAFT, out)

    def test_the_accept_key_is_not_reachable_by_pressing_enter(self):
        # The property the author chose the key for. Stated as an assertion so
        # that changing the key to "" -- or to anything a stray newline produces
        # -- is a failing test rather than a silent regression.
        self.assertTrue(cli.ACCEPT_DRAFT)
        self.assertNotIn(cli.ACCEPT_DRAFT, ("", "\n", "\r", " "))

    def test_the_accept_key_with_nothing_on_offer_records_nothing(self):
        """Someone who learned the key and pressed it on an item that had no
        draft must not end up with a summary reading `=`."""
        self._item(body="no criteria, no history")
        code, out, err = self._interactively([cli.ACCEPT_DRAFT, ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        self.assertNotIn("draft:", out)
        self.assertIsNone(self._closing())

    def test_typing_your_own_words_is_recorded_as_yours(self):
        self._item(body=_acceptance((True, "one"), (False, "two")))
        self._interactively(["Migrated all 47 posts, not 3.", ""], "done", "NA-0005")
        closing = self._closing()
        self.assertEqual(closing.summary, "Migrated all 47 posts, not 3.")
        self.assertEqual(closing.summary_source, "human")

    def test_the_flag_is_recorded_as_yours_as_well(self):
        self._item()
        self._run("done", "NA-0005", "--summary", "did it")
        self.assertEqual(self._closing().summary_source, "human")

    def test_a_scripted_run_never_pays_for_a_draft_it_cannot_show(self):
        """Deriving one reads a git log per project directory. A `done` in a
        pipeline is answered by its flags before any question is asked, so it
        must not shell out for an offer nobody is there to take."""
        self._item()
        with mock.patch("nextbrief.cli._closing_drafts",
                        side_effect=AssertionError("derived a draft for a script")):
            code, _out, err = self._run("done", "NA-0005", "--summary", "did it")
        self.assertEqual(code, 0, err)

    def test_a_record_written_before_the_field_existed_gains_no_provenance(self):
        """Reading an old closing record must not invent one. `""` is a fourth
        value -- "nobody recorded this" -- and it is different from `none`."""
        text = items.upsert_closing("body\n", items.Closing("2026-03-16", "old", []))
        self.assertNotIn("summary_source", text)
        self.assertEqual(items.parse_closing(text).summary_source, "")
        self.assertEqual(items.parse_closing(text).summary, "old")


class DraftsAreDerivedNotInvented(TempCase):
    """What a draft may say, checked against the two items that motivated it.

    NA-0017 was closed at 0/6 and NA-0024 at 5/5, one week apart, and between
    them they cover both ends. The regression is not "a draft appears" -- it is
    that neither draft claims anything the workspace cannot show.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def _drafts(self, item_id, **fields):
        path = write_backlog_item(self.ws, item_id, **fields)
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        ws = Workspace(root=self.ws, out=self.ws, source="test")
        return cli._closing_drafts(ws, fm, body, None)

    def test_nothing_ticked_offers_no_follow_ups_at_all(self):
        """The NA-0017 shape: six criteria, none ticked, all six in fact shipped.

        Drafting the unticked ones here would have minted six backlog items for
        finished work -- the engine cannot tell "not done" from "not ticked",
        and at 0/n the tick habit is what failed, not the work.
        """
        summary, future = self._drafts(
            "NA-0017", body=_acceptance(*[(False, "criterion %d" % i) for i in range(1, 7)]))
        self.assertEqual(future, [])
        self.assertIn("AC 0/6", summary)

    def test_everything_ticked_offers_no_follow_ups_either(self):
        """The NA-0024 shape: five of five, and genuinely nothing left over."""
        summary, future = self._drafts(
            "NA-0024", body=_acceptance(*[(True, "criterion %d" % i) for i in range(1, 6)]))
        self.assertEqual(future, [])
        self.assertIn("AC 5/5", summary)

    def test_a_half_finished_item_offers_what_is_left(self):
        """The only shape where unticked criteria are evidence of anything: some
        were ticked, so the others are outstanding rather than merely untouched.

        What it offers is the criteria's own text -- prose a human wrote, and
        which only a human may edit. Accepting it hands your own sentence back
        to you, so nothing here launders a machine's words into a person's."""
        _summary, future = self._drafts(
            "NA-0010", body=_acceptance((True, "sjtuaa"), (False, "robots"),
                                        (False, "aigc-lecture")))
        self.assertEqual(future, ["#2 robots", "#3 aigc-lecture"])

    def test_the_summary_draft_never_claims_authorship_of_commits(self):
        """Not one commit in a real workspace names an item id, so git cannot
        tell this item's work from that day's work. The draft therefore states a
        scope -- the project, and what it saw since the item was opened -- and
        the count is attached to the project, never to the item."""
        summary, _future = self._drafts("NA-0005", body=_acceptance((True, "one")))
        self.assertTrue(summary.startswith("Orchard"))
        self.assertNotIn("NA-0005", summary)

    @requires_git
    def test_several_commits_are_counted_and_none_of_them_quoted(self):
        """Picking a subject by recency names whatever was committed last.

        Both items this was regression-tested against were closed on a day their
        project had been busy with other work, and in both the most recent
        commit belonged to something else -- a true sentence about the wrong
        thing, offered where a summary goes.
        """
        project = self.ws / "projects" / "orchard"
        git_init(project)
        for n in range(3):
            (project / ("f%d.txt" % n)).write_text("x", encoding="utf-8")
            git_commit_all(project, "orchard: unrelated change %d" % n,
                           when="2026-03-12T09:00:00+00:00")
        summary, _future = self._drafts("NA-0005", created_date="2026-03-01")
        self.assertIn("3 commits since 2026-03-01", summary)
        self.assertNotIn("unrelated change", summary)

    @requires_git
    def test_a_single_commit_is_quoted_because_there_is_nothing_to_choose(self):
        project = self.ws / "projects" / "orchard"
        git_init(project)
        git_commit_all(project, "orchard: the only thing that happened",
                       when="2026-03-12T09:00:00+00:00")
        summary, _future = self._drafts("NA-0005", created_date="2026-03-01")
        self.assertIn("the only thing that happened", summary)

    @requires_git
    def test_commits_made_earlier_the_same_day_are_not_dropped(self):
        """`git log --since=2026-03-16` is read at the *current time of day*.

        A bare date therefore hides everything committed earlier today -- which
        is every commit that matters when an item is opened and closed on the
        same day, the normal case for this command. The fixture commit is pinned
        one second after midnight so that a bare `--since` would find nothing.
        """
        today = dt.date.today().isoformat()
        project = self.ws / "projects" / "orchard"
        git_init(project)
        git_commit_all(project, "orchard: work done at dawn",
                       when="%sT00:00:01+00:00" % today)
        summary, _future = self._drafts("NA-0005", created_date=today)
        self.assertIn("at dawn", summary)


class FollowUpListing(TempCase):
    """The list, and the promotion that follows it.

    The old list printed `  .` in a column that aligned to `-> NA-0026`. On a
    freshly closed item nothing has been promoted, so every row carried a lone
    `.` standing in for a shape the reader had never seen -- and `.` already
    means "unconfirmed" in `ls`, where a footer explains it.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        write_backlog_item(self.ws, "NA-0005", title="Run 3 probes")

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _close_with(self, *future):
        args = ["done", "NA-0005"]
        for text in future:
            args += ["--future-work", text]
        self._run(*args)

    def test_nothing_promoted_yet_prints_no_column(self):
        self._close_with("Write down the hotlink fix", "Do the same for sjtuaa")
        out = self._run("followup", "NA-0005")[1]
        rows = [ln for ln in out.splitlines() if ln.strip().startswith(("1)", "2)"))]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertNotIn(".", row.split(")", 1)[0] + row.split(")", 1)[1][:4])
        self.assertIn("1) Write down the hotlink fix", out)

    def test_once_something_is_promoted_the_column_says_so_in_words(self):
        self._close_with("Write down the hotlink fix", "Do the same for sjtuaa")
        self._run("followup", "NA-0005", "--promote", "1")
        out = self._run("followup", "NA-0005")[1]
        self.assertIn("already NA-0006", out)
        self.assertIn("not promoted", out)

    def test_the_column_is_padded_by_display_width(self):
        """`_pad`, not `%-12s`. It was written for `ls` because a CJK glyph is
        one character and two terminal cells, and it sits directly below this
        function -- the localised marks here need it exactly as much."""
        self._close_with("first", "second")
        self._run("followup", "NA-0005", "--promote", "1")
        out = self._run("followup", "NA-0005")[1]
        rows = [ln for ln in out.splitlines() if ln.strip().startswith(("1)", "2)"))]
        self.assertEqual(len(rows), 2)
        self.assertEqual(*[cli._width(r[:r.index(t)]) for r, t
                           in zip(rows, ("first", "second"))])

    def test_promote_says_what_it_will_create_before_it_creates_it(self):
        self._close_with("Write down the hotlink fix")
        out = self._run("followup", "NA-0005", "--all")[1]
        self.assertIn("About to create", out)
        self.assertLess(out.index("About to create"), out.index("discovered_from: NA-0005."))

    def test_it_says_so_even_when_the_write_then_fails(self):
        """The proof that the announcement precedes the write rather than merely
        being printed above it. `--promote` mints files and produces two commits
        per item; describing that after the fact is the same shape as `done`
        closing an item it never named."""
        self._close_with("Write down the hotlink fix")
        with mock.patch("nextbrief.cli.write_text", side_effect=OSError("no")):
            code, out, _err = self._run("followup", "NA-0005", "--all")
        self.assertEqual(code, 1)
        self.assertIn("About to create", out)
        self.assertIn("NA-0006", out)
        self.assertEqual(len(list(self.ws.glob("backlog/NA-0006-*.md"))), 0)

    def test_an_all_run_with_nothing_left_to_do_announces_nothing(self):
        self._close_with("Write down the hotlink fix")
        self._run("followup", "NA-0005", "--all")
        code, out, _err = self._run("followup", "NA-0005", "--all")
        self.assertEqual(code, 0)
        self.assertIn("already", out)
        self.assertNotIn("About to create", out)


if __name__ == "__main__":
    unittest.main()
