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

from nextbrief.launch import LaunchContext, LaunchError, build_context, tr
from nextbrief.paths import resolve_workspace

# A complete backlog entry in the schema's own vocabulary, shared with the
# frontmatter tests so that both read the same document a user would write.
ITEM = fixture("backlog-item.md")


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
        self.assertNotIn("legacy sidecar", self._section("**Done when**:"))

    def test_the_criteria_that_do_apply_are_still_there(self):
        section = self._section("**Done when**:")
        self.assertIn("- [x] #1 the exporter writes one file per crate", section)
        self.assertIn("- [ ] #3 the migration guide names the new flag", section)

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
        done = self._section(prompt, "**Done when**:")
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
