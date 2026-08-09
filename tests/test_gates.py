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


class GatedMaps(GateCase):
    """``delegated`` and ``decision_notes`` are model prose too.

    They are keyed by project id rather than shaped like an action, and that is
    the only reason they used to skip the gate -- ``decision_notes`` reaching the
    reader through BRIEF.html alone, which is the artifact ``nextbrief open``
    shows, three lines above a footer stating that every claim had passed it.
    """

    def setUp(self):
        super().setUp()
        write_snapshot(
            self.ws,
            make_snapshot(
                projects=[
                    make_project_entry(pid="lantern",
                                       has_own_daily_entry="lantern/DECISIONS.md"),
                    make_project_entry(
                        pid="atlas", blocked_by="decision",
                        open_decision={"question": "Does the rewrite ship this quarter?",
                                       "evidence_needed": "the per-tenant latency split"},
                    ),
                ]
            ),
        )

    def html(self):
        return (self.ws / "BRIEF.html").read_text(encoding="utf-8")

    def test_bare_strings_carry_no_evidence_and_are_not_rendered(self):
        # This is the shape brief.schema.json documents, so it is the shape a
        # compliant model produces: a sentence with nothing behind it.
        write_brief_json(self.ws, {
            "delegated": {"lantern": "UNGATED 3 open questions waiting on you"},
            "decision_notes": {"atlas": "UNGATED the numbers already say ship it"},
        })
        code, _, err = self.render()
        self.assertEqual(code, 0, err)

        self.assertNotIn("UNGATED", self.brief())
        self.assertNotIn("UNGATED", self.html())
        # ...and the deterministic fallback still points at the daily entry, so
        # dropping the model's line costs the reader nothing they can act on.
        self.assertIn("DECISIONS.md", self.brief())

        where = sorted(r["where"] for r in self.rejected() if r["kind"] == "no_evidence")
        self.assertEqual(where, ["decision_notes", "delegated"])
        self.assertEqual(self.runs()[-1]["dropped_claims"], 2)

    def test_an_unresolvable_source_is_dropped_from_both_artifacts(self):
        write_brief_json(self.ws, {
            "delegated": {"lantern": {"text": "FABRICATED 9 open questions",
                                      "evidence": [{"kind": "file_mtime",
                                                    "source": "lantern/NOT_A_REAL_FILE.md"}]}},
            "decision_notes": {"atlas": {"text": "FABRICATED the benchmark cleared it",
                                         "evidence": [{"kind": "file_mtime",
                                                       "source": "atlas/NOT_A_REAL_FILE.md"}]}},
        })
        self.assertEqual(self.render()[0], 0)
        self.assertNotIn("FABRICATED", self.brief())
        self.assertNotIn("FABRICATED", self.html())
        sources = sorted(r["source"] for r in self.rejected()
                         if r["kind"] == "unresolvable_evidence")
        self.assertEqual(sources, ["atlas/NOT_A_REAL_FILE.md", "lantern/NOT_A_REAL_FILE.md"])

    def test_a_sourced_note_reaches_both_artifacts_identically(self):
        # The gate is not a ban on these sections; it is a requirement. What
        # survives it must appear in both renderings, or "the two cannot drift
        # apart" is a claim about only the parts somebody remembered to check.
        write_brief_json(self.ws, {
            "delegated": {"lantern": {"text": "SOURCED 3 open questions waiting on you",
                                      "evidence": GOOD_EVIDENCE}},
            "decision_notes": {"atlas": {"text": "SOURCED the latency split already exists",
                                         "evidence": GOOD_EVIDENCE}},
        })
        self.assertEqual(self.render()[0], 0)
        for text in ("SOURCED 3 open questions waiting on you",
                     "SOURCED the latency split already exists"):
            self.assertIn(text, self.brief())
            self.assertIn(text, self.html())
        self.assertEqual(self.runs()[-1]["dropped_claims"], 0)

    def test_a_renderer_called_directly_cannot_show_ungated_text(self):
        # The gate writes what survived to its own key rather than back over the
        # input, so importing a renderer and handing it a raw brief.json is not a
        # way around gate 1.
        from nextbrief import html as html_mod
        from nextbrief.i18n import load_catalog

        snap = json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        raw = {"delegated": {"lantern": "UNGATED text"},
               "decision_notes": {"atlas": "UNGATED text"}}
        cat = load_catalog("en")
        page = html_mod.render_html(snap, raw, [], {}, {}, cat)
        md, _meta = render.render_brief(snap, raw, [], {}, {}, cat, {"conflicts": []})
        self.assertNotIn("UNGATED", page)
        self.assertNotIn("UNGATED", md)


class MalformedBrief(GateCase):
    """``brief.json`` is model output, so it is malformed sooner or later.

    The module docstring promises fail-open and the loader already tolerates a
    brief.json that does not parse at all. A shape the evidence gate did not
    expect used to be *worse* than that: an AttributeError killed the whole run,
    leaving no BRIEF.md, no success sentinel in runs.jsonl, and yesterday's brief
    on disk looking current.
    """

    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot())

    SHAPES = {
        "evidence_is_an_object": {"kind": "doc_declared", "source": "orchard/PROJECT_STATUS.md"},
        "evidence_is_a_string": "orchard/PROJECT_STATUS.md",
        "evidence_is_a_number": 5,
        "evidence_is_null": None,
        "evidence_is_a_list_of_strings": ["orchard/PROJECT_STATUS.md"],
        "evidence_is_a_list_of_lists": [["doc_declared", "orchard/PROJECT_STATUS.md"]],
        "evidence_is_deeply_nested": {"a": {"b": {"c": [{"d": {"kind": "commit"}}]}}},
        "evidence_is_a_list_of_nulls": [None, None],
        "evidence_source_is_a_list": [{"kind": "commit", "source": ["a", "b"]}],
    }

    def test_no_evidence_shape_can_kill_the_run(self):
        for name, evidence in sorted(self.SHAPES.items()):
            with self.subTest(shape=name):
                write_brief_json(self.ws, {
                    "next_actions": [{"title": "MALFORMED %s" % name,
                                      "project": "orchard", "evidence": evidence}],
                })
                code, _, err = self.render()
                self.assertEqual(code, 0, err)
                self.assertTrue((self.ws / "BRIEF.md").is_file())
                self.assertNotIn("MALFORMED", self.brief())
                # The sentinel is the only reliable liveness signal there is; a
                # run that produced a brief must leave one behind.
                self.assertTrue(self.runs()[-1]["ok"])

    def test_the_shape_is_named_in_the_rejection_log(self):
        write_brief_json(self.ws, {
            "next_actions": [{"title": "X", "project": "orchard",
                              "evidence": {"kind": "commit"}}],
        })
        self.assertEqual(self.render()[0], 0)
        entries = [r for r in self.rejected() if r["kind"] == "no_evidence"]
        self.assertEqual(len(entries), 1)
        self.assertIn("dict", entries[0]["why"])

    def test_a_non_object_evidence_entry_is_recorded_as_malformed(self):
        write_brief_json(self.ws, {
            "next_actions": [{"title": "X", "project": "orchard",
                              "evidence": ["orchard/PROJECT_STATUS.md"]}],
        })
        self.assertEqual(self.render()[0], 0)
        entries = [r for r in self.rejected() if r["kind"] == "malformed_evidence"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["where"], "next_actions")

    def test_a_section_that_is_not_an_array_does_not_kill_the_run(self):
        write_brief_json(self.ws, {"next_actions": {"title": "X"}, "project_lines": "nonsense"})
        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        self.assertTrue(self.runs()[-1]["ok"])
        wheres = sorted(r["where"] for r in self.rejected() if r["kind"] == "malformed_section")
        self.assertEqual(wheres, ["next_actions", "project_lines"])

    def test_a_claim_that_is_not_an_object_does_not_kill_the_run(self):
        write_brief_json(self.ws, {"next_actions": ["MALFORMED just a string", 7]})
        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        self.assertNotIn("MALFORMED", self.brief())
        self.assertEqual(len([r for r in self.rejected() if r["kind"] == "malformed_claim"]), 2)

    def test_a_brief_json_that_is_not_an_object_degrades_to_v0(self):
        (self.ws / "state" / "brief.json").write_text("[1, 2, 3]\n", encoding="utf-8")
        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runs()[-1]["mode"], "v0")


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

    def test_check_reports_an_out_of_bounds_edit_without_repairing_it(self):
        """`--check` must answer the question, not change the answer.

        The gate rewrites the file on disk when it reverts, which is right on a
        real run and wrong on a check -- a command asked "would a run change
        anything" that itself performs the change has made its own answer false,
        and the next check reports clean. `render --check` therefore runs the
        gate in dry-run mode.

        Placed here rather than beside the other check tests because the gate is
        inert without a git baseline, and this is the only fixture that has one.
        """
        self._rewrite("NA-0001", "status: open", "status: done")
        before = (self.ws / "backlog" / "NA-0001.md").read_text(encoding="utf-8")
        capture(render.main, ["--workspace", str(self.ws), "--check", "--no-notify"])
        self.assertEqual((self.ws / "backlog" / "NA-0001.md").read_text(encoding="utf-8"),
                         before, "--check repaired the file it was asked about")
        self.assertEqual(self._fields("NA-0001")["status"], "done")

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

    def test_deferring_is_a_human_status_too_and_is_reverted(self):
        """Parking an item takes it off the page exactly as closing it does.

        An agent that could write `deferred` could hide work nobody would be
        asked about again -- the same harm as a false completion, in a word that
        does not look like one. So the gate treats it as terminal even though the
        item is not closed.
        """
        self._rewrite("NA-0001", "status: open", "status: deferred")
        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        self.assertEqual(self._fields("NA-0001")["status"], "open")
        entries = [r for r in self.rejected() if r["kind"] == "illegal_field_write"]
        self.assertEqual([e["attempted"] for e in entries], ["deferred"])

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

    def test_a_renamed_file_is_still_compared_against_its_baseline(self):
        # Renaming is an ordinary editing action, and looking the baseline up by
        # filename alone made it a way to switch this gate off for one item while
        # the run still recorded a clean gate run.
        old = self.ws / "backlog" / "NA-0001.md"
        new = self.ws / "backlog" / "NA-0001-an-open-item.md"
        text = old.read_text(encoding="utf-8")
        new.write_text(text.replace("status: open", "status: done"), encoding="utf-8")
        os.remove(str(old))

        code, _, err = self.render()
        self.assertEqual(code, 0, err)

        fields = parse_frontmatter(new.read_text(encoding="utf-8"))[0]
        self.assertEqual(fields["status"], "open")
        entries = [r for r in self.rejected() if r["kind"] == "illegal_field_write"]
        self.assertEqual([e["field"] for e in entries], ["status"])
        self.assertEqual(self.runs()[-1]["reverted_fields"], 1)
        # And the fallback says how the baseline was found, so a rename is
        # visible rather than merely survivable.
        renamed = [r for r in self.rejected() if r["kind"] == "renamed_entry"]
        self.assertEqual([r["id"] for r in renamed], ["NA-0001"])

    def test_an_entry_with_no_baseline_at_all_is_counted_not_called_clean(self):
        # Zero reverted fields only means "clean" next to "and every entry had a
        # baseline". An entry the gate could not compare is neither clean nor
        # dirty, and runs.jsonl used to report it as the former.
        write_backlog_item(self.ws, "NA-0003", title="Brand new", priority=1)
        self.assertEqual(self.render()[0], 0)
        record = self.runs()[-1]
        self.assertEqual(record["write_gate"], "ran")
        self.assertEqual(record["reverted_fields"], 0)
        self.assertEqual(record["write_gate_unchecked"], 1)
        no_base = [r for r in self.rejected() if r["kind"] == "no_baseline"]
        self.assertEqual([r["id"] for r in no_base], ["NA-0003"])

    def test_a_fully_committed_backlog_leaves_nothing_unchecked(self):
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self.runs()[-1]["write_gate_unchecked"], 0)


@requires_git
class AHumanOnlyFieldTheBaselineNeverHadIsStillHumanOnly(GateCase):
    """★ The absence of a key was an unguarded write channel. ★

    The gate compared ``if field in old_fm and it.get(field) != old_fm.get(field)``,
    so a human-only field the committed copy did not carry was never looked at.
    Measured against this engine before the fix: an item whose baseline had no
    ``human_confirmed`` line kept ``human_confirmed: true`` after a full render,
    with ``reverted_fields: 0``, an empty ``rejected.jsonl`` and ``write_gate:
    ran``. That flag freezes the automation block against the agent and exempts
    the entry from decay, and the agent could grant it to itself. ``priority: 0``
    and ``is_next_action: true`` landed the same way, which is an agent putting
    its own entry at the top of tomorrow's page.

    ``docs/ARCHITECTURE.md`` has listed `human_confirmed` under *an agent may not
    write* since the gate was introduced. It was true for every item that already
    had the key and false for every item that did not, and nothing could tell the
    two apart -- the recurring failure here, a guard that exists, is documented,
    and is not wired to the case it names.

    Frontmatter is not uniform: ``items.new_item_text`` writes a different key set
    from ``schema/BACKLOG_TEMPLATE.md``, and hand-written entries are their own
    shape. The hole was one missing line away on any item.
    """

    ITEM = "NA-0201"
    BASE = "\n".join([
        "---",
        "id: %s" % ITEM,
        "title: No priority, no confirmation, no next-action flag",
        "project: orchard",
        "status: open",
        "blocked_by: none",
        "created_by: human",
        "updated_date: 2026-03-16",
        "---",
        "",
        "## Acceptance",
        "",
        "- [ ] It is done",
        "",
    ])

    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot())
        self._path().write_text(self.BASE, encoding="utf-8")
        git_init(self.ws)
        git_commit_all(self.ws, "an entry that never carried these keys")
        for absent in ("priority", "is_next_action", "human_confirmed"):
            self.assertNotIn("%s:" % absent, self._text(),
                             "the fixture cannot reach the case it is about")

    def _path(self):
        return self.ws / "backlog" / ("%s.md" % self.ITEM)

    def _text(self):
        return self._path().read_text(encoding="utf-8")

    def _fields(self):
        return parse_frontmatter(self._text())[0]

    def _add(self, lines):
        self._path().write_text(
            self._text().replace("blocked_by: none", "blocked_by: none\n" + lines, 1),
            encoding="utf-8")

    def test_an_added_confirmation_flag_is_reverted_by_removing_the_line(self):
        self._add("human_confirmed: true")
        code, _out, err = self.render()
        self.assertEqual(code, 0, err)
        self.assertNotIn("human_confirmed", self._text(),
                         "an agent granted itself human confirmation and kept it")
        # Removed, not nulled. `human_confirmed: null` would be a value the next
        # run reads as an answer somebody gave.
        self.assertNotIn("null", self._text())

    def test_added_priority_and_next_action_go_the_same_way(self):
        self._add("priority: 0\nis_next_action: true")
        self.assertEqual(self.render()[0], 0)
        fields = self._fields()
        self.assertIsNone(fields.get("priority"),
                          "an agent set its own priority on an item that had none")
        self.assertIsNone(fields.get("is_next_action"))

    def test_every_removal_is_logged_and_counted(self):
        # Silent repair is the failure one level up: the reader is told how many
        # fields were reverted, and `rejected.jsonl` is where the attempt stays
        # recoverable.
        self._add("priority: 0\nis_next_action: true\nhuman_confirmed: true")
        self.assertEqual(self.render()[0], 0)
        entries = [r for r in self.rejected() if r["kind"] == "illegal_field_write"]
        self.assertEqual(sorted(e["field"] for e in entries),
                         ["human_confirmed", "is_next_action", "priority"])
        for entry in entries:
            self.assertEqual(entry["restored"], "removed")
            self.assertIn("no such key", entry["why"])
        self.assertEqual(self.runs()[-1]["reverted_fields"], 3)
        self.assertIn("Reverted 3", self.brief())

    def test_the_rest_of_the_frontmatter_and_the_body_survive(self):
        # `remove_fields` deletes lines out of a file a person owns. Taking the
        # wrong one is a worse outcome than the write it is reverting.
        self._add("human_confirmed: true")
        self.assertEqual(self.render()[0], 0)
        fields = self._fields()
        self.assertEqual(fields["id"], self.ITEM)
        self.assertEqual(fields["project"], "orchard")
        self.assertEqual(fields["blocked_by"], "none")
        self.assertIn("- [ ] It is done", self._text())

    def test_a_field_the_baseline_does_carry_is_untouched_by_this_path(self):
        # The control. `created_by: human` is in HEAD and unchanged, so it must
        # not be swept up by a rule about keys that are not there.
        self._add("human_confirmed: true")
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self._fields()["created_by"], "human")

    def test_check_mode_reports_it_without_removing_anything(self):
        # `--check` answers "would a run change anything". A check that performs
        # the repair has falsified its own answer, and the next one reports clean.
        self._add("human_confirmed: true")
        before = self._text()
        capture(render.main, ["--workspace", str(self.ws), "--check", "--no-notify"])
        self.assertEqual(self._text(), before,
                         "--check repaired the file it was asked about")


@requires_git
class AProposalSurvivesTheGateEvenWhenTheKeyIsNew(GateCase):
    """★ The one write the nightly pass exists to make must reach the disk. ★

    ``proposed_status`` is the whole proposal channel: an agent may never close an
    item, only suggest, and the brief lists the suggestion under *waiting for your
    confirmation*. Everything upstream of that -- the prompt, the digest's
    criteria counts, the renderer's section -- is inert if this gate reverts the
    field on the way through.

    The case that would have made it inert quietly is an item whose frontmatter
    has no ``proposed_status`` key **at all**. Nine of the entries in the author's
    own backlog are that shape, because ``items.new_item_text`` does not write the
    key and ``cli._mark`` deliberately refuses to add a null one -- reading
    tolerates the absence, so nothing looks wrong. A gate that treated a *new* key
    differently from a changed one would have switched this feature off for the
    nine newest items and for nothing else, which is the kind of gap nobody finds
    by reading.

    Both shapes are here on purpose. An assertion about the added key alone would
    still pass if the gate stopped allowing proposals altogether.
    """

    ABSENT, PRESENT = "NA-0101", "NA-0102"

    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot())
        # No `proposed_status` key: `write_backlog_item` writes none by default,
        # which is the same shape the CLI mints.
        write_backlog_item(self.ws, self.ABSENT, title="Key absent from HEAD")
        write_backlog_item(self.ws, self.PRESENT, title="Key present and null",
                           proposed_status="null")
        git_init(self.ws)
        git_commit_all(self.ws, "workspace baseline")
        self.assertNotIn("proposed_status", self._text(self.ABSENT),
                         "the fixture cannot reach the case it is about")
        self.assertIn("proposed_status: null", self._text(self.PRESENT))

    def _path(self, item_id):
        return self.ws / "backlog" / ("%s.md" % item_id)

    def _text(self, item_id):
        return self._path(item_id).read_text(encoding="utf-8")

    def _fields(self, item_id):
        return parse_frontmatter(self._text(item_id))[0]

    def _propose(self):
        """Write the field the way the nightly pass does: an edit to the file."""
        absent = self._text(self.ABSENT)
        self._path(self.ABSENT).write_text(
            absent.replace("status: open", "status: open\nproposed_status: done", 1),
            encoding="utf-8")
        self._path(self.PRESENT).write_text(
            self._text(self.PRESENT).replace("proposed_status: null",
                                             "proposed_status: done"),
            encoding="utf-8")

    def test_an_added_proposal_is_still_on_disk_after_the_gate(self):
        self._propose()
        code, _out, err = self.render()
        self.assertEqual(code, 0, err)
        # `.get`, not `[...]`. The way this gate reverts an added key is by
        # removing the line, so the failure mode being guarded against deletes
        # the key -- and a KeyError here would be the assertion never running.
        self.assertEqual(self._fields(self.ABSENT).get("proposed_status"), "done",
                         "the write gate rolled back a proposal on an item whose "
                         "baseline never carried the field")
        self.assertEqual(self._fields(self.PRESENT).get("proposed_status"), "done",
                         "the write gate rolled back a proposal on an item whose "
                         "baseline carried the field as null")

    def test_no_proposal_is_logged_as_an_illegal_write(self):
        self._propose()
        self.assertEqual(self.render()[0], 0)
        offending = [r for r in self.rejected()
                     if r["kind"] == "illegal_field_write"
                     and r["field"] == "proposed_status"]
        self.assertEqual(offending, [],
                         "the write gate rolled back a proposal into rejected.jsonl")
        self.assertEqual(self.runs()[-1]["reverted_fields"], 0)
        # And the gate really ran, so "nothing reverted" is a finding rather than
        # a gate that was never in a position to object.
        self.assertEqual(self.runs()[-1]["write_gate"], "ran")
        self.assertEqual(self.runs()[-1]["write_gate_unchecked"], 0)

    def test_both_proposals_reach_the_reader(self):
        # The end of the chain. A field that survives the gate and never reaches
        # the page is `proposed_status` back where it started: written, unread.
        self._propose()
        self.assertEqual(self.render()[0], 0)
        brief = self.brief()
        self.assertIn("Waiting for your confirmation", brief)
        for item_id in (self.ABSENT, self.PRESENT):
            self.assertIn(item_id, brief.split("Waiting for your confirmation", 1)[1],
                          "%s never reached the confirmation section" % item_id)

    def test_the_status_beside_it_is_still_reverted(self):
        # The control. `proposed_status` passing has to be a property of that
        # field, not of a gate that has stopped looking at this file.
        self._propose()
        path = self._path(self.ABSENT)
        path.write_text(self._text(self.ABSENT).replace("status: open", "status: done", 1),
                        encoding="utf-8")
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self._fields(self.ABSENT)["status"], "open")
        self.assertEqual(self._fields(self.ABSENT)["proposed_status"], "done")


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
