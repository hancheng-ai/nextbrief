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

from nextbrief import priority

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


if __name__ == "__main__":
    unittest.main()
