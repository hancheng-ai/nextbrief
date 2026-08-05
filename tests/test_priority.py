"""The band arithmetic, proved rather than asserted at a few points.

The formula's whole safety property is that a modifier's authority is bounded:
observed activity may reorder projects a human ranked equally, and may never
overturn what they said. The multiplicative version this replaces had no such
bound -- a decay spanning 40x destroyed a 4x impact input, and the largest
project in a portfolio ranked below an experiment for being large.

So these are exhaustive over the input space rather than sampled. The space is
small enough (4 impacts x 4 positionings x 2 urgency states x 2 collision states
x the evidence ladder) that a property test is simply a loop, and a bound that
holds at three chosen points is not a bound.
"""

from __future__ import annotations

import itertools
import unittest

from helpers import make_project_entry, make_snapshot

from nextbrief import priority, render
from nextbrief.i18n import load_catalog

POSITIONINGS = tuple(priority.DORMANCY_DAYS)
IMPACTS = priority.IMPACT_LADDER

# Day counts chosen to land on every rung of the evidence ladder for every
# dormancy value, including both sides of each boundary.
DAY_POINTS = (0, 1, 5, 6, 11, 12, 15, 22, 23, 44, 46, 60, 61, 90, 91, 136, 181, 271, 400)


def scores_at(ordinal_impact, **kw):
    """Every score reachable at one impact level, across everything else."""
    out = []
    for positioning, days, unc, repo in itertools.product(
            POSITIONINGS, DAY_POINTS, (False, True), (False, True)):
        got = priority.priority_score(
            ordinal_impact, positioning, days,
            uncommitted=unc, has_repo_signal=repo, **kw)
        if got is not None:
            out.append(got)
    return out


class TheEvidenceTermIsBounded(unittest.TestCase):
    def test_evidence_never_leaves_its_range(self):
        for positioning, days, unc, repo in itertools.product(
                POSITIONINGS, DAY_POINTS, (False, True), (False, True)):
            got = priority.evidence_step(
                days, priority.expected_dormancy(positioning), unc, repo)
            self.assertGreaterEqual(got, priority.E_MIN)
            self.assertLessEqual(got, priority.E_MAX)

    def test_evidence_alone_can_never_cross_a_band(self):
        """Max at level I is 8I+3; min at I+1 is 8I+5. 3 < 5.

        Stated as the comparison that matters: the BEST any project can do at one
        impact level stays below the WORST any project can do one level up, as
        long as neither is urgent.
        """
        for lower in range(1, len(IMPACTS)):
            best_below = max(scores_at(IMPACTS[lower - 1], inside_cliff=False))
            worst_above = min(scores_at(IMPACTS[lower], inside_cliff=False))
            self.assertLess(
                best_below, worst_above,
                "activity alone overturned a human's stated importance "
                "(%d >= %d between rungs %d and %d)"
                % (best_below, worst_above, lower - 1, lower))

    def test_no_repo_signal_is_neither_credit_nor_penalty(self):
        """A project with no VCS has not gone quiet -- it has never been audible.
        Scoring it as dormant would punish a choice rather than observe a fact."""
        for positioning in POSITIONINGS:
            d = priority.expected_dormancy(positioning)
            self.assertEqual(priority.evidence_step(9999, d, has_repo_signal=False), 0)
            self.assertEqual(priority.evidence_step(None, d), 0)


class ANegativeAgeCannotEscapeTheBound(unittest.TestCase):
    """A bug this project shipped, carried forward as a guard.

    Under the multiplicative model `0.5 ** (days / half_life)` was bounded by 1.0
    for days >= 0 and unbounded below it, so a file dated a year ahead -- a wrong
    clock on a NAS, an archive unpacked with its original timestamps -- scored
    477,911 against a normal maximum of 4. Silent, because nothing else reads
    `days_since` as a number.

    The additive model cannot produce that, but "cannot" is a claim and this is
    the test of it.
    """

    def test_a_negative_age_is_read_as_today_rather_than_as_recent(self):
        """Asserted as equality with today, not as "under the ceiling".

        The first version of this test checked only that a future date scored no
        higher than the ceiling -- which held with the clamp REMOVED, because an
        unclamped negative simply falls into the "within D/4" rung and scores 2
        instead of 3. Bounded either way, so the assertion could not fail and the
        clamp it claimed to guard was untested.
        """
        for positioning in POSITIONINGS:
            d = priority.expected_dormancy(positioning)
            today = priority.evidence_step(0, d)
            for ahead in (-1, -7, -365, -100000):
                self.assertEqual(
                    priority.evidence_step(ahead, d), today,
                    "days_since=%s was not clamped to today" % ahead)

    def test_no_value_of_days_since_leaves_the_band(self):
        """Stated once as a property: nothing `days_since` can hold makes a score
        exceed what its impact band allows."""
        for days in (-10**6, -1, 0, 1, 10**6):
            for positioning in POSITIONINGS:
                got = priority.priority_score(5, positioning, days)
                self.assertLessEqual(got, priority.BAND * len(IMPACTS) + priority.E_MAX)
                self.assertGreaterEqual(got, priority.BAND * len(IMPACTS) + priority.E_MIN)


class TheUrgencyCliffCrossesExactlyOneBand(unittest.TestCase):
    def test_urgency_plus_evidence_can_cross_one_band(self):
        """It is supposed to. A deadline is the one thing allowed to promote a
        project past what its owner said it was worth -- by one step."""
        for lower in range(1, len(IMPACTS)):
            best_urgent = max(scores_at(IMPACTS[lower - 1], inside_cliff=True))
            worst_above = min(scores_at(IMPACTS[lower], inside_cliff=False))
            self.assertGreater(
                best_urgent, worst_above,
                "an urgent project could not overtake a calmer one a single "
                "rung above it, which makes the cliff decorative")

    def test_urgency_plus_evidence_can_never_cross_two(self):
        """Max at I is 8I+11; min at I+2 is 8I+13. 11 < 13.

        The bound that stops a deadline laundering a minor project into the top
        of the list.
        """
        for lower in range(len(IMPACTS) - 2):
            best_urgent = max(scores_at(IMPACTS[lower], inside_cliff=True))
            worst_two_up = min(scores_at(IMPACTS[lower + 2], inside_cliff=False))
            self.assertLess(
                best_urgent, worst_two_up,
                "urgency crossed two bands (%d >= %d from rung %d)"
                % (best_urgent, worst_two_up, lower))


class TheExpediteQuota(unittest.TestCase):
    """If two things are urgent on the same morning, promoting both says nothing
    about which to start. An expedite lane that holds two is not one."""

    def test_a_collision_withholds_urgency_from_everybody(self):
        self.assertEqual(priority.urgency_step(True, colliding=True), 0)
        self.assertEqual(priority.urgency_step(True, colliding=False),
                         priority.U_STEP)

    def test_a_collision_is_reported_rather_than_silently_applied(self):
        got = priority.explain(4, "flagship", 1, inside_cliff=True, colliding=True)
        self.assertTrue(got["urgency_withheld_for_collision"])
        self.assertEqual(got["urgency"], 0)

    def test_not_being_in_the_cliff_is_not_a_collision(self):
        got = priority.explain(4, "flagship", 1, inside_cliff=False, colliding=True)
        self.assertFalse(got["urgency_withheld_for_collision"])


class TheLowestBandIsStillDeclared(unittest.TestCase):
    """The caveat that changed the memo's numbers.

    At ordinals 0..3 the bottom band scores `0 + U + E` -- purely observed
    activity. That collapses declared into observed at exactly the point the
    engine is least entitled to guess: a project whose owner said it matters
    least would be ranked on how busy it looks.
    """

    def test_the_weakest_answer_still_contributes_a_base(self):
        self.assertEqual(priority.impact_ordinal(IMPACTS[0]), 1)
        base = priority.explain(IMPACTS[0], "experiment", 500)["base"]
        self.assertEqual(base, priority.BAND)

    def test_no_score_is_reachable_from_evidence_alone(self):
        """The floor of the lowest band still exceeds everything the evidence
        term can subtract, so a ranked project never scores at or below zero."""
        for got in scores_at(IMPACTS[0], inside_cliff=False):
            self.assertGreater(got, 0)


class UnjudgedIsNotZero(unittest.TestCase):
    def test_an_unranked_project_scores_none(self):
        """Not a low number. Scoring an unjudged project is meaningless rather
        than imprecise -- every term is anchored to a stated importance, and
        nothing stands in for one that was never given."""
        for value in (None, "", "unknown", {}, []):
            self.assertIsNone(priority.impact_ordinal(value))
            self.assertIsNone(priority.priority_score(value, "flagship", 1))

    def test_the_shapes_a_hand_edited_registry_can_hold(self):
        """`registry.jsonc` invites hand-editing and nothing validates `ice`.

        Each of these must read as "nobody has said" rather than raise, because
        raising costs the whole brief on the unattended path. Two are subtler
        than they look: `True` is an `int` and would otherwise rank as the
        weakest real answer, and NaN compares false against everything, so the
        nearest-rung search returns the FIRST rung -- a fabricated judgement
        rather than an absent one.
        """
        for bad in ("high", float("nan"), float("inf"), float("-inf"), True, False, []):
            self.assertIsNone(priority.impact_ordinal(bad), repr(bad))
            self.assertIsNone(priority.priority_score(bad, "platform", 0), repr(bad))


class ReadingAHandWrittenNumber(unittest.TestCase):
    def test_every_offered_answer_maps_to_its_own_rung(self):
        got = [priority.impact_ordinal(v) for v in IMPACTS]
        self.assertEqual(got, [1, 2, 3, 4])

    def test_a_number_between_rungs_snaps_to_the_lower_one(self):
        """The scale is the human's, so a hand-written 3 is read rather than
        rejected. Ties go downward: reading an ambiguous number as the more
        important of two options is how a scale inflates."""
        self.assertEqual(priority.impact_ordinal(3), 2)

    def test_out_of_range_numbers_clamp_rather_than_explode(self):
        self.assertEqual(priority.impact_ordinal(-99), 1)
        self.assertEqual(priority.impact_ordinal(99), len(IMPACTS))


class PositioningCalibratesRatherThanScores(unittest.TestCase):
    def test_the_same_quiet_means_different_things(self):
        """Sixty silent days is normal for an experiment and a contradiction for
        a flagship. Same fact, opposite sign."""
        self.assertEqual(priority.evidence_step(60, priority.expected_dormancy("experiment")), 1)
        self.assertEqual(priority.evidence_step(60, priority.expected_dormancy("flagship")), -2)

    def test_positioning_is_not_added_to_the_score(self):
        """If it were a term, a flagship would outrank a platform on the label
        alone -- and the label is not a claim about what to do today."""
        for a, b in itertools.combinations(POSITIONINGS, 2):
            self.assertEqual(
                priority.priority_score(4, a, None, has_repo_signal=False),
                priority.priority_score(4, b, None, has_repo_signal=False),
                "positioning moved a score with the evidence term neutralised")

    def test_an_unknown_positioning_falls_back_rather_than_raising(self):
        self.assertEqual(priority.expected_dormancy("nonsense"),
                         priority.DORMANCY_FALLBACK)
        self.assertEqual(priority.expected_dormancy(None),
                         priority.DORMANCY_FALLBACK)


class TheWiringIntoClassify(unittest.TestCase):
    """What the formula sees once it is reading real snapshot entries.

    Every test here is about a way the wiring can be right in isolation and wrong
    in place -- which is where a replacement goes wrong, not in the arithmetic.
    """

    def _p(self, pid, impact=4, status="active", days=1, **over):
        got = make_project_entry(pid=pid, ice={"impact": impact})
        got["status"] = status
        got["positioning"] = over.pop("positioning", "platform")
        got["evidence"] = dict(got["evidence"], days_since=days)
        got["git_declared"] = over.pop("git_declared", "declared")
        got.update(over)
        return got

    def _meta(self, projects, outcomes=None):
        snap = make_snapshot(projects=projects)
        if outcomes is not None:
            snap["outcomes"] = outcomes
        return render.classify(snap, [], {}, None, None)

    def test_only_an_active_project_is_ranked(self):
        """Status gates rather than scores. It used to be a multiplier, which put
        a frozen flagship into a numeric contest with an active experiment and
        let the experiment win -- an ordering with two meanings."""
        meta = self._meta([self._p("a", status="active"),
                           self._p("b", status="maintenance"),
                           self._p("c", status="frozen"),
                           self._p("d", status="done")])
        self.assertEqual([p["id"] for p in meta["ranked"]], ["a"])
        self.assertEqual([p["id"] for p in meta["gated"]], ["b", "c", "d"])

    def test_a_gated_project_is_listed_and_not_dropped(self):
        """Excluding a project from the ORDERING is honest -- nobody claimed it is
        competing. Excluding it from the PAGE is the bug the split exists to
        avoid."""
        meta = self._meta([self._p("a"), self._p("z", status="frozen")])
        listed = {p["id"] for p in meta["ranked"] + meta["gated"]}
        self.assertEqual(listed, {"a", "z"})

    def test_a_higher_stated_impact_outranks_recent_activity(self):
        meta = self._meta([self._p("busy_but_minor", impact=1, days=0),
                           self._p("important_but_quiet", impact=5, days=40)])
        self.assertEqual([p["id"] for p in meta["ranked"]][0], "important_but_quiet")

    def test_a_lone_deadline_promotes_and_two_cancel(self):
        """The expedite quota. If two projects are urgent on the same morning,
        promoting both says nothing about which to start."""
        dated = {"date": "2026-03-20", "label": "ship", "in_lead_window": True,
                 "overdue": False, "days_until": 4}
        # `calm` is a rung above and has gone quiet, so the promotion is legible.
        # With both freshly worked the two land on exactly 18 -- a real tie, and
        # the correct one: the cliff is worth one band, so it reaches parity with
        # the rung above rather than passing it.
        one = self._meta([self._p("urgent", impact=1, days=1, deadlines=[dated]),
                          self._p("calm", impact=2, days=200)])
        self.assertEqual([p["id"] for p in one["ranked"]][0], "urgent")
        self.assertEqual(one["urgency_collision"], [])

        two = self._meta([self._p("urgent", impact=1, days=1, deadlines=[dated]),
                          self._p("also", impact=1, days=1, deadlines=[dated]),
                          self._p("calm", impact=2, days=200)])
        self.assertEqual([p["id"] for p in two["ranked"]][0], "calm",
                         "a collision still promoted somebody")
        self.assertEqual(two["urgency_collision"], ["also", "urgent"])

    def test_the_cliff_reaches_parity_with_the_rung_above_not_past_it(self):
        """The tie the fixture above deliberately avoids, asserted on purpose.

        An urgent project one rung down, equally fresh, ties rather than wins.
        That is the bound working: urgency is worth one band, and a band is
        exactly what it buys.
        """
        dated = {"date": "2026-03-20", "label": "ship", "in_lead_window": True,
                 "overdue": False, "days_until": 4}
        meta = self._meta([self._p("urgent", impact=1, days=1, deadlines=[dated]),
                           self._p("calm", impact=2, days=1)])
        self.assertEqual(meta["scores"]["urgent"], meta["scores"]["calm"])

    def test_urgency_comes_from_served_outcomes_too(self):
        """The regression this nearly shipped with.

        A project is on the hook for its own deadlines AND for the dated outcomes
        it serves. Reading only the former drops the urgency of every project
        whose commitment is shared -- which is exactly the kind a portfolio has,
        since a shared date is declared once as an outcome precisely so it is not
        duplicated into three registry entries.
        """
        outcome = {"id": "launch", "kind": "dated", "date": "2026-03-20",
                   "in_lead_window": True, "overdue": False, "done": False}
        meta = self._meta(
            [self._p("serves_it", impact=1, days=1, serves=["launch"]),
             self._p("calm", impact=2, days=200)],
            outcomes=[outcome])
        self.assertEqual([p["id"] for p in meta["ranked"]][0], "serves_it",
                         "a project on the hook for a shared date got no urgency")

    def test_no_git_is_not_punished_as_dormancy(self):
        """Asserted on the scores, not on the order.

        Ordering alone cannot see this: with the distinction removed both
        projects score identically and the tie breaks alphabetically, which put
        the right answer first for the wrong reason.
        """
        meta = self._meta([self._p("with_git", impact=4, days=200),
                           self._p("no_git", impact=4, days=200,
                                   git_declared="none")])
        self.assertGreater(
            meta["scores"]["no_git"], meta["scores"]["with_git"],
            "a project that never had a repo was scored as having gone quiet")


class WhenTheScaleStopsDiscriminating(unittest.TestCase):
    """Every rating scale inflates.

    Given four options and a portfolio they care about, people mark most things
    critical. The result is not a wrong ordering but an ABSENT one dressed as a
    ranking: once most projects share the top band, what decides the order is
    `U + E` -- a deadline and how recently something was touched. That is a real
    ordering of activity presented as an ordering of importance, which is worse
    than no order at all because it is believable.
    """

    def test_a_spread_portfolio_still_ranks(self):
        self.assertTrue(priority.ordering_discriminates([4, 3, 2, 1]))
        self.assertTrue(priority.ordering_discriminates([4, 4, 2, 1]))

    def test_a_portfolio_that_is_mostly_top_band_does_not(self):
        self.assertFalse(priority.ordering_discriminates([4, 4, 4, 1]))
        self.assertFalse(priority.ordering_discriminates([4, 4, 4, 4]))

    def test_the_threshold_is_a_share_and_not_a_count(self):
        """Three of five is 60% and allowed; three of four is 75% and is not. A
        count would fire on a small portfolio that is simply small."""
        self.assertTrue(priority.ordering_discriminates([4, 4, 4, 2, 1]))
        self.assertFalse(priority.ordering_discriminates([4, 4, 4, 2]))

    def test_it_is_the_top_band_that_matters_not_the_value_four(self):
        """A portfolio where nothing is rated above 2 still discriminates if the
        2s are a minority. The scale has not collapsed just because nobody used
        its upper half."""
        self.assertTrue(priority.ordering_discriminates([2, 1, 1, 1]))
        self.assertFalse(priority.ordering_discriminates([2, 2, 2, 1]))

    def test_unrated_projects_are_not_counted_either_way(self):
        self.assertTrue(priority.ordering_discriminates([4, 1, None, None]))

    def test_too_few_to_compare_makes_no_claim(self):
        self.assertTrue(priority.ordering_discriminates([]))
        self.assertTrue(priority.ordering_discriminates([4]))


class TiesPrintAsTies(unittest.TestCase):
    def test_equal_scores_are_grouped(self):
        got = priority.tie_groups({"a": 18, "b": 18, "c": 10})
        self.assertEqual(got, {18: ["a", "b"]})

    def test_a_lone_score_is_not_a_tie(self):
        self.assertEqual(priority.tie_groups({"a": 18, "b": 10}), {})

    def test_unscored_projects_are_not_tied_with_each_other(self):
        """`None` means nobody ranked them. Grouping them as equals would assert
        a comparison that was never made."""
        self.assertEqual(priority.tie_groups({"a": None, "b": None}), {})


class SuppressionInPractice(unittest.TestCase):
    def _p(self, pid, impact):
        got = make_project_entry(pid=pid, ice={"impact": impact})
        got["status"] = "active"
        got["positioning"] = "platform"
        got["evidence"] = dict(got["evidence"], days_since=1)
        return got

    def _meta(self, projects):
        return render.classify(make_snapshot(projects=projects), [], {}, None, None)

    def test_a_discriminating_portfolio_is_ordered_by_score(self):
        meta = self._meta([self._p("low", 1), self._p("high", 5)])
        self.assertFalse(meta["ordering_suppressed"])
        self.assertEqual([p["id"] for p in meta["ranked"]], ["high", "low"])

    def test_an_inflated_portfolio_is_listed_alphabetically_and_says_so(self):
        """Alphabetical is not a fallback ranking -- it is the absence of one,
        made visible. The flag is what stops the reader taking the order as a
        judgement."""
        meta = self._meta([self._p("zebra", 5), self._p("apple", 5),
                           self._p("mango", 5), self._p("kiwi", 1)])
        self.assertTrue(meta["ordering_suppressed"])
        self.assertEqual([p["id"] for p in meta["ranked"]],
                         ["apple", "kiwi", "mango", "zebra"])

    def test_the_brief_prints_the_reason(self):
        """A suppressed ordering that looks like an ordering is the failure this
        avoids, so the page has to say it out loud."""
        snap = make_snapshot(projects=[self._p("zebra", 5), self._p("apple", 5),
                                       self._p("mango", 5), self._p("kiwi", 1)])
        md, _ = render.render_brief(snap, {}, [], {}, {}, load_catalog("en"),
                                    {"conflicts": []})
        self.assertIn("Not ranked today", md)

    def test_a_ranked_brief_stays_quiet(self):
        """Rule 8. A line that appears on a healthy portfolio is one people learn
        to skip, and then miss on the morning it matters."""
        snap = make_snapshot(projects=[self._p("low", 1), self._p("high", 5)])
        md, _ = render.render_brief(snap, {}, [], {}, {}, load_catalog("en"),
                                    {"conflicts": []})
        self.assertNotIn("Not ranked today", md)


if __name__ == "__main__":
    unittest.main()
