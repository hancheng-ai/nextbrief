"""What to work on next, as an order rather than a number.

    score = 8*I + U + E

Three terms, added and never multiplied. The multiplicative version this
replaces is the reason: a decay factor spanning 40x destroys a 4x impact input,
so `impact 4 x 0.025` lost to `impact 1 x 1.0` ten to one, and the largest
project in a portfolio ranked below an experiment for the crime of being large.
Adding soft estimates keeps each term's authority bounded by construction, which
is the property the whole formula is arranged around.

**I -- what a human said this is worth.** The only input with an external
referent, and the only one that is not a measurement of the past.

**U -- an urgency cliff, 0 or 8.** Not a ramp. Cost of delay on a fixed date is
zero until the latest start, not until the deadline, so a days-remaining term
would raise every dated project every morning with no new evidence -- the
archetypal warning that fires daily for a harmless reason.

**E -- an evidence tie-break, -3..+3, and nothing more.** Commit volume is sunk
cost. Activity predicts that work will continue; it does not prescribe that it
should, and scoring on it makes the brief a mirror that tells you to work on
whatever you are already working on. So observed activity orders projects the
human ranked equally, and does nothing else.

**Positioning is not a score term.** It sets `D`, the expected dormancy, which is
what makes observed quiet interpretable: sixty silent days is normal for an
experiment and a contradiction for a flagship. Same fact, opposite meaning, and
only a stated positioning can tell them apart.

The band arithmetic, which is the safety property and is proved by test:

* **E alone can never cross a band.** Max at level I is `8I+3`; min at I+1 is
  `8I+5`. 3 < 5.
* **U + E crosses exactly one band, never two.** Max at I is `8I+11`; min at I+2
  is `8I+13`. 11 < 13.

So a human's stated importance can be overtaken by urgency plus evidence, by one
step, and never by two. That is the whole guarantee.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

__all__ = ["IMPACT_LADDER", "DORMANCY_DAYS", "BAND", "E_MIN", "E_MAX", "U_STEP",
           "impact_ordinal", "expected_dormancy", "evidence_step", "urgency_step",
           "priority_score"]

# The stored values the four review answers map to, weakest first. A registry may
# also carry a hand-written number on the same 1-5 scale, which is snapped to the
# nearest rung rather than rejected -- the scale is the human's, and refusing to
# read a 3 because no question offers one would discard a real judgement.
IMPACT_LADDER = (1, 2, 4, 5)

# Ordinals run 1..4, not 0..3.
#
# The memo specifies bases {0, 8, 16, 24}, and at zero the bottom band scores
# `0 + U + E` -- purely observed activity. That collapses declared into observed
# at exactly the point the engine is least entitled to guess: a project whose
# owner has said it matters least would be ranked by how busy it looks, which is
# the mirror this formula exists to avoid. Shifting the ladder to 1..4 costs
# nothing, because every invariant above is about the GAP between bands, and the
# gap is still 8.
BAND = 8
E_MIN, E_MAX = -3, 3
U_STEP = 8

# Expected dormancy in days, by positioning. The number a project's quiet is
# measured against, never added to its score.
DORMANCY_DAYS = {
    "flagship": 21,
    "platform": 45,
    "supporting": 60,
    "experiment": 90,
}
DORMANCY_FALLBACK = 60


def impact_ordinal(value: Any) -> Optional[int]:
    """1..4 from a stored impact, or ``None`` when nobody has said.

    ``None`` is not zero and must never become zero. An unanswered project is not
    a project answered "least important" -- it is one nobody has ranked, and the
    caller's job is to list it rather than to rank it.
    """
    # `registry.jsonc` invites hand-editing and nothing validates `ice`, so a
    # string, a bool or a NaN reaches here. Raising would cost the whole brief on
    # the unattended path, which is the opposite of failing open -- so each is
    # read as "nobody has said", which is what it is.
    #
    # `bool` is excluded before the float conversion because `True` is an `int`
    # and would otherwise rank as the weakest real answer. NaN is excluded
    # because it compares false against everything: the nearest-rung search below
    # would silently return the first rung rather than no rung, which is a
    # fabricated judgement rather than an absent one.
    if isinstance(value, bool):
        return None
    try:
        got = float(value)
    except (TypeError, ValueError):
        return None
    if got != got or got in (float("inf"), float("-inf")):
        return None
    # Snap to the nearest rung. Ties go to the lower rung: reading an ambiguous
    # number as the more important of two options is how a scale inflates.
    best = min(range(len(IMPACT_LADDER)),
               key=lambda i: (abs(IMPACT_LADDER[i] - got), i))
    return best + 1


def expected_dormancy(positioning: Any) -> int:
    """`D` -- how long this kind of project is expected to stay quiet."""
    return DORMANCY_DAYS.get(str(positioning or "").strip().lower(),
                             DORMANCY_FALLBACK)


def evidence_step(days_since: Optional[int], dormancy: int,
                  uncommitted: bool = False, has_repo_signal: bool = True) -> int:
    """`E` -- a bounded tie-break over sensed activity, never a magnitude.

    The ladder is non-linear on purpose: the distance between "touched today" and
    "touched last week" matters, and the distance between "quiet for a year" and
    "quiet for two" does not.

    Absence of a repository signal is 0 -- neither credit nor penalty. It is the
    one honest reading: a project with no VCS has not gone quiet, it has never
    been audible, and scoring it as dormant would punish a choice rather than
    observe a fact.
    """
    if uncommitted:
        return E_MAX
    if not has_repo_signal or days_since is None:
        return 0
    days = max(0, int(days_since))
    if days == 0:
        return E_MAX
    if days <= max(1, dormancy // 4):
        return 2
    if days <= dormancy:
        return 1
    if days > 3 * dormancy:
        return E_MIN
    return -2


def urgency_step(inside_cliff: bool, colliding: bool = False) -> int:
    """`U` -- 0 or 8, and 0 for everybody when two projects collide.

    The quota is structural rather than tuned. If two things are urgent on the
    same morning, promoting both says nothing about which to start, so neither is
    promoted and the collision itself becomes the line the brief prints. An
    expedite lane that can hold two items is not an expedite lane.
    """
    if colliding or not inside_cliff:
        return 0
    return U_STEP


def priority_score(impact: Any, positioning: Any, days_since: Optional[int],
                   inside_cliff: bool = False, colliding: bool = False,
                   uncommitted: bool = False,
                   has_repo_signal: bool = True) -> Optional[int]:
    """`8I + U + E`, or ``None`` for a project nobody has ranked.

    Returning ``None`` rather than a low number is the point. Scoring an
    unjudged project is meaningless rather than merely imprecise: every term
    below is anchored to a human's stated importance, and there is no number that
    stands in for one that was never given.
    """
    ordinal = impact_ordinal(impact)
    if ordinal is None:
        return None
    dormancy = expected_dormancy(positioning)
    return (BAND * ordinal
            + urgency_step(inside_cliff, colliding)
            + evidence_step(days_since, dormancy, uncommitted, has_repo_signal))


def collides(inside_cliff_ids: Sequence[str]) -> bool:
    """Whether the expedite lane is over its quota of one."""
    return len(inside_cliff_ids) > 1


def explain(impact: Any, positioning: Any, days_since: Optional[int],
            inside_cliff: bool = False, colliding: bool = False,
            uncommitted: bool = False,
            has_repo_signal: bool = True) -> Dict[str, Any]:
    """The terms behind a score, for a brief that has to justify its order.

    Nothing here is persisted. A derived number written to disk is a number that
    outlives the facts it was derived from.
    """
    ordinal = impact_ordinal(impact)
    dormancy = expected_dormancy(positioning)
    return {
        "impact_ordinal": ordinal,
        "base": None if ordinal is None else BAND * ordinal,
        "urgency": urgency_step(inside_cliff, colliding),
        "evidence": evidence_step(days_since, dormancy, uncommitted, has_repo_signal),
        "dormancy_days": dormancy,
        "urgency_withheld_for_collision": bool(colliding and inside_cliff),
    }
