"""Outcomes: the thing in the world that projects serve.

A deadline written into three projects is three deadlines as far as the renderer
is concerned. Each one boosts its own project independently, so one commitment
produces three urgent rows; and all three mint the same bare ``deadline:<date>``
evidence handle, which collides in the index and keeps only the first project's
label. An outcome is that commitment named once, with contributors pointing at it.

The asymmetry under test throughout: a *dated* outcome carries urgency because a
date is a fact and days-until is arithmetic over it. A *compounding* outcome
carries none. It has no date to be close to, and a constant standing in for
"long-term work counts extra" would be an unciteable number of exactly the kind
the evidence gate exists to keep off the page.
"""

from __future__ import annotations

import json
import unittest

from helpers import AS_OF, TempCase, base_registry, capture, make_project_entry

from nextbrief import render, sense


def outcome(oid="exam-window", kind="dated", by="2026-04-01", **over):
    o = {"id": oid, "kind": kind, "statement": "A thing in the world"}
    if kind == "dated":
        o["by"] = by
        o["lead_days"] = 30
    o.update(over)
    return o


class OutcomeCase(TempCase):
    def build(self, outcomes=None, serves=None):
        """A workspace whose first project optionally serves outcomes."""
        reg = base_registry()
        if outcomes is not None:
            reg["outcomes"] = outcomes
        if serves is not None:
            reg["projects"][0]["serves"] = serves
        self.ws = self.workspace(registry=reg)
        code, _, err = capture(
            sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        return json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))

    def digest(self):
        return json.loads((self.ws / "state" / "digest.json").read_text(encoding="utf-8"))


class Sensing(OutcomeCase):
    def test_a_dated_outcome_is_sensed_with_its_arithmetic(self):
        snap = self.build([outcome(by="2026-04-01")])
        o = snap["outcomes"][0]
        self.assertEqual(o["id"], "exam-window")
        self.assertEqual(o["by"], "2026-04-01")
        self.assertEqual(o["days_until"], 16)          # AS_OF is 2026-03-16
        self.assertTrue(o["in_lead_window"])
        self.assertFalse(o["overdue"])

    def test_a_compounding_outcome_carries_no_date_arithmetic(self):
        snap = self.build([outcome("reach", kind="compounding")])
        o = snap["outcomes"][0]
        self.assertIsNone(o["by"])
        self.assertIsNone(o["days_until"])
        self.assertFalse(o["in_lead_window"])

    def test_contributors_are_inverted_from_serves(self):
        snap = self.build([outcome()], serves=["exam-window"])
        self.assertEqual(snap["outcomes"][0]["contributors"], ["orchard"])
        by_id = {p["id"]: p for p in snap["projects"]}
        self.assertEqual(by_id["orchard"]["serves"], ["exam-window"])
        self.assertEqual(by_id["kiln"]["serves"], [])

    def test_the_outcome_is_citable_under_one_handle(self):
        # One handle per outcome, so contributors cite the same commitment
        # instead of colliding on a bare date the way per-project deadlines do.
        snap = self.build([outcome()], serves=["exam-window"])
        self.assertIn("outcome:exam-window", snap["evidence_index"])
        cite = {p["id"]: p["cite"] for p in self.digest()["projects"]}
        self.assertIn("outcome:exam-window", cite["orchard"])
        self.assertNotIn("outcome:exam-window", cite["kiln"])

    def test_serving_an_undeclared_outcome_is_recorded_not_ignored(self):
        # Dropping it silently leaves the project looking unattached, which is
        # indistinguishable from never having declared the link -- and the link
        # is the whole reason the field exists.
        snap = self.build([outcome()], serves=["exam-window", "no-such-outcome"])
        codes = {f["code"] for f in snap["parse_failed"]}
        self.assertIn("unknown_outcome", codes)
        by_id = {p["id"]: p for p in snap["projects"]}
        self.assertEqual(by_id["orchard"]["serves"], ["exam-window"])

    def test_a_dated_outcome_with_an_unparseable_date_is_recorded(self):
        snap = self.build([outcome(by="next Tuesday")])
        codes = {f["code"] for f in snap["parse_failed"]}
        self.assertIn("bad_outcome_date", codes)
        self.assertEqual(snap["outcomes"], [])

    def test_the_digest_carries_outcomes_so_stage_two_can_see_them(self):
        # A ranking signal the model never receives changes nothing it writes.
        self.build([outcome()], serves=["exam-window"])
        d = self.digest()
        self.assertEqual([o["id"] for o in d["outcomes"]], ["exam-window"])
        by_id = {p["id"]: p for p in d["projects"]}
        self.assertEqual(by_id["orchard"]["serves"], ["exam-window"])


class Shapes(TempCase):
    def test_a_dated_outcome_must_carry_a_date(self):
        # The one shape that would fail silently: it parses, carries no urgency,
        # and looks exactly like a compounding outcome that was mislabelled.
        reg = base_registry()
        reg["outcomes"] = [{"id": "x", "kind": "dated", "statement": "s"}]
        ws = self.workspace(registry=reg)
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertNotEqual(code, 0)
        self.assertIn("outcomes[0].by", err)

    def test_an_unknown_kind_is_rejected(self):
        reg = base_registry()
        reg["outcomes"] = [{"id": "x", "kind": "someday", "statement": "s"}]
        ws = self.workspace(registry=reg)
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertNotEqual(code, 0)
        self.assertIn("outcomes[0].kind", err)


class Ranking(unittest.TestCase):
    """score_project's arithmetic, exercised directly."""

    def dated(self, days_until, lead=30):
        return {"id": "o", "kind": "dated", "days_until": days_until,
                "lead_days": lead, "in_lead_window": 0 <= days_until <= lead,
                "overdue": days_until < 0}

    def test_a_served_dated_outcome_lifts_a_contributor(self):
        cfg = {}
        plain = make_project_entry(tier="active")
        serving = make_project_entry(tier="active", serves=["o"])
        outs = {"o": self.dated(3)}
        self.assertGreater(render.score_project(serving, cfg, outs),
                           render.score_project(plain, cfg, outs))

    def test_it_lifts_exactly_as_much_as_an_equivalent_own_deadline(self):
        # The win is that the date is declared once and cites one handle -- not
        # that contributors are treated differently from a project that wrote the
        # deadline into its own entry.
        cfg = {}
        own = make_project_entry(tier="active", deadlines=[
            {"date": "2026-03-19", "label": "d", "days_until": 3, "lead_days": 30,
             "hard": True, "in_lead_window": True, "overdue": False}])
        served = make_project_entry(tier="active", serves=["o"])
        self.assertAlmostEqual(render.score_project(own, cfg, {}),
                               render.score_project(served, cfg, {"o": self.dated(3)}))

    def test_an_overdue_outcome_boosts_like_an_overdue_deadline(self):
        cfg = {}
        served = make_project_entry(tier="active", serves=["o"])
        far = render.score_project(served, cfg, {"o": self.dated(300)})
        late = render.score_project(served, cfg, {"o": self.dated(-5)})
        self.assertGreater(late, far)

    def test_a_finished_outcome_stops_boosting(self):
        # An outcome whose date has passed is `overdue`, which takes the maximum
        # boost -- right for one you missed, permanent nonsense for one you met.
        # The engine cannot tell those apart. `done` is the human saying which.
        cfg = {}
        served = make_project_entry(tier="active", serves=["o"])
        missed = dict(self.dated(-5))
        met = dict(missed, done=True)
        self.assertGreater(render.score_project(served, cfg, {"o": missed}),
                           render.score_project(served, cfg, {"o": met}))
        # And a met commitment leaves the project scoring as if it served nothing.
        self.assertEqual(render.score_project(served, cfg, {"o": met}),
                         render.score_project(make_project_entry(tier="active"), cfg, {}))

    def test_a_compounding_outcome_changes_no_score(self):
        # No date to be near, and a constant for "long-term counts extra" would
        # be an uncitable number on a page that requires citations.
        cfg = {}
        plain = make_project_entry(tier="active")
        serving = make_project_entry(tier="active", serves=["o"])
        outs = {"o": {"id": "o", "kind": "compounding"}}
        self.assertEqual(render.score_project(serving, cfg, outs),
                         render.score_project(plain, cfg, outs))

    def test_serving_nothing_is_unaffected_by_outcomes_existing(self):
        cfg = {}
        p = make_project_entry(tier="active")
        self.assertEqual(render.score_project(p, cfg, {"o": self.dated(1)}),
                         render.score_project(p, cfg, None))

    def test_a_dangling_serves_id_does_not_raise(self):
        # sense records it as parse_failed, but render must survive a snapshot
        # that predates the outcome being removed from the registry.
        cfg = {}
        p = make_project_entry(tier="active", serves=["gone"])
        self.assertGreater(render.score_project(p, cfg, {}), 0.0)


if __name__ == "__main__":
    unittest.main()
