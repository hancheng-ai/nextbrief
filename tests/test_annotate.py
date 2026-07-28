"""The question channel, and the overlay answers land in.

The thing under test is a refusal as much as a feature. Nothing here may ask for
a number, nothing may write to `registry.jsonc`, and nothing may turn an
unanswered question into data — which is the mistake discovery made and had to
have walked back, one release earlier, in this same codebase.
"""

from __future__ import annotations

import json
import unittest

from helpers import AS_OF, TempCase, capture, make_project_entry, make_snapshot, write_snapshot

from nextbrief import annotate, cli, render, sense
from nextbrief.annotate import (
    ANNOTATIONS_NAME,
    apply_annotations,
    derive_effort,
    load_annotations,
    needs_annotating,
    record_answers,
)
from nextbrief.paths import resolve_workspace


def entry(pid, ice=None, days=1, files=100):
    p = make_project_entry(pid=pid, ice=ice)
    p["evidence"] = dict(p["evidence"], days_since=days)
    p["fs"] = dict(p["fs"], total_files=files)
    return p


class WhoGetsAsked(unittest.TestCase):
    def test_a_project_with_no_ice_and_some_activity_is_asked_about(self):
        snap = make_snapshot(projects=[entry("newthing", ice=None)])
        self.assertEqual([p["id"] for p in needs_annotating(snap)], ["newthing"])

    def test_a_fully_answered_project_is_not(self):
        snap = make_snapshot(projects=[
            entry("done", ice={"impact": 4, "confidence": 3, "effort": 2})])
        self.assertEqual(needs_annotating(snap), [])

    def test_a_partially_answered_project_still_is(self):
        snap = make_snapshot(projects=[entry("half", ice={"impact": 4})])
        self.assertEqual([p["id"] for p in needs_annotating(snap)], ["half"])

    def test_a_project_with_no_evidence_at_all_is_left_alone(self):
        # Asking someone to rank a directory they have never touched is filing,
        # not prioritisation.
        p = entry("dead", ice=None)
        p["evidence"] = dict(p["evidence"], days_since=None)
        self.assertEqual(needs_annotating(make_snapshot(projects=[p])), [])

    def test_the_workspace_itself_is_never_asked_about(self):
        snap = make_snapshot(projects=[entry("pm", ice=None)])
        self.assertEqual(needs_annotating(snap, {"pm"}), [])

    def test_most_recently_active_is_asked_first(self):
        # The person answering has the best answer for what they touched today,
        # and may well stop after the first one.
        snap = make_snapshot(projects=[entry("old", ice=None, days=30),
                                       entry("today", ice=None, days=0),
                                       entry("mid", ice=None, days=5)])
        self.assertEqual([p["id"] for p in needs_annotating(snap)],
                         ["today", "mid", "old"])


class EffortIsMeasured(unittest.TestCase):
    def test_bands_follow_size(self):
        self.assertEqual(derive_effort(entry("a", files=10)), 1)
        self.assertEqual(derive_effort(entry("a", files=150)), 2)
        self.assertEqual(derive_effort(entry("a", files=900)), 3)
        self.assertEqual(derive_effort(entry("a", files=4000)), 4)
        self.assertEqual(derive_effort(entry("a", files=90000)), 5)

    def test_no_question_ever_asks_for_effort(self):
        # The axis where a human guess is worse than a count.
        self.assertNotIn("effort", [q.field for q in annotate.QUESTIONS])

    def test_no_question_asks_for_a_number(self):
        # Every answer is a consequence with a fixed set of choices.
        for q in annotate.QUESTIONS:
            self.assertGreaterEqual(len(q.choices), 3, q.field)
            for value, key in q.choices:
                self.assertIsInstance(value, int)
                self.assertTrue(key.startswith("review.a."), key)


class Overlay(TempCase):
    def setUp(self):
        super().setUp()
        self.ws_root = self.workspace()
        self.ws = resolve_workspace(str(self.ws_root))

    def test_a_missing_overlay_is_simply_no_answers(self):
        self.assertEqual(load_annotations(self.ws), {})

    def test_a_corrupt_overlay_costs_precision_not_the_run(self):
        (self.ws_root / ANNOTATIONS_NAME).write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(load_annotations(self.ws), {})

    def test_answers_round_trip(self):
        record_answers(self.ws, {"orchard": {"ice": {"impact": 5, "effort": 2}}})
        self.assertEqual(load_annotations(self.ws)["orchard"]["ice"],
                         {"impact": 5, "effort": 2})

    def test_a_second_pass_merges_rather_than_replaces(self):
        record_answers(self.ws, {"orchard": {"ice": {"impact": 5}}})
        record_answers(self.ws, {"orchard": {"ice": {"confidence": 3}}})
        self.assertEqual(load_annotations(self.ws)["orchard"]["ice"],
                         {"impact": 5, "confidence": 3})

    def test_the_registry_is_never_written(self):
        before = self.ws.registry_path.read_bytes()
        record_answers(self.ws, {"orchard": {"ice": {"impact": 5}}})
        self.assertEqual(self.ws.registry_path.read_bytes(), before)

    def test_the_file_explains_where_it_came_from(self):
        record_answers(self.ws, {"orchard": {"ice": {"impact": 5}}})
        text = (self.ws_root / ANNOTATIONS_NAME).read_text(encoding="utf-8")
        self.assertIn("nextbrief review", text)
        self.assertIn("Safe to delete", text)

    def test_a_hand_edited_ice_of_the_wrong_shape_does_not_kill_the_run(self):
        """The file's own header invites hand-editing, and check_shapes never
        sees it -- so a wrong shape reaches the merge unvalidated. It used to
        raise straight out of `build`: on the unattended path that is a stack
        trace and no brief, the opposite of the stated fail-open contract."""
        for bad in ('"high"', "5", '["impact", 5]', "null"):
            (self.ws_root / ANNOTATIONS_NAME).write_text(
                '{"projects": {"orchard": {"ice": %s}}}' % bad, encoding="utf-8")
            code, _, err = capture(
                sense.main, ["--workspace", str(self.ws_root), "--as-of", AS_OF])
            self.assertEqual(code, 0, "ice=%s killed the run: %s" % (bad, err))

    def test_a_discovered_project_can_be_annotated_without_being_declared(self):
        """The whole point: the person who has not written a registry entry is
        exactly the person being asked, so the overlay is applied after
        discovery rather than merged into the registry first."""
        (self.ws_root / "projects" / "latecomer").mkdir(parents=True)
        record_answers(self.ws, {"latecomer": {"ice": {"impact": 5, "confidence": 4}}})
        code, _, err = capture(
            sense.main, ["--workspace", str(self.ws_root), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((self.ws_root / "state" / "snapshot.json").read_text(encoding="utf-8"))
        got = {p["id"]: p for p in snap["projects"]}
        self.assertIn("latecomer", got)
        self.assertEqual(got["latecomer"]["ice"], {"impact": 5, "confidence": 4})
        # Still not "declared" — an answer is not a registry entry.
        self.assertFalse(got["latecomer"]["declared"])

    def test_the_registry_still_wins_through_a_full_run(self):
        # orchard declares confidence 3 in the fixture; the overlay must not
        # quietly undo a value its owner typed themselves.
        record_answers(self.ws, {"orchard": {"ice": {"confidence": 4}}})
        code, _, err = capture(
            sense.main, ["--workspace", str(self.ws_root), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((self.ws_root / "state" / "snapshot.json").read_text(encoding="utf-8"))
        got = {p["id"]: p.get("ice") for p in snap["projects"]}
        self.assertEqual(got["orchard"]["confidence"], 3)


class TheRegistryWins(unittest.TestCase):
    """A value someone typed into their own file outranks one they clicked."""

    def test_a_hand_written_axis_is_not_overwritten(self):
        reg = {"projects": [{"id": "a", "ice": {"impact": 2}}]}
        out = apply_annotations(reg, {"a": {"ice": {"impact": 5, "confidence": 3}}})
        ice = out["projects"][0]["ice"]
        self.assertEqual(ice["impact"], 2)       # the registry's own value survives
        self.assertEqual(ice["confidence"], 3)   # the gap is filled

    def test_a_hand_written_scalar_is_not_overwritten(self):
        reg = {"projects": [{"id": "a", "tier": "flagship"}]}
        out = apply_annotations(reg, {"a": {"tier": "active"}})
        self.assertEqual(out["projects"][0]["tier"], "flagship")

    def test_an_annotation_for_an_unknown_project_is_inert(self):
        reg = {"projects": [{"id": "a"}]}
        self.assertEqual(apply_annotations(reg, {"ghost": {"ice": {"impact": 5}}}), reg)

    def test_no_annotations_returns_the_registry_untouched(self):
        reg = {"projects": [{"id": "a"}]}
        self.assertIs(apply_annotations(reg, {}), reg)


class TheBriefAsks(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def render(self, projects):
        write_snapshot(self.ws, make_snapshot(projects=projects))
        code, _, err = capture(
            render.main, ["--workspace", str(self.ws), "--no-notify"])
        self.assertEqual(code, 0, err)
        return (self.ws / "BRIEF.md").read_text(encoding="utf-8")

    def test_the_question_reaches_the_page_with_its_choices(self):
        text = self.render([entry("newthing", ice=None)])
        self.assertIn("slipped by a month", text)
        self.assertIn("it is exploration", text)
        self.assertIn("I would drop other things to protect it", text)

    def test_it_disappears_once_answered(self):
        text = self.render([entry("done", ice={"impact": 4, "confidence": 3, "effort": 2})])
        self.assertNotIn("slipped by a month", text)

    def test_both_renderings_ask_the_same_question(self):
        """The invariant this feature broke on arrival.

        BRIEF.md and BRIEF.html are rendered from one gated dataset and neither
        is allowed to decide anything for itself, so they cannot disagree. The
        first cut of this feature added the section to the Markdown only, which
        meant the HTML reader was never asked at all -- silently, since nothing
        compares the two.
        """
        text = self.render([entry("newthing", ice=None)])
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertIn("slipped by a month", text)
        self.assertIn("slipped by a month", html)
        self.assertIn("it is exploration", html)
        self.assertIn("I would drop other things to protect it", html)

    def test_neither_rendering_asks_once_it_is_answered(self):
        self.render([entry("done", ice={"impact": 4, "confidence": 3, "effort": 2})])
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertNotIn("slipped by a month", html)

    def test_the_section_is_capped(self):
        # It must never compete with the brief it is printed inside.
        text = self.render([entry("a", ice=None, days=1), entry("b", ice=None, days=2),
                            entry("c", ice=None, days=3), entry("d", ice=None, days=4)])
        self.assertEqual(text.count("slipped by a month"), 2)


class ReviewCommand(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace()

    def run_review(self):
        return capture(cli.main, ["--workspace", str(self.ws), "review"])

    def test_it_refuses_to_prompt_without_a_terminal(self):
        """A scheduled run that blocks on a prompt at 21:30 produces nothing at
        all, and this command is named in the brief."""
        write_snapshot(self.ws, make_snapshot(projects=[entry("newthing", ice=None)]))
        code, out, err = self.run_review()
        self.assertEqual(code, 0, err)
        self.assertIn("newthing", out)
        self.assertFalse((self.ws / ANNOTATIONS_NAME).exists())

    def test_it_says_so_when_there_is_nothing_to_ask(self):
        write_snapshot(self.ws, make_snapshot(
            projects=[entry("done", ice={"impact": 4, "confidence": 3, "effort": 2})]))
        code, out, err = self.run_review()
        self.assertEqual(code, 0, err)
        self.assertNotIn("done", out.replace("Nothing", ""))

    def test_it_explains_itself_when_there_is_no_snapshot(self):
        code, _, err = self.run_review()
        self.assertNotEqual(code, 0)
        self.assertIn("sense", err)


if __name__ == "__main__":
    unittest.main()
