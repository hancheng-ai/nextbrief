"""`needs`: the one relationship the engine could not represent.

A project deliberately parked until the thing it waits on exists, and one
nobody remembered, look identical from outside: both are quiet. The renderer
called both *neglected* — a verdict about the decision its owner reasoned
hardest about, delivered every morning until they stopped reading the section.

Twelve nodes is a small graph, and it is worth being a graph rather than a list
for three reasons, each tested below: a dangling edge is a declaration error, a
cycle can never resolve and should say so, and the transitive closure answers
what a project *ultimately* waits on — which is the question its owner has.

What is deliberately absent: any rule for when a need is *met*. That is a
judgement belonging to whoever wrote the declaration, and inventing one — "met
once the other project is hot" — would be the same invention this engine spends
the rest of its code refusing.
"""

from __future__ import annotations

import json
import unittest

from helpers import AS_OF, TempCase, base_registry, capture, make_project_entry, make_snapshot

from nextbrief import render, sense


def resolve(projects):
    failures = []
    graph = sense.resolve_needs(projects, failures)
    return graph, failures


def proj(pid, needs=None):
    p = {"id": pid, "paths": [pid]}
    if needs is not None:
        p["needs"] = needs
    return p


class TheGraph(unittest.TestCase):
    def test_a_direct_edge_is_recorded_both_ways(self):
        graph, failures = resolve([proj("beacon", ["quarry"]), proj("quarry")])
        self.assertEqual(graph["beacon"]["needs"], ["quarry"])
        self.assertEqual(graph["quarry"]["unlocks"], ["beacon"])
        self.assertEqual(failures, [])

    def test_the_closure_answers_what_it_ultimately_waits_on(self):
        # The owner's real question is not "what is the next edge" but "what has
        # to happen before this can move at all".
        graph, _ = resolve([proj("a", ["b"]), proj("b", ["c"]), proj("c")])
        self.assertEqual(graph["a"]["needs"], ["b"])
        self.assertEqual(graph["a"]["needs_all"], ["b", "c"])

    def test_a_dangling_edge_is_reported_not_dropped(self):
        graph, failures = resolve([proj("a", ["ghost"])])
        self.assertEqual(graph["a"]["needs"], [])
        self.assertEqual([f["code"] for f in failures], ["unknown_need"])

    def test_a_project_cannot_need_itself(self):
        graph, failures = resolve([proj("a", ["a"])])
        self.assertEqual(graph["a"]["needs"], [])
        self.assertEqual([f["code"] for f in failures], ["self_need"])

    def test_a_cycle_is_reported(self):
        # A waiting on B waiting on A can never resolve. Saying so beats leaving
        # both parties permanently and inexplicably "waiting".
        _, failures = resolve([proj("a", ["b"]), proj("b", ["a"])])
        codes = [f["code"] for f in failures]
        self.assertIn("needs_cycle", codes)
        why = next(f["why"] for f in failures if f["code"] == "needs_cycle")
        self.assertIn("a", why)
        self.assertIn("b", why)

    def test_a_long_chain_does_not_exhaust_the_stack(self):
        # A registry is hand-written; a deep chain must not be able to kill the
        # nightly run with a RecursionError.
        chain = [proj("p%d" % i, ["p%d" % (i + 1)]) for i in range(400)] + [proj("p400")]
        graph, failures = resolve(chain)
        self.assertEqual([f for f in failures if f["code"] == "needs_cycle"], [])
        self.assertEqual(len(graph["p0"]["needs_all"]), 400)

    def test_the_result_is_deterministic(self):
        projects = [proj("z", ["m", "a"]), proj("a"), proj("m")]
        self.assertEqual(resolve(projects)[0], resolve(projects)[0])
        self.assertEqual(resolve(projects)[0]["z"]["needs"], ["a", "m"])

    def test_nothing_decides_whether_a_need_is_met(self):
        # There is no "satisfied" flag, on purpose. Only the person who wrote the
        # declaration can retire it.
        graph, _ = resolve([proj("a", ["b"]), proj("b")])
        self.assertNotIn("satisfied", graph["a"])
        self.assertNotIn("met", graph["a"])


class WaitingIsNotNeglect(unittest.TestCase):
    """The verdict this exists to prevent."""

    def _classify(self, projects):
        return render.classify(make_snapshot(projects=projects), [], {}, {})

    def test_a_quiet_project_with_unmet_needs_is_waiting_not_neglected(self):
        old = {"best_kind": "commit", "best_date": "2026-01-01", "days_since": 200,
               "signal": "dormant", "caveat_code": None, "caveat": None}
        beacon = make_project_entry(pid="beacon", tier="flagship", evidence=old)
        beacon["needs"] = ["quarry"]
        meta = self._classify([beacon, make_project_entry(pid="quarry")])
        self.assertEqual([p["id"] for p in meta["waiting_on_work"]], ["beacon"])
        self.assertNotIn("beacon", {p["id"] for p in meta["neglected"]})

    def test_a_quiet_project_with_no_needs_is_still_neglected(self):
        # The narrowing must not swallow the case the verdict exists for.
        old = {"best_kind": "commit", "best_date": "2026-01-01", "days_since": 200,
               "signal": "dormant", "caveat_code": None, "caveat": None}
        forgotten = make_project_entry(pid="forgotten", tier="flagship", evidence=old)
        meta = self._classify([forgotten])
        self.assertIn("forgotten", {p["id"] for p in meta["neglected"]})


class ThroughAFullRun(TempCase):
    def test_needs_reaches_the_snapshot_and_the_digest(self):
        reg = base_registry()
        reg["projects"][0]["needs"] = ["kiln"]
        ws = self.workspace(registry=reg)
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)

        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        by_id = {p["id"]: p for p in snap["projects"]}
        self.assertEqual(by_id["orchard"]["needs"], ["kiln"])
        self.assertEqual(by_id["kiln"]["unlocks"], ["orchard"])

        digest = json.loads((ws / "state" / "digest.json").read_text(encoding="utf-8"))
        d = {p["id"]: p for p in digest["projects"]}
        self.assertEqual(d["orchard"]["needs"], ["kiln"])
        self.assertEqual(d["kiln"]["unlocks"], ["orchard"])

    def test_a_bad_declaration_is_recorded_rather_than_fatal(self):
        reg = base_registry()
        reg["projects"][0]["needs"] = ["no-such-project"]
        ws = self.workspace(registry=reg)
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        self.assertIn("unknown_need", {f["code"] for f in snap["parse_failed"]})

    def test_a_discovered_project_can_be_needed(self):
        # The graph is built after discovery, so a directory nobody declared can
        # still be named as the thing another project waits on.
        reg = base_registry()
        reg["projects"][0]["needs"] = ["latecomer"]
        ws = self.workspace(registry=reg)
        (ws / "projects" / "latecomer").mkdir(parents=True)
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        by_id = {p["id"]: p for p in snap["projects"]}
        self.assertEqual(by_id["orchard"]["needs"], ["latecomer"])
        self.assertNotIn("unknown_need", {f["code"] for f in snap["parse_failed"]})


if __name__ == "__main__":
    unittest.main()
