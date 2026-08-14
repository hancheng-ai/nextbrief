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
import os
import sys
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

from nextbrief import cli, html, items
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
             items.FutureWork("Do the same for larkspur", "NA-0042")])
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

    def test_the_shape_of_the_next_id_is_read_off_the_backlog(self):
        # `followup` has an item in hand to copy the convention from; `new` does
        # not, and inventing "NA-" for a backlog numbered P-001 mints into a
        # namespace nothing else uses -- which reads as a working command.
        existing = ["P-007", "P-012"]
        self.assertEqual(
            items.next_item_id(existing, items.id_shape(existing)), "P-013",
            "the next id left the prefix the backlog actually uses")

    def test_the_widest_padding_wins_when_a_backlog_has_grown(self):
        # `NA-001` and `NA-0044` in one directory is what a backlog that outgrew
        # its first numbering looks like. Narrowing is the direction that
        # collides: at 3 digits the next one after NA-0044 is NA-045.
        self.assertEqual(items.id_shape(["NA-001", "NA-0044"]), "NA-0001")

    def test_an_empty_backlog_still_has_to_call_the_first_item_something(self):
        self.assertEqual(items.id_shape([]), "NA-0001")
        self.assertEqual(items.next_item_id([], items.id_shape([])), "NA-0001")


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
        """"After Fernwood ships" is a good reason and a useless trigger.
        Both are kept: the condition is what a person reads, the date is the only
        part anything can act on."""
        code, out, err = self._run("defer", "NA-0001", "--until", "after Fernwood ships")
        self.assertEqual(code, 0, err)
        fm = self._fields()
        self.assertEqual(fm["deferred_when"], "after Fernwood ships")
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
            "--future-work", "Do the same for larkspur")
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

    def _interactively(self, answers, *args, **kw):
        """Run `done` as a person would, with `answers` typed in.

        The tick step comes first and only appears when the item has an unticked
        criterion, so the skip is prepended on exactly that condition -- read
        from the fixture rather than assumed. Prepending it unconditionally
        shifted every later answer by one on items with no criteria, which is how
        an `=` meant for the summary ended up filed as a follow-up.

        Pass ``tick=`` to answer the step instead of skipping it.
        """
        answers = list(answers)
        # Read from disk rather than through a per-class accessor: the two test
        # classes name theirs differently, and a `try/except` around the wrong
        # one silently produced "no criteria", which skipped the prepend and
        # shifted every answer instead of failing.
        body = "".join(f.read_text(encoding="utf-8")
                       for f in sorted((self.ws / "backlog").glob("*.md")))
        if any(m == cli.AC_OPEN for _i, m, _txt in cli._ac_lines(body)):
            answers = [kw.pop("tick", "")] + answers
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

    ``ticked`` may also be a mark character -- ``cli.AC_DROPPED`` -- for the
    third state, so a fixture can start from a file that already records one.
    """
    lines = ["<!-- AC:BEGIN -->"]
    lines += ["- [%s] #%d %s" % (ok if isinstance(ok, str) else ("x" if ok else " "),
                                 i, text)
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
        # Three Enters: the tick step, then both questions. The header must come
        # before all of them -- it is the only chance to notice a mistyped id.
        typed = iter(["", "", ""])
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

    def _interactively(self, answers, *args, **kw):
        """Run `done` as a person would, with `answers` typed in.

        The tick step comes first and only appears when the item has an unticked
        criterion, so the skip is prepended on exactly that condition -- read
        from the fixture rather than assumed. Prepending it unconditionally
        shifted every later answer by one on items with no criteria, which is how
        an `=` meant for the summary ended up filed as a follow-up.

        Pass ``tick=`` to answer the step instead of skipping it.
        """
        answers = list(answers)
        # Read from disk rather than through a per-class accessor: the two test
        # classes name theirs differently, and a `try/except` around the wrong
        # one silently produced "no criteria", which skipped the prepend and
        # shifted every answer instead of failing.
        body = "".join(f.read_text(encoding="utf-8")
                       for f in sorted((self.ws / "backlog").glob("*.md")))
        if any(m == cli.AC_OPEN for _i, m, _txt in cli._ac_lines(body)):
            answers = [kw.pop("tick", "")] + answers
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
        # The draft is the TICKED criterion -- the reader's own sentence for
        # what was done -- not the scope line, which answers a different
        # question and is shown separately as context.
        self.assertEqual(closing.summary, "#1 one")
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
        _summary, future, scope = self._drafts(
            "NA-0017", body=_acceptance(*[(False, "criterion %d" % i) for i in range(1, 7)]))
        self.assertEqual(future, [])
        self.assertIn("AC 0/6", scope)

    def test_everything_ticked_offers_no_follow_ups_either(self):
        """The NA-0024 shape: five of five, and genuinely nothing left over."""
        _summary, future, scope = self._drafts(
            "NA-0024", body=_acceptance(*[(True, "criterion %d" % i) for i in range(1, 6)]))
        self.assertEqual(future, [])
        self.assertIn("AC 5/5", scope)

    def test_a_half_finished_item_offers_what_is_left(self):
        """The only shape where unticked criteria are evidence of anything: some
        were ticked, so the others are outstanding rather than merely untouched.

        What it offers is the criteria's own text -- prose a human wrote, and
        which only a human may edit. Accepting it hands your own sentence back
        to you, so nothing here launders a machine's words into a person's."""
        _summary, future, _scope = self._drafts(
            "NA-0010", body=_acceptance((True, "larkspur"), (False, "robots"),
                                        (False, "sitemap")))
        self.assertEqual(future, ["#2 robots", "#3 sitemap"])

    def test_the_summary_draft_never_claims_authorship_of_commits(self):
        """Not one commit in a real workspace names an item id, so git cannot
        tell this item's work from that day's work. The draft therefore states a
        scope -- the project, and what it saw since the item was opened -- and
        the count is attached to the project, never to the item."""
        summary, _future, scope = self._drafts(
            "NA-0005", body=_acceptance((True, "one")))
        # The SCOPE names the project; the DRAFT is the ticked criterion,
        # which is the reader's own sentence rather than the engine's.
        self.assertTrue(scope.startswith("Orchard"))
        self.assertNotIn("NA-0005", scope)
        self.assertEqual(summary, "#1 one")

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
        summary, _future, scope = self._drafts("NA-0005", created_date="2026-03-01")
        self.assertIn("3 commits since 2026-03-01", scope)
        self.assertNotIn("unrelated change", scope)
        # Several commits and nothing ticked: no honest draft exists, so
        # none is offered and the scope stays context.
        self.assertEqual(summary, "")

    @requires_git
    def test_a_single_commit_is_quoted_because_there_is_nothing_to_choose(self):
        project = self.ws / "projects" / "orchard"
        git_init(project)
        git_commit_all(project, "orchard: the only thing that happened",
                       when="2026-03-12T09:00:00+00:00")
        summary, _future, _scope = self._drafts("NA-0005", created_date="2026-03-01")
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
        summary, _future, _scope = self._drafts("NA-0005", created_date=today)
        # One commit and nothing ticked, so the subject IS the candidate answer
        # and the scope line doubles as the draft.
        self.assertIn("at dawn", summary)


class TickingIsPossibleAtAll(TempCase):
    """Reported from real use: the brief printed `0/9 ticked` on every close, and
    nothing in the package could tick a box.

    Measured on a real backlog at the time: 1 item of 25 carried a single tick.
    So the number that was meant to make you pause read zero for everybody, and
    the two rules that read ticks -- the closing draft, and follow-ups drafted
    from what is left -- were dead in 24 cases out of 25.

    Asked at the close, because that is the moment somebody knows and the last
    moment anyone will be in a position to say.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        git_init(self.ws)
        write_backlog_item(self.ws, "NA-0005", title="Run 3 probes",
                           body=_acceptance((False, "migrate"), (False, "write up")))
        git_commit_all(self.ws)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _text(self):
        return (self.ws / "backlog" / "NA-0005.md").read_text(encoding="utf-8")

    def _typed(self, answers, *args):
        typed = iter(answers)
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            return self._run(*args)

    def test_picking_a_number_ticks_that_criterion(self):
        code, _out, err = self._typed(["1", "", ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        ticked = cli._ticked_acs(self._text())
        self.assertEqual(ticked, ["#1 migrate"])

    def test_the_criterion_text_is_never_rewritten(self):
        """A criterion is a sentence a person wrote. Only the box may change."""
        before = self._text()
        self._typed(["1", "", ""], "done", "NA-0005")
        after = self._text()
        # Compared line by line over the criteria alone, so the closing record
        # `done` also writes does not mask a change to the text.
        b = [ln for ln in before.splitlines() if ln.strip().startswith(("- [", "* ["))]
        a = [ln for ln in after.splitlines() if ln.strip().startswith(("- [", "* ["))]
        self.assertEqual(len(a), len(b))
        for was, now in zip(b, a):
            self.assertEqual(was[5:], now[5:], "the criterion's own text changed")
        self.assertEqual(a[0][:5].strip(), "- [x]".strip())

    def test_all_ticks_everything_left(self):
        code, _out, err = self._typed([cli.TICK_ALL, "", ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(cli._ticked_acs(self._text())), 2)

    def test_enter_ticks_nothing(self):
        """The reflex answer must not assert that work was done."""
        self._typed(["", "", ""], "done", "NA-0005")
        self.assertEqual(cli._ticked_acs(self._text()), [])

    def test_what_was_ticked_becomes_the_offered_draft(self):
        """The point of asking here rather than anywhere else: the answer to
        "what actually happened" is now the reader's own criteria."""
        code, _out, err = self._typed(["1", cli.ACCEPT_DRAFT, ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        closing = items.parse_closing(self._text())
        self.assertIn("#1 migrate", closing.summary)
        self.assertEqual(closing.summary_source, "accepted_draft")

    def test_a_mistyped_number_is_named_rather_than_ignored(self):
        """A number that ticks nothing looks exactly like a criterion that was
        already done."""
        code, out, _err = self._typed(["9", "", ""], "done", "NA-0005")
        self.assertEqual(code, 0)
        self.assertEqual(cli._ticked_acs(self._text()), [])
        self.assertIn("ignored 9", out)

    def test_ctrl_c_at_the_tick_step_closes_nothing(self):
        def interrupt(_prompt=""):
            raise KeyboardInterrupt

        before = self._text()
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", interrupt):
            code, _out, err = self._run("done", "NA-0005")
        self.assertNotEqual(code, 0)
        self.assertEqual(self._text(), before, "an interrupted tick step wrote")
        self.assertIn("not closed", err)

    def test_a_scripted_run_is_never_asked(self):
        """`done --summary x` and any non-tty run must stay one command."""
        def never(_prompt=""):
            raise AssertionError("a scripted run was asked to tick")

        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", never):
            code, _out, err = self._run("done", "NA-0005", "--summary", "did it")
        self.assertEqual(code, 0, err)

    def test_an_item_with_nothing_left_is_not_asked(self):
        write_backlog_item(self.ws, "NA-0006", title="Done already",
                           body=_acceptance((True, "all of it")))
        git_commit_all(self.ws)
        typed = iter(["", ""])          # two questions only, no tick step
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            code, _out, err = self._run("done", "NA-0006")
        self.assertEqual(code, 0, err)


class TheSelector(unittest.TestCase):
    """Arrows to move, space to toggle, `-` to drop, Enter to accept.

    Typing numbers still works and is the fallback, but a typo there fails
    silently in the worst way: `9` on a two-item list ticks nothing, and a
    criterion nobody ticked reads exactly like one that was already done. A
    cursor on a line cannot miss.

    Every assertion here is on the pair `(ticked, dropped)`, so a key that puts
    a line in the wrong one of the two cannot pass by matching on the half the
    test happened to look at.
    """

    ROWS = [(1, "#1 one"), (2, "#2 two"), (3, "#3 three")]

    def _drive(self, keys, term="xterm"):
        """Run the selector against a scripted keyboard."""
        chars = iter(list(keys))

        class FakeIn:
            def isatty(self):
                return True

            def fileno(self):
                return 0

            def read(self, _n=1):
                return next(chars)

        fake_termios = mock.MagicMock()
        fake_termios.tcgetattr.return_value = object()
        with mock.patch.dict(os.environ, {"TERM": term}), \
                mock.patch.dict(sys.modules, {"termios": fake_termios,
                                              "tty": mock.MagicMock()}), \
                mock.patch.object(cli.sys, "stdin", FakeIn()), \
                mock.patch.object(cli.sys, "stdout", mock.MagicMock(**{"isatty.return_value": True})):
            return cli._select_ticks(self.ROWS, None)

    def test_space_toggles_the_line_under_the_cursor(self):
        self.assertEqual(self._drive([" ", "\r"]), ([1], []))

    def test_the_arrows_move(self):
        self.assertEqual(self._drive(["\x1b", "[", "B", " ", "\r"]), ([2], []))

    def test_toggling_twice_leaves_it_unticked(self):
        """The reason it is a toggle and not a set: changing your mind must not
        need a restart."""
        self.assertEqual(self._drive([" ", " ", "\r"]), ([], []))

    def test_several_can_be_picked_out_of_order(self):
        got = self._drive(["\x1b", "[", "B", "\x1b", "[", "B", " ",
                           "\x1b", "[", "A", "\x1b", "[", "A", " ", "\r"])
        self.assertEqual(got, ([1, 3], []))

    def test_it_cannot_walk_off_either_end(self):
        """Clamped rather than wrapped. A list that jumps from the last line to
        the first is a list you tick the wrong thing on."""
        self.assertEqual(self._drive(["\x1b", "[", "A", "\x1b", "[", "A", " ", "\r"]),
                         ([1], []))
        keys = ["\x1b", "[", "B"] * 6 + [" ", "\r"]
        self.assertEqual(self._drive(keys), ([3], []))

    def test_enter_with_nothing_toggled_ticks_nothing(self):
        self.assertEqual(self._drive(["\r"]), ([], []))

    # -- the author's key ---------------------------------------------------

    def test_minus_drops_the_line_under_the_cursor(self):
        """`-` on the row the cursor is on, and nothing else moves. The design
        the author gave: one keypress, no prompt."""
        self.assertEqual(self._drive([cli.DROP_KEY, "\r"]), ([], [1]))

    def test_minus_drops_where_the_cursor_actually_is(self):
        """The half a fixed cursor would hide: move first, then drop."""
        self.assertEqual(self._drive(["\x1b", "[", "B", cli.DROP_KEY, "\r"]), ([], [2]))

    def test_dropping_twice_puts_it_back(self):
        """Same discipline as space. A drop is a judgement, and a judgement you
        cannot take back inside the same selector is one you make by restarting
        the command."""
        self.assertEqual(self._drive([cli.DROP_KEY, cli.DROP_KEY, "\r"]), ([], []))

    def test_a_tick_and_a_drop_come_back_separately(self):
        """The property the pair exists for: two answers to two different
        questions about the same list, and neither may be read as the other."""
        got = self._drive([" ", "\x1b", "[", "B", cli.DROP_KEY, "\r"])
        self.assertEqual(got, ([1], [2]))

    def test_either_key_overrides_the_other(self):
        """Pressing the wrong one first must not need a restart either."""
        self.assertEqual(self._drive([" ", cli.DROP_KEY, "\r"]), ([], [1]))
        self.assertEqual(self._drive([cli.DROP_KEY, " ", "\r"]), ([1], []))

    def test_a_dropped_row_draws_its_own_mark(self):
        """It has to be visible before Enter. A drop that looks identical to an
        untouched line is one you confirm without knowing you made it -- and the
        box is the only thing on the row that may change, because the criterion's
        text belongs to whoever wrote it."""
        drawn = self._drawn(self.ROWS, [cli.DROP_KEY, "\r"], 80)
        last = drawn[-len(self.ROWS):]
        self.assertIn("[%s]" % cli.AC_DROPPED, last[0])
        self.assertIn("#1 one", last[0], "the criterion's text was rewritten")
        for line in last[1:]:
            self.assertNotIn("[%s]" % cli.AC_DROPPED, line,
                             "a row the cursor was never on was marked")

    def _drawn(self, rows, keys, columns):
        """The selector's own output lines, at a given terminal width."""
        chars = iter(list(keys))
        written = []

        class FakeIn:
            def isatty(self): return True
            def fileno(self): return 0
            def read(self, _n=1): return next(chars)

        class FakeOut:
            def isatty(self): return True
            def write(self, s): written.append(s)
            def flush(self): pass

        fake_termios = mock.MagicMock()
        fake_termios.tcgetattr.return_value = object()
        with mock.patch.dict(os.environ, {"TERM": "xterm"}), \
                mock.patch.dict(sys.modules, {"termios": fake_termios,
                                              "tty": mock.MagicMock()}), \
                mock.patch.object(cli.shutil, "get_terminal_size",
                                  return_value=os.terminal_size((columns, 24))), \
                mock.patch.object(cli.sys, "stdin", FakeIn()), \
                mock.patch.object(cli.sys, "stdout", FakeOut()):
            cli._select_ticks(rows, None)
        return [ln for ln in "".join(written).split("\r\n") if "[" in ln]

    def test_a_long_criterion_is_cut_to_one_line(self):
        """The reported bug. Criteria are sentences, so they wrapped -- and the
        redraw moves the cursor up by one line PER ROW. On wrapped rows it landed
        inside the list and every keypress appended a fresh copy instead of
        overwriting, so the list grew down the screen as you moved through it.

        Cutting each row to the terminal width is what makes that arithmetic
        true. Measured in CELLS, because a CJK glyph takes two of them and a
        criterion written in Chinese would otherwise be cut at twice the width it
        actually occupies.
        """
        rows = [(1, "x" * 300), (2, "字" * 200), (3, "short")]
        for columns in (40, 80, 120):
            drawn = self._drawn(rows, ["\r"], columns)
            self.assertTrue(drawn, "nothing was drawn at %d columns" % columns)
            for line in drawn:
                clean = line.replace("\x1b[2K", "")
                self.assertLessEqual(
                    cli._width(clean), columns,
                    "a row is %d cells wide in a %d-column terminal, so it wraps "
                    "and the redraw desynchronises" % (cli._width(clean), columns))

    def test_the_number_of_lines_drawn_never_grows(self):
        """The symptom, asserted directly: one line per row per redraw, however
        many keys are pressed."""
        rows = [(1, "a" * 200), (2, "b" * 200)]
        drawn = self._drawn(rows, [" ", "\x1b", "[", "B", " ", "\x1b", "[", "A", "\r"], 80)
        # 4 redraws (initial + one per key that changes state) x 2 rows.
        self.assertEqual(len(drawn) % len(rows), 0)
        self.assertEqual(len(drawn), (len(drawn) // len(rows)) * len(rows))
        self.assertGreaterEqual(len(drawn) // len(rows), 2, "no redraw happened")

    def test_a_dumb_terminal_declines_rather_than_breaking(self):
        """CI, a pipe, a scheduler. The caller asks the numeric question, and a
        `done` that cannot draw is still a `done` that can ask."""
        self.assertIsNone(self._drive([" ", "\r"], term="dumb"))

    def test_a_non_tty_declines(self):
        with mock.patch.object(cli.sys, "stdin",
                               mock.MagicMock(**{"isatty.return_value": False})):
            self.assertIsNone(cli._select_ticks(self.ROWS, None))


class TheThirdMarkIsReadByEveryReader(unittest.TestCase):
    """One parser, four readers, and the failure that has no symptom.

    A mark three readers know about and the fourth does not is not a crash. It is
    a subtraction: `AC 2/5` prints as `AC 2/4`, which reads as an item that only
    ever had four criteria. The promise, and the fact that it was set aside, both
    disappear -- and nothing on the screen is wrong-looking enough to check.

    So every reader is asserted here against ONE body carrying all three marks,
    rather than each being trusted to remember.
    """

    BODY = "\n".join([
        "<!-- AC:BEGIN -->",
        "- [x] #1 the exporter writes one file per crate",
        "- [~] #2 the legacy sidecar keeps working",
        "- [ ] #3 the migration guide names the new flag",
        "<!-- AC:END -->",
    ])

    def test_the_parser_reports_the_mark_rather_than_discarding_the_line(self):
        self.assertEqual([(m, t) for _i, m, t in cli._ac_lines(self.BODY)],
                         [(cli.AC_DONE, "#1 the exporter writes one file per crate"),
                          (cli.AC_DROPPED, "#2 the legacy sidecar keeps working"),
                          (cli.AC_OPEN, "#3 the migration guide names the new flag")])

    def test_the_count_keeps_it_in_the_total(self):
        """It was promised. A denominator that shrinks when a criterion is set
        aside hides the promise along with it -- and reports the drop as though
        the item had always been smaller."""
        self.assertEqual(cli._ac_progress(self.BODY), (1, 1, 3))

    def test_it_is_not_reported_as_done(self):
        """The original lie the mark exists to refuse: a criterion nobody met,
        filed as work that happened."""
        self.assertEqual(cli._ticked_acs(self.BODY),
                         ["#1 the exporter writes one file per crate"])

    def test_it_is_not_reported_as_outstanding(self):
        """The other lie, and the expensive one -- see the class below."""
        self.assertEqual(cli._unticked_acs(self.BODY),
                         ["#3 the migration guide names the new flag"])

    def test_the_html_brief_strips_the_mark_like_any_other_box(self):
        """The reader outside `cli`. `BRIEF.html` selects criteria on `- [` and
        then strips the checkbox as a PREFIX, so a mark the pattern does not know
        survives into the page: one line rendering as `- [~] #2 ...` beside three
        clean ones reads as a formatting fault, not as a state."""
        line = "- [~] #2 the legacy sidecar keeps working"
        self.assertEqual(html._AC_PREFIX.sub("", line, count=1),
                         "#2 the legacy sidecar keeps working")


class ACriterionIsWhatIsBetweenTheMarkers(unittest.TestCase):
    """You must be able to quote a criterion without writing one.

    Observed three times on 2026-08-12, the third while the bug report was being
    typed: a line of NOTES showing *what a criterion looks like* was counted as a
    criterion. `nb show NA-0046` went from `共 6 条` to `共 7 条`, and the phantom
    carried `(you)`.

    A wrong number would be cheap. This is not one. Per `_unticked_acs`, an
    unticked criterion is what `done` drafts as `future_work`, and `followup`
    mints a `future_work` entry into a real backlog item -- so a sentence about
    criteria becomes a task somebody is asked to do. NA-0031 spent an entire
    item closing that door; the entrance this time is prose.

    The markers were already in the file -- `_item_text` has written them since
    items had bodies. The edge existed and no reader knew about it.
    """

    BODY = "\n".join([
        "<!-- SECTION:NEXT_ACTION:BEGIN -->",
        "write the red test first",
        "<!-- SECTION:NEXT_ACTION:END -->",
        "",
        "<!-- AC:BEGIN -->",
        "- [x] #1 (agent) the scan stops at the markers",
        "- [ ] #2 (agent) indentation is not an exemption",
        "<!-- AC:END -->",
        "",
        "<!-- SECTION:NOTES:BEGIN -->",
        "A criterion is written like this:",
        "",
        "    - [ ] #9 (you) decide the posture: advice or enforcement",
        "",
        "...and the four spaces above make that a code block.",
        "<!-- SECTION:NOTES:END -->",
    ])

    def test_prose_quoting_a_criterion_does_not_become_one(self):
        """The bug, at the parser. Two criteria are declared; two are read."""
        self.assertEqual([t for _i, _m, t in items.ac_lines(self.BODY)],
                         ["#1 (agent) the scan stops at the markers",
                          "#2 (agent) indentation is not an exemption"])

    def test_the_decoy_is_indented_inside_a_code_block(self):
        """★ Guarding the guard. ★

        `ac_lines` did `line.strip()` before looking, so indentation offered no
        protection at all and a fenced or indented block was no safer than a bare
        line. A decoy at column zero would pass against a fix that merely skipped
        indented lines -- which is not the fix, and would break every criterion
        anyone has ever nested under a bullet.

        So this asserts the fixture itself: the decoy is indented, and it is
        still shaped exactly like a criterion once stripped.
        """
        decoy = [ln for ln in self.BODY.splitlines() if "#9" in ln][0]
        self.assertTrue(decoy.startswith("    "), "the decoy must be indented")
        self.assertEqual(decoy.strip(), "- [ ] #9 (you) decide the posture: "
                                        "advice or enforcement")
        # Indented criteria inside the block are still criteria: the fix is a
        # scan RANGE, not an indentation rule.
        nested = "\n".join(["<!-- AC:BEGIN -->", "  - [ ] #1 nested under a bullet",
                            "<!-- AC:END -->"])
        self.assertEqual([t for _i, _m, t in items.ac_lines(nested)],
                         ["#1 nested under a bullet"])

    def test_the_phantom_is_not_counted_in_the_denominator(self):
        """`AC 1/2`, not `AC 1/3`. The item reads as done as it actually is."""
        self.assertEqual(items.ac_progress(self.BODY), (1, 0, 2))

    def test_the_line_indexes_stay_relative_to_the_whole_body(self):
        """★ The trap in this fix. ★

        `_apply_marks` writes a mark at `body.splitlines()[i]`. Parsing the span
        as its own string and letting `enumerate` restart would return indexes
        offset by however long the preamble is, and ticking `#1` would rewrite a
        line in NEXT_ACTION or in the frontmatter instead -- silently, since
        `_apply_marks` only splices characters 3..5 of whatever it lands on.
        """
        lines = self.BODY.splitlines()
        for i, mark, _text in items.ac_lines(self.BODY):
            self.assertEqual(lines[i].strip()[3], mark, "index %d is not that line" % i)

    def test_a_criterion_before_the_block_is_not_read_either(self):
        """The range has two ends. A checkbox above `AC:BEGIN` -- a template
        left in NEXT_ACTION, a checklist in a heading -- is outside it too."""
        body = "\n".join(["- [ ] #0 left over from a template",
                          "<!-- AC:BEGIN -->", "- [ ] #1 the real one",
                          "<!-- AC:END -->"])
        self.assertEqual([t for _i, _m, t in items.ac_lines(body)], ["#1 the real one"])

    def test_the_markers_are_recognised_by_the_whole_line_not_a_substring(self):
        """A `find()` would land in the frontmatter.

        Real items in this workspace name the markers *inline* -- NA-0051's own
        `what_agent_can_do:` field quotes both of them in one sentence, and the
        prose of three more items does the same. Substring search would open the
        span at that mention and read the rest of the frontmatter as criteria.
        """
        body = "\n".join([
            "---",
            "what_agent_can_do: make `<!-- AC:BEGIN -->` and `<!-- AC:END -->` the range",
            "- [ ] #0 a frontmatter line shaped like a box",
            "---",
            "<!-- AC:BEGIN -->",
            "- [ ] #1 the real one",
            "<!-- AC:END -->",
        ])
        self.assertEqual([t for _i, _m, t in items.ac_lines(body)], ["#1 the real one"])

    def test_an_indented_marker_does_not_open_the_span(self):
        """The markers are quoted in prose in this repo as often as criteria are.
        An indented `<!-- AC:BEGIN -->` is somebody showing you the marker."""
        body = "\n".join(["    <!-- AC:BEGIN -->",
                          "    - [ ] #9 the shape of a criterion",
                          "    <!-- AC:END -->",
                          "<!-- AC:BEGIN -->", "- [ ] #1 the real one",
                          "<!-- AC:END -->"])
        self.assertEqual([t for _i, _m, t in items.ac_lines(body)], ["#1 the real one"])


class WhenTheMarkersAreMissing(unittest.TestCase):
    """★ The chosen degradation: scan the whole body, exactly as before. ★

    This is a decision, not a leftover. The alternative -- report zero -- was
    rejected, and the reason is the one written at the top of `ac_lines`: a
    reader that does not recognise a criterion fails by **subtraction**, and
    subtraction has no symptom. `AC 2/5` becoming `AC 0/0` does not read as a
    broken parser. It reads as an item that never promised anything.

    Measured before choosing, on 2026-08-12:

    * 51 of 51 items in the live workspace carry the pair, and 3 of 3 in
      `examples/workspace/backlog/` -- so nothing `nextbrief new` writes ever
      reaches this path, and the narrowing above is free;
    * but `helpers.write_backlog_item` writes its default body **without** them,
      and four of the eight test modules build marker-less bodies by hand. A
      body somebody typed is the normal shape for anything the engine did not
      mint, and an item older than the markers has no way to grow them.

    `_needs_you` settled the identical question in the same file and got the
    same answer: "every criterion written before the marker existed is
    unmarked", and reading that absence as a claim "would empty the tick
    selector for the entire existing backlog in one move". Rule 6 of
    CONTRIBUTING points the same way -- fail open.

    So the fallback is today's behaviour verbatim, which makes this change one
    that can only ever narrow. Nothing that is counted today stops being
    counted. The honest cost: a marker-less item is still open to the phantom
    above, and the only fix for that is markers -- which `new` already writes.
    """

    def test_a_body_with_no_markers_is_read_whole(self):
        body = "\n".join(["## Acceptance", "", "- [ ] It is done",
                          "- [x] #2 and this one was"])
        self.assertEqual(items.ac_progress(body), (1, 0, 2))

    def test_a_begin_with_no_end_falls_back_rather_than_running_to_the_bottom(self):
        """Half a pair is a malformed file, and the rule stays one sentence:
        the range applies when both ends are there. Running `AC:BEGIN` to EOF
        would make an unclosed marker silently swallow NOTES -- the very bug
        this item is about, arriving through the fix for it."""
        body = "\n".join(["- [ ] #0 above", "<!-- AC:BEGIN -->", "- [ ] #1 inside"])
        self.assertEqual([t for _i, _m, t in items.ac_lines(body)],
                         ["#0 above", "#1 inside"])

    def test_an_end_before_a_begin_falls_back_too(self):
        body = "\n".join(["<!-- AC:END -->", "- [ ] #0 stray", "<!-- AC:BEGIN -->"])
        self.assertEqual([t for _i, _m, t in items.ac_lines(body)], ["#0 stray"])

    def test_an_empty_block_reports_zero_rather_than_falling_back(self):
        """A well-formed pair is an answer even when it is empty. Falling back
        here would resurrect every checkbox in NOTES on exactly the items whose
        criteria have all been dropped."""
        body = "\n".join(["<!-- AC:BEGIN -->", "<!-- AC:END -->",
                          "<!-- SECTION:NOTES:BEGIN -->", "- [ ] #9 (you) a quote",
                          "<!-- SECTION:NOTES:END -->"])
        self.assertEqual(items.ac_lines(body), [])


class APhantomCriterionMustNotMintATask(TempCase):
    """★ Where a counting bug stops being a counting bug. ★

    `_unticked_acs` says it in its own docstring: the unticked list is what
    `done` drafts as `future_work`, and `followup` turns a `future_work` entry
    into a real backlog item carrying `discovered_from` -- "a minted task
    travels". So a sentence in NOTES *about* criteria could be drafted as
    outstanding work, accepted in one keystroke, and minted into a task nobody
    ever meant to create. NA-0031 spent a whole item shutting that door on
    dropped criteria. This is the same door with prose at it.

    Asserted at all three depths, because the seam is the point and the two
    ends fail differently: the parser can be right while the draft is wrong.
    """

    NOTES = "\n".join([
        "<!-- SECTION:NOTES:BEGIN -->",
        "The shape to write is:",
        "",
        "    - [ ] #9 (you) decide the posture: advice or enforcement",
        "",
        "<!-- SECTION:NOTES:END -->",
    ])

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        # Half-ticked on purpose: `_closing_drafts` only offers follow-ups when
        # some criteria ARE ticked, so this is the one shape where the phantom
        # could actually reach the draft.
        self.body = _acceptance((True, "larkspur"), (False, "robots")) + "\n" + self.NOTES

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def test_the_phantom_is_not_in_the_unticked_list(self):
        self.assertEqual(cli._unticked_acs(self.body), ["#2 robots"])

    def test_the_phantom_is_not_drafted_as_future_work(self):
        """The seam itself, driven the way `done` drives it."""
        path = write_backlog_item(self.ws, "NA-0005", body=self.body)
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        _summary, future, scope = cli._closing_drafts(
            Workspace(root=self.ws, out=self.ws, source="test"), fm, body, None)
        self.assertEqual(future, ["#2 robots"])
        # The trigger really fired: without this, a draft list that is empty for
        # some unrelated reason would pass the assertion above.
        self.assertIn("AC 1/2", scope)

    def test_followup_mints_one_item_and_it_is_not_the_prose(self):
        """All the way to the file on disk. `followup --all` is the command
        that turns a draft into somebody's next morning."""
        write_backlog_item(self.ws, "NA-0005", body=self.body)
        for text in cli._unticked_acs(self.body):
            self.assertEqual(self._run("done", "NA-0005", "--future-work", text)[0], 0)
        code, out, err = self._run("followup", "NA-0005", "--all")
        self.assertEqual(code, 0, err)
        minted = sorted(p.name for p in self.ws.glob("backlog/*.md")
                        if not p.name.startswith("NA-0005"))
        self.assertEqual(len(minted), 1, "minted %r from one real criterion" % (minted,))
        self.assertNotIn("posture", minted[0])
        self.assertNotIn("posture", out)


class DroppingACriterionTheDesignMovedPast(TempCase):
    """`-` in the tick selector: this one no longer applies.

    Reported from real use, closing a real item: a design change had made several
    tick boxes obsolete, and the file had nowhere to say so. Ticking one claims
    work that never happened. Leaving it unticked reads as a shortfall -- and
    does not sit still, because an unticked criterion is exactly what `done`
    drafts as `future_work` and `followup` turns into a backlog item. The cheap
    mistake mints a task for work that was deliberately abandoned.

    Dropped means MARKED, not deleted, in the sense this CLI already uses for
    `drop <id>`: the file stays, and so does its git history. The line and its
    text stay; only the box changes.

    Everything below drives the numbered fallback rather than the selector,
    because that is what a captured stdout is -- which makes these the tests
    that cover the path a terminal that cannot draw takes. The keypresses
    themselves are covered in `TheSelector`.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        git_init(self.ws)
        write_backlog_item(
            self.ws, "NA-0005", title="Rewrite the crate exporter",
            body=_acceptance((False, "the exporter writes one file per crate"),
                             (False, "the legacy sidecar keeps working"),
                             (False, "the migration guide names the new flag")))
        git_commit_all(self.ws)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _text(self):
        return (self.ws / "backlog" / "NA-0005.md").read_text(encoding="utf-8")

    def _typed(self, answers, *args):
        typed = iter(answers)
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            return self._run(*args)

    def _criteria(self):
        return [ln for ln in self._text().splitlines()
                if ln.strip().startswith(("- [", "* ["))]

    # -- the one the whole thing is for -------------------------------------

    def test_a_dropped_criterion_is_never_drafted_as_future_work(self):
        """★ The red one. ★

        Tick #1, drop #2, leave #3. `=` at the follow-up question takes every
        draft on offer, so whatever reached `future_work` is asserted whole
        rather than by a substring that could match somewhere else.

        Both halves matter. The drafts are NOT empty here -- #3 is on offer and
        is accepted -- so the test cannot pass by there being nothing to take,
        which is the shape of a guard that would go green with the feature ripped
        out.
        """
        code, _out, err = self._typed(["1 -2", "", cli.ACCEPT_DRAFT, ""],
                                      "done", "NA-0005")
        self.assertEqual(code, 0, err)
        closing = items.parse_closing(self._text())
        recorded = [e.text for e in closing.future_work]
        self.assertEqual(recorded, ["#3 the migration guide names the new flag"])
        self.assertNotIn("#2 the legacy sidecar keeps working", recorded)

    def test_and_followup_mints_no_backlog_item_for_it(self):
        """The harm, stated where it actually lands. `future_work` is not a note:
        `followup` turns each entry into a real item carrying `discovered_from`,
        and the next reader has nothing to tell them the work is dead."""
        self._typed(["1 -2", "", cli.ACCEPT_DRAFT, ""], "done", "NA-0005")
        code, _out, err = self._run("followup", "NA-0005", "--all")
        self.assertEqual(code, 0, err)
        minted = sorted(self.ws.glob("backlog/NA-0006*.md")) + \
            sorted(self.ws.glob("backlog/NA-0007*.md"))
        self.assertEqual(len(minted), 1, "a dropped criterion became a backlog item")
        created = minted[0].read_text(encoding="utf-8")
        self.assertIn("the migration guide names the new flag", created)
        self.assertNotIn("legacy sidecar", created)

    # -- marked, not deleted -------------------------------------------------

    def test_the_line_stays_and_only_its_box_changes(self):
        """`drop <id>` keeps the file and its git history; `-` keeps the line and
        its sentence. Erasing the words would erase the one fact worth having --
        that the goal moved."""
        before = self._criteria()
        code, _out, err = self._typed(["-2", "", ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        after = self._criteria()
        self.assertEqual(len(after), len(before), "a criterion line was removed")
        for was, now in zip(before, after):
            self.assertEqual(was[5:], now[5:], "the criterion's own text changed")
        self.assertEqual([ln.strip()[:5] for ln in after],
                         ["- [ ]", "- [~]", "- [ ]"])

    def test_the_header_count_still_says_three(self):
        """The symptom a missed reader produces, asserted where a person would
        see it: `1/3` printed as `1/2`, which does not read as a bug -- it reads
        as an item that only ever had two criteria.

        Read back off a file that already records a drop, so this covers the
        readers rather than the run that wrote the mark.
        """
        write_backlog_item(
            self.ws, "NA-0009", title="Rewrite the crate exporter",
            body=_acceptance((True, "the exporter writes one file per crate"),
                             (cli.AC_DROPPED, "the legacy sidecar keeps working"),
                             (False, "the migration guide names the new flag")))
        git_commit_all(self.ws)
        code, out, err = self._run("done", "NA-0009", "--summary", "shipped")
        self.assertEqual(code, 0, err)
        self.assertIn("1/3", out)
        self.assertIn("1 dropped", out)

    # -- one keypress, and no third question ---------------------------------

    def test_dropping_adds_no_question(self):
        """`_ask_closing` is exactly two questions and stays that way. A prompt
        for the reason would reintroduce the friction the key was added to
        remove."""
        asked = []
        answers = ["-2", "", ""]

        def counting(prompt=""):
            asked.append(prompt)
            # Anything past the third question is answered with Enter rather
            # than running off the end of the list: a fourth prompt must be
            # reported by the count below, not by an IndexError from the fixture.
            return answers[len(asked) - 1] if len(asked) <= len(answers) else ""

        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", counting):
            code, _out, err = self._run("done", "NA-0005")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(asked), 3,
                         "asked something other than the tick step plus two "
                         "questions: %r" % (asked,))

    def test_what_was_dropped_is_offered_as_the_summary_draft(self):
        """The reason does not go unrecorded just because nothing asked for it:
        it pre-fills the draft of a question that was being asked anyway."""
        _code, out, _err = self._typed(["-2", "", ""], "done", "NA-0005")
        self.assertIn("dropped 1 criteria: #2 the legacy sidecar keeps working", out)

    def test_enter_still_skips_that_draft(self):
        """★ A draft, never the Enter default. ★

        Both halves: the draft really was on offer, and Enter recorded nothing.
        If Enter took it, the reflex that answers every form would start filing
        machine sentences under a person's name -- and it does not become
        acceptable because the sentence is about something they abandoned.
        """
        # A follow-up is typed so that a closing record IS written: "no block at
        # all" must not be what does the work here. The summary field itself has
        # to be empty and say so.
        _code, out, _err = self._typed(
            ["-2", "", "the sidecar owners need telling", ""], "done", "NA-0005")
        self.assertIn("dropped 1 criteria", out, "no draft was offered to skip")
        closing = items.parse_closing(self._text())
        self.assertEqual(closing.summary, "")
        self.assertEqual(closing.summary_source, "none")

    def test_a_criterion_dropped_before_this_run_is_not_re_reported(self):
        """The draft says what happened in THIS close. A criterion set aside
        weeks ago is already recorded in the file, and re-offering it as news
        would put a stale sentence in front of the one key that files a draft
        verbatim -- with nobody at the keyboard in a position to know it is old.
        """
        write_backlog_item(
            self.ws, "NA-0009", title="Rewrite the crate exporter",
            body=_acceptance((cli.AC_DROPPED, "the legacy sidecar keeps working"),
                             (False, "the migration guide names the new flag")))
        git_commit_all(self.ws)
        typed = iter(["1", "", ""])
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            code, out, err = self._run("done", "NA-0009")
        self.assertEqual(code, 0, err)
        # The trigger really happened: the run had something to tick and a draft
        # was offered, so this cannot pass by nothing being drafted at all.
        self.assertIn("draft:", out)
        self.assertNotIn("dropped 1 criteria", out)
        # And the older drop is still SEEN -- as the count, which is context.
        # Being absent from the draft is not the same as being forgotten.
        self.assertIn("1 dropped", out)

    def test_the_accept_key_files_it_as_a_draft_not_as_testimony(self):
        _code, _out, err = self._typed(["-2", cli.ACCEPT_DRAFT, ""],
                                       "done", "NA-0005")
        closing = items.parse_closing(self._text())
        self.assertIn("dropped 1 criteria: #2 the legacy sidecar keeps working",
                      closing.summary, err)
        self.assertEqual(closing.summary_source, "accepted_draft")

    # -- the fallback path ---------------------------------------------------

    def test_a_mistyped_drop_is_named_rather_than_ignored(self):
        """Same rule the tick numbers already follow. A `-9` that silently drops
        nothing looks exactly like a criterion that was never obsolete."""
        code, out, _err = self._typed(["-9", "", ""], "done", "NA-0005")
        self.assertEqual(code, 0)
        self.assertIn("ignored -9", out)
        self.assertNotIn(cli.AC_DROPPED, "".join(self._criteria()))

    # -- who may write it ----------------------------------------------------

    def test_a_scripted_run_can_never_drop_anything(self):
        """Same discipline as ticking, and as `proposed_status`: an agent may
        suggest, and only a person at a keyboard may write a criterion off.
        A non-interactive `done` is not asked, so it cannot answer."""
        # Bounded rather than a constant: if the gate that skips the questions
        # were removed, this must run out of answers loudly rather than loop on
        # the follow-up prompt forever.
        typed = iter(["-2", "", ""])
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            code, _out, err = self._run("done", "NA-0005", "--summary", "shipped")
        self.assertEqual(code, 0, err)
        self.assertNotIn(cli.AC_DROPPED, "".join(self._criteria()))


class WhoCanSayThisIsDone(TempCase):
    """`(you)` and `(agent)`: which criteria are actually a person's to answer.

    The count that produced this: three items that could not be closed carried 20
    acceptance criteria between them, of which exactly 2 needed the author -- one
    UAT, one set of credentials. The other 18 were things a command could settle,
    and every one of them was sitting in the same list, in the same shape, in
    front of the same person.

    So the expense was never the ticking. It was that "which of these actually
    need me" had to be worked out from scratch on every close, and that is
    precisely the recomputation this tool exists to spend rather than charge.

    Marked, held back, and SAID. A shorter list with no explanation is this
    repository's characteristic failure -- it does not read as something missing,
    it reads as an item that was always this small.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        git_init(self.ws)
        write_backlog_item(
            self.ws, "NA-0005", title="Ship the exporter",
            body=_acceptance((False, "(agent) the exporter writes one file per crate"),
                             (False, "(you) the sample export reads right to you"),
                             (False, "(agent) the migration guide names the new flag")))
        git_commit_all(self.ws)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _text(self):
        return (self.ws / "backlog" / "NA-0005.md").read_text(encoding="utf-8")

    def _typed(self, answers, *args):
        typed = iter(answers)
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            return self._run(*args)

    def _criteria(self):
        return [ln.strip() for ln in self._text().splitlines()
                if ln.strip().startswith(("- [", "* ["))]

    # -- reading the marker --------------------------------------------------

    def test_the_marker_is_read_off_the_criterion_after_its_number(self):
        self.assertEqual(cli._ac_owner("#4 (you) the brief reads right on a phone"),
                         cli.AC_YOU)
        self.assertEqual(cli._ac_owner("#1 (agent) ruff is clean"), cli.AC_AGENT)
        self.assertIsNone(cli._ac_owner("#2 nobody has classified this one"))

    def test_an_unmarked_criterion_counts_as_yours(self):
        """★ The half that keeps the existing backlog working. ★

        Every criterion written before the marker existed carries none, so
        reading "no marker" as "the agent's" would empty the tick selector for
        the whole backlog in one move -- and empty is the one thing it must not
        be. `done` could not ask at all until recently: 1 ticked box across 25
        items is what that measured.
        """
        self.assertTrue(cli._needs_you("#2 nobody has classified this one"))
        self.assertTrue(cli._needs_you("#3 (you) you have to look at it"))
        self.assertFalse(cli._needs_you("#1 (agent) ruff is clean"))

    # -- what the selector offers --------------------------------------------

    def test_only_the_ones_marked_for_you_are_asked_about(self):
        """The numbered fallback is what a captured stdout drives, so the list it
        prints is the list. One entry, and it is the `(you)` one."""
        _code, out, _err = self._typed(["", "", ""], "done", "NA-0005")
        self.assertIn("1. #2 (you) the sample export reads right to you", out)
        self.assertNotIn("the exporter writes one file per crate", out)
        self.assertNotIn("the migration guide names the new flag", out)

    def test_the_ones_held_back_are_counted_out_loud(self):
        """Never silently. A list shorter than the file, with nothing saying so,
        is indistinguishable from an item that only ever had one criterion."""
        _code, out, _err = self._typed(["", "", ""], "done", "NA-0005")
        self.assertIn("2 still open and the agent's to verify", out)
        self.assertIn("--all-criteria", out)

    def test_numbering_follows_the_list_that_was_shown(self):
        """`1` means the first row on screen, not the first criterion in the
        file. Getting this wrong ticks a criterion nobody was asked about, which
        is the same falsehood as ticking one yourself."""
        code, _out, err = self._typed(["1", "", ""], "done", "NA-0005")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._criteria(), [
            "- [ ] #1 (agent) the exporter writes one file per crate",
            "- [x] #2 (you) the sample export reads right to you",
            "- [ ] #3 (agent) the migration guide names the new flag"])

    def test_an_item_that_is_all_the_agents_asks_nothing_but_still_reports(self):
        write_backlog_item(
            self.ws, "NA-0009", title="Ship the exporter",
            body=_acceptance((False, "(agent) ruff is clean"),
                             (False, "(agent) the suite is green")))
        git_commit_all(self.ws)
        _code, out, _err = self._typed(["", ""], "done", "NA-0009")
        self.assertIn("2 still open and the agent's to verify", out)
        self.assertNotIn("ruff is clean", out)

    # -- the escape hatch, which is what dropping one needs -------------------

    def test_all_criteria_puts_them_back_so_one_can_be_set_aside(self):
        """★ Why holding them back could not be the only behaviour. ★

        A criterion the design moved past is most often one of the agent's --
        this very feature's own item has nine and not one of them is the
        author's. With no way to reach them, the third mark would exist and be
        unreachable for the exact case it was built for, and the only remaining
        way to record it would be hand-editing the file, which is where this
        whole flow started.
        """
        code, out, err = self._typed(["-1", "", ""], "done", "NA-0005",
                                     "--all-criteria")
        self.assertEqual(code, 0, err)
        self.assertIn("1. #1 (agent) the exporter writes one file per crate", out)
        self.assertEqual(self._criteria()[0],
                         "- [~] #1 (agent) the exporter writes one file per crate")

    def test_nothing_is_held_back_and_nothing_is_announced_with_the_flag(self):
        _code, out, _err = self._typed(["", "", ""], "done", "NA-0005",
                                       "--all-criteria")
        self.assertNotIn("the agent's to verify", out)

    # -- what happens to the ones nobody was asked about ----------------------

    def test_an_open_agent_criterion_still_drafts_as_future_work(self):
        """Held back from the question, not from the record. An agent criterion
        left open is outstanding work, and the follow-up draft is where
        outstanding work has always gone -- so the criteria that stop occupying a
        person's attention do not stop being tracked."""
        code, _out, err = self._typed(["1", "", cli.ACCEPT_DRAFT, ""],
                                      "done", "NA-0005")
        self.assertEqual(code, 0, err)
        recorded = [e.text for e in items.parse_closing(self._text()).future_work]
        self.assertEqual(recorded, ["#1 (agent) the exporter writes one file per crate",
                                    "#3 (agent) the migration guide names the new flag"])

    def test_the_header_still_counts_every_criterion(self):
        """The denominator is the item's promise, and it does not shrink because
        some of the promise is not this person's to check."""
        _code, out, _err = self._typed(["1", "", ""], "done", "NA-0005")
        self.assertIn("0/3", out)


class WarningAboutCriteriaNobodyCanAnswer(TempCase):
    """`check` says when an item's criteria are shaped wrong.

    Two shapes, both measured rather than invented. More than two criteria
    needing a person is a problem with the item -- across the three items that
    jammed, 20 criteria and 2 that genuinely needed the author. And a criterion
    with no marker at all is one nobody has classified, which is why `done` still
    has to ask about it.

    One line per rule, however large the backlog, and that is the load-bearing
    part: the obvious shape -- one line per item -- would print twenty-odd
    warnings on the first run of any real workspace, and a warning that fires
    twenty times on day one is a warning people stop reading, after which the one
    that matters goes past unread too.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def _warnings(self):
        return cli._criteria_warnings(Workspace(self.ws, self.ws, "test"), None)

    def test_an_item_with_no_markers_is_reported_once(self):
        write_backlog_item(self.ws, "NA-0005",
                           body=_acceptance((False, "it works"), (False, "it is fast")))
        got = "\n".join(self._warnings())
        self.assertIn("NA-0005", got)
        self.assertIn("no (agent)/(you) marker", got)

    def test_a_fully_marked_item_is_not_reported(self):
        write_backlog_item(self.ws, "NA-0005",
                           body=_acceptance((False, "(agent) it works"),
                                            (True, "(you) it reads right")))
        self.assertEqual(self._warnings(), [])

    def test_a_criterion_the_design_moved_past_does_not_count_against_you(self):
        """Found by UAT on the real backlog, 2026-08-08.

        A `[~]` criterion is set aside: nobody has to answer it, so it cannot be
        part of "too many criteria need you". Counting it inflated a live item to
        4 when 2 were open, and the warning then told the author his item was
        badly shaped on the strength of two criteria he had already retired.

        This is the third state's own failure mode arriving one level up. Every
        reader of the mark has to know about it, and this warning shipped in the
        same batch as the mark without knowing.
        """
        write_backlog_item(self.ws, "NA-0005",
                           body=_acceptance((cli.AC_DROPPED, "(you) retired one"),
                                            (cli.AC_DROPPED, "(you) retired two"),
                                            (False, "(you) still open"),
                                            (False, "(agent) checkable")))
        self.assertEqual(self._warnings(), [])

    def test_too_many_criteria_on_a_person_is_reported_with_the_count(self):
        write_backlog_item(self.ws, "NA-0005",
                           body=_acceptance((False, "(you) one"), (False, "(you) two"),
                                            (False, "(you) three")))
        got = "\n".join(self._warnings())
        self.assertIn("NA-0005 (3)", got)
        self.assertIn("more than 2", got)

    def test_exactly_two_is_not_too_many(self):
        write_backlog_item(self.ws, "NA-0005",
                           body=_acceptance((False, "(you) one"), (False, "(you) two")))
        self.assertEqual(self._warnings(), [])

    def test_one_line_per_rule_however_many_items_are_wrong(self):
        """The property that keeps this readable, asserted at a size where the
        naive shape would already be unreadable."""
        for n in range(12):
            write_backlog_item(self.ws, "NA-00%02d" % (n + 10),
                               body=_acceptance((False, "unmarked and open")))
        got = self._warnings()
        self.assertEqual(len(got), 1, got)
        self.assertIn("12 open item(s)", got[0])
        self.assertIn("(+9)", got[0], "the ids were not capped: %s" % got[0])

    def test_a_closed_item_is_never_warned_about(self):
        """True, permanent and impossible to act on, which is the definition of
        noise. Its criteria are history now."""
        write_backlog_item(self.ws, "NA-0005", status="done",
                           body=_acceptance((False, "unmarked"), (False, "also unmarked")))
        self.assertEqual(self._warnings(), [])

    def test_an_item_with_no_criteria_at_all_is_not_warned_about(self):
        write_backlog_item(self.ws, "NA-0005", body="Nothing to verify here.")
        self.assertEqual(self._warnings(), [])

    def test_check_prints_them_without_touching_its_exit_code(self):
        """★ Exit 3 means "out of date". ★

        A scheduler branches on it, and an item worded awkwardly is not a reason
        to re-run the pipeline. So the warnings ride along on stderr and the
        contract the exit code carries is left exactly as it was.
        """
        ws = self.workspace("wsb", with_git=False)
        write_backlog_item(ws, "NA-0005", body=_acceptance((False, "unmarked")))
        code, _out, err = capture(cli.main, ["--workspace", str(ws), "v0", "--no-notify"])
        self.assertEqual(code, 0, err)
        code, _out, err = capture(cli.main, ["--workspace", str(ws), "check"])
        self.assertEqual(code, 0, "the warning changed the exit code")
        self.assertIn("no (agent)/(you) marker", err)


class TheClosedListSeparatesWhatMovedFromWhatWasDone(TempCase):
    """`closed` has to answer two different questions at once.

    What a project finished, and where its goals went instead. The second one had
    no shape here at all: a criterion set aside appeared only if somebody had
    happened to mention it in the summary, so a project's history read as though
    it had always meant exactly what it shipped.

    It also must not land in the follow-up list. Those lines are the work
    somebody should pick up; a dropped criterion is precisely the work nobody
    should, and mixing them is the original mistake in a new place.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        git_init(self.ws)
        write_backlog_item(
            self.ws, "NA-0005", title="Rewrite the crate exporter",
            body=_acceptance((True, "the exporter writes one file per crate"),
                             (cli.AC_DROPPED, "the legacy sidecar keeps working"),
                             (False, "the migration guide names the new flag")))
        git_commit_all(self.ws)

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def _blocks(self, *args):
        """`closed` output cut into one chunk per item, keyed by id.

        A substring search over the whole page cannot tell "printed" from
        "printed under the right item", and printing them apart from each other
        is this view's entire claim. Item lines carry two spaces of indent and
        their detail rows five, which is the shape every row here already has.
        """
        code, out, err = self._run("closed", *args)
        self.assertEqual(code, 0, err)
        blocks, current = {}, None
        for line in out.splitlines():
            if not line.startswith("  "):
                current = None
            elif not line.startswith("     "):
                current = line.strip().split("  ")[0]
                blocks[current] = []
            elif current is not None:
                blocks[current].append(line)
        return {key: "\n".join(value) for key, value in blocks.items()}

    def test_a_criterion_the_design_moved_past_is_listed_under_its_own_mark(self):
        self._run("done", "NA-0005", "--summary", "shipped the exporter")
        code, out, err = self._run("closed")
        self.assertEqual(code, 0, err)
        self.assertIn("%s  #2 the legacy sidecar keeps working" % cli.AC_DROPPED, out)
        self.assertIn("shipped the exporter", out)

    def test_it_is_not_shown_as_something_to_pick_up(self):
        """The follow-up lines are `->` when promoted and `-` when not. A dropped
        criterion must wear neither, or `closed` reads as a list of work waiting
        for somebody."""
        self._run("done", "NA-0005", "--summary", "shipped",
                  "--future-work", "port the docs")
        _code, out, _err = self._run("closed")
        lines = [ln.strip() for ln in out.splitlines()
                 if "legacy sidecar" in ln or "port the docs" in ln]
        self.assertEqual(lines, ["-  port the docs",
                                 "~  #2 the legacy sidecar keeps working"])

    def test_the_footer_says_nobody_is_meant_to_pick_them_up(self):
        self._run("done", "NA-0005", "--summary", "shipped")
        _code, out, _err = self._run("closed")
        self.assertIn("Set aside (~): 1", out)
        self.assertIn("nobody is meant to pick these up", out)

    def test_an_item_with_nothing_set_aside_says_nothing_about_it(self):
        write_backlog_item(self.ws, "NA-0009", title="Something else",
                           body=_acceptance((True, "it works")))
        git_commit_all(self.ws)
        self._run("done", "NA-0009", "--summary", "done")
        _code, out, _err = self._run("closed")
        self.assertNotIn("Set aside", out)

    def test_the_mark_stays_under_the_item_that_earned_it(self):
        """Two items closed the same way, one of which stopped meaning to do a
        third of what it promised.

        Asserted per item rather than per page. Every other test here searches
        the whole of stdout, and "somewhere in the output" is not what telling
        them apart means -- a renderer that printed every set-aside criterion
        under every closed item would satisfy all of them.
        """
        write_backlog_item(
            self.ws, "NA-0006", title="Publish the crate index",
            body=_acceptance((True, "the index lists every crate"),
                             (False, "the index is regenerated nightly")))
        git_commit_all(self.ws)
        self._run("done", "NA-0005", "--summary", "Exporter ships.")
        self._run("done", "NA-0006", "--summary", "Index is up.")
        blocks = self._blocks()
        # Both branches were reached: two items closed, one with nothing set
        # aside. Without this the two assertions below pass on an empty page.
        self.assertEqual(sorted(blocks), ["NA-0005", "NA-0006"])
        self.assertIn("the legacy sidecar keeps working", blocks["NA-0005"])
        self.assertNotIn(cli.AC_DROPPED, blocks["NA-0006"])

    def test_the_footer_answers_to_the_view_rather_than_to_the_workspace(self):
        """The legend is noise on an ordinary close, and most closes are ordinary.

        Scoped to a second project rather than to a second workspace, because
        the item from `setUp` is still here and still has a criterion set aside.
        A footer computed from the backlog instead of from the rows actually
        printed comes back for it, and a fresh workspace would never notice.
        """
        write_backlog_item(
            self.ws, "NA-0006", title="Publish the crate index", project="birch",
            body=_acceptance((True, "the index lists every crate")))
        git_commit_all(self.ws)
        self._run("done", "NA-0005", "--summary", "Exporter ships.")
        self._run("done", "NA-0006", "--summary", "Index is up.")
        _code, out, _err = self._run("closed", "birch")
        # A page with an item on it, not a filter that matched nothing and said
        # so -- which would suppress the footer for the wrong reason.
        self.assertIn("Index is up.", out)
        self.assertNotIn("the legacy sidecar", out)
        self.assertNotIn("Set aside", out)

    def test_it_survives_an_item_that_recorded_nothing_else(self):
        """The thinnest close there is, and the one where this matters most.

        Answering neither question writes no closing block at all, so the mark
        in the body is the only thing the file has left to say about how the
        item ended. Read off the row rather than off the closing record, which
        is why it still appears -- and the assertion on `parse_closing` is what
        keeps the fixture honest about being the thin case.
        """
        write_backlog_item(
            self.ws, "NA-0006", title="Publish the crate index",
            body=_acceptance((cli.AC_DROPPED, "the index is regenerated nightly")))
        git_commit_all(self.ws)
        # `done` offers the tick selector when there is no summary to skip it,
        # so a suite run from an interactive shell would stop here for a keypress.
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=False):
            self._run("done", "NA-0006")
        self.assertIsNone(
            items.parse_closing(
                (self.ws / "backlog" / "NA-0006.md").read_text(encoding="utf-8")),
            "the fixture wrote a closing record, so this is not the thin case")
        block = self._blocks()["NA-0006"]
        self.assertIn("(no closing record)", block)
        self.assertIn("%s  #1 the index is regenerated nightly" % cli.AC_DROPPED,
                      block)


class ShowSaysHowMuchOfThisIsYours(TempCase):
    """`show` answers "how much of this needs me" before the file is read.

    That question was being answered by reading all nine criteria and working it
    out again, every time, and the answer was almost always "two of them". The
    file itself cannot show it: the criteria are one flat list in one shape, and
    the two that need a person look exactly like the seven that do not.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        write_backlog_item(
            self.ws, "NA-0005", title="Ship the exporter",
            body=_acceptance((True, "(agent) ruff is clean"),
                             (cli.AC_DROPPED, "(agent) the legacy sidecar keeps working"),
                             (False, "(agent) the guide names the new flag"),
                             (False, "(you) the sample export reads right to you")))

    def _run(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws)] + list(args))

    def test_the_open_ones_that_need_you_are_printed_in_full(self):
        """A count cannot be acted on, and by design there are never more than
        two of these. Everything else is counted, which is the claim."""
        code, out, err = self._run("show", "NA-0005")
        self.assertEqual(code, 0, err)
        head = out.split("---")[0]
        self.assertIn("#4 (you) the sample export reads right to you", head)
        self.assertIn("1 of 1 marked (you) still open", head)
        self.assertIn("3 marked (agent)", head)

    def test_the_totals_come_from_the_same_counter_as_everywhere_else(self):
        _code, out, _err = self._run("show", "NA-0005")
        self.assertIn("Acceptance criteria: 4 · 1 ticked · 1 set aside",
                      out.split("---")[0])

    def test_the_file_is_still_printed_byte_for_byte(self):
        """★ The header is a reading of the record, never a layer over it. ★"""
        _code, out, _err = self._run("show", "NA-0005")
        raw = (self.ws / "backlog" / "NA-0005.md").read_text(encoding="utf-8")
        self.assertTrue(out.endswith(raw),
                        "the file did not come through unchanged")

    def test_unmarked_criteria_are_named_as_unclassified(self):
        """They are treated as yours everywhere else, and saying so is the honest
        version: that is a default standing in for an answer nobody gave, not a
        decision somebody made."""
        write_backlog_item(self.ws, "NA-0009", title="Older item",
                           body=_acceptance((False, "it works"), (False, "it is fast")))
        _code, out, _err = self._run("show", "NA-0009")
        self.assertIn("2 with no (agent)/(you) marker", out.split("---")[0])

    def test_an_item_with_no_criteria_gets_no_header(self):
        write_backlog_item(self.ws, "NA-0010", title="No criteria",
                           body="Nothing to verify here.")
        _code, out, _err = self._run("show", "NA-0010")
        self.assertTrue(out.startswith("---"), out[:80])


class TheDraftEitherAnswersOrIsNotOffered(TempCase):
    """Reported from real use: `done` on an item with seven commits and 0/9
    ticked offered `nextbrief · 7 commits since 2026-08-06 · AC 0/9` under
    "what actually happened?", with `=` to accept it.

    That is a true sentence and a non-answer, and `=` filed it as the summary --
    a statistic wearing a finding's clothes, which is the exact substitution the
    closing record exists to prevent. The scope is worth SEEING; it is not worth
    ACCEPTING.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def _drafts(self, item_id, **fields):
        path = write_backlog_item(self.ws, item_id, **fields)
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        ws = Workspace(root=self.ws, out=self.ws, source="test")
        return cli._closing_drafts(ws, fm, body, None)

    def test_nothing_ticked_and_several_commits_offers_no_draft(self):
        """The reported shape. No honest candidate exists, so none is offered."""
        project = self.ws / "projects" / "orchard"
        git_init(project)
        for n in range(3):
            (project / ("f%d.txt" % n)).write_text("x\n", encoding="utf-8")
            git_commit_all(project, "orchard: change %d" % n,
                           when="2026-03-1%dT09:00:00+00:00" % (n + 2))
        summary, _future, scope = self._drafts(
            "NA-0005", created_date="2026-03-01",
            body=_acceptance((False, "one"), (False, "two")))
        self.assertEqual(summary, "",
                         "a scope line was offered as the answer to what happened")
        self.assertIn("AC 0/2", scope, "the scope was lost rather than demoted")

    def test_what_is_ticked_becomes_the_draft(self):
        """The better answer that was available all along: a ticked box is a
        person saying that thing is done, in their own words. The engine can
        read the tick and cannot make it."""
        summary, _future, _scope = self._drafts(
            "NA-0006", body=_acceptance((True, "migrated the probes"),
                                        (False, "wrote it up")))
        self.assertIn("migrated the probes", summary)
        self.assertNotIn("wrote it up", summary,
                         "an unticked criterion was reported as done")

    def test_the_scope_is_never_silently_the_summary(self):
        """The property, stated once. Whatever the draft is, it is not the
        statistics line -- unless that line is the single-commit case, where the
        subject genuinely is evidence about this item."""
        project = self.ws / "projects" / "orchard"
        git_init(project)
        for n in range(3):
            (project / ("f%d.txt" % n)).write_text("x\n", encoding="utf-8")
            git_commit_all(project, "orchard: change %d" % n,
                           when="2026-03-1%dT09:00:00+00:00" % (n + 2))
        summary, _future, scope = self._drafts(
            "NA-0007", created_date="2026-03-01",
            body=_acceptance((True, "did the thing"), (False, "the other")))
        self.assertNotEqual(summary, scope)
        self.assertIn("did the thing", summary)


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
        self._close_with("Write down the hotlink fix", "Do the same for larkspur")
        out = self._run("followup", "NA-0005")[1]
        rows = [ln for ln in out.splitlines() if ln.strip().startswith(("1)", "2)"))]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertNotIn(".", row.split(")", 1)[0] + row.split(")", 1)[1][:4])
        self.assertIn("1) Write down the hotlink fix", out)

    def test_once_something_is_promoted_the_column_says_so_in_words(self):
        self._close_with("Write down the hotlink fix", "Do the same for larkspur")
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
