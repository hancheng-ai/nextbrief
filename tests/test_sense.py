"""Stage 1: deterministic sensing.

The property under test is the one everything downstream rests on: given the same
tree and the same ``--as-of``, sensing produces byte-identical output. Without it
``--check`` means nothing, the renderer's idempotence is unprovable, and a re-run
of an unchanged workspace registers as fresh activity in the next run's counts.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import unittest

from helpers import AS_OF, AS_OF_DATE, TempCase, capture, requires_git, set_mtime

from nextbrief import sense
from nextbrief.paths import resolve_workspace


class GoldenSnapshot(TempCase):
    """Two runs over the shipped example workspace, compared byte for byte."""

    def setUp(self):
        super().setUp()
        self.ws = self.copy_example()

    def _sense(self, *args):
        return capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF] + list(args))

    def test_two_runs_are_byte_identical(self):
        code, out, err = self._sense()
        self.assertEqual(code, 0, err)
        first_snapshot = (self.ws / "state" / "snapshot.json").read_bytes()
        first_digest = (self.ws / "state" / "digest.json").read_bytes()

        code, _, err = self._sense()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.ws / "state" / "snapshot.json").read_bytes(), first_snapshot)
        self.assertEqual((self.ws / "state" / "digest.json").read_bytes(), first_digest)

    def test_stdout_matches_what_would_be_written(self):
        self.assertEqual(self._sense()[0], 0)
        code, printed, err = self._sense("--stdout")
        self.assertEqual(code, 0, err)
        self.assertEqual(printed, (self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))

    def test_stdout_writes_nothing(self):
        code, _, err = self._sense("--stdout")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.ws / "state").exists())

    def test_snapshot_shape(self):
        code, _, err = self._sense()
        self.assertEqual(code, 0, err)
        snap = json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["run"]["as_of_date"], AS_OF)
        self.assertEqual(snap["schema_version"], 2)
        ids = [p["id"] for p in snap["projects"]]
        # Registry order is preserved: the snapshot reports, it does not rank.
        self.assertEqual(ids[0], "orchard-api")
        self.assertIn("kiln", ids)
        for project in snap["projects"]:
            self.assertIn(project["evidence"]["signal"],
                          ("hot", "warm", "cold", "dormant", "unknown"))

    def test_digest_excludes_the_evidence_index(self):
        # The digest is the model's only input; the full index stays behind so the
        # renderer can check the model against something it never saw.
        code, _, err = self._sense()
        self.assertEqual(code, 0, err)
        digest = json.loads((self.ws / "state" / "digest.json").read_text(encoding="utf-8"))
        self.assertNotIn("evidence_index", digest)
        self.assertIn("projects", digest)
        for project in digest["projects"]:
            self.assertIn("cite", project)

    def test_snapshot_rotates_to_prev(self):
        self.assertEqual(self._sense()[0], 0)
        first = (self.ws / "state" / "snapshot.json").read_text(encoding="utf-8")
        self.assertEqual(self._sense()[0], 0)
        self.assertEqual((self.ws / "state" / "snapshot.prev.json").read_text(encoding="utf-8"),
                         first)


class CheckMode(TempCase):
    """``--check`` is the contract a scheduler branches on: 3 means out of date."""

    def setUp(self):
        super().setUp()
        self.ws = self.copy_example()

    def _sense(self, *args):
        return capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF] + list(args))

    def test_missing_snapshot_is_stale(self):
        code, _, err = self._sense("--check")
        self.assertEqual(code, sense.EXIT_STALE)
        self.assertIn("does not exist", err)

    def test_current_snapshot_exits_zero(self):
        self.assertEqual(self._sense()[0], 0)
        code, out, _ = self._sense("--check")
        self.assertEqual(code, sense.EXIT_OK)
        self.assertIn("current", out)

    def test_changed_tree_exits_three(self):
        self.assertEqual(self._sense()[0], 0)
        new_file = self.ws / "projects" / "quarry" / "NEW_NOTE.md"
        new_file.write_text("# A file that did not exist when we sensed\n", encoding="utf-8")
        set_mtime(new_file)
        code, _, err = self._sense("--check")
        self.assertEqual(code, sense.EXIT_STALE)
        self.assertIn("out of date", err)

    def test_check_writes_nothing(self):
        self.assertEqual(self._sense()[0], 0)
        before = (self.ws / "state" / "snapshot.json").stat().st_mtime_ns
        self.assertEqual(self._sense("--check")[0], 0)
        self.assertEqual((self.ws / "state" / "snapshot.json").stat().st_mtime_ns, before)


class Determinism(TempCase):
    """The same properties on a fixture we control, including one with git."""

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()

    def _sense(self, *args):
        return capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF] + list(args))

    def test_repeated_runs_agree(self):
        self.assertEqual(self._sense()[0], 0)
        first = (self.ws / "state" / "snapshot.json").read_bytes()
        self.assertEqual(self._sense()[0], 0)
        self.assertEqual((self.ws / "state" / "snapshot.json").read_bytes(), first)

    @requires_git
    def test_git_facts_are_attributed_to_the_repository_that_owns_them(self):
        self.assertEqual(self._sense()[0], 0)
        snap = json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        by_id = {p["id"]: p for p in snap["projects"]}
        self.assertTrue(by_id["orchard"]["has_git"])
        self.assertEqual(by_id["orchard"]["git"][0]["commits_since"]["30"], 1)
        # kiln declares `git: none`; a caveat has to say so rather than letting a
        # file-mtime count read like commit activity.
        self.assertFalse(by_id["kiln"]["has_git"])
        self.assertEqual(by_id["kiln"]["evidence"]["caveat_code"], "no_git")

    def test_non_goals_are_lifted_verbatim(self):
        self.assertEqual(self._sense()[0], 0)
        snap = json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        by_id = {p["id"]: p for p in snap["projects"]}
        self.assertEqual(
            by_id["orchard"]["non_goals"], ["Build a mobile app", "Add a plugin system"]
        )

    def test_declared_date_drives_staleness_not_the_mtime(self):
        self.assertEqual(self._sense()[0], 0)
        snap = json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        by_id = {p["id"]: p for p in snap["projects"]}
        doc = by_id["kiln"]["status_docs"][0]
        # The file was touched two days before as-of, but declares 2026-02-01.
        self.assertEqual(doc["mtime_date"], "2026-03-14")
        self.assertEqual(doc["declared_date"], "2026-02-01")
        self.assertTrue(doc["stale"])


class FailOpen(TempCase):
    """A broken input is recorded, never fatal -- except where continuing would
    produce a plausible-but-wrong snapshot."""

    def test_missing_status_document_is_recorded_and_the_run_continues(self):
        ws = self.workspace()
        os.remove(str(ws / "projects" / "orchard" / "PROJECT_STATUS.md"))
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        codes = [f["code"] for f in snap["parse_failed"]]
        self.assertIn("status_doc_missing", codes)

    def test_missing_projects_root_aborts(self):
        # The one thing that must *not* fail open: an empty-but-plausible snapshot
        # reads as "nothing is happening" rather than "you are not configured".
        ws = self.workspace()
        registry = json.loads(
            (ws / "registry.jsonc").read_text(encoding="utf-8").split("\n", 1)[1]
        )
        registry["defaults"]["root"] = "./no-such-root"
        (ws / "registry.jsonc").write_text(json.dumps(registry, indent=2), encoding="utf-8")
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("does not exist", err)
        self.assertFalse((ws / "state" / "snapshot.json").exists())

    def test_unparseable_registry_aborts_with_the_path(self):
        ws = self.workspace()
        (ws / "registry.jsonc").write_text("{ this is not json }", encoding="utf-8")
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("registry.jsonc", err)

    def test_bad_as_of_is_a_usage_error(self):
        ws = self.workspace()
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", "not-a-date"])
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("--as-of", err)


class PureFunctions(unittest.TestCase):
    """The threshold function is deliberately not a model's job, so it is pinned."""

    CFG = {"signal": {"hot_days": 3, "warm_days": 10, "cold_days": 30}}

    def test_signal_buckets(self):
        self.assertEqual(sense.classify_signal(0, self.CFG), "hot")
        self.assertEqual(sense.classify_signal(3, self.CFG), "hot")
        self.assertEqual(sense.classify_signal(4, self.CFG), "warm")
        self.assertEqual(sense.classify_signal(10, self.CFG), "warm")
        self.assertEqual(sense.classify_signal(11, self.CFG), "cold")
        self.assertEqual(sense.classify_signal(30, self.CFG), "cold")
        self.assertEqual(sense.classify_signal(31, self.CFG), "dormant")
        self.assertEqual(sense.classify_signal(None, self.CFG), "unknown")

    def test_as_of_pins_the_clock_too(self):
        # Otherwise two runs with the same --as-of still differ in `run`.
        day, now = sense._parse_as_of(AS_OF)
        self.assertEqual(day, AS_OF_DATE)
        self.assertEqual(now, dt.datetime.combine(AS_OF_DATE, dt.time(12, 0)))
        day, now = sense._parse_as_of("2026-03-16T21:30:00")
        self.assertEqual(day, AS_OF_DATE)
        self.assertEqual(now.hour, 21)

    def test_canonical_ignores_the_run_block(self):
        a = {"run": {"generated_at": "2026-03-16T12:00:00"}, "projects": []}
        b = {"run": {"generated_at": "2026-03-17T09:00:00"}, "projects": []}
        self.assertEqual(sense.canonical(a), sense.canonical(b))


class Structure(TempCase):
    """`build` is a pure function of (workspace, config, registry, date)."""

    def test_build_twice_returns_equal_structures(self):
        ws_dir = self.workspace()
        ws = resolve_workspace(str(ws_dir))
        cfg = json.loads(
            (ws_dir / "config.jsonc").read_text(encoding="utf-8").split("\n", 1)[1]
        )
        reg = json.loads(
            (ws_dir / "registry.jsonc").read_text(encoding="utf-8").split("\n", 1)[1]
        )
        now = dt.datetime.combine(AS_OF_DATE, dt.time(12, 0))
        first = sense.build(ws, cfg, reg, AS_OF_DATE, now)
        second = sense.build(ws, cfg, reg, AS_OF_DATE, now)
        self.assertEqual(sense.canonical(first), sense.canonical(second))


if __name__ == "__main__":
    unittest.main()
