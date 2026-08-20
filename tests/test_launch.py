"""``nextbrief do <id>``: the context handed to an agent session.

The premise is that you should not have to re-explain a task to an agent just to
start working on it. So the assertions are about what survives the trip from the
backlog file into the opening message -- in particular the two things a paraphrase
would quietly destroy: the acceptance criteria and the "only I may do this" list.

Directories are *proposed*, never chosen. This module returns a list; the picker
lives in the CLI and is not exercised here.
"""

from __future__ import annotations

import unittest

from helpers import TempCase, fixture, write_backlog_item

from nextbrief.items import HUMAN_ONLY_STATUSES
from nextbrief.launch import LaunchContext, LaunchError, build_context, tr
from nextbrief.paths import resolve_workspace

# A complete backlog entry in the schema's own vocabulary, shared with the
# frontmatter tests so that both read the same document a user would write.
ITEM = fixture("backlog-item.md")

# The headings the criteria are dealt out under. Written once here because every
# assertion below is about *which* list a line landed in, and a test that spells
# a heading itself is one that keeps passing after the split stops happening.
SETTLED = "**Already settled**"
OPEN_YOURS = "**Done when** -- still open, and yours to settle:"
OPEN_MINE = "**Done when -- but mine, not yours**"
SET_ASIDE = "**No longer applies**"
SETTLEMENT = "**Settle what you can before you start, not on the way out.**"


class Context(TempCase):
    def setUp(self):
        super().setUp()
        self.ws_dir = self.workspace(with_git=False)
        self.item = self.ws_dir / "backlog" / "NA-0001.md"
        self.item.write_text(ITEM, encoding="utf-8")
        self.ws = resolve_workspace(str(self.ws_dir))
        self.ctx = build_context(self.ws, self.item)

    def test_returns_a_context_not_shell_assignments(self):
        # The original printed assignments for `eval`; a dataclass makes a
        # quoting bug impossible.
        self.assertIsInstance(self.ctx, LaunchContext)
        self.assertEqual(self.ctx.title, "Split the tenancy latency report per tenant")
        self.assertEqual(self.ctx.project, "Orchard")

    def test_directories_are_proposed_with_a_reason_each(self):
        self.assertTrue(self.ctx.dirs)
        for _directory, why in self.ctx.dirs:
            self.assertTrue(why)
        self.assertEqual(self.ctx.cwd, self.ctx.dirs[0][0])
        # The project's own directory comes first: most likely to be right.
        #
        # Compared as a whole path rather than by a posix-shaped suffix. `cwd`
        # is a real directory the launcher changes into and prints to the
        # reader, so on Windows its separators are backslashes and
        # `endswith("projects/orchard")` was asserting about the separator
        # instead of about which directory got chosen.
        self.assertEqual(self.ctx.cwd, str(self.ws_dir / "projects" / "orchard"))

    def test_a_relative_registry_root_resolves_against_the_workspace(self):
        # `"root": "./projects"` is what the shipped example declares, so this is
        # the ordinary case and not an edge one.
        self.assertEqual(self.ctx.root, str(self.ws_dir / "projects"))

    def test_the_prompt_carries_the_briefing_the_entry_already_holds(self):
        prompt = self.ctx.prompt
        self.assertIn("NA-0001", prompt)
        self.assertIn("Re-run the harness", prompt)
        self.assertIn("Decide whether the tail matters", prompt)
        self.assertIn("Read one existing result file", prompt)
        self.assertIn("orchard/PROJECT_STATUS.md", prompt)

    def test_acceptance_criteria_are_copied_verbatim(self):
        # Paraphrasing the definition of done is a way of moving the goalposts.
        self.assertIn("- [ ] p95 is reported per tenant", self.ctx.prompt)
        self.assertIn("- [ ] the old aggregate is still available", self.ctx.prompt)

    def test_a_stale_source_document_is_declared_as_such(self):
        self.assertIn("2026-03-10", self.ctx.prompt)

    def test_the_closing_stays_with_the_human(self):
        self.assertIn("nextbrief done NA-0001", self.ctx.prompt)


class ACriterionTheDesignMovedPastIsNotHandedOverAsWork(TempCase):
    """★ The prompt is where an abandoned goal is cheapest to restart. ★

    `- [~]` was carried into the session prompt verbatim, under the heading
    "**Done when**". That is an instruction to go and do it -- and `~` reads as
    "in progress" to anyone who does not know this convention, which is most
    readers of this prompt. It is the same failure the mark was added to stop,
    one step earlier in the chain than the follow-up drafts where it was caught
    the first time: there, an abandoned goal minted a task; here, it opens a
    session that starts on it.

    The line is still shown, because deleting it would leave an agent free to
    propose the abandoned thing back as a fresh idea. It is shown under a heading
    that says what it is.
    """

    def setUp(self):
        super().setUp()
        self.ws_dir = self.workspace(with_git=False)
        path = write_backlog_item(
            self.ws_dir, "NA-0004",
            body="\n".join(["<!-- AC:BEGIN -->",
                            "- [x] #1 the exporter writes one file per crate",
                            "- [~] #2 the legacy sidecar keeps working",
                            "- [ ] #3 the migration guide names the new flag",
                            "<!-- AC:END -->"]))
        self.prompt = build_context(resolve_workspace(str(self.ws_dir)), path).prompt

    def _section(self, heading):
        """Everything after ``heading`` and before the next blank line."""
        rest = self.prompt.split(heading, 1)[1]
        return rest.split("\n\n", 1)[0]

    def test_it_is_not_listed_under_done_when(self):
        self.assertNotIn("legacy sidecar", self._section(SETTLED))
        self.assertNotIn("legacy sidecar", self._section(OPEN_MINE))

    def test_the_criteria_that_do_apply_are_still_there(self):
        # One ticked, one open, and they are dealt into different lists now --
        # see `TheOpeningMessageSaysWhichBoxesAlreadyHold`. Both are still here,
        # which is what this has always been about.
        self.assertIn("- [x] #1 the exporter writes one file per crate",
                      self._section(SETTLED))
        self.assertIn("- [ ] #3 the migration guide names the new flag",
                      self._section(OPEN_MINE))

    def test_it_is_shown_under_a_heading_saying_not_to_do_it(self):
        """Kept rather than hidden. An agent that cannot see the withdrawn goal
        is an agent free to suggest it back as a good idea."""
        self.assertIn("No longer applies", self.prompt)
        self.assertIn("- [~] #2 the legacy sidecar keeps working",
                      self._section("propose them back:"))

    def test_an_item_with_nothing_set_aside_gains_no_heading(self):
        path = write_backlog_item(
            self.ws_dir, "NA-0005",
            body="\n".join(["<!-- AC:BEGIN -->",
                            "- [ ] #1 it works",
                            "<!-- AC:END -->"]))
        prompt = build_context(resolve_workspace(str(self.ws_dir)), path).prompt
        self.assertNotIn("No longer applies", prompt)
        self.assertIn("- [ ] #1 it works", prompt)


class ProseAboutCriteriaIsNotHandedToAnAgent(TempCase):
    """★ The most expensive of the three readers NA-0051 touched. ★

    `ac_lines` claims in its docstring to be the one parser, and names `launch`
    as a reason it lives in `items` at all: "the session prompt quotes the
    criteria at an agent, and a criterion that was set aside must not arrive as
    part of the definition of done". It then scanned the whole body for `- [`
    itself, which is the second copy that docstring says must not exist.

    So a sentence in NOTES *showing what a criterion looks like* arrived under
    "**Done when**" -- not a miscount in a report somebody skims, but an
    instruction handed to an agent. `future_work` mints a task somebody may yet
    read and reject; this one opens the session that starts on it.
    """

    DECOY = "\n".join([
        "<!-- SECTION:NOTES:BEGIN -->",
        "A criterion is written like this:",
        "",
        "    - [ ] #9 (you) decide the posture: advice or enforcement",
        "<!-- SECTION:NOTES:END -->",
    ])

    def setUp(self):
        super().setUp()
        self.ws_dir = self.workspace(with_git=False)

    def _prompt(self, item_id, body):
        path = write_backlog_item(self.ws_dir, item_id, body=body)
        return build_context(resolve_workspace(str(self.ws_dir)), path).prompt

    def _section(self, prompt, heading):
        return prompt.split(heading, 1)[1].split("\n\n", 1)[0]

    def test_it_does_not_arrive_under_done_when(self):
        prompt = self._prompt("NA-0006", "\n".join([
            "<!-- AC:BEGIN -->",
            "- [ ] #1 the exporter writes one file per crate",
            "<!-- AC:END -->", self.DECOY]))
        done = self._section(prompt, OPEN_MINE)
        # The real criterion did arrive, so the absence below is the parser's
        # doing and not an empty section.
        self.assertIn("- [ ] #1 the exporter writes one file per crate", done)
        self.assertNotIn("decide the posture", prompt)

    def test_it_does_not_arrive_under_no_longer_applies_either(self):
        """A phantom `[~]` in prose would otherwise mint the opposite mistake:
        a heading telling an agent not to do something nobody proposed."""
        prompt = self._prompt("NA-0007", "\n".join([
            "<!-- AC:BEGIN -->",
            "- [ ] #1 the exporter writes one file per crate",
            "<!-- AC:END -->",
            "<!-- SECTION:NOTES:BEGIN -->",
            "A withdrawn criterion is written like this:",
            "",
            "    - [~] #9 the legacy sidecar keeps working",
            "<!-- SECTION:NOTES:END -->"]))
        self.assertNotIn("No longer applies", prompt)
        self.assertNotIn("legacy sidecar", prompt)


class TheOpeningMessageSaysWhichBoxesAlreadyHold(TempCase):
    """★ "Which of these still need doing" is a question the engine can answer. ★

    One list headed "Done when", carrying `[x]` and `[ ]` together, handed that
    question back to the reader every session -- and an item is routinely older
    than the work. Criteria come true while nobody is looking; a session that
    cannot see which ones starts by redoing them, and the marks it would have to
    read to find out are the same marks the engine already parsed to build the
    list.

    So the deal is: settled ones say so, open ones say whose they are, and the
    withdrawn ones keep the heading they already had.
    """

    BODY = "\n".join([
        "<!-- AC:BEGIN -->",
        "- [x] #1 (agent) the exporter writes one file per crate",
        "- [ ] #2 (agent) `ruff check` is clean",
        "- [ ] #3 (you) the tail is worth a per-tenant schema",
        "- [~] #4 (agent) the legacy sidecar keeps working",
        "<!-- AC:END -->",
    ])

    def setUp(self):
        super().setUp()
        self.ws_dir = self.workspace(with_git=False)
        self.prompt = self._prompt("NA-0010", self.BODY)

    def _prompt(self, item_id, body):
        path = write_backlog_item(self.ws_dir, item_id, body=body)
        return build_context(resolve_workspace(str(self.ws_dir)), path).prompt

    def _section(self, heading, prompt=None):
        """Everything after ``heading`` and before the next blank line."""
        rest = (prompt if prompt is not None else self.prompt).split(heading, 1)[1]
        return rest.split("\n\n", 1)[0]

    def test_a_ticked_criterion_is_shown_as_already_settled(self):
        self.assertIn("- [x] #1 (agent) the exporter writes one file per crate",
                      self._section(SETTLED))

    def test_it_is_not_also_listed_as_work(self):
        # The whole point. A settled criterion under "Done when" reads as an
        # instruction to go and do it, which is what this had been printing.
        self.assertNotIn("one file per crate", self._section(OPEN_YOURS))
        self.assertNotIn("one file per crate", self._section(OPEN_MINE))

    def test_an_open_agent_criterion_is_handed_over_as_work(self):
        self.assertIn("- [ ] #2 (agent) `ruff check` is clean",
                      self._section(OPEN_YOURS))

    def test_a_withdrawn_criterion_keeps_its_own_heading(self):
        self.assertIn("- [~] #4 (agent) the legacy sidecar keeps working",
                      self._section(SET_ASIDE))

    def test_every_criterion_still_arrives_somewhere(self):
        """★ Three lists must not become a way of losing one. ★

        Splitting a list is exactly how a criterion goes missing without anything
        looking wrong: the prompt still reads as a complete definition of done,
        one heading shorter. So this counts the lines rather than trusting the
        sections above to be exhaustive.
        """
        for text in ("one file per crate", "`ruff check` is clean",
                     "per-tenant schema", "legacy sidecar"):
            self.assertIn(text, self.prompt)

    def test_an_item_with_nothing_settled_gains_no_settled_heading(self):
        prompt = self._prompt("NA-0011", "\n".join([
            "<!-- AC:BEGIN -->",
            "- [ ] #1 (agent) it works",
            "<!-- AC:END -->"]))
        self.assertNotIn(SETTLED, prompt)
        self.assertIn("- [ ] #1 (agent) it works", self._section(OPEN_YOURS, prompt))

    def test_an_item_with_everything_settled_asks_for_no_pass(self):
        # Nothing open is nothing to check. An instruction to go and verify
        # things with nothing in scope is the sort of line people learn to skip,
        # and the next one they skip is the one that mattered.
        prompt = self._prompt("NA-0012", "\n".join([
            "<!-- AC:BEGIN -->",
            "- [x] #1 (agent) it works",
            "<!-- AC:END -->"]))
        self.assertIn(SETTLED, prompt)
        self.assertNotIn(SETTLEMENT, prompt)


class TheSettlementPassIsToldWhatItMayNotTouch(TempCase):
    """★ The two things a pass that ticks boxes must never do. ★

    An agent that may tick `(agent)` boxes is one keystroke from two failures
    that look identical to success on the page: ticking a box only the author can
    settle, and writing the status that takes the item off the page altogether.
    The first is the second in a smaller box -- `pm/CLAUDE.md` rule 2b says so in
    those words -- and neither leaves a mark that reads as wrong afterwards.

    Half of the boundary is structural and half is a sentence, deliberately:

    * **Structural.** A criterion that is not the agent's is dealt into a list of
      its own, under a heading that says not to tick it. Nothing has to be
      applied correctly for that to hold, and `items.needs_you` -- the same
      predicate `done` asks its questions with -- decides membership, so an
      unmarked criterion counts as the author's here exactly as it does there.
    * **A sentence.** "Do not write a terminal status" cannot be enforced by the
      shape of a list, so it is said where the ticking is asked for rather than
      in a document the session may never open.
    """

    def setUp(self):
        super().setUp()
        self.ws_dir = self.workspace(with_git=False)
        self.prompt = self._prompt("NA-0013", "\n".join([
            "<!-- AC:BEGIN -->",
            "- [ ] #1 (agent) `ruff check` is clean",
            "- [ ] #2 (you) the tail is worth a per-tenant schema",
            "- [ ] #3 nobody has classified this one",
            "<!-- AC:END -->"]))

    def _prompt(self, item_id, body):
        path = write_backlog_item(self.ws_dir, item_id, body=body)
        return build_context(resolve_workspace(str(self.ws_dir)), path).prompt

    def _section(self, heading):
        return self.prompt.split(heading, 1)[1].split("\n\n", 1)[0]

    def test_a_you_criterion_is_never_offered_as_work(self):
        self.assertNotIn("per-tenant schema", self._section(OPEN_YOURS))
        self.assertIn("- [ ] #2 (you) the tail is worth a per-tenant schema",
                      self._section(OPEN_MINE))

    def test_an_unmarked_criterion_lands_on_the_same_side_as_a_you_one(self):
        """★ Unmarked is the author's, here as in `done`. ★

        Reading the absence of a marker as "the agent's" would hand every
        criterion written before the marker existed to something entitled to tick
        it -- silently, and across a whole backlog in one move. `items.needs_you`
        holds the reasoning; this is the assertion that `launch` asks it rather
        than re-deciding.
        """
        self.assertNotIn("nobody has classified", self._section(OPEN_YOURS))
        self.assertIn("- [ ] #3 nobody has classified this one",
                      self._section(OPEN_MINE))

    def test_the_heading_over_them_says_not_to_tick_them(self):
        # Shown rather than hidden: an agent that cannot see the criterion cannot
        # tell the author it looks settled, and that report is worth having. What
        # it may not do is tick it.
        self.assertIn("Never tick one of these", self._section(OPEN_MINE))

    def test_the_pass_is_told_not_to_write_a_terminal_status(self):
        """Read off ``HUMAN_ONLY_STATUSES`` rather than spelled here, so a
        fourth status that takes an item off the page cannot be added without
        this line growing to name it."""
        self.assertIn(SETTLEMENT, self.prompt)
        for status in HUMAN_ONLY_STATUSES:
            self.assertIn(status, self._section(SETTLEMENT))
        self.assertGreaterEqual(len(HUMAN_ONLY_STATUSES), 3)

    def test_the_pass_asks_for_evidence_rather_than_a_verdict(self):
        # A tick with nothing under it is the false completion this whole
        # boundary exists to refuse, so the instruction is about what to write
        # down, not about being careful.
        section = self._section(SETTLEMENT)
        self.assertIn("NOTES", section)
        self.assertIn("what you ran and what you saw", section)


class Failures(TempCase):
    def setUp(self):
        super().setUp()
        self.ws_dir = self.workspace(with_git=False)
        self.ws = resolve_workspace(str(self.ws_dir))

    def test_a_missing_file(self):
        with self.assertRaises(LaunchError):
            build_context(self.ws, self.ws_dir / "backlog" / "absent.md")

    def test_a_file_without_frontmatter(self):
        path = write_backlog_item(self.ws_dir, "NA-0002")
        path.write_text("# No frontmatter here\n", encoding="utf-8")
        with self.assertRaises(LaunchError) as caught:
            build_context(self.ws, path)
        self.assertIn("frontmatter", str(caught.exception))

    def test_an_unregistered_project_still_launches(self):
        # An item can name a project the registry has not caught up with; that is
        # a reason to fall back to the workspace root, not to refuse.
        path = write_backlog_item(self.ws_dir, "NA-0003", project="not-registered")
        ctx = build_context(self.ws, path)
        self.assertEqual(ctx.project, "not-registered")
        self.assertTrue(ctx.cwd)


class Translation(unittest.TestCase):
    def test_tr_falls_back_to_the_english_at_the_call_site(self):
        # `Catalog.t` renders an unknown key as the key itself, which is right for
        # a rendered brief and useless in an interactive prompt.
        self.assertEqual(tr(None, "cli.do.hint", "Pick one"), "Pick one")
        self.assertEqual(tr(None, "x", "Hello {name}", name="world"), "Hello world")
        self.assertEqual(tr(None, "x", "Hello {name}"), "Hello {name}")


if __name__ == "__main__":
    unittest.main()
