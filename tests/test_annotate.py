"""The question channel, and the overlay answers land in.

The thing under test is a refusal as much as a feature. Nothing here may ask for
a number, nothing may write to `registry.jsonc`, and nothing may turn an
unanswered question into data — which is the mistake discovery made and had to
have walked back, one release earlier, in this same codebase.
"""

from __future__ import annotations

import datetime as dt
import json
import unittest

from helpers import AS_OF, TempCase, capture, make_project_entry, make_snapshot, write_snapshot

from nextbrief import annotate, cli, priority, render, sense
from nextbrief.annotate import (
    ANNOTATIONS_NAME,
    apply_annotations,
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

    def test_one_answer_is_the_whole_answer(self):
        # There is one question now, so there is no half-answered state. A
        # registry entry carrying only `impact` is complete, and asking again
        # would be asking something already said.
        snap = make_snapshot(projects=[entry("half", ice={"impact": 4})])
        self.assertEqual(needs_annotating(snap), [])

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


class WhatIsAskedAndWhatIsNot(unittest.TestCase):
    def test_effort_is_neither_asked_nor_derived(self):
        """It was derived from file count and called "the axis where a
        measurement beats a guess". True of repo SIZE; false of the work needed
        to reach the impact, which is what ICE means. A small finished tool
        scored lowest and a large active one scored high, so the divisor
        penalised a project for being large and rewarded one for being done."""
        self.assertNotIn("effort", [q.field for q in annotate.QUESTIONS])
        self.assertFalse(hasattr(annotate, "derive_effort"))
        self.assertFalse(hasattr(annotate, "EFFORT_BANDS"))

    def test_urgency_is_never_asked(self):
        """Urgency is already known: it comes from the dates in `outcomes` and
        `deadlines`, which the renderer turns into a boost. Asking for it again
        is asking someone to re-derive arithmetic the engine does better."""
        keys = " ".join(q.key for q in annotate.QUESTIONS)
        self.assertNotIn("urgen", keys)
        self.assertIn("importance", keys)

    def test_the_question_asks_about_success_not_delay(self):
        """The bug this replaced. "If this slipped by a month, what happens?" is
        a delay-consequence question -- urgency wearing importance's name. It
        scored a portfolio's centre piece at 1, because nothing happens when a
        platform blocked on its own ecosystem slips another month."""
        from nextbrief.i18n import load_catalog

        text = load_catalog("en").t(annotate.QUESTIONS[0].key).lower()
        self.assertIn("succeed", text)
        self.assertNotIn("slip", text)

    def test_each_question_asks_a_different_thing(self):
        """Four, and no two of them are the same question in other words.

        Two was once one too many, when the second measured actionability,
        called it confidence, and multiplied a low-importance project by five.
        These four are separable by counter-example: a project can be small today
        and be the flagship (impact vs positioning), busy and finished evolving
        (activity vs status), and important with no date at all (impact vs
        deadline).
        """
        fields = [q.field for q in annotate.QUESTIONS]
        self.assertEqual(fields, ["impact", "positioning", "status", "deadline"])
        self.assertEqual(len(set(fields)), len(fields))

    def test_no_question_asks_for_a_number_on_an_undefined_scale(self):
        """A choice names its meaning; "rate this 1-5" does not. The one
        exception is the date, which is a fact rather than a rating."""
        for q in annotate.QUESTIONS:
            if q.kind == "date":
                self.assertEqual(q.choices, ())
                continue
            self.assertGreaterEqual(len(q.choices), 3, q.field)
            for _value, key in q.choices:
                self.assertTrue(key.startswith("review.a."), key)

    def test_every_question_declares_where_its_answer_goes(self):
        for q in annotate.QUESTIONS:
            self.assertIn(q.target, ("ice", "project"), q.field)
            self.assertIn(q.kind, ("choice", "date"), q.field)

    def test_every_offered_answer_lands_on_its_own_rung(self):
        """`review` asks importance and nothing else, so the four answers it
        offers must map to four distinct bands -- otherwise two answers a person
        distinguished are collapsed by the scorer that reads them."""
        rungs = [priority.impact_ordinal(q) for q in (1, 2, 4, 5)]
        self.assertEqual(rungs, [1, 2, 3, 4])
        self.assertEqual(sorted(set(rungs)), rungs, "two answers share a band")

    def test_the_answers_the_questions_offer_are_the_ladder_the_scorer_reads(self):
        """The two are declared in different modules, so nothing but a test stops
        them drifting apart -- and drift would be silent, since an unrecognised
        value simply snaps to the nearest rung."""
        offered = tuple(value for value, _label in annotate.QUESTIONS[0].choices)
        self.assertEqual(offered, priority.IMPACT_LADDER)


class RewordingInvalidatesOldAnswers(TempCase):
    """An answer belongs to the question that produced it."""

    def setUp(self):
        super().setUp()
        self.ws_root = self.workspace()
        self.ws = resolve_workspace(str(self.ws_root))

    def test_an_answer_from_an_older_wording_is_dropped_not_reinterpreted(self):
        # "2" against "what breaks if this slips" is not the same statement as
        # "2" against "what changes if this succeeds". Carrying it over would put
        # words in someone's mouth they never said.
        (self.ws_root / ANNOTATIONS_NAME).write_text(
            json.dumps({"asked_version": 1,
                        "projects": {"orchard": {"ice": {"impact": 2}}}}),
            encoding="utf-8")
        self.assertEqual(load_annotations(self.ws), {})

    def test_the_current_wording_is_honoured(self):
        record_answers(self.ws, {"orchard": {"ice": {"impact": 5}}})
        self.assertEqual(load_annotations(self.ws)["orchard"]["ice"]["impact"], 5)

    def test_what_is_written_carries_its_version(self):
        record_answers(self.ws, {"orchard": {"ice": {"impact": 5}}})
        raw = (self.ws_root / ANNOTATIONS_NAME).read_text(encoding="utf-8")
        self.assertIn('"asked_version"', raw)


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
        self.assertIn("succeeded completely", text)
        self.assertIn("a tool, or an experiment", text)
        self.assertIn("most of the plan rests on it", text)

    def test_it_disappears_once_answered(self):
        text = self.render([entry("done", ice={"impact": 4, "confidence": 3, "effort": 2})])
        self.assertNotIn("succeeded completely", text)

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
        self.assertIn("succeeded completely", text)
        self.assertIn("succeeded completely", html)
        self.assertIn("a tool, or an experiment", html)
        self.assertIn("most of the plan rests on it", html)

    def test_neither_rendering_asks_once_it_is_answered(self):
        self.render([entry("done", ice={"impact": 4, "confidence": 3, "effort": 2})])
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertNotIn("succeeded completely", html)

    def _set_cap(self, lines):
        cfg = json.loads((self.ws / "config.jsonc").read_text(encoding="utf-8").split("\n", 1)[1])
        cfg["caps"]["brief_max_lines"] = lines
        (self.ws / "config.jsonc").write_text(
            "// fixture\n" + json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    def test_the_warnings_come_before_the_questions(self):
        """The ordering that 0.1.0rc9 got backwards.

        Gate 4 keeps the first `brief_max_lines` and drops the tail, so whatever
        sits lowest is what a full brief loses. With the questions placed above
        the reminders they pushed them off the page entirely -- verified on a
        real workspace: 58 lines and 0 truncated before, 59 and 15 after, with
        the reminders and the provenance footer both gone.

        A question that waits a night costs nothing. A warning that disappears
        is the failure this engine exists to prevent.
        """
        text = self.render([entry("newthing", ice=None)])
        self.assertIn("Reminders", text)
        self.assertIn("succeeded completely", text)
        self.assertLess(text.index("Reminders"), text.index("succeeded completely"))

    def test_a_cut_that_reaches_the_questions_still_spares_the_warnings(self):
        # The cap is derived from this brief rather than guessed, so the test
        # stays honest if the fixture's line count ever moves.
        full = self.render([entry("newthing", ice=None)])
        lines = full.splitlines()
        q = next(i for i, ln in enumerate(lines) if "Only you can answer" in ln)
        self._set_cap(q + 3)

        write_snapshot(self.ws, make_snapshot(projects=[entry("newthing", ice=None)]))
        code, _, err = capture(render.main, ["--workspace", str(self.ws), "--no-notify"])
        self.assertEqual(code, 0, err)
        text = (self.ws / "BRIEF.md").read_text(encoding="utf-8")

        self.assertIn("Reminders", text, "the warnings were evicted by the questions")
        self.assertNotIn("succeeded completely", text,
                         "the cut did not reach the questions; test is not exercising the fix")

    def test_the_html_says_when_the_markdown_was_cut(self):
        # BRIEF.md is line-capped and BRIEF.html is not, so the moment the cap
        # bites they carry different content. Silence about that is its own kind
        # of disagreement.
        full = self.render([entry("newthing", ice=None)])
        self._set_cap(max(8, len(full.splitlines()) - 4))
        write_snapshot(self.ws, make_snapshot(projects=[entry("newthing", ice=None)]))
        self.assertEqual(capture(render.main,
                                 ["--workspace", str(self.ws), "--no-notify"])[0], 0)
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertIn("only here", html)

    def test_the_section_is_capped(self):
        # It must never compete with the brief it is printed inside.
        text = self.render([entry("a", ice=None, days=1), entry("b", ice=None, days=2),
                            entry("c", ice=None, days=3), entry("d", ice=None, days=4)])
        self.assertEqual(text.count("succeeded completely"), 2)


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


class AnswersExpire(TempCase):
    """Importance drifts, and nothing in the engine can observe that.

    The alternative to re-asking is a command for correcting an answer, which
    assumes the reader remembers a number they set half a year ago and thinks to
    revisit it. Periodic beats manual for the same reason the whole tool exists.
    """

    def _project(self, **over):
        p = make_project_entry(pid="thing", tier="active", ice={"impact": 4})
        p["answered"] = True
        p["evidence"] = dict(p["evidence"], days_since=1)
        p.update(over)
        return p

    def _asked(self, snap, **kw):
        return [p["id"] for p in annotate.needs_annotating(snap, **kw)]

    def test_a_fresh_answer_is_not_re_asked(self):
        p = self._project(asked_on="2026-03-01")
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), [])

    def test_an_answer_past_the_window_comes_back(self):
        p = self._project(asked_on="2025-06-01")
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), ["thing"])

    def test_an_undated_answer_is_treated_as_unknown_age(self):
        """Undated means nobody knows when it was said. Reading that as fresh is
        the same defaulting mistake as reading an absent impact as the midpoint.
        """
        p = self._project()
        p.pop("asked_on", None)
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), ["thing"])

    def test_a_hand_written_registry_answer_never_expires(self):
        """`answered` marks an overlay value. A declaration typed into the
        registry is standing, and is not ours to retire."""
        p = self._project(asked_on=None)
        p["answered"] = False
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), [])

    def test_restate_after_zero_asks_everything(self):
        """What `review --all` passes."""
        p = self._project(asked_on="2026-03-16")
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(
            self._asked(snap, as_of=dt.date(2026, 3, 16), restate_after=0), ["thing"])

    def test_recording_stamps_the_date_per_field(self):
        ws_dir = self.workspace()
        ws = resolve_workspace(str(ws_dir))
        annotate.record_answers(ws, {"orchard": {"ice": {"impact": 4}}},
                                asked_on=dt.date(2026, 3, 16))
        got = annotate.load_annotations(ws)
        self.assertEqual(got["orchard"]["asked_on"], {"ice": "2026-03-16"})

    def test_answering_one_field_does_not_restamp_another(self):
        """The reason the stamp is per field at all.

        The four questions go stale at different rates, and correcting a phase is
        not restating a strategy. One date per project launders the cheap answer
        into the expensive one, so a field goes quietly dead while reporting
        itself fresh.
        """
        ws_dir = self.workspace()
        ws = resolve_workspace(str(ws_dir))
        annotate.record_answers(ws, {"orchard": {"ice": {"impact": 4}}},
                                asked_on=dt.date(2025, 6, 1))
        annotate.record_answers(ws, {"orchard": {"status": "active"}},
                                asked_on=dt.date(2026, 3, 16))
        got = annotate.load_annotations(ws)["orchard"]["asked_on"]
        self.assertEqual(got["status"], "2026-03-16")
        self.assertEqual(got["ice"], "2025-06-01",
                         "answering the phase question refreshed the impact clock")

    def test_an_overlay_written_before_this_still_reads(self):
        """A bare string is what every overlay written before per-field stamps
        contains, and it means "all of these were answered that day" -- which was
        true, because one pass recorded them all. Nobody's file is rewritten; the
        next `review` upgrades the entry it touches and leaves the rest alone.
        """
        old = {"asked_on": "2025-06-01"}
        stamps = annotate._stamps_of(old)
        self.assertEqual(set(stamps), {q.key for q in annotate.QUESTIONS})
        self.assertTrue(all(v == "2025-06-01" for v in stamps.values()))

    def test_a_legacy_string_stamp_still_expires(self):
        """The migration has to preserve behaviour, not merely parse. An old
        entry that was stale yesterday must not read as fresh today."""
        p = self._project(asked_on="2025-06-01")
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), ["thing"])

    def test_a_legacy_string_stamp_inside_the_window_stays_fresh(self):
        """The other half. Without it the test above is satisfied by a migration
        that reads every legacy entry as expired."""
        p = self._project(asked_on="2026-03-01")
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), [])

    def test_a_stale_impact_brings_a_project_back(self):
        """A fresh phase answer must not mask a stale strategy answer. That
        laundering is what one stamp per project made possible."""
        p = self._project(asked_on={"ice": "2025-06-01", "status": "2026-03-16"})
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(
            self._asked(snap, as_of=dt.date(2026, 3, 16)), ["thing"])

    def test_every_field_fresh_is_not_re_asked(self):
        p = self._project(asked_on={"ice": "2026-03-01", "status": "2026-03-16"})
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), [])

    def test_an_ancient_phase_answer_alone_does_not_re_ask(self):
        """The timer was wrong in both directions and this is the harmless one.

        Re-asking about a maintenance project answered correctly 181 days ago is
        a warning that fires for a reason that is not a reason -- and the miss
        that actually costs something, a project abandoned last week, is one no
        calendar can see. Phase is re-asked when the evidence contradicts it.
        """
        p = self._project(asked_on={"ice": "2026-03-01", "status": "2020-01-01"})
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), [])

    def test_an_ancient_deadline_answer_alone_does_not_re_ask(self):
        """A date expires at its own date, which is the only honest expiry a date
        has. Asking again because the ANSWER is old says nothing about whether
        the date has passed."""
        p = self._project(asked_on={"ice": "2026-03-01", "deadline": "2020-01-01"})
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), [])

    def test_a_stale_positioning_brings_a_project_back(self):
        """Kept on the timer with impact, and for the same reason: these are the
        two fields inline correction is forbidden to touch, so nothing else
        refreshes them and a timer is the only thing that can."""
        p = self._project(asked_on={"ice": "2026-03-01", "positioning": "2025-06-01"})
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), ["thing"])

    def test_asking_for_everything_overrides_every_field(self):
        """`review --all` is the person asking rather than the timer, so the
        per-field policy does not get to refuse them."""
        p = self._project(asked_on={"status": "2026-03-16", "deadline": "2026-03-16"})
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        self.assertEqual(
            self._asked(snap, as_of=dt.date(2026, 3, 16), restate_after=0), ["thing"])

    def test_an_explicit_window_applies_to_untimed_fields_too(self):
        """The test above cannot see this: `restate_after=0` short-circuits at a
        guard above the per-field loop, so it never reaches the override at all.

        A POSITIVE window is what exercises it -- someone passing
        `--restate-after 30` is asking for everything older than thirty days,
        including the fields the default policy leaves untimed.
        """
        p = self._project(asked_on={"status": "2026-01-01"})
        snap = make_snapshot([p])
        snap["run"]["asked_version"] = annotate.ASKED_VERSION
        # Default policy: phase carries no timer, so nothing is due.
        self.assertEqual(self._asked(snap, as_of=dt.date(2026, 3, 16)), [])
        # Asked explicitly: 74 days old is past a 30-day window.
        self.assertEqual(
            self._asked(snap, as_of=dt.date(2026, 3, 16), restate_after=30), ["thing"])
