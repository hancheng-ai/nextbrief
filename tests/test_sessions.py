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

from nextbrief import render, sense, transcripts
from nextbrief.jsonc import load_jsonc

# Three separated days inside the sensing window, so a day count of 3 is
# distinguishable from a file count of 3 and from a hardcoded 0.
DAY_A = dt.datetime(2026, 3, 11, 9, 0).timestamp()
DAY_B = dt.datetime(2026, 3, 12, 9, 0).timestamp()
DAY_C = dt.datetime(2026, 3, 14, 9, 0).timestamp()

# Every fixture transcript is given an mtime that CONTRADICTS its content, on a
# day no test ever asserts. So a test that passes is passing on what the file
# says rather than on when it was last touched -- which is the entire subject of
# the change these fixtures cover.
WRONG_MTIME = dt.datetime(2026, 3, 2, 9, 0).timestamp()


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

    def record(self, when, cwd=None):
        """One transcript line, timestamped in UTC with a trailing Z.

        Written the way the real format writes it, because the trailing Z is the
        part the floor interpreter's `fromisoformat` refuses.
        """
        utc = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=when)
        rec = {"type": "user", "timestamp": utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        if cwd is not None:
            rec["cwd"] = str(cwd)
        return json.dumps(rec) + "\n"

    def transcript(self, directory, name, *days, **kw):
        """A transcript whose CONTENT says it ran on each of `days`."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        body = kw.get("body")
        if body is None:
            body = "".join(self.record(d) for d in days)
        path.write_text(body, encoding="utf-8")
        set_mtime(path, WRONG_MTIME)
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


class ContentBeatsMtime(SessionSensingCase):
    """When the file says one thing and the filesystem says another.

    Measured over a real store, the filesystem was the one lying: 96% of
    transcripts end on a record carrying no timestamp -- a title rewrite, a mode
    change -- so the mtime was timing metadata churn rather than conversation. It
    invented 8 session-days that never happened and missed 27 that did.
    """

    def test_the_day_comes_from_the_content_not_the_mtime(self):
        def build(store, ws):
            self.transcript(store / self.slug_for(ws), "a.jsonl", DAY_C)

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["last_active_date"],
                         dt.date.fromtimestamp(DAY_C).isoformat())
        self.assertNotEqual(sessions["last_active_date"],
                            dt.date.fromtimestamp(WRONG_MTIME).isoformat())

    def test_one_transcript_spanning_two_days_counts_two(self):
        """The defect no timestamp can fix.

        A third of real transcripts run past midnight, and one mtime can only
        ever mark one day. This is the half of the undercount that survives even
        a perfectly accurate last-modified time.
        """
        def build(store, ws):
            self.transcript(store / self.slug_for(ws), "a.jsonl", DAY_A, DAY_B)

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["session_files"], 1)
        self.assertEqual(sessions["distinct_session_days"], 2)

    def test_a_transcript_with_no_timestamps_is_a_session_with_no_dates(self):
        """Readable, and carrying nothing datable.

        It still happened, so it counts as a session file; it dates nothing, so
        it contributes no day; and the count of such files is published, because
        a run that could not date four transcripts and one that dated them all
        must not report the same number with no way to tell them apart.
        """
        def build(store, ws):
            self.transcript(store / self.slug_for(ws), "a.jsonl",
                            body='{"type":"file-history-snapshot"}\n')

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["session_files"], 1)
        self.assertEqual(sessions["distinct_session_days"], 0)
        self.assertEqual(sessions["transcripts_without_dates"], 1)
        self.assertIsNone(sessions["last_active_date"])

    def test_a_torn_line_costs_that_line_and_not_the_file(self):
        """A transcript is appended to while this runs, so the last line may be
        half-written. Failing open on the record is the difference between
        losing a message and losing a day."""
        def build(store, ws):
            good = self.record(DAY_C)
            self.transcript(store / self.slug_for(ws), "a.jsonl",
                            body=good + '{"type":"user","timestamp":"2026-')

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["distinct_session_days"], 1)
        self.assertEqual(sessions["last_active_date"],
                         dt.date.fromtimestamp(DAY_C).isoformat())

    def test_no_transcript_path_reaches_the_digest(self):
        """`parse_failed` is copied into digest.json, which the model reads and
        which becomes a git-tracked page. A transcript path is an absolute path
        on somebody's machine, so the count is published and the path is not."""
        def build(store, ws):
            self.transcript(store / self.slug_for(ws), "a.jsonl",
                            body='{"type":"mode"}\n')

        snap = self.sense_with(build)
        blob = json.dumps(snap)
        self.assertNotIn(".jsonl", blob,
                         "a transcript filename escaped into the snapshot")
        for entry in snap.get("parse_failed") or []:
            self.assertNotIn(".jsonl", json.dumps(entry))


class ASessionIsASequenceOfDirectories(SessionSensingCase):
    """Where the work went, not just where it was launched.

    The launch directory is a lossless encoding of where a session started --
    measured over a real store, attributing by it and by the directory name give
    identical results on every file. So "read the starting cwd instead of the
    slug" buys nothing, and that is not what this is.

    What it buys is the mid-session move. The working directory is recorded per
    record; a third of real transcripts carry more than one and one carries
    seventeen. Attributing per record finds 82 project-days where the launch
    directory finds 57, adds four projects that appeared to have no sessions at
    all, and loses none.
    """

    def test_work_done_in_a_second_project_is_credited_to_that_project(self):
        """One transcript, launched in one project, moving to another.

        Under attribution-by-directory this whole file counts for `orchard` and
        `kiln` shows nothing -- which is how a project you spent a day inside
        gets called neglected.
        """
        def build(store, ws):
            body = (self.record(DAY_A, cwd=ws / "projects" / "orchard")
                    + self.record(DAY_C, cwd=ws / "projects" / "kiln"))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        snap = self.sense_with(build)
        by_id = {p["id"]: p for p in snap["projects"]}
        self.assertIsNotNone(by_id["kiln"]["sessions"],
                             "a day spent in kiln was credited to orchard")
        self.assertEqual(by_id["kiln"]["sessions"]["distinct_session_days"], 1)
        self.assertEqual(by_id["kiln"]["sessions"]["last_active_date"],
                         dt.date.fromtimestamp(DAY_C).isoformat())
        self.assertEqual(by_id["orchard"]["sessions"]["distinct_session_days"], 1)

    def test_last_active_is_per_project_not_per_transcript(self):
        """The reason it is worth doing carefully.

        A session that worked in one project all morning and moved to another
        after lunch did not leave the first one active until midnight. Taking the
        file's final timestamp for every project it touched would overstate the
        recency of everything it passed through -- and recency is what decides
        hot/warm/cold and what gets called neglected.
        """
        def build(store, ws):
            body = (self.record(DAY_A, cwd=ws / "projects" / "orchard")
                    + self.record(DAY_C, cwd=ws / "projects" / "kiln"))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        by_id = {p["id"]: p for p in self.sense_with(build)["projects"]}
        self.assertEqual(by_id["orchard"]["sessions"]["last_active_date"],
                         dt.date.fromtimestamp(DAY_A).isoformat(),
                         "orchard inherited kiln's later timestamp")

    def test_a_deeper_project_wins_over_the_one_containing_it(self):
        reg = base_registry()
        reg["projects"] = [
            dict(reg["projects"][0], id="orchard", paths=["orchard"]),
            dict(reg["projects"][1], id="inner", paths=["orchard/inner"]),
        ]

        def build(store, ws):
            inner = ws / "projects" / "orchard" / "inner"
            inner.mkdir(parents=True, exist_ok=True)
            (inner / "README.md").write_text("# inner\n", encoding="utf-8")
            self.transcript(store / self.slug_for(ws), "a.jsonl",
                            body=self.record(DAY_C, cwd=inner))

        by_id = {p["id"]: p for p in self.sense_with(build, registry=reg)["projects"]}
        self.assertEqual(by_id["inner"]["sessions"]["distinct_session_days"], 1)
        # orchard still owns the session -- it was launched there, and that is
        # what `session_files` reports. What it must not own is the DAY, which
        # was spent in the child.
        self.assertEqual(by_id["orchard"]["sessions"]["session_files"], 1)
        self.assertEqual(by_id["orchard"]["sessions"]["distinct_session_days"], 0,
                         "the parent absorbed a day that belonged to the child")

    def test_a_sibling_with_a_shared_name_prefix_is_not_a_containing_project(self):
        """`orchard` must not swallow `orchardry`.

        Prefix matching on a raw string does exactly that; the separator is what
        makes it a path boundary. The sibling is itself discovered as a project,
        so the day is not merely withheld from `orchard` -- it lands where it was
        actually spent, which is the stronger claim.
        """
        def build(store, ws):
            sibling = ws / "projects" / "orchardry"
            sibling.mkdir(parents=True, exist_ok=True)
            (sibling / "README.md").write_text("# orchardry\n", encoding="utf-8")
            self.transcript(store / self.slug_for(ws), "a.jsonl",
                            body=self.record(DAY_C, cwd=sibling))

        by_id = {p["id"]: p for p in self.sense_with(build)["projects"]}
        self.assertEqual(by_id["orchard"]["sessions"]["distinct_session_days"], 0,
                         "orchard swallowed a day spent in orchardry")
        self.assertEqual(by_id["orchardry"]["sessions"]["distinct_session_days"], 1)

    def test_a_directory_under_no_project_at_all_is_counted_as_unattributed(self):
        """The other half of the boundary: somewhere the portfolio genuinely does
        not describe, so there is no project to discover and nothing to credit."""
        def build(store, ws):
            outside = self.tmp / "not-in-the-portfolio"
            outside.mkdir(parents=True, exist_ok=True)
            self.transcript(store / self.slug_for(ws), "a.jsonl",
                            body=self.record(DAY_C, cwd=outside))

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["distinct_session_days"], 0)
        self.assertEqual(sessions["records_unattributed"], 1)
        self.assertEqual(sessions["transcripts_without_dates"], 0,
                         "a placement failure was reported as a parse failure")

    def test_time_spent_outside_every_project_is_counted_not_dropped(self):
        """20.6% of transcripts in a real store spend time somewhere the
        portfolio does not describe. A day count that quietly excludes them reads
        exactly like one that included them and found nothing."""
        def build(store, ws):
            outside = self.tmp / "somewhere-else"
            outside.mkdir(parents=True, exist_ok=True)
            body = (self.record(DAY_A, cwd=ws / "projects" / "orchard")
                    + self.record(DAY_C, cwd=outside))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["distinct_session_days"], 1)
        self.assertEqual(sessions["records_unattributed"], 1)

    def test_a_record_with_no_cwd_falls_back_to_where_the_session_began(self):
        """The time field appears on more record types than the directory does,
        so timestamped records with no cwd are normal. Discarding them would lose
        real work for want of a field the launch directory already answers."""
        def build(store, ws):
            self.transcript(store / self.slug_for(ws), "a.jsonl", DAY_C)

        sessions = self.sessions_of(self.sense_with(build))
        self.assertEqual(sessions["distinct_session_days"], 1)
        self.assertEqual(sessions["records_unattributed"], 0)

    def test_no_working_directory_reaches_the_snapshot(self):
        """A cwd is an absolute path on somebody's machine. The reader takes a
        resolver and returns buckets precisely so that no caller can leak one by
        forgetting to strip it."""
        def build(store, ws):
            outside = self.tmp / "somewhere-else"
            outside.mkdir(parents=True, exist_ok=True)
            body = (self.record(DAY_A, cwd=ws / "projects" / "orchard")
                    + self.record(DAY_C, cwd=outside))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        blob = json.dumps(self.sense_with(build))
        self.assertNotIn("somewhere-else", blob)
        self.assertNotIn(str(self.tmp / "sessions"), blob)


class AttributionRuleDirectly(SessionSensingCase):
    """`scan_sessions` called on its own, with no discovery in the way.

    Both tests here exist because the same assertions made through the full
    pipeline silently could not fail. Discovery registers any directory under the
    root as a project, so a sibling planted to test the path boundary becomes a
    project itself and is matched by the longest-path rule before the boundary is
    ever consulted -- the mutation that deletes the separator survives. And a
    bucket holding one record cannot tell "the newest timestamp" from "the last
    one seen", so the per-bucket recency rule needs a bucket with two.
    """

    def scan(self, build, projects):
        store = self.tmp / "store"
        root = self.tmp / "root"
        (root / "orchard").mkdir(parents=True, exist_ok=True)
        build(store, root)
        return sense.scan_sessions(str(root), projects, sessions_dir=str(store))

    def test_a_sibling_sharing_a_name_prefix_is_outside_the_project(self):
        """Without the separator, `orchard` also claims `orchardry` -- a
        different project whose work is silently credited to this one."""
        projects = [{"id": "orchard", "paths": ["orchard"]}]

        def build(store, root):
            sibling = root / "orchardry"
            sibling.mkdir(parents=True, exist_ok=True)
            self.transcript(store / sense.slugify_path(root / "orchard"), "a.jsonl",
                            body=self.record(DAY_C, cwd=sibling))

        got = self.scan(build, projects)
        self.assertEqual(got["orchard"]["distinct_session_days"], 0,
                         "orchard claimed a directory that merely starts like it")
        self.assertEqual(got["orchard"]["records_unattributed"], 1)

    def test_recency_within_a_project_is_the_newest_record_not_the_last(self):
        """File order is not time order -- in a real store 2.46% of adjacent
        records step backwards. Taking whichever landed last reports a project as
        staler than it is, and staleness is what gets it called neglected."""
        projects = [{"id": "orchard", "paths": ["orchard"]}]

        def build(store, root):
            body = (self.record(DAY_C, cwd=root / "orchard")
                    + self.record(DAY_A, cwd=root / "orchard"))
            self.transcript(store / sense.slugify_path(root / "orchard"), "a.jsonl",
                            body=body)

        got = self.scan(build, projects)
        self.assertEqual(got["orchard"]["last_active_date"],
                         dt.date.fromtimestamp(DAY_C).isoformat())
        self.assertEqual(got["orchard"]["distinct_session_days"], 2)


class TokensPerProject(SessionSensingCase):
    """Charge each message once, however many records carry it.

    Naive summing overcounts by 2.697x over a real store, from two independent
    paths that a fixture has to reproduce separately or it is only testing one.
    """

    def assistant(self, when, cwd, mid, inp=0, out=0, request=None):
        """One assistant record carrying a usage block."""
        utc = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=when)
        rec = {
            "type": "assistant",
            "timestamp": utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "cwd": str(cwd),
            "message": {"id": mid, "usage": {
                "input_tokens": inp, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0, "output_tokens": out}},
        }
        if request is not None:
            rec["requestId"] = request
        return json.dumps(rec) + "\n"

    def tokens_of(self, snap, pid="orchard", window="7"):
        return {p["id"]: p for p in snap["projects"]}[pid]["sessions"]["tokens"]

    def test_one_response_split_across_content_blocks_is_charged_once(self):
        """The API writes one record per content block -- 2.77 of them per
        message in a real store -- with the usage replicated identically. Summing
        records multiplies every figure by that factor."""
        def build(store, ws):
            orchard = ws / "projects" / "orchard"
            body = "".join(self.assistant(DAY_C, orchard, "msg_1", inp=100, out=50)
                           for _ in range(3))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        got = self.tokens_of(self.sense_with(build))
        self.assertEqual(got["output"]["7"], 50, "one message was charged three times")
        self.assertEqual(got["input"]["7"], 100)

    def test_a_message_replayed_into_another_file_is_charged_once(self):
        """Resuming a session rewrites earlier records into a new transcript, so
        26.7% of requests appear in more than one file. A per-file ledger charges
        each of them again -- which is why the ledger spans the whole scan."""
        def build(store, ws):
            orchard = ws / "projects" / "orchard"
            d = store / self.slug_for(ws)
            first = self.assistant(DAY_A, orchard, "msg_1", inp=10, out=7)
            self.transcript(d, "a.jsonl", body=first)
            self.transcript(d, "b.jsonl",
                            body=first + self.assistant(DAY_C, orchard, "msg_2",
                                                        inp=1, out=3))

        got = self.tokens_of(self.sense_with(build))
        self.assertEqual(got["output"]["7"], 10, "the replayed message was charged twice")
        self.assertEqual(got["input"]["7"], 11)

    def test_the_key_is_the_message_not_the_request(self):
        """Specified as `(message.id, requestId)`; measured, that pair is FINER
        than the message -- 19,224 message ids against 19,183 request ids -- so
        keying on it splits messages that should be charged once and leaves part
        of the overcount in place."""
        def build(store, ws):
            orchard = ws / "projects" / "orchard"
            body = (self.assistant(DAY_C, orchard, "msg_1", out=50, request="req_a")
                    + self.assistant(DAY_C, orchard, "msg_1", out=50, request="req_b"))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        self.assertEqual(self.tokens_of(self.sense_with(build))["output"]["7"], 50)

    def test_a_truncated_replica_does_not_lower_the_charge(self):
        """Replicas normally agree exactly. When they disagree the larger figure
        is the one that was not truncated: a partially written record carries a
        short count, never a long one."""
        def build(store, ws):
            orchard = ws / "projects" / "orchard"
            body = (self.assistant(DAY_C, orchard, "msg_1", out=50)
                    + self.assistant(DAY_C, orchard, "msg_1", out=0))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        self.assertEqual(self.tokens_of(self.sense_with(build))["output"]["7"], 50)

    def test_tokens_follow_the_project_the_record_was_in(self):
        def build(store, ws):
            body = (self.assistant(DAY_C, ws / "projects" / "orchard", "m1", out=10)
                    + self.assistant(DAY_C, ws / "projects" / "kiln", "m2", out=90))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        snap = self.sense_with(build)
        self.assertEqual(self.tokens_of(snap, "orchard")["output"]["7"], 10)
        self.assertEqual(self.tokens_of(snap, "kiln")["output"]["7"], 90)

    def test_a_window_excludes_what_falls_outside_it(self):
        def build(store, ws):
            orchard = ws / "projects" / "orchard"
            old = dt.datetime(2026, 2, 1, 9, 0).timestamp()
            body = (self.assistant(old, orchard, "m_old", out=1000)
                    + self.assistant(DAY_C, orchard, "m_new", out=7))
            self.transcript(store / self.slug_for(ws), "a.jsonl", body=body)

        got = self.tokens_of(self.sense_with(build))
        self.assertEqual(got["output"]["7"], 7, "a month-old message counted as this week")
        self.assertEqual(got["output"]["30"], 7)

    def test_no_money_anywhere_in_the_snapshot(self):
        """A token count is a fact the transcript states. A cost is a guess about
        a price list that changes without telling anyone, and a wrong number
        about somebody's money is worse than no number."""
        def build(store, ws):
            self.transcript(store / self.slug_for(ws), "a.jsonl",
                            body=self.assistant(DAY_C, ws / "projects" / "orchard",
                                                "m1", inp=5, out=9))

        blob = json.dumps(self.sense_with(build)).lower()
        for word in ("usd", "dollar", "\"cost\"", "price", "cents", "$"):
            self.assertNotIn(word, blob, "the snapshot priced something")


class TimestampParsing(unittest.TestCase):
    """The conversion, in isolation.

    Every timestamp in a transcript is UTC with a literal trailing Z. Every date
    elsewhere in the package is naive local. Both halves of that are tested here
    because a mistake in either is a silent off-by-one-day, not an exception.
    """

    def test_a_trailing_z_parses(self):
        """`datetime.fromisoformat` raises on this string on the floor
        interpreter and accepts it from 3.11. Since every real timestamp carries
        the Z, using it would have parsed nothing at all on 3.9 -- reporting a
        project with no sessions rather than an error."""
        got = transcripts.parse_utc_to_local("2026-03-14T09:00:00.000Z")
        self.assertIsNotNone(got)

    def test_utc_is_converted_to_local_rather_than_taken_verbatim(self):
        expect = dt.datetime.fromtimestamp(
            (dt.datetime(2026, 3, 14, 9, 0) - dt.datetime(1970, 1, 1)).total_seconds())
        self.assertEqual(transcripts.parse_utc_to_local("2026-03-14T09:00:00.000Z"),
                         expect)

    def test_fractional_seconds_are_optional(self):
        self.assertIsNotNone(transcripts.parse_utc_to_local("2026-03-14T09:00:00Z"))

    def test_junk_returns_none_rather_than_raising(self):
        for junk in (None, "", "yesterday", 17, {"t": 1}, "2026-03-14T09:00:00+05:00"):
            self.assertIsNone(transcripts.parse_utc_to_local(junk), junk)


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
