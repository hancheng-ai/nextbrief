"""How an agent session becomes a fact, and what stops it becoming a claim.

A mutation run over the session code found 15 of 19 deliberate breakages
surviving the whole suite: attribution could lose its prefix branch, the day
count could be hardcoded to zero, the last-active timestamp could take the
*oldest* transcript, and the citation handle could be minted under the wrong
evidence kind -- all with a green build. The cause was three lines in the shared
fixture rather than missing intent: `sessions.dir` pointed at a directory nothing
created, every handcrafted project carried `sessions: null`, and the evidence
index held no session entry. So no session fact had ever reached the gate or the
page in a test.

Each test here is written to fail if one specific behaviour reverses. Where a
test asserts a count, it asserts a count that a plausible mutation changes --
never merely that the key exists.
"""

from __future__ import annotations

import datetime as dt
import json
import unittest

from helpers import (
    AS_OF,
    TempCase,
    base_registry,
    capture,
    make_snapshot,
    read_jsonl,
    set_mtime,
    write_brief_json,
    write_snapshot,
)

from nextbrief import render, sense
from nextbrief.jsonc import load_jsonc

# Three separated days inside the sensing window, so a day count of 3 is
# distinguishable from a file count of 3 and from a hardcoded 0.
DAY_A = dt.datetime(2026, 3, 11, 9, 0).timestamp()
DAY_B = dt.datetime(2026, 3, 12, 9, 0).timestamp()
DAY_C = dt.datetime(2026, 3, 14, 9, 0).timestamp()

TRANSCRIPT = '{"type":"user"}\n'


class SessionSensingCase(TempCase):
    """Builds a workspace whose session store is a directory we control."""

    def sense_with(self, build, registry=None):
        ws = self.workspace(registry=registry)
        store = self.tmp / "sessions"
        store.mkdir(exist_ok=True)
        build(store, ws)
        cfg = load_jsonc(str(ws / "config.jsonc"))
        cfg["sessions"] = {"dir": str(store)}
        (ws / "config.jsonc").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        return json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))

    def slug_for(self, ws, rel="orchard"):
        return sense.slugify_path(ws / "projects" / rel)

    def transcript(self, directory, name, when):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(TRANSCRIPT, encoding="utf-8")
        set_mtime(path, when)
        return path

    def sessions_of(self, snap, pid="orchard"):
        return {p["id"]: p for p in snap["projects"]}[pid]["sessions"]


class Attribution(SessionSensingCase):
    """Which project a transcript directory is credited to.

    The rule is a string match between the directory's NAME and a slugified
    project path -- equal, or the slug followed by a hyphen. Every branch of it
    is load-bearing and none of them was covered.
    """

    def test_a_directory_named_exactly_the_slug_is_attributed(self):
        def build(store, ws):
            self.transcript(store / self.slug_for(ws), "a.jsonl", DAY_C)

        snap = self.sense_with(build)
        self.assertEqual(self.sessions_of(snap)["session_files"], 1)

    def test_a_directory_with_a_suffix_after_the_slug_is_attributed(self):
        """The agent appends to the slug when a session starts in a subdirectory.

        Dropping the `startswith(slug + "-")` branch leaves exact matches working,
        so a suite that only ever writes an exact directory name cannot see it go.
        """
        def build(store, ws):
            self.transcript(store / (self.slug_for(ws) + "-docs"), "a.jsonl", DAY_C)

        snap = self.sense_with(build)
        self.assertEqual(self.sessions_of(snap)["session_files"], 1)

    def test_a_name_that_merely_starts_with_the_slug_is_not_attributed(self):
        """The hyphen is what makes the prefix rule a path boundary.

        Without it `orchard` also claims `orchardry`, which is a different
        project whose sessions would be silently credited to this one.
        """
        def build(store, ws):
            self.transcript(store / (self.slug_for(ws) + "ry"), "a.jsonl", DAY_C)

        snap = self.sense_with(build)
        self.assertIsNone(self.sessions_of(snap))

    def test_a_nested_project_is_credited_to_itself_not_its_parent(self):
        """Longest match wins.

        Collapsing this to first-match-wins is invisible unless two slugs are
        prefixes of one another, which needs a registry built for it.
        """
        reg = base_registry()
        reg["projects"] = [
            dict(reg["projects"][0], id="orchard", paths=["orchard"]),
            dict(reg["projects"][1], id="inner", paths=["orchard/inner"]),
        ]

        def build(store, ws):
            (ws / "projects" / "orchard" / "inner").mkdir(parents=True, exist_ok=True)
            (ws / "projects" / "orchard" / "inner" / "README.md").write_text(
                "# inner\n", encoding="utf-8")
            self.transcript(store / self.slug_for(ws, "orchard/inner"), "a.jsonl", DAY_C)

        snap = self.sense_with(build, registry=reg)
        by_id = {p["id"]: p for p in snap["projects"]}
        self.assertIsNotNone(by_id["inner"]["sessions"],
                             "the nested project got none of its own sessions")
        self.assertEqual(by_id["inner"]["sessions"]["session_files"], 1)
        self.assertIsNone(by_id["orchard"]["sessions"],
                          "the parent absorbed a session belonging to the child")

    def test_an_unrelated_directory_is_attributed_to_nobody(self):
        def build(store, ws):
            self.transcript(store / "somewhere-else-entirely", "a.jsonl", DAY_C)

        snap = self.sense_with(build)
        for project in snap["projects"]:
            self.assertIsNone(project["sessions"])


class WhatCounts(SessionSensingCase):
    """Which files count, and what the numbers derived from them mean."""

    def test_a_non_transcript_file_is_not_counted(self):
        """The suffix filter is the only thing separating a transcript from the
        lock files, indexes and caches the agent keeps in the same directory."""
        def build(store, ws):
            d = store / self.slug_for(ws)
            self.transcript(d, "a.jsonl", DAY_C)
            for noise in ("notes.md", "index.json", "a.jsonl.lock"):
                p = d / noise
                p.write_text("x\n", encoding="utf-8")
                set_mtime(p, DAY_C)

        snap = self.sense_with(build)
        self.assertEqual(self.sessions_of(snap)["session_files"], 1)

    def test_distinct_session_days_counts_days_and_not_files(self):
        """Four transcripts across three days is three days.

        Asserted as 3 with 4 files present, so returning the file count, or zero,
        or one both fail. A test that used one file per day could not tell a day
        count from a file count.
        """
        def build(store, ws):
            d = store / self.slug_for(ws)
            self.transcript(d, "a.jsonl", DAY_A)
            self.transcript(d, "b.jsonl", DAY_B)
            self.transcript(d, "c.jsonl", DAY_C)
            self.transcript(d, "d.jsonl", DAY_C)   # same day as c

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["session_files"], 4)
        self.assertEqual(sessions["distinct_session_days"], 3)

    def test_last_active_is_the_newest_transcript_not_the_oldest(self):
        """Written so the oldest and the newest are different days.

        Swapping the comparison is the single most plausible edit here and it
        survives any fixture whose transcripts share a timestamp.
        """
        def build(store, ws):
            d = store / self.slug_for(ws)
            self.transcript(d, "old.jsonl", DAY_A)
            self.transcript(d, "new.jsonl", DAY_C)

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["last_active_date"],
                         dt.date.fromtimestamp(DAY_C).isoformat())
        self.assertTrue(sessions["last_active"].startswith(
            dt.date.fromtimestamp(DAY_C).isoformat()))


class TheCitationHandle(SessionSensingCase):
    """`session:<id>` is what lets a model say "an agent session" and be believed."""

    def test_the_handle_is_minted_with_the_session_kind(self):
        """Minting it under the wrong kind is a silent downgrade.

        The gate checks the declared kind against this list for exactly two kinds.
        If `session` is missing from it, every session claim is rejected; if some
        other kind is written here instead, the check passes on a claim it was
        built to catch. Neither shows up as an error anywhere.
        """
        def build(store, ws):
            self.transcript(store / self.slug_for(ws), "a.jsonl", DAY_C)

        snap = self.sense_with(build)
        entry = snap["evidence_index"].get("session:orchard")
        self.assertIsNotNone(entry, "no handle was minted for a real session")
        self.assertIn("session", entry["kinds"])

    def test_the_handle_carries_the_last_active_date_as_its_value(self):
        def build(store, ws):
            d = store / self.slug_for(ws)
            self.transcript(d, "old.jsonl", DAY_A)
            self.transcript(d, "new.jsonl", DAY_C)

        snap = self.sense_with(build)
        entry = snap["evidence_index"]["session:orchard"]
        self.assertEqual(entry["value"], dt.date.fromtimestamp(DAY_C).isoformat())

    def test_no_handle_without_a_transcript(self):
        """A directory that outlives its transcripts is the normal state after a
        cleanup, and its empty block is still a truthy dict."""
        def build(store, ws):
            (store / self.slug_for(ws)).mkdir(parents=True, exist_ok=True)

        snap = self.sense_with(build)
        self.assertNotIn("session:orchard", snap["evidence_index"])


class TheGateOnSessionClaims(TempCase):
    """The half of the kind check that had never been exercised.

    `check_evidence` verifies the declared kind against the index entry for
    exactly two kinds, `commit` and `session`. Only the commit half was tested,
    so removing `session` from that check left the suite green.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def render(self, *args):
        return capture(render.main,
                       ["--workspace", str(self.ws), "--no-notify"] + list(args))

    def brief(self):
        return (self.ws / "BRIEF.md").read_text(encoding="utf-8")

    def rejected(self):
        return read_jsonl(self.ws / "log" / "rejected.jsonl")

    def write(self, index):
        write_snapshot(self.ws, make_snapshot(evidence_index=index))

    def test_a_session_claim_on_a_handle_that_cannot_supply_one_is_rejected(self):
        self.write({
            "orchard/README.md": {"kinds": ["file_mtime"], "value": None},
            "orchard/PROJECT_STATUS.md": {
                "kinds": ["doc_declared", "file_mtime"], "value": "2026-03-10"},
        })
        write_brief_json(self.ws, {"next_actions": [{
            "title": "MISLABELLED three agent sessions this week",
            "project": "orchard",
            "evidence": [{"kind": "session", "source": "orchard/README.md"}],
        }]})
        self.assertEqual(self.render()[0], 0)
        self.assertNotIn("MISLABELLED", self.brief())
        entries = [r for r in self.rejected() if r["kind"] == "evidence_kind_mismatch"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["declared"], "session")
        self.assertEqual(entries[0]["actual"], ["file_mtime"])

    def test_a_session_claim_on_a_real_session_handle_survives(self):
        """The other half. Without it the test above is satisfied by a renderer
        that rejects every session claim ever made."""
        self.write({
            "session:orchard": {"kinds": ["session"], "value": "2026-03-14"},
            "orchard/PROJECT_STATUS.md": {
                "kinds": ["doc_declared", "file_mtime"], "value": "2026-03-10"},
        })
        write_brief_json(self.ws, {"next_actions": [{
            "title": "KEPT pick up where the last agent session left off",
            "project": "orchard",
            "evidence": [{"kind": "session", "source": "session:orchard"}],
        }]})
        self.assertEqual(self.render()[0], 0)
        self.assertIn("KEPT", self.brief())
        self.assertEqual(
            [r for r in self.rejected() if r["kind"] == "evidence_kind_mismatch"], [])


if __name__ == "__main__":
    unittest.main()
