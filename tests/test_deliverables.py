"""A deliverable lying in the tree, an item reading 0/n, and no edge between them.

2026-08-12: two design spikes were delivered -- 45KB and 25KB of prose, one of
them already committed -- and both items read as not started. The evidence was
sitting in the workspace the whole time, under a name that says which item it
belongs to: `docs/design/NA-0033-reconciler.md`. **The convention was already in
practice; nothing read it.**

Two candidate signals were measured before either was built, and only one
survived: `<id>-*` files scored 2 hits / 2 true positives, while "a path
mentioned in the item's prose exists" scored 7 / 0 -- every one of them a
README or a CLAUDE.md named in passing. A warning at that precision is worse
than no warning, because it teaches the reader to scroll past the one that
matters.

So the tests here are in four groups, and the middle two are the ones that keep
the precision:

* :class:`ADeliverableNobodyTicked` -- the signal fires.
* :class:`TheItemFileIsNotItsOwnDeliverable` -- `backlog/NA-0049-*.md` follows
  the same convention as the deliverable, and is not one.
* :class:`TheScanStaysInsideTheWorkspace` -- another workspace's `NA-0001` is
  not this workspace's `NA-0001`.
* :class:`TheDayTwoDesignSpikesReadAsNotStarted` -- the two real items, in the
  state they were actually in that day.

And one that guards the line `done` prints: a filename is context, never a
draft summary somebody can accept by reflex.
"""

from __future__ import annotations

import unittest
from unittest import mock

from helpers import TempCase, capture, write_backlog_item

from nextbrief import cli
from nextbrief.frontmatter import parse_frontmatter
from nextbrief.paths import Workspace


def acceptance(*criteria):
    """A body whose acceptance criteria are exactly ``criteria``.

    Each entry is ``(ticked, text)``. Written out here rather than imported
    from another test module so that a fixture this file depends on cannot be
    changed by an edit to a test about something else.
    """
    lines = ["<!-- AC:BEGIN -->"]
    lines += ["- [%s] #%d %s" % ("x" if ok else " ", i, text)
              for i, (ok, text) in enumerate(criteria, 1)]
    lines.append("<!-- AC:END -->")
    return "\n".join(lines)


def write_file(path, text="a deliverable, in prose\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class DeliverableCase(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def warnings(self):
        return cli._delivered_but_unticked(Workspace(self.ws, self.ws, "test"), None)

    def item(self, item_id="NA-9999", ticks=(False, False), slug="the-thing-itself",
             **fields):
        """One backlog entry, named the way real ones are named.

        ``slug`` is not decoration. A real entry is `NA-0049-some-words.md`,
        which matches `NA-0049-*` exactly as its deliverable does -- so a
        fixture named `NA-0049.md` would quietly stop testing the collision
        this whole signal has to survive.
        """
        path = write_backlog_item(
            self.ws, item_id,
            body=acceptance(*[(t, "(agent) criterion %d" % i)
                              for i, t in enumerate(ticks, 1)]),
            **fields)
        named = path.with_name("%s-%s.md" % (item_id, slug))
        path.rename(named)
        return named


class ADeliverableNobodyTicked(DeliverableCase):
    """The one the whole item exists for."""

    def test_a_file_named_after_the_item_is_reported(self):
        self.item()
        write_file(self.ws / "docs" / "design" / "NA-9999-reconciler.md")
        got = self.warnings()
        self.assertEqual(len(got), 1,
                         "a deliverable is on disk and the item reads 0/2; check "
                         "said %r" % (got,))
        self.assertIn("NA-9999", got[0])
        self.assertIn("docs/design/NA-9999-reconciler.md", got[0],
                      "the path is the actionable part -- a warning that only "
                      "names the id sends the reader looking for the file")

    def test_one_tick_is_enough_to_stay_quiet(self):
        """The signal is *zero* progress against a deliverable that exists.

        One tick means somebody has already been here with their eyes open, and
        a warning about an item under active hand is an alarm that always rings.
        """
        self.item(ticks=(True, False))
        write_file(self.ws / "docs" / "design" / "NA-9999-reconciler.md")
        self.assertEqual(self.warnings(), [])

    def test_a_closed_item_is_quiet(self):
        """A deliverable next to a closed item is the normal, finished state --
        it is what closing an item is supposed to leave behind."""
        self.item(status="done")
        write_file(self.ws / "docs" / "design" / "NA-9999-reconciler.md")
        self.assertEqual(self.warnings(), [])

    def test_an_item_with_no_criteria_at_all_is_quiet(self):
        """"Not one box ticked" needs boxes. At 0/0 the sentence is not false so
        much as empty, and there is nothing the reader could go and do about it."""
        self.item(ticks=())
        write_file(self.ws / "docs" / "design" / "NA-9999-reconciler.md")
        self.assertEqual(self.warnings(), [])

    def test_an_item_with_no_deliverable_is_quiet(self):
        self.item()
        self.assertEqual(self.warnings(), [])

    def test_a_file_merely_mentioning_the_id_is_not_a_deliverable(self):
        """The measured signal is the *name*, not the contents.

        Grepping for the id was the other candidate, and on the real workspace
        it was the one that scored 7 hits and 0 true positives.
        """
        self.item()
        write_file(self.ws / "notes" / "monday.md", "NA-9999 came up again\n")
        self.assertEqual(self.warnings(), [])

    def test_the_id_has_to_start_the_name(self):
        """`ARCHIVE-NA-9999-notes.md` is somebody else's naming scheme, and
        matching it anywhere in the name is how a signal starts drifting."""
        self.item()
        write_file(self.ws / "docs" / "ARCHIVE-NA-9999-notes.md")
        self.assertEqual(self.warnings(), [])


class TheItemFileIsNotItsOwnDeliverable(DeliverableCase):
    """★ The item file follows the very convention being read. ★

    `backlog/NA-0049-deliverable-and-item-never-meet.md` matches `NA-0049-*`
    exactly as the deliverable does. Without this exclusion every item in every
    workspace reports itself on the first run -- an alarm that fires on
    everything, which is the failure mode this signal was measured to avoid.
    """

    def test_the_backlog_entry_does_not_report_itself(self):
        self.item()
        self.assertEqual(self.warnings(), [],
                         "the item's own file was read as its deliverable")

    def test_every_item_in_the_workspace_stays_quiet(self):
        for n in range(1, 6):
            self.item("NA-000%d" % n)
        self.assertEqual(self.warnings(), [])


class TheScanStaysInsideTheWorkspace(DeliverableCase):
    """Ids are only unique within one workspace, and there is more than one.

    `examples/workspace/backlog/NA-0001-*` in the engine's own repository is an
    invented item in an invented workspace. It has nothing to do with the
    NA-0001 in the workspace being checked, and a scan that reaches it reports
    six false positives at once -- which is what an early scan of `~/Projects`
    actually did.
    """

    def test_another_workspaces_backlog_inside_this_tree_is_not_a_deliverable(self):
        self.item("NA-0001")
        write_file(self.ws / "examples" / "workspace" / "backlog"
                   / "NA-0001-orchard-tenancy-latency-split.md")
        self.assertEqual(self.warnings(), [],
                         "another workspace's item file was read as this "
                         "workspace's deliverable")

    def test_a_nested_workspaces_whole_backlog_stays_out(self):
        nested = self.ws / "examples" / "workspace" / "backlog"
        for name in ("NA-0001-orchard-tenancy-latency-split.md",
                     "NA-0002-lantern-march-post-draft.md",
                     "NA-0003-tidepool-getting-started-page.md"):
            write_file(nested / name)
        for n in (1, 2, 3):
            self.item("NA-000%d" % n)
        self.assertEqual(self.warnings(), [])

    def test_a_file_outside_the_workspace_root_is_never_seen(self):
        """The scan is rooted at the workspace, not at the portfolio. A sibling
        repository's file is not evidence about this workspace's item."""
        self.item()
        write_file(self.tmp / "somewhere-else" / "NA-9999-reconciler.md")
        self.assertEqual(self.warnings(), [])


class TheClosingReferenceLine(DeliverableCase):
    """`done` had one place to look for evidence and it was the wrong repository.

    `project: nextbrief` says what the item is *about*; for a design spike the
    output lands somewhere else entirely, so the reference line counted 51
    commits belonging to other work. The file named after the item is the fact
    that was actually available.
    """

    def drafts(self, path):
        ws = cli.resolve_workspace(str(self.ws), None)
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        return cli._closing_drafts(ws, fm or {}, body or "", None)

    def test_the_reference_line_names_the_deliverable(self):
        path = self.item()
        write_file(self.ws / "docs" / "design" / "NA-9999-reconciler.md")
        _summary, _future, scope = self.drafts(path)
        self.assertIn("docs/design/NA-9999-reconciler.md", scope)

    def test_the_deliverable_is_never_offered_as_the_summary_draft(self):
        """★ A filename is not an answer to "what actually happened?" ★

        The reference line is shown; the draft is what `=` files under a
        person's name. A path put on the wrong side of that line is a machine
        sentence signed by a human, which is the failure the whole record
        exists to prevent.
        """
        path = self.item()
        write_file(self.ws / "docs" / "design" / "NA-9999-reconciler.md")
        summary, _future, _scope = self.drafts(path)
        self.assertNotIn("NA-9999-reconciler.md", summary)

    def test_done_prints_the_reference_line(self):
        self.item()
        write_file(self.ws / "docs" / "design" / "NA-9999-reconciler.md")
        typed = iter(["", "", ""])
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", lambda _p="": next(typed)):
            code, out, err = capture(cli.main,
                                     ["--workspace", str(self.ws), "done", "NA-9999"])
        self.assertEqual(code, 0, err)
        self.assertIn("docs/design/NA-9999-reconciler.md", out)

    def test_an_item_with_no_deliverable_says_nothing_about_one(self):
        path = self.item()
        _summary, _future, scope = self.drafts(path)
        self.assertNotIn("NA-9999-", scope)


class TheDayTwoDesignSpikesReadAsNotStarted(DeliverableCase):
    """The regression, in the state of 2026-08-12 rather than a made-up one.

    Both items were `status: open` with every box unticked -- NA-0033 at 0/6 and
    NA-0029 at 0/4 -- while `pm/docs/design/` held a file named after each. Both
    have to be reported, and reported by name, because "one of them showed up"
    is how a signal that only works on the easy case passes for working.
    """

    def setUp(self):
        super().setUp()
        # `status: open`, every box unticked, is what `git show` reports for both
        # files on the morning of 2026-08-12 -- not a state chosen to be easy.
        self.item("NA-0033", ticks=(False,) * 6, slug="reconciler-design-spike",
                  status="open", project="nextbrief",
                  title="nextbrief: reconciler design spike")
        self.item("NA-0029", ticks=(False,) * 4, slug="nextbrief-decisions-first-class",
                  status="open", project="nextbrief",
                  title="nextbrief: decisions as first-class objects")

    def deliver(self):
        write_file(self.ws / "docs" / "design" / "NA-0033-reconciler.md")
        write_file(self.ws / "docs" / "design" / "NA-0029-decisions-schema.md")

    def test_both_are_reported(self):
        self.deliver()
        got = "\n".join(self.warnings())
        self.assertIn("NA-0033", got)
        self.assertIn("NA-0029", got)
        self.assertIn("docs/design/NA-0033-reconciler.md", got)
        self.assertIn("docs/design/NA-0029-decisions-schema.md", got)

    def test_check_prints_them(self):
        self.deliver()
        _code, _out, err = capture(cli.main, ["--workspace", str(self.ws), "check"])
        self.assertIn("NA-0033", err)
        self.assertIn("NA-0029", err)

    def test_the_exit_code_is_the_same_with_and_without_the_deliverables(self):
        """A warning, not exit 3. Exit 3 is a signal to a scheduler, and running
        the pipeline again cannot tick a box that needs a person to read a file.

        Asserted as a difference rather than against a constant: a fresh
        workspace is legitimately out of date, and pinning the number here would
        test the snapshot rather than this warning.
        """
        before, _out, _err = capture(cli.main, ["--workspace", str(self.ws), "check"])
        self.deliver()
        after, _out, _err = capture(cli.main, ["--workspace", str(self.ws), "check"])
        self.assertEqual(before, after,
                         "the deliverable warning moved the exit code, and a "
                         "scheduler now re-runs the pipeline over it")


if __name__ == "__main__":
    unittest.main()
