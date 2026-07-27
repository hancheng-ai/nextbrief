"""The four gates.

Each gate gets a crafted input that it must catch, and each test asserts on both
halves of the contract: what the reader sees, and what the log records. A gate
that silently drops something is only half a gate -- the dropped thing has to be
recoverable, or the next thing it drops will be one you needed.

The gates are deliberately *not* uniform. Three of them remove or revert; the
non-goals gate only flags, because silently deleting a good suggestion is worse
than one visible false positive. That asymmetry is tested as an asymmetry.
"""

from __future__ import annotations

import json
import os
import unittest

from helpers import (
    TempCase,
    capture,
    git_commit_all,
    git_init,
    make_project_entry,
    make_snapshot,
    read_jsonl,
    requires_git,
    write_backlog_item,
    write_brief_json,
    write_snapshot,
)

from nextbrief import render
from nextbrief.frontmatter import parse_frontmatter

# A claim that resolves. Used as the control in every gate test so that a
# "nothing was rendered" failure cannot be mistaken for a gate working.
GOOD_EVIDENCE = [{"kind": "doc_declared", "source": "orchard/PROJECT_STATUS.md"}]


class GateCase(TempCase):
    def setUp(self):
        super().setUp()
        # No repository inside the project tree: these tests hand the renderer a
        # handcrafted snapshot, and a nested repo would only complicate the
        # workspace's own git baseline in the write-permission tests.
        self.ws = self.workspace(with_git=False)

    def render(self, *args):
        return capture(render.main, ["--workspace", str(self.ws), "--no-notify"] + list(args))

    def brief(self):
        return (self.ws / "BRIEF.md").read_text(encoding="utf-8")

    def rejected(self):
        return read_jsonl(self.ws / "log" / "rejected.jsonl")

    def deferred(self):
        return read_jsonl(self.ws / "log" / "deferred.jsonl")

    def runs(self):
        return read_jsonl(self.ws / "log" / "runs.jsonl")


# ---------------------------------------------------------------------------
# Gate 1 -- evidence
# ---------------------------------------------------------------------------


class EvidenceGate(GateCase):
    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot())

    def test_unresolvable_source_is_not_rendered_and_is_logged(self):
        write_brief_json(
            self.ws,
            {
                "next_actions": [
                    {
                        "title": "Split the tenancy report per tenant",
                        "project": "orchard",
                        "evidence": GOOD_EVIDENCE,
                    },
                    {
                        # Nothing in the snapshot's evidence index answers to this
                        # path. That is what fabrication looks like from here.
                        "title": "FABRICATED shipped the tenancy rewrite",
                        "project": "orchard",
                        "evidence": [
                            {"kind": "file_mtime", "source": "orchard/docs/NOT_A_REAL_FILE.md"}
                        ],
                    },
                ]
            },
        )
        code, _, err = self.render()
        self.assertEqual(code, 0, err)

        brief = self.brief()
        self.assertIn("Split the tenancy report per tenant", brief)
        self.assertNotIn("FABRICATED", brief)
        # There is no "render it with a warning" option: a warning next to a
        # fabricated sentence still reads as a fact.
        self.assertNotIn("NOT_A_REAL_FILE", brief)

        entries = [r for r in self.rejected() if r["kind"] == "unresolvable_evidence"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], "orchard/docs/NOT_A_REAL_FILE.md")
        self.assertIn("FABRICATED", entries[0]["text"])
        self.assertEqual(entries[0]["where"], "next_actions")
        self.assertIn("at", entries[0])

        self.assertEqual(self.runs()[-1]["dropped_claims"], 1)
        self.assertIn("Dropped 1 claim", brief)

    def test_a_claim_with_no_evidence_at_all_is_dropped(self):
        write_brief_json(
            self.ws, {"next_actions": [{"title": "UNSOURCED do the thing", "project": "orchard"}]}
        )
        self.assertEqual(self.render()[0], 0)
        self.assertNotIn("UNSOURCED", self.brief())
        self.assertEqual([r["kind"] for r in self.rejected() if r["kind"] == "no_evidence"],
                         ["no_evidence"])

    def test_kind_none_is_allowed_only_for_the_no_signal_phrasing(self):
        write_brief_json(
            self.ws,
            {
                "project_lines": [
                    {"project": "orchard", "text": "no signal since 2026-01-04",
                     "next": "ALLOWED pick a next step", "evidence": [{"kind": "none"}]},
                    {"project": "orchard", "text": "REPHRASED it is going well",
                     "next": "REPHRASED ship it", "evidence": [{"kind": "none"}]},
                ]
            },
        )
        self.assertEqual(self.render()[0], 0)
        kinds = [r["kind"] for r in self.rejected()]
        self.assertIn("bad_none", kinds)
        self.assertNotIn("REPHRASED", self.brief())
        self.assertIn("ALLOWED pick a next step", self.brief())

    def test_a_file_cannot_be_cited_as_commit_grade_evidence(self):
        # Only commit and session have their kind checked: those two assert
        # visibly more confidence than "some file was touched".
        write_brief_json(
            self.ws,
            {
                "next_actions": [
                    {
                        "title": "MISLABELLED nine commits landed this week",
                        "project": "orchard",
                        "evidence": [{"kind": "commit", "source": "orchard/README.md"}],
                    }
                ]
            },
        )
        self.assertEqual(self.render()[0], 0)
        self.assertNotIn("MISLABELLED", self.brief())
        entries = [r for r in self.rejected() if r["kind"] == "evidence_kind_mismatch"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["declared"], "commit")
        self.assertEqual(entries[0]["actual"], ["file_mtime"])

    def test_an_imprecise_but_resolvable_kind_is_kept(self):
        # The gate exists to stop fabrication, not imprecision. A status document
        # legitimately supports several kinds at once.
        write_brief_json(
            self.ws,
            {
                "next_actions": [
                    {
                        "title": "KEPT read the status document",
                        "project": "orchard",
                        "evidence": [
                            {"kind": "human", "source": "orchard/PROJECT_STATUS.md"}
                        ],
                    }
                ]
            },
        )
        self.assertEqual(self.render()[0], 0)
        self.assertIn("KEPT read the status document", self.brief())
        self.assertEqual([r for r in self.rejected() if r["kind"] != "gate_disabled"], [])

    def test_backlog_entries_are_citable(self):
        # Sensing never loads the backlog, so the renderer indexes it before the
        # gate runs; otherwise "this is already on the list" would be unsayable.
        write_backlog_item(self.ws, "NA-0001", title="An item that exists")
        write_brief_json(
            self.ws,
            {
                "next_actions": [
                    {
                        "title": "CITED already on the backlog",
                        "project": "orchard",
                        "evidence": [{"kind": "human", "source": "NA-0001"}],
                    }
                ]
            },
        )
        self.assertEqual(self.render()[0], 0)
        self.assertIn("CITED already on the backlog", self.brief())


# ---------------------------------------------------------------------------
# Gate 2 -- non-goals: flag, never block
# ---------------------------------------------------------------------------


class NonGoalGate(GateCase):
    NON_GOAL = "Build a mobile app"

    def setUp(self):
        super().setUp()
        write_snapshot(
            self.ws,
            make_snapshot(projects=[make_project_entry(non_goals=[self.NON_GOAL, "Add a plugin system"])]),
        )

    def test_a_proposal_matching_a_non_goal_is_flagged_and_still_rendered(self):
        write_brief_json(
            self.ws,
            {
                "next_actions": [
                    {
                        "title": "Build a mobile app for the field team",
                        "project": "orchard",
                        "why": "Support keeps asking",
                        "evidence": GOOD_EVIDENCE,
                    }
                ]
            },
        )
        code, _, err = self.render()
        self.assertEqual(code, 0, err)

        brief = self.brief()
        self.assertIn("Build a mobile app for the field team", brief)
        self.assertIn("declared non-goal", brief)
        self.assertIn(self.NON_GOAL, brief)

        # ...and, unlike every other gate, nothing is logged as rejected, because
        # nothing was.
        self.assertEqual([r for r in self.rejected() if r["kind"] != "gate_disabled"], [])
        self.assertEqual(self.runs()[-1]["dropped_claims"], 0)

    def test_the_flag_reaches_the_html_too(self):
        write_brief_json(
            self.ws,
            {
                "next_actions": [
                    {
                        "title": "Build a mobile app for the field team",
                        "project": "orchard",
                        "evidence": GOOD_EVIDENCE,
                    }
                ]
            },
        )
        self.assertEqual(self.render()[0], 0)
        # Computed once, rendered twice: the HTML receives the already-gated data.
        self.assertIn(self.NON_GOAL, (self.ws / "BRIEF.html").read_text(encoding="utf-8"))

    def test_matching_normalises_separators_and_case(self):
        self.assertEqual(render.non_goal_flag("build a mobile-app now", [self.NON_GOAL]),
                         self.NON_GOAL)
        self.assertEqual(render.non_goal_flag("Build   a  mobile app", [self.NON_GOAL]),
                         self.NON_GOAL)
        self.assertIsNone(render.non_goal_flag("Ship the tenancy rewrite", [self.NON_GOAL]))
        self.assertIsNone(render.non_goal_flag("anything", []))


# ---------------------------------------------------------------------------
# Gate 3 -- write permissions
# ---------------------------------------------------------------------------


@requires_git
class WritePermissionGate(GateCase):
    """The mechanical enforcement of "no agent may close your items"."""

    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot())
        write_backlog_item(self.ws, "NA-0001", title="An open item", status="open", priority=2)
        write_backlog_item(self.ws, "NA-0002", title="Another open item", status="open", priority=3)
        git_init(self.ws)
        git_commit_all(self.ws, "workspace baseline")

    def _fields(self, item_id):
        path = self.ws / "backlog" / ("%s.md" % item_id)
        return parse_frontmatter(path.read_text(encoding="utf-8"))[0]

    def _rewrite(self, item_id, old, new):
        path = self.ws / "backlog" / ("%s.md" % item_id)
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def test_a_terminal_status_written_by_an_agent_is_reverted(self):
        self._rewrite("NA-0001", "status: open", "status: done")
        code, _, err = self.render()
        self.assertEqual(code, 0, err)

        self.assertEqual(self._fields("NA-0001")["status"], "open")
        entries = [r for r in self.rejected() if r["kind"] == "illegal_field_write"]
        self.assertEqual([e["field"] for e in entries], ["status"])
        self.assertEqual(entries[0]["attempted"], "done")
        self.assertEqual(entries[0]["reverted_to"], "open")
        self.assertEqual(entries[0]["restored"], "file")
        self.assertEqual(self.runs()[-1]["reverted_fields"], 1)
        self.assertEqual(self.runs()[-1]["write_gate"], "ran")
        self.assertIn("Reverted 1", self.brief())

    def test_a_changed_priority_is_reverted(self):
        # Priority is a commitment, not an observation. An agent may retract its
        # own guesses; it may never reorder yours.
        self._rewrite("NA-0002", "priority: 3", "priority: 1")
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self._fields("NA-0002")["priority"], 3)
        fields = [r["field"] for r in self.rejected() if r["kind"] == "illegal_field_write"]
        self.assertEqual(fields, ["priority"])

    def test_both_at_once(self):
        self._rewrite("NA-0001", "status: open", "status: done")
        self._rewrite("NA-0002", "priority: 3", "priority: 1")
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self._fields("NA-0001")["status"], "open")
        self.assertEqual(self._fields("NA-0002")["priority"], 3)
        self.assertEqual(self.runs()[-1]["reverted_fields"], 2)

    def test_a_legal_edit_is_left_alone(self):
        # The gate is field-level, not file-level: an agent updating a field it is
        # allowed to write must not be reverted along with everything else.
        self._rewrite("NA-0001", "title: An open item", "title: An open item, reworded")
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self._fields("NA-0001")["title"], "An open item, reworded")
        self.assertEqual([r for r in self.rejected() if r["kind"] == "illegal_field_write"], [])

    def test_a_human_status_already_in_the_baseline_is_not_touched(self):
        # `nextbrief done` commits immediately for exactly this reason: once the
        # closure is the baseline, the gate has nothing to object to.
        self._rewrite("NA-0001", "status: open", "status: done")
        git_commit_all(self.ws, "human: close NA-0001")
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self._fields("NA-0001")["status"], "done")
        self.assertEqual([r for r in self.rejected() if r["kind"] == "illegal_field_write"], [])

    def test_a_new_entry_has_no_baseline_and_is_accepted(self):
        write_backlog_item(self.ws, "NA-0003", title="Brand new", priority=1)
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self._fields("NA-0003")["priority"], 1)


class WriteGateDegradation(GateCase):
    """With no git at all the gate must say so, loudly.

    A tri-state outcome exists because an integer could not distinguish "checked
    everything, found nothing" from "never ran" -- and a machine with no git
    binary reported a clean run forever.
    """

    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot())
        write_backlog_item(self.ws, "NA-0001", title="An open item")

    def test_missing_git_binary_is_recorded_not_silently_clean(self):
        empty_path = self.tmp / "empty-bin"
        empty_path.mkdir()
        os.environ["PATH"] = str(empty_path)

        code, out, err = self.render()
        self.assertEqual(code, 0, err)

        disabled = [r for r in self.rejected() if r["kind"] == "gate_disabled"]
        self.assertEqual(len(disabled), 1)
        self.assertEqual(disabled[0]["gate"], "write_permissions")
        self.assertIn("git", disabled[0]["why"])

        record = self.runs()[-1]
        self.assertEqual(record["write_gate"], "no_repo")
        self.assertEqual(record["write_gate_detail"], "git-missing")
        # Zero reverted fields is only meaningful next to "the gate ran".
        self.assertEqual(record["reverted_fields"], 0)

        # And the reader is told, rather than being shown a brief that merely
        # happens to contain no warnings.
        self.assertIn("write-permission gate did not run", self.brief())
        self.assertIn("no git binary", self.brief())
        self.assertIn("did not run", err)

    @requires_git
    def test_a_workspace_outside_any_repository_is_also_recorded(self):
        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        disabled = [r for r in self.rejected() if r["kind"] == "gate_disabled"]
        self.assertEqual(len(disabled), 1)
        self.assertEqual(self.runs()[-1]["write_gate_detail"], "no-repo")
        self.assertIn("not inside a git repository", self.brief())


# ---------------------------------------------------------------------------
# Gate 4 -- caps
# ---------------------------------------------------------------------------


class CapsGate(GateCase):
    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot())

    def _actions(self, n):
        return [
            {
                "title": "Proposal number %d" % i,
                "project": "orchard",
                "evidence": GOOD_EVIDENCE,
            }
            for i in range(1, n + 1)
        ]

    def test_overflow_is_deferred_not_dropped(self):
        write_brief_json(self.ws, {"next_actions": self._actions(5)})
        code, _, err = self.render()
        self.assertEqual(code, 0, err)

        brief = self.brief()
        for kept in (1, 2, 3):
            self.assertIn("Proposal number %d" % kept, brief)
        for cut in (4, 5):
            self.assertNotIn("Proposal number %d" % cut, brief)

        deferred = self.deferred()
        self.assertEqual(len(deferred), 2)
        self.assertEqual([d["item"]["title"] for d in deferred],
                         ["Proposal number 4", "Proposal number 5"])
        for record in deferred:
            self.assertEqual(record["section"], "next_actions")
            self.assertEqual(record["why"], "over caps.next_actions")
            # The stamp comes from the snapshot, so a re-render of the same
            # snapshot writes a record that is recognisably the same run.
            self.assertEqual(record["at"], "2026-03-16T12:00:00")

        self.assertEqual(self.runs()[-1]["deferred"], 2)
        self.assertIn("Deferred past the caps: 2", brief)

    def test_the_cap_is_the_whole_portfolio_not_per_project(self):
        # Six projects times five items each is how these systems die.
        actions = self._actions(3) + [
            {"title": "Proposal number 4", "project": "kiln", "evidence": GOOD_EVIDENCE}
        ]
        write_brief_json(self.ws, {"next_actions": actions})
        self.assertEqual(self.render()[0], 0)
        self.assertNotIn("Proposal number 4", self.brief())
        self.assertEqual(len(self.deferred()), 1)

    def test_the_line_ceiling_truncates_and_says_so(self):
        # Enforced by the renderer rather than by asking a model to be brief.
        config = json.loads((self.ws / "config.jsonc").read_text(encoding="utf-8").split("\n", 1)[1])
        config["caps"]["brief_max_lines"] = 12
        (self.ws / "config.jsonc").write_text(json.dumps(config, indent=2), encoding="utf-8")
        write_brief_json(self.ws, {"next_actions": self._actions(3)})
        self.assertEqual(self.render()[0], 0)
        brief = self.brief()
        self.assertLessEqual(len(brief.splitlines()), 12)
        self.assertIn("line ceiling", brief)
        self.assertGreater(self.runs()[-1]["truncated_lines"], 0)


if __name__ == "__main__":
    unittest.main()
