"""`settle` -- recording a decision about a criterion without closing the item.

The gap this fills, measured on this portfolio 2026-08-20: there was no command
that recorded a decision about an acceptance criterion on an OPEN item. `ok`,
`done`, `drop` and `defer` are item-level; criterion-level settlement existed
only inside `done`, which is terminal -- you could only settle at the moment you
closed -- and inside `do`, which spawns an agent session.

So a ruling made in conversation reached the file by three hops and two actors:
the owner decided, an agent typed prose into NOTES, and the box was ticked days
later inside `done`. The tick and the reason were recorded separately, by
different actors, at different times, and nothing connected them.

`review` was the obvious home -- its own docstring says "the questions only a
person can answer" -- but its answers are project-keyed, land in
`annotations.jsonc`, and are fixed multiple choice. A criterion is item-keyed,
belongs in the backlog file the engine already writes to, and its substance is
whatever a person wrote. So this reuses `done`'s selector instead: same
keystrokes, nothing new to learn, and `done` is untouched.
"""

from __future__ import annotations

import unittest
from unittest import mock

from helpers import TempCase, capture, write_backlog_item

from nextbrief import cli
from nextbrief.frontmatter import parse_frontmatter

BODY = """<!-- SECTION:NEXT_ACTION:BEGIN -->
Do the thing.
<!-- SECTION:NEXT_ACTION:END -->

<!-- AC:BEGIN -->
- [ ] #1 (you) decide the direction
- [ ] #2 (agent) prove it with a test
- [x] #3 (agent) already settled, and must stay that way
<!-- AC:END -->

<!-- SECTION:NOTES:BEGIN -->
Existing note.
<!-- SECTION:NOTES:END -->
"""


class SettleRecordsADecisionWithoutClosingTheItem(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        write_backlog_item(self.ws, "NA-0001", body=BODY, status="open")

    def _path(self):
        return next(self.ws.glob("backlog/NA-0001*.md"))

    def _text(self):
        return self._path().read_text(encoding="utf-8")

    def _marks(self):
        return [ln.strip()[3] for ln in self._text().splitlines()
                if ln.strip().startswith("- [")]

    def _settle(self, picked=(), dropped=(), argv=()):
        # A person at the keyboard: the selector itself has its own tests, so
        # what is under test here is what the command does with an answer.
        with mock.patch.object(cli, "_ask_ticks",
                               return_value=(list(picked), list(dropped))), \
                mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(cli, "input", create=True, return_value=""):
            return capture(cli.main, ["settle", "NA-0001",
                                      "--workspace", str(self.ws)] + list(argv))

    def test_a_tick_lands_without_closing_the_item(self):
        idx = [i for i, ln in enumerate(self._text().splitlines())
               if "#1 (you)" in ln][0]
        code, _out, err = self._settle(picked=[idx])
        self.assertEqual(code, 0, err)
        self.assertEqual(self._marks(), ["x", " ", "x"])
        fm, _ = parse_frontmatter(self._text())
        self.assertEqual(fm.get("status"), "open",
                         "settling a criterion closed the item, which is `done`'s "
                         "job and nobody else's")

    def test_the_reason_is_recorded_beside_the_tick_in_one_write(self):
        # The whole point: the mark and why it was made are one act by one
        # person, not two records made days apart by two actors.
        idx = [i for i, ln in enumerate(self._text().splitlines())
               if "#1 (you)" in ln][0]
        code, _out, err = self._settle(picked=[idx],
                                       argv=["--note", "union by sha, ruled today"])
        self.assertEqual(code, 0, err)
        text = self._text()
        self.assertIn("union by sha, ruled today", text)
        self.assertIn("Existing note.", text, "the existing NOTES were overwritten")
        self.assertLess(text.index("Existing note."),
                        text.index("union by sha, ruled today"),
                        "the note was prepended; NOTES is a log and appends")

    def test_an_already_settled_criterion_is_never_touched(self):
        """A write happens, and the already-marked line survives it.

        The first version of this called `_settle(picked=[])`, which returns at
        "nothing marked" before `_apply_marks` is ever reached -- so it asserted
        that an untouched file was untouched, and would have stayed green however
        badly the writer behaved. It has to mark something to mean anything.
        """
        idx = [i for i, ln in enumerate(self._text().splitlines())
               if "#1 (you)" in ln][0]
        code, _out, err = self._settle(picked=[idx])
        self.assertEqual(code, 0, err)
        self.assertEqual(self._marks(), ["x", " ", "x"],
                         "the writer touched a line it was not handed")

    def test_marking_nothing_writes_nothing(self):
        before = self._text()
        code, _out, err = self._settle()
        self.assertEqual(code, 0, err)
        self.assertEqual(self._text(), before)

    def test_a_closed_item_is_refused_and_says_which_command_owns_it(self):
        write_backlog_item(self.ws, "NA-0002", body=BODY, status="done")
        with mock.patch.object(cli, "_ask_ticks", return_value=([], [])):
            code, out, err = capture(cli.main, ["settle", "NA-0002",
                                                "--workspace", str(self.ws)])
        self.assertNotEqual(code, 0)
        self.assertIn("done", (out + err).lower())

    def test_it_refuses_to_ask_when_nobody_is_there_and_says_what_it_would_ask(self):
        """A scheduled run that blocks on a prompt produces nothing, silently.

        `review` already refuses on a non-tty for this reason; this follows it.
        Saying what it *would* have asked is what makes the refusal a report
        rather than a shrug.
        """
        # `_ask_ticks` is replaced with a tripwire rather than left alone: with
        # the guard removed it would sit on a real stdin read and this test would
        # HANG instead of failing. A guard whose mutation hangs CI is not a guard.
        def never(*_a, **_k):
            raise AssertionError("asked for input with nobody at the keyboard")

        with mock.patch.object(cli.sys.stdin, "isatty", return_value=False), \
                mock.patch.object(cli, "_ask_ticks", side_effect=never):
            code, out, err = capture(cli.main, ["settle", "NA-0001",
                                                "--workspace", str(self.ws)])
        self.assertEqual(code, 0, err)
        self.assertIn("#1", out + err)
        self.assertEqual(self._marks(), [" ", " ", "x"], "it wrote without asking")


class SettleTakesTheDecisionAsAnArgument(TempCase):
    """The TUI is wrong for the case it was built for.

    Feedback from the owner, 2026-08-20, after the first real use: the process is
    not intuitive, and it does not survive an item with more than one criterion
    only a person can decide.

    Both come from the same place. A decision reached in conversation is already
    made by the time the command runs, and the selector asks for it to be
    re-expressed by keyboard -- four keys, each criterion clipped to one terminal
    line, and a free-text prompt arriving after the marks with nothing tying it
    to them. The artifact shows the consequence: the note `settle` wrote on
    NA-0065 names no criterion at all. One `(you)` box and you can infer it; three
    and you cannot, and a tick and a set-aside in the same pass would share one
    reason while being opposite decisions.

    So the decision becomes an argument. `--set '#3=x: why'` carries a mark AND
    its own reason, per criterion, repeatable -- which is a form an agent can
    draft after the conversation and a person can read, edit and run. The
    selector stays for browsing; nothing about it changes.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        write_backlog_item(self.ws, "NA-0001", body=BODY, status="open")

    def _path(self):
        return next(self.ws.glob("backlog/NA-0001*.md"))

    def _text(self):
        return self._path().read_text(encoding="utf-8")

    def _marks(self):
        return [ln.strip()[3] for ln in self._text().splitlines()
                if ln.strip().startswith("- [")]

    def _run(self, *argv):
        # Deliberately NOT a terminal: the whole point is that this path works
        # where the selector cannot -- a pipe, a script, a command pasted back.
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=False):
            return capture(cli.main, ["settle", "NA-0001",
                                      "--workspace", str(self.ws)] + list(argv))

    def test_a_decision_can_be_given_as_an_argument_with_no_terminal(self):
        code, _out, err = self._run("--set", "#1=x: ruled in conversation")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._marks(), ["x", " ", "x"])
        self.assertIn("ruled in conversation", self._text())

    def test_each_criterion_carries_its_own_reason(self):
        """The failure this class exists for.

        Two decisions in one pass, and they are not the same decision. A single
        shared note cannot say which is which, and one of these is a tick while
        the other is a set-aside.
        """
        code, _out, err = self._run("--set", "#1=x: this one is settled",
                                    "--set", "#2=~: superseded by the new design")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._marks(), ["x", "~", "x"])
        text = self._text()
        for anchor, reason in (("#1", "this one is settled"),
                               ("#2", "superseded by the new design")):
            line = [ln for ln in text.splitlines() if reason in ln]
            self.assertEqual(len(line), 1, "reason %r not written once" % reason)
            self.assertIn(anchor, line[0],
                          "the reason does not name the criterion it belongs to, "
                          "which is the defect the artifact showed")

    def test_a_reason_may_contain_a_colon(self):
        # `#N=mark: reason` splits once. A reason is prose and prose has colons.
        self._run("--set", "#1=x: because of this: and that")
        self.assertIn("because of this: and that", self._text())

    def test_an_unknown_criterion_is_refused_and_names_the_real_ones(self):
        code, out, err = self._run("--set", "#9=x: nope")
        self.assertNotEqual(code, 0)
        self.assertIn("#9", out + err)
        self.assertEqual(self._marks(), [" ", " ", "x"], "it wrote anyway")

    def test_an_already_marked_criterion_is_refused_rather_than_overwritten(self):
        # #3 is already `[x]`. Clearing or restating a mark would let this command
        # take back a statement its author made, which `_apply_marks` refuses to
        # do and this must refuse before it gets there.
        code, out, err = self._run("--set", "#3=~: changed my mind")
        self.assertNotEqual(code, 0)
        self.assertEqual(self._marks()[2], "x")
        self.assertIn("#3", out + err)

    def test_an_unknown_mark_is_refused(self):
        code, _out, err = self._run("--set", "#1=q: what is q")
        self.assertNotEqual(code, 0)
        self.assertEqual(self._marks(), [" ", " ", "x"])

    def test_nothing_is_written_when_any_one_of_them_is_bad(self):
        """All or nothing. A half-applied batch is a state nobody asked for, and
        the one that failed is the one you would not notice."""
        code, _out, _err = self._run("--set", "#1=x: fine",
                                     "--set", "#9=x: not fine")
        self.assertNotEqual(code, 0)
        self.assertEqual(self._marks(), [" ", " ", "x"])


    def test_a_fully_settled_item_still_says_which_one_was_already_marked(self):
        """`--set` must reach its own refusal.

        With every box marked, the command used to return "no open criteria --
        nothing to settle" before it had read `--set` at all. That is an answer
        to a question nobody asked: the person named a criterion, and what they
        need to hear is that THAT one already carries a mark.
        """
        self._run("--set", "#1=x: a", "--set", "#2=x: b")     # nothing open left
        code, out, err = self._run("--set", "#2=~: changed my mind")
        self.assertNotEqual(code, 0)
        self.assertIn("#2", out + err)
        self.assertNotIn("nothing to settle", (out + err).lower())



class TheInteractivePassAsksPerCriterion(TempCase):
    """One prompt per decision, each naming what it is about.

    `--set` fixed the scripted path; it did not fix the one a person actually
    sits in front of. The interactive pass still asked a single free-text
    question for the whole batch, which is the same defect wearing the same
    clothes: two decisions, one reason, and nothing saying which is which.

    So the prompt is now per marked criterion, and it prints that criterion's
    text in full first. Full, not clipped: the selector cuts each row to one
    terminal line so the list stays readable, and confirming a truncated
    sentence is half of why the pass did not feel trustworthy.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        write_backlog_item(self.ws, "NA-0001", body=BODY, status="open")

    def _text(self):
        return next(self.ws.glob("backlog/NA-0001*.md")).read_text(encoding="utf-8")

    def _indexes(self, *needles):
        lines = self._text().splitlines()
        return [next(i for i, ln in enumerate(lines) if n in ln) for n in needles]

    def _settle(self, picked=(), dropped=(), answers=(), argv=()):
        prompts = []

        def fake_input(prompt=""):
            prompts.append(prompt)
            return list(answers)[len(prompts) - 1] if len(answers) >= len(prompts) else ""

        with mock.patch.object(cli, "_ask_ticks",
                               return_value=(list(picked), list(dropped))), \
                mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(cli, "input", create=True, side_effect=fake_input):
            code, out, err = capture(cli.main, ["settle", "NA-0001",
                                                "--workspace", str(self.ws)] + list(argv))
        return code, out + err, prompts

    def test_each_marked_criterion_gets_its_own_prompt_and_its_own_reason(self):
        one, two = self._indexes("#1 (you)", "#2 (agent)")
        code, _out, prompts = self._settle(
            picked=[one], dropped=[two],
            answers=["the direction is B", "superseded by the new design"])
        self.assertEqual(code, 0)
        self.assertEqual(len(prompts), 2, "one prompt was asked for two decisions")
        text = self._text()
        for anchor, reason in (("#1", "the direction is B"),
                               ("#2", "superseded by the new design")):
            line = [ln for ln in text.splitlines() if reason in ln]
            self.assertEqual(len(line), 1, "reason %r not written once" % reason)
            self.assertIn(anchor, line[0])

    def test_the_prompt_shows_the_criterion_in_full(self):
        one, = self._indexes("#1 (you)")
        _code, out, _prompts = self._settle(picked=[one], answers=["because"])
        self.assertIn("decide the direction", out,
                      "the prompt never showed which criterion it was asking about")

    def test_enter_skips_one_reason_without_losing_its_mark(self):
        one, two = self._indexes("#1 (you)", "#2 (agent)")
        self._settle(picked=[one, two], answers=["", "only this one has a reason"])
        text = self._text()
        marks = [ln.strip()[3] for ln in text.splitlines() if ln.strip().startswith("- [")]
        self.assertEqual(marks, ["x", "x", "x"], "a skipped reason lost its mark")
        self.assertIn("only this one has a reason", text)
        self.assertEqual(len([ln for ln in text.splitlines()
                              if "settled by hand" in ln or "手动定的" in ln]), 1)

    def test_note_given_up_front_is_applied_to_each_one_it_covers(self):
        # `--note` stays: sometimes one sentence really does cover the batch.
        # It is still written per criterion, so the record never has to be
        # untangled later.
        one, two = self._indexes("#1 (you)", "#2 (agent)")
        _code, _out, prompts = self._settle(picked=[one, two],
                                            argv=["--note", "same call for both"])
        self.assertEqual(prompts, [], "--note was given and it asked anyway")
        anchored = [ln for ln in self._text().splitlines() if "same call for both" in ln]
        self.assertEqual(len(anchored), 2)
        self.assertTrue(any("#1" in ln for ln in anchored))
        self.assertTrue(any("#2" in ln for ln in anchored))

if __name__ == "__main__":
    unittest.main()
