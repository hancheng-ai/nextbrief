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
        self.assertTrue(self.ctx.cwd.endswith("projects/orchard"))

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
