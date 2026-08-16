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
import re
import unittest

from helpers import (
    AS_OF,
    AS_OF_DATE,
    RECENT_MTIME,
    REPO_ROOT,
    TempCase,
    base_registry,
    capture,
    git,
    git_commit_all,
    git_init,
    requires_git,
    set_mtime,
    set_tree_mtime,
    write_backlog_item,
)

from nextbrief import sense
from nextbrief.discovery import discover
from nextbrief.jsonc import load_jsonc
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


class CheckCoversTheDigest(TempCase):
    """``check`` has to cover every artifact a re-run would change.

    The backlog lives only in the digest, so ``ok`` / ``done`` / ``drop`` -- the
    commands the brief itself tells you to run -- leave the snapshot untouched.
    Comparing the snapshot alone reported "current" for a brief that was already
    out of date, and a scheduler running `nextbrief check || nextbrief run` on
    the strength of it never re-ran.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()

    def _sense(self, *args):
        return capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF] + list(args))

    def test_a_new_backlog_item_alone_makes_the_run_stale(self):
        self.assertEqual(self._sense()[0], 0)
        self.assertEqual(self._sense("--check")[0], sense.EXIT_OK)
        write_backlog_item(self.ws, "orchard-001", title="Ship the tenancy rewrite")
        code, _, err = self._sense("--check")
        self.assertEqual(code, sense.EXIT_STALE)
        self.assertIn("digest", err)

    def test_an_edited_backlog_item_alone_makes_the_run_stale(self):
        write_backlog_item(self.ws, "orchard-001", title="Ship the tenancy rewrite")
        self.assertEqual(self._sense()[0], 0)
        write_backlog_item(self.ws, "orchard-001", title="Ship the tenancy rewrite",
                           status="done")
        code, _, err = self._sense("--check")
        self.assertEqual(code, sense.EXIT_STALE)
        self.assertIn("digest", err)

    def test_a_missing_digest_is_stale_even_with_a_current_snapshot(self):
        self.assertEqual(self._sense()[0], 0)
        os.remove(str(self.ws / "state" / "digest.json"))
        code, _, err = self._sense("--check")
        self.assertEqual(code, sense.EXIT_STALE)
        self.assertIn("digest.json", err)

    def test_an_unchanged_workspace_is_still_current(self):
        self.assertEqual(self._sense()[0], 0)
        code, out, _ = self._sense("--check")
        self.assertEqual(code, sense.EXIT_OK)
        self.assertIn("current", out)


class TheDigestCarriesTheCriteriaCounts(TempCase):
    """★ The evidence behind the one judgement stage 2 is asked to make. ★

    ``proposed_status: done`` is the only thing in the system that can say a
    backlog item looks finished, and both prompt locales ask for it -- while the
    digest, the model's only input, shipped sixteen fields per item and not one
    of them concerned acceptance criteria. The body was parsed one line above the
    dict the model reads and thrown away.

    Measured on the real workspace before this existed: an item reached the
    digest with ``proposed_status: null`` while its own file carried five of five
    criteria ticked, and the nightly pass ran on schedule and had nothing to say.
    It was not being cautious. It could not see.
    """

    BODY = "\n".join([
        "<!-- AC:BEGIN -->",
        "- [x] #1 (agent) the exporter writes one file per crate",
        "- [~] #2 (agent) the legacy sidecar keeps working",
        "- [ ] #3 (you) the migration guide reads right on a phone",
        "- [ ] #4 (agent) ruff is clean",
        "- [ ] #5 nobody has classified this one",
        "<!-- AC:END -->",
    ])

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()

    def _entries(self):
        """Through ``sense`` itself, because the digest is the file the model
        reads. A unit test on ``load_backlog_summary`` would pass whether or not
        the counts ever reached ``state/digest.json``."""
        code, _out, err = capture(sense.main,
                                  ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        digest = json.loads((self.ws / "state" / "digest.json").read_text(encoding="utf-8"))
        return {b["id"]: b for b in digest["backlog"]}

    def test_the_four_counts_reach_the_digest(self):
        write_backlog_item(self.ws, "NA-0001", body=self.BODY)
        entry = self._entries()["NA-0001"]
        self.assertEqual(
            (entry["criteria_done"], entry["criteria_dropped"], entry["criteria_total"],
             entry["criteria_open_needing_human"]),
            (1, 1, 5, 1),
            "the criteria counts did not reach the digest as written")

    def test_a_dropped_criterion_is_resolved_and_stays_in_the_total(self):
        """The silent regression, stated as a test.

        Miss the ``~`` mark and this item reports four criteria instead of five,
        which does not read as a bug -- it reads as an item that only ever had
        four, and the promise somebody set aside is gone with nothing left to say
        it was made. Dropped is counted separately from done for the other half
        of the same reason: "we did this" and "we stopped meaning to" are
        different answers and only one of them is an achievement.
        """
        write_backlog_item(self.ws, "NA-0001", body=self.BODY)
        entry = self._entries()["NA-0001"]
        self.assertEqual(entry["criteria_total"], 5,
                         "a dropped criterion vanished from the denominator")
        self.assertEqual(entry["criteria_dropped"], 1)
        self.assertNotIn(entry["criteria_done"], (2,),
                         "a dropped criterion was counted as done")

    def test_only_criteria_that_are_open_and_marked_you_are_counted_as_human(self):
        # Ticked and dropped `(you)` criteria are settled, and an item nobody can
        # act on tonight is a different report from an item nobody has finished.
        write_backlog_item(self.ws, "NA-0002", body="\n".join([
            "- [x] #1 (you) you already looked at it",
            "- [~] #2 (you) and this one stopped mattering",
            "- [ ] #3 (agent) one command settles this",
        ]))
        self.assertEqual(self._entries()["NA-0002"]["criteria_open_needing_human"], 0,
                         "a settled criterion was counted as waiting on a person")

    def test_the_shape_that_warrants_a_proposal_is_reportable(self):
        # done + dropped == total, total > 0, nothing open. This is the exact
        # condition both prompts now name, so the digest has to be able to say it.
        write_backlog_item(self.ws, "NA-0003", body="\n".join([
            "- [x] #1 (agent) it ships",
            "- [~] #2 (you) the old flow keeps working",
        ]))
        entry = self._entries()["NA-0003"]
        self.assertEqual(entry["criteria_done"] + entry["criteria_dropped"],
                         entry["criteria_total"])
        self.assertGreater(entry["criteria_total"], 0)
        self.assertEqual(entry["criteria_open_needing_human"], 0)

    def test_an_item_with_no_criteria_says_zero_rather_than_nothing(self):
        # `total: 0` is a statement the prompt can act on -- "this is evidence of
        # nothing" -- and a missing key is not. An absent field would arrive as
        # None and read as "unknown", which is the shape a model fills in.
        write_backlog_item(self.ws, "NA-0004", body="No checkboxes here at all.")
        entry = self._entries()["NA-0004"]
        for field in ("criteria_done", "criteria_dropped", "criteria_total",
                      "criteria_open_needing_human"):
            self.assertEqual(entry[field], 0, field)

    def test_the_criteria_text_itself_does_not_reach_the_digest(self):
        """Counts, not prose, and this is the guard on that decision.

        ``load_backlog_summary`` exists for cost: the measurement in its own
        docstring is what folding the backlog into one file bought. A count
        answers the only question being asked; the sentences would be paid for on
        every round of every night to answer it a second time.
        """
        write_backlog_item(self.ws, "NA-0005", body=self.BODY)
        self._entries()
        digest = (self.ws / "state" / "digest.json").read_text(encoding="utf-8")
        for phrase in ("one file per crate", "the legacy sidecar keeps working",
                       "reads right on a phone", "nobody has classified this one"):
            self.assertNotIn(phrase, digest,
                             "criterion text reached the model's input: %r" % phrase)


class ClosedItemsLeaveTheDigestAsNamesOnly(TempCase):
    """The digest's one unbounded term, held down.

    Every night can close an item and no night ever un-closes one, so in full
    form the closed items grow forever inside the file whose entire purpose is
    to stay small. They are also the only entries stage 2 has no judgement to
    make about: the single call it is asked for is ``proposed_status``, and an
    item that is already closed has answered it.

    So the cut is the shape, not the count -- the name survives, the decision
    fields go. Measured on this engine's own workspace, 23 closed items cost
    18.2KB in full form against 3.9KB compact.

    A recency window was written before this and deleted after measuring it: the
    workspace it was written for closes items in bursts, so a 14-day window kept
    22 of 23 and saved nothing. That is why the cap here counts items rather than
    days.

    And the cap applies to ``done`` only. The first version of this queued both
    terminal statuses together, which deletes the wrong one: ``done`` is an event
    and expires, ``dropped`` is a decision that constrains every future proposal
    and does not. Measured on the workspace this was written for, the single
    ``dropped`` item in 23 ranked 19th and fell outside the window -- a refusal
    that workspace's own agent rules quote as binding.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()

    def _digest(self):
        code, _out, err = capture(sense.main,
                                  ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        return json.loads((self.ws / "state" / "digest.json").read_text(encoding="utf-8"))

    def test_a_closed_item_is_not_in_the_actionable_list(self):
        write_backlog_item(self.ws, "NA-0001", status="open")
        write_backlog_item(self.ws, "NA-0002", status="done")
        write_backlog_item(self.ws, "NA-0003", status="dropped")
        digest = self._digest()
        self.assertEqual([b["id"] for b in digest["backlog"]], ["NA-0001"],
                         "a closed item is still occupying the list stage 2 acts on")

    def test_the_closed_block_keeps_the_name_and_drops_the_decision_fields(self):
        write_backlog_item(self.ws, "NA-0002", status="done")
        entry = self._digest()["closed"]["done"]["recent"][0]
        self.assertEqual(sorted(entry),
                         ["id", "project", "status", "title", "updated_date"])
        for gone in ("next_probe", "what_needs_human", "source_doc", "criteria_total",
                     "estimate_min", "automation_tier", "blocked_by", "priority"):
            self.assertNotIn(gone, entry,
                             "%s survived into a closed entry, where no decision "
                             "is left for it to feed" % gone)

    def test_a_dropped_entry_is_named_the_same_way_a_done_one_is(self):
        write_backlog_item(self.ws, "NA-0003", status="dropped")
        entry = self._digest()["closed"]["dropped"][0]
        self.assertEqual(sorted(entry),
                         ["id", "project", "status", "title", "updated_date"])
        self.assertEqual(entry["status"], "dropped",
                         "the reader cannot tell a refusal from a completion "
                         "without the status it was filed under")

    def test_the_cap_reports_the_number_it_capped_from(self):
        """A cap nobody can see reads as "that was all of them"."""
        for n in range(sense.DIGEST_CLOSED_SHOWN + 3):
            write_backlog_item(self.ws, "NA-%04d" % (n + 10), status="done",
                               updated_date="2026-03-%02d" % (n + 1))
        closed = self._digest()["closed"]
        self.assertEqual(closed["total"], sense.DIGEST_CLOSED_SHOWN + 3)
        self.assertEqual(closed["done"]["total"], sense.DIGEST_CLOSED_SHOWN + 3)
        self.assertEqual(closed["done"]["shown"], sense.DIGEST_CLOSED_SHOWN)
        self.assertEqual(len(closed["done"]["recent"]), sense.DIGEST_CLOSED_SHOWN)

    def test_the_ones_kept_are_the_ones_closed_most_recently(self):
        # Ordered by when the item last moved, not by id. Ids are minted in
        # creation order, so sorting by id would read as recency and not be it --
        # in the workspace this was written for, NA-0001 was closed three weeks
        # after NA-0050 was.
        write_backlog_item(self.ws, "NA-0001", status="done", updated_date="2026-03-14")
        write_backlog_item(self.ws, "NA-0050", status="done", updated_date="2026-02-01")
        recent = self._digest()["closed"]["done"]["recent"]
        self.assertEqual([e["id"] for e in recent], ["NA-0001", "NA-0050"],
                         "closed items came back in id order, which is not recency")

    def test_every_dropped_item_survives_however_old_it_is(self):
        """The one this class was reopened for.

        `dropped` is the rare kind and the durable kind at once, so a recency cap
        over the pooled list deletes it first and deletes it silently. The
        fixture is the shape of the real failure: enough `done` to fill the cap
        twice over, and the refusals older than all of them.
        """
        for n in range(sense.DIGEST_CLOSED_SHOWN * 2):
            write_backlog_item(self.ws, "NA-%04d" % (n + 20), status="done",
                               updated_date="2026-05-%02d" % (n + 1))
        write_backlog_item(self.ws, "NA-0011", status="dropped",
                           updated_date="2026-01-02")
        write_backlog_item(self.ws, "NA-0012", status="dropped",
                           updated_date="2026-01-01")

        closed = self._digest()["closed"]
        self.assertEqual([e["id"] for e in closed["dropped"]], ["NA-0011", "NA-0012"],
                         "a refusal aged out of the digest -- which is the one "
                         "thing a decision is not allowed to do")
        self.assertEqual(closed["done"]["shown"], sense.DIGEST_CLOSED_SHOWN,
                         "keeping the refusals must not relax the cap on `done`")
        self.assertEqual(closed["total"], sense.DIGEST_CLOSED_SHOWN * 2 + 2)

    def test_a_dropped_item_never_occupies_a_capped_slot(self):
        """The mutation guard for the test above.

        Pooling the two statuses back into one capped list passes
        `test_every_dropped_item_survives...` for the wrong reason whenever the
        refusals happen to be recent. Here they are the NEWEST entries, so a
        pooled implementation puts them in `recent` and pushes a `done` out --
        and the only visible symptom is a count.
        """
        for n in range(sense.DIGEST_CLOSED_SHOWN):
            write_backlog_item(self.ws, "NA-%04d" % (n + 20), status="done",
                               updated_date="2026-05-%02d" % (n + 1))
        write_backlog_item(self.ws, "NA-0011", status="dropped",
                           updated_date="2026-09-01")

        closed = self._digest()["closed"]
        self.assertEqual(closed["done"]["shown"], sense.DIGEST_CLOSED_SHOWN,
                         "a dropped item took a slot the cap had reserved for "
                         "`done`, so a completion fell off the end to make room")
        self.assertNotIn("NA-0011", [e["id"] for e in closed["done"]["recent"]],
                         "a refusal was filed under `done`")

    def test_a_deferred_item_is_still_actionable_and_stays_in_the_list(self):
        # `deferred` is a human parking something with a date on it, not an
        # ending. Folding it in with the closed ones would hide a decision that
        # is still owed from the one pass that is meant to surface it.
        write_backlog_item(self.ws, "NA-0004", status="deferred")
        digest = self._digest()
        self.assertEqual([b["id"] for b in digest["backlog"]], ["NA-0004"])
        self.assertEqual(digest["closed"]["total"], 0)
        self.assertEqual(digest["closed"]["dropped"], [])


class NestedCheckoutsArePrunedAndCounted(TempCase):
    """A second copy of somebody's tree is not this project's week of work.

    Materialising a checkout -- ``git worktree add``, ``clone``, a submodule
    update -- writes every file at once, so an mtime-derived window swallows the
    whole subtree on the day it appeared. Measured 2026-08-15 on the portfolio
    this engine runs against: **540 of the 595 files** one project reported as
    changed in seven days were a single agent worktree created three days
    earlier, and three of its eight ``top_changed_paths`` pointed inside it. The
    true figure was 55. Claude Code creates these directories by itself, so
    nobody has to act for it to recur.

    The rule is "is this directory a checkout", never a list of names to skip:
    the list would need an entry per tool per location, and this portfolio has
    already watched one such list grow from one site to two to four.
    """

    STEM = "only-in-the-checkout"     # appears nowhere else in the fixture

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        self.orchard = self.ws / "projects" / "orchard"

    def _snapshot(self):
        code, _out, err = capture(sense.main,
                                  ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        return (self.ws / "state" / "snapshot.json").read_text(encoding="utf-8")

    def _orchard(self):
        return {p["id"]: p for p in json.loads(self._snapshot())["projects"]}["orchard"]

    def _plant(self, rel, marker=".git", content="gitdir: /elsewhere/.git/worktrees/wt\n",
               extra=("a.py", "b.py")):
        """A checkout at ``rel``, with ``marker`` as a file or a directory."""
        base = self.orchard / rel
        base.mkdir(parents=True, exist_ok=True)
        if content is None:
            (base / marker).mkdir(exist_ok=True)
            (base / marker / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        else:
            (base / marker).write_text(content, encoding="utf-8")
        for name in extra:
            (base / ("%s-%s" % (self.STEM, name))).write_text("x = 1\n", encoding="utf-8")
        set_tree_mtime(base, RECENT_MTIME)
        return base

    def test_its_files_are_not_counted_as_the_projects_own(self):
        before = self._orchard()["fs"]
        self._plant(".claude/worktrees/wt")
        after = self._orchard()["fs"]
        self.assertEqual(after["total_files"], before["total_files"],
                         "a checkout added files to the project's own count")
        self.assertEqual(after["changed"], before["changed"],
                         "a checkout registered as a week of activity")

    def test_no_path_inside_one_reaches_the_snapshot(self):
        self._plant(".claude/worktrees/wt")
        # A substring search over the raw serialisation, the same way the privacy
        # tests are written: a path leaking into a field nobody thought of is the
        # failure worth catching, and a structured walk would miss it.
        self.assertNotIn(self.STEM, self._snapshot())

    def test_the_prune_is_reported_with_its_kind_and_size(self):
        # Without this the fix is just a quieter defect: the files stop being
        # counted and nothing says they were ever there.
        self._plant(".claude/worktrees/wt")
        nested = self._orchard()["fs"]["nested_checkouts"]
        self.assertEqual(len(nested), 1, nested)
        self.assertEqual(nested[0]["rel"], "orchard/.claude/worktrees/wt")
        self.assertEqual(nested[0]["kind"], "worktree")
        self.assertEqual(nested[0]["files"], 2, "the two planted files")

    def test_a_dot_git_directory_is_a_repository_not_a_worktree(self):
        self._plant("vendored", content=None)
        nested = self._orchard()["fs"]["nested_checkouts"]
        self.assertEqual([(n["rel"], n["kind"]) for n in nested],
                         [("orchard/vendored", "repo")])

    def test_a_pointer_that_is_not_a_worktree_is_labelled_honestly(self):
        # Submodules carry the same one-line pointer; only the worktree form
        # names `.git/worktrees/`. Guessing "worktree" for both would put a wrong
        # word in the snapshot, and a wrong label is worse than a vague one.
        self._plant("sub", content="gitdir: /elsewhere/.git/modules/sub\n")
        self.assertEqual(self._orchard()["fs"]["nested_checkouts"][0]["kind"], "linked")

    def test_the_reported_size_is_what_would_have_been_counted(self):
        # Not a raw file count: the number is read as "this many were left out of
        # your total", and an overstated exclusion misleads as badly as a hidden
        # one. `**/__pycache__/**` is ignored for the project, so it is ignored
        # inside the checkout too.
        base = self._plant(".claude/worktrees/wt")
        (base / "__pycache__").mkdir()
        for n in range(5):
            (base / "__pycache__" / ("m%d.pyc" % n)).write_text("", encoding="utf-8")
        set_tree_mtime(base, RECENT_MTIME)
        self.assertEqual(self._orchard()["fs"]["nested_checkouts"][0]["files"], 2)

    def test_a_checkout_under_a_private_path_is_never_named(self):
        # Privacy is checked first on purpose. Reporting the prune here would put
        # a private directory's name into the snapshot through a side door -- the
        # exact leak the never_read rule exists to prevent.
        kiln = self.ws / "projects" / "kiln" / "fixtures" / "private" / "wt"
        kiln.mkdir(parents=True, exist_ok=True)
        (kiln / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")
        (kiln / ("%s-c.py" % self.STEM)).write_text("x = 1\n", encoding="utf-8")
        set_tree_mtime(kiln, RECENT_MTIME)

        text = self._snapshot()
        self.assertNotIn(self.STEM, text)
        self.assertNotIn("fixtures/private/wt", text)
        by_id = {p["id"]: p for p in json.loads(text)["projects"]}
        self.assertEqual(by_id["kiln"]["fs"]["nested_checkouts"], [])

    def test_the_walk_root_may_itself_be_a_checkout(self):
        """The boundary the rule must not cross.

        Every project of a portfolio kept in linked worktrees would otherwise
        report zero files -- the fix erasing the thing it was meant to measure.
        Asserted against `walk_project` directly, because routing it through a
        full run would also exercise the git probes and stop testing the walk.
        """
        root = self.tmp / "root-is-a-worktree"
        (root / "src").mkdir(parents=True)
        (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")
        (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
        (root / "README.md").write_text("# hi\n", encoding="utf-8")
        set_tree_mtime(root, RECENT_MTIME)

        out = sense.walk_project(root, sense.PathFilter(["**/.git/**"]),
                                 AS_OF_DATE, (7, 30))
        self.assertEqual(out["total_files"], 2,
                         "the project root was pruned as somebody else's checkout")
        self.assertEqual(out["nested_checkouts"], [])

    def test_checkout_kind_is_a_predicate_on_a_plain_directory(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertIsNone(sense.checkout_kind(plain))


@requires_git
class WorktreesFoldIntoTheirRepository(TempCase):
    """A linked worktree is a branch with a directory, not a second project.

    Measured 2026-08-15 on the portfolio this engine runs against: a worktree
    sitting beside its repository was adopted as its own project and reported
    350 commits/30d next to the repository's 369 -- most of them the same
    commits -- so portfolio activity read high and the project count read 16
    instead of 14. The owner's words were "I thought it should be a branch". It
    already was one; the engine could not see it.

    Three readings of the commit count were available and only one is a fact:
    summing the checkouts double-counts shared history (369 + 350 = 719 for a
    repository holding 375 commits), reading the primary checkout alone drops
    whatever has not been merged (six real commits here), and the sha union is
    neither. The owner ruled for the union on 2026-08-15.
    """

    def setUp(self):
        super().setUp()
        self.projects = self.tmp / "portfolio"
        self.projects.mkdir(parents=True, exist_ok=True)

    def _repo(self, name="atlas"):
        repo = self.projects / name
        (repo).mkdir(parents=True, exist_ok=True)
        (repo / "README.md").write_text("# %s\n" % name, encoding="utf-8")
        git_init(repo)
        git_commit_all(repo, "%s: first" % name)
        return repo

    def _worktree(self, repo, at, branch="side", commits=1):
        """A linked worktree of ``repo`` at ``at``, ``commits`` ahead of main."""
        git(repo, "worktree", "add", "-b", branch, str(at))
        for n in range(commits):
            (at / ("extra%d.txt" % n)).write_text("%d\n" % n, encoding="utf-8")
            git_commit_all(at, "side: commit %d" % n)
        return at

    # -- D6: discovery ------------------------------------------------------

    def test_a_worktree_beside_its_repository_is_not_a_second_project(self):
        repo = self._repo()
        self._worktree(repo, self.projects / "atlas-side")
        found = {e["id"] for e in discover(self.projects, {"projects": []})}
        self.assertEqual(found, {"atlas"},
                         "the worktree was adopted as a project of its own, so "
                         "its repository's commits are about to be counted twice")

    def test_a_worktree_whose_repository_is_elsewhere_is_still_discovered(self):
        """The boundary that makes this "fold", not "ignore".

        If the repository is outside the portfolio, the worktree is the only copy
        of that work anywhere in it. Skipping it would hide the work rather than
        deduplicate it -- and a portfolio kept entirely in linked checkouts would
        discover nothing at all.
        """
        outside = self._repo("offsite")
        target = self.projects / "offsite-work"
        # Move the repository out of the portfolio, leaving only the worktree.
        moved = self.tmp / "elsewhere" / "offsite"
        moved.parent.mkdir(parents=True, exist_ok=True)
        git(outside, "worktree", "add", "-b", "side", str(target))
        os.rename(str(outside), str(moved))
        git(moved, "worktree", "repair", str(target))

        found = {e["id"] for e in discover(self.projects, {"projects": []})}
        self.assertIn("offsite-work", found,
                      "a worktree whose repository is not in the portfolio is "
                      "the only copy of that work, and it was dropped")

    def test_a_declared_repository_still_wins_over_discovery(self):
        # The skip must not swallow a directory somebody named by hand.
        repo = self._repo()
        self._worktree(repo, self.projects / "atlas-side")
        reg = {"projects": [{"id": "declared-side", "paths": ["atlas-side"]}]}
        self.assertEqual([e["id"] for e in discover(self.projects, reg)], ["atlas"])

    # -- D5: counting -------------------------------------------------------

    def _facts(self, repo, rel):
        return sense.git_facts(self.projects, [rel], AS_OF_DATE)[0]

    def test_the_count_is_the_union_not_the_primary_checkout(self):
        repo = self._repo()
        self._worktree(repo, self.projects / "atlas-side", commits=3)
        facts = self._facts(repo, "atlas")

        # 1 on main + 3 on the worktree's branch, none of them shared. Reading
        # only the primary checkout gives 1, which is the reading that dropped
        # six real commits on the portfolio this was written for.
        self.assertEqual(facts["commits_since"]["30"], 4)

        primary_only = git(repo, "rev-list", "--count", "--since=2026-02-14", "HEAD")
        self.assertEqual(int(primary_only.stdout.strip()), 1,
                         "fixture is wrong: the branch is not actually ahead")

    def test_shared_history_is_counted_once_not_once_per_checkout(self):
        # The failure this replaces: two checkouts of the same repository, each
        # reporting the whole shared history, added together.
        repo = self._repo()
        self._worktree(repo, self.projects / "atlas-side", commits=0)
        self.assertEqual(self._facts(repo, "atlas")["commits_since"]["30"], 1,
                         "the one shared commit was counted once per checkout")

    def test_the_checkouts_the_count_came_from_are_reported(self):
        repo = self._repo()
        self._worktree(repo, self.projects / "atlas-side")
        wts = self._facts(repo, "atlas")["worktrees"]
        self.assertEqual([w["path"] for w in wts], ["atlas", "atlas-side"])
        self.assertEqual([w["branch"] for w in wts], ["main", "side"])
        self.assertEqual([w["primary"] for w in wts], [True, False])

    def test_an_ordinary_repository_reports_no_worktrees_at_all(self):
        # Everyone who does not use worktrees keeps the shape they had.
        repo = self._repo()
        self.assertEqual(self._facts(repo, "atlas")["worktrees"], [])


class ThePromptsNameTheDigestFieldsInBothLocales(TempCase):
    """Two failures, one check, and this repository has produced both.

    A field named in ``daily.en.md`` and not in ``daily.zh.md`` is a capability
    that exists in one locale -- the Chinese nightly pass is told to judge from
    data it was never told is there. A field named in a prompt that the digest
    does not ship is the mirror image: an instruction to read something that is
    not in the file.

    Matched inside code spans and fenced blocks only. ``file``, ``title`` and
    ``status`` are ordinary English words, and matching them in prose made the
    comparison agree for reasons that had nothing to do with the prompt naming a
    field.
    """

    PROMPT_DIR = REPO_ROOT / "src" / "nextbrief" / "prompts"
    COUNTS = {"criteria_done", "criteria_dropped", "criteria_total",
              "criteria_open_needing_human"}

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        write_backlog_item(self.ws, "NA-0001")

    @staticmethod
    def _code(text):
        fenced = re.findall(r"```.*?```", text, flags=re.S)
        rest = re.sub(r"```.*?```", "", text, flags=re.S)
        return "\n".join(fenced + re.findall(r"`[^`\n]+`", rest))

    def _named(self, prompt, fields):
        text = self._code((self.PROMPT_DIR / prompt).read_text(encoding="utf-8"))
        return {f for f in fields if re.search(r"\b%s\b" % re.escape(f), text)}

    def _shipped(self):
        """The real field list, from the real function, over a real entry."""
        entries = sense.load_backlog_summary(resolve_workspace(str(self.ws)))
        self.assertEqual(len(entries), 1, "the fixture wrote no backlog entry")
        return set(entries[0])

    def test_every_field_one_prompt_names_the_other_names_too(self):
        fields = self._shipped()
        en = self._named("daily.en.md", fields)
        zh = self._named("daily.zh.md", fields)
        self.assertEqual(
            sorted(en - zh), [],
            "daily.en.md names digest fields daily.zh.md never mentions")
        self.assertEqual(
            sorted(zh - en), [],
            "daily.zh.md names digest fields daily.en.md never mentions")

    def test_both_prompts_name_the_criteria_counts(self):
        # The check above is satisfied by two prompts that both say nothing, so
        # it cannot stand alone: this one says which fields have to be in there.
        for prompt in ("daily.en.md", "daily.zh.md"):
            with self.subTest(prompt=prompt):
                self.assertEqual(self._named(prompt, self.COUNTS), self.COUNTS,
                                 "%s never names %s" % (
                                     prompt,
                                     sorted(self.COUNTS - self._named(prompt, self.COUNTS))))

    def test_the_counts_the_prompts_name_are_counts_the_digest_ships(self):
        # The other direction. A prompt telling the model to read
        # `criteria_total` when nothing writes it is a rule that can never fire,
        # and nothing downstream would ever go red.
        self.assertEqual(sorted(self.COUNTS - self._shipped()), [],
                         "the prompts name criteria counts load_backlog_summary "
                         "does not put in the digest")


class GitPathResolution(TempCase):
    """A project reached through a symlink, or spelled in another case, is a live
    project. Both used to report as a dead one.

    ``rev-parse --show-toplevel`` answers with the physical path in git's own
    casing. A relpath computed from the unresolved registry path therefore
    escapes the repository, and git accepts the resulting pathspec, matches
    nothing and exits 0 -- so the project came back with no branch, no commits
    and an empty ``parse_failed``. Silence that reads as fact is the failure mode
    this file exists to avoid.
    """

    def _sense(self, ws):
        return capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])

    def _orchard(self, ws):
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        return {p["id"]: p for p in snap["projects"]}["orchard"], snap

    def _case_insensitive(self) -> bool:
        probe = self.tmp / "CaseProbe"
        probe.mkdir(exist_ok=True)
        return (self.tmp / "caseprobe").is_dir()

    @requires_git
    def test_a_project_reached_through_a_symlink_keeps_its_git_evidence(self):
        ws = self.workspace()
        real = ws / "projects-real"
        os.rename(str(ws / "projects"), str(real))
        os.symlink(str(real), str(ws / "projects"))   # defaults.root now a symlink

        code, _, err = self._sense(ws)
        self.assertEqual(code, 0, err)
        orchard, _snap = self._orchard(ws)
        self.assertTrue(orchard["has_git"])
        self.assertFalse(orchard["git"][0]["no_commits"])
        self.assertEqual(orchard["git"][0]["commits_since"]["30"], 1)
        self.assertEqual(orchard["git"][0]["pathspec"], [])

    @requires_git
    def test_a_registry_path_in_the_wrong_case_keeps_its_git_evidence(self):
        if not self._case_insensitive():
            self.skipTest("filesystem is case-sensitive, so the two paths are two places")
        ws = self.workspace()
        # The registry says ./projects; the directory on disk is PROJECTS. Every
        # part of the OS accepts that except a git pathspec.
        os.rename(str(ws / "projects"), str(ws / "PROJECTS"))

        code, _, err = self._sense(ws)
        self.assertEqual(code, 0, err)
        orchard, _snap = self._orchard(ws)
        self.assertTrue(orchard["has_git"])
        self.assertFalse(orchard["git"][0]["no_commits"])
        self.assertEqual(orchard["git"][0]["commits_since"]["30"], 1)

    @requires_git
    def test_a_pathspec_matching_nothing_is_recorded_not_reported_as_silence(self):
        ws = self.workspace()
        untracked = ws / "projects" / "orchard" / "untracked"
        untracked.mkdir()
        (untracked / "notes.md").write_text("# not committed\n", encoding="utf-8")
        set_tree_mtime(ws / "projects")

        reg = base_registry()
        reg["projects"] = [dict(reg["projects"][0], id="orchard-sub",
                                paths=["orchard/untracked"], status_docs=[],
                                non_goals_doc=None)]
        (ws / "registry.jsonc").write_text(json.dumps(reg, indent=2), encoding="utf-8")

        code, _, err = self._sense(ws)
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        failures = [f for f in snap["parse_failed"] if f["code"] == "git_pathspec_unmatched"]
        self.assertEqual(len(failures), 1, snap["parse_failed"])
        self.assertEqual(failures[0]["path"], "orchard-sub")
        # And the empty answer is still reported, just no longer as the whole story.
        self.assertTrue(snap["projects"][0]["git"][0]["no_commits"])

    def test_repo_subpath_resolves_a_symlinked_path(self):
        real = self.tmp / "real"
        (real / "pkg").mkdir(parents=True)
        link = self.tmp / "link"
        os.symlink(str(real), str(link))
        self.assertEqual(sense.repo_subpath(link / "pkg", real), "pkg")
        self.assertEqual(sense.repo_subpath(link, real), ".")

    def test_repo_subpath_answers_in_the_casing_on_disk(self):
        if not self._case_insensitive():
            self.skipTest("filesystem is case-sensitive, so the two paths are two places")
        repo = self.tmp / "repo"
        (repo / "apps" / "portal").mkdir(parents=True)
        self.assertEqual(sense.repo_subpath(repo / "APPS" / "Portal", repo), "apps/portal")

    def test_repo_subpath_refuses_a_path_outside_the_repository(self):
        repo = self.tmp / "repo"
        other = self.tmp / "other"
        repo.mkdir()
        other.mkdir()
        self.assertIsNone(sense.repo_subpath(other, repo))


class GitIsReadOnly(TempCase):
    """Sensing must leave every repository it reads exactly as it found it.

    Without ``--no-optional-locks`` git refreshes and rewrites ``.git/index``
    (taking ``index.lock`` on the way) in every repository it is asked about --
    which contradicts the invariant this file opens with and can collide with an
    editor or a build running in the same tree.
    """

    @requires_git
    def test_sensing_does_not_rewrite_the_git_index(self):
        ws = self.workspace()
        index = ws / "projects" / "orchard" / ".git" / "index"
        before = (index.read_bytes(), index.stat().st_mtime_ns)

        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)

        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)
        self.assertFalse((index.parent / "index.lock").exists())


class Hotspots(TempCase):
    """Hotspots come from git, which reports paths the walk never sees."""

    VENDOR = "vendor/bundled.js"
    OWN = "src/engine.py"

    def _workspace(self, scc="/nonexistent/scc"):
        reg = base_registry()
        reg["projects"] = [reg["projects"][0]]
        reg["projects"][0]["ignore_globs"] = ["**/vendor/**"]
        cfg = None
        ws = self.workspace(registry=reg, config=cfg)
        orchard = ws / "projects" / "orchard"
        for rel, lines in ((self.VENDOR, 200), (self.OWN, 90)):
            path = orchard / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join("x = %d" % i for i in range(lines)) + "\n",
                            encoding="utf-8")
        git_commit_all(orchard, "orchard: vendored dependency and engine")
        set_tree_mtime(ws / "projects")

        # Pinned to a path that does not exist so the run is the same on a
        # machine with scc installed and one without.
        config = json.loads(
            (ws / "config.jsonc").read_text(encoding="utf-8").split("\n", 1)[1])
        config["external_tools"] = {"scc": scc}
        (ws / "config.jsonc").write_text(json.dumps(config, indent=2), encoding="utf-8")
        return ws

    def _hotspots(self, ws):
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        return snap["projects"][0]

    @requires_git
    def test_an_ignored_vendor_tree_does_not_take_the_hotspot_slots(self):
        # 200 churned lines of vendored code outrank 90 of your own on every
        # measure, which is exactly why the ignore list has to reach this far.
        project = self._hotspots(self._workspace())
        paths = [h["path"] for h in project["hotspots"]]
        self.assertIn(self.OWN, paths)
        self.assertNotIn(self.VENDOR, paths)

    @requires_git
    def test_the_metric_names_the_measure_that_was_used(self):
        # A fake scc that parses but reports nothing: installed is not the same
        # as having answered, and the label may not claim complexity over a list
        # that was ranked by line count.
        fake = self.tmp / "bin" / "scc"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text("#!/bin/sh\necho '[]'\n", encoding="utf-8")
        os.chmod(str(fake), 0o755)

        project = self._hotspots(self._workspace(scc=str(fake)))
        self.assertTrue(project["hotspots"])
        self.assertTrue(all(h["complexity"] is None for h in project["hotspots"]))
        self.assertEqual(project["hotspot_metric_kind"], "lines")
        self.assertIn("line count", project["hotspot_metric"])


class MisconfiguredInputs(TempCase):
    """A broken input file names itself. Sending the reader to edit the wrong
    file, or handing them an AttributeError, is a worse answer than no answer."""

    def _sense(self, ws, *args):
        return capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF] + list(args))

    def _write_registry(self, ws, registry):
        (ws / "registry.jsonc").write_text(json.dumps(registry, indent=2), encoding="utf-8")

    def test_an_undecodable_config_is_reported_as_a_config_problem(self):
        # UnicodeDecodeError is a ValueError raised by the read, not the parser,
        # so a shared handler blamed it on --as-of: a flag the reader never used.
        ws = self.workspace()
        (ws / "config.jsonc").write_bytes(b'{"locale": "\xff\xfe not utf-8"}\n')
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("config.jsonc", err)
        self.assertNotIn("--as-of", err)

    def test_an_undecodable_registry_is_reported_as_a_registry_problem(self):
        ws = self.workspace()
        (ws / "registry.jsonc").write_bytes(b'{"meta": "\xff\xfe not utf-8"}\n')
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("registry.jsonc", err)
        self.assertNotIn("config.jsonc", err)

    def test_a_registry_problem_does_not_blame_config(self):
        ws = self.workspace()
        reg = base_registry()
        del reg["projects"][0]["id"]
        self._write_registry(ws, reg)
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("projects[0].id", err)
        self.assertNotIn("config.jsonc", err)

    def test_projects_must_be_an_array(self):
        ws = self.workspace()
        reg = base_registry()
        reg["projects"] = {"orchard": {"paths": ["orchard"]}}
        self._write_registry(ws, reg)
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("registry projects must be an array, not object", err)
        self.assertFalse((ws / "state" / "snapshot.json").exists())

    def test_a_project_entry_must_be_an_object(self):
        ws = self.workspace()
        reg = base_registry()
        reg["projects"][1] = "kiln"
        self._write_registry(ws, reg)
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("registry projects[1] must be an object, not string", err)

    def test_project_paths_must_be_an_array(self):
        ws = self.workspace()
        reg = base_registry()
        reg["projects"][0]["paths"] = "orchard"
        self._write_registry(ws, reg)
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("projects[0].paths must be an array, not string", err)

    def test_never_read_must_be_an_array(self):
        ws = self.workspace()
        reg = base_registry()
        reg["projects"][1]["privacy"]["never_read"] = "fixtures/private/**"
        self._write_registry(ws, reg)
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("never_read must be an array of globs", err)

    def test_a_watch_entry_must_carry_a_path(self):
        ws = self.workspace()
        reg = base_registry()
        reg["watch"] = ["projects/orchard"]
        self._write_registry(ws, reg)
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("registry watch[0] must be an object with a path", err)

    def test_config_must_be_an_object(self):
        ws = self.workspace()
        (ws / "config.jsonc").write_text("[]\n", encoding="utf-8")
        code, _, err = self._sense(ws)
        self.assertEqual(code, sense.EXIT_ERROR)
        self.assertIn("config.jsonc must be a JSON object, not array", err)


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


class CheckableDeclarations(TempCase):
    """`git: "none"` is a claim about the world, and the world can be asked.

    The registry wins over the overlay because someone who opened their own file
    said something deliberate. That rule is about *judgements* — importance,
    phase, positioning — none of which anything else can measure. Whether a
    directory is a git repository is not a judgement, and a declaration of it
    goes stale the moment somebody runs `git init`.

    What it costs to trust the stale one: the brief prints "a bad delete is
    unrecoverable" every morning about a repository that has been recording
    every change all along. A warning that is *false* is worse than one that is
    merely frequent, because acting on it wastes the reader's time and not
    acting on it teaches them to skip the column.
    """

    def _ws_declaring_none_with_a_repo(self):
        ws = self.workspace()
        reg = load_jsonc(str(ws / "registry.jsonc"))
        # `kiln` is the fixture's non-git project. Declare it none, then make it
        # a repository anyway -- which is exactly what `git init` on a directory
        # somebody described months ago produces.
        target = [p for p in reg["projects"] if p["id"] == "kiln"][0]
        target["git"] = "none"
        (ws / "registry.jsonc").write_text(json.dumps(reg, indent=2), encoding="utf-8")
        (ws / "projects" / "kiln" / ".git").mkdir(parents=True, exist_ok=True)
        return ws

    def test_a_repository_under_a_none_declaration_is_reported(self):
        ws = self._ws_declaring_none_with_a_repo()
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        codes = [f["code"] for f in snap["parse_failed"]]
        self.assertIn("git_declared_none_but_present", codes)

    def test_the_observation_is_recorded_beside_the_declaration_not_over_it(self):
        # Both survive: `git_declared` is what a person typed and is never
        # rewritten by us; `git_present` is what the disk says. Downstream has to
        # be able to tell a claim from a measurement, which is the whole reason
        # the snapshot separates declared from observed everywhere else.
        ws = self._ws_declaring_none_with_a_repo()
        capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        kiln = {p["id"]: p for p in snap["projects"]}["kiln"]
        self.assertEqual(kiln["git_declared"], "none")
        self.assertTrue(kiln["git_present"])

    def test_the_daily_false_warning_actually_stops_appearing(self):
        """The user-visible half of this, which had no test at all.

        Everything else here asserts on snapshot fields. Nobody rendered a brief
        and looked for the sentence the whole change exists to remove -- so the
        clause suppressing it could be deleted with all 623 tests green, which a
        mutation audit duly demonstrated.
        """
        ws = self._ws_declaring_none_with_a_repo()
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        from nextbrief import render as render_mod
        code, _, err = capture(render_mod.main, ["--workspace", str(ws), "--no-notify"])
        self.assertEqual(code, 0, err)
        brief = (ws / "BRIEF.md").read_text(encoding="utf-8")
        offending = [ln for ln in brief.splitlines() if "unrecoverable" in ln]
        self.assertEqual(offending, [],
                         "the brief still calls a repository unrecoverable: %r" % offending)

    def test_an_honest_none_declaration_is_not_nagged(self):
        # The half that decides whether the check above is a fix or a new daily
        # warning: a project that really has no repository must produce nothing.
        ws = self.workspace()
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        codes = [f["code"] for f in snap["parse_failed"]]
        self.assertNotIn("git_declared_none_but_present", codes)
        kiln = {p["id"]: p for p in snap["projects"]}["kiln"]
        self.assertFalse(kiln["git_present"])


class SessionEvidence(TempCase):
    """What a `session:<id>` citation is allowed to mean.

    The gate resolves a claim's source against the evidence index and, for
    `commit` and `session`, checks that the source can supply that kind of fact.
    Neither check looks at magnitude — so a handle that exists at all is a handle
    a model can cite, and everything downstream of it is trusted.
    """

    def _sense_with_sessions(self, make):
        ws = self.workspace()
        sessions = self.tmp / "sessions"
        sessions.mkdir(exist_ok=True)
        # The scan matches a directory NAME against the slugified project path,
        # which is how the agent names them.
        slug = sense.slugify_path(ws / "projects" / "orchard")
        make(sessions / slug)
        cfg = load_jsonc(str(ws / "config.jsonc"))
        cfg["sessions"] = {"dir": str(sessions)}
        (ws / "config.jsonc").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        return json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))

    def test_a_directory_with_no_transcripts_mints_no_citable_handle(self):
        """An empty session directory is not evidence of a session.

        `scan_sessions` creates a project's entry as soon as the directory name
        matches, so a project that has never had an agent session — but whose
        directory survives, which is the normal state after a transcript is
        cleaned up — carried a `sessions` block full of zeros. That block is a
        truthy dict, and the handle was minted on its truthiness rather than on
        anything in it.

        The consequence is not cosmetic. The handle resolves, the kind matches,
        and the model may therefore write "three agent sessions this week" about
        a project with none and have it printed under a footer promising every
        claim was checked.
        """
        snap = self._sense_with_sessions(lambda d: d.mkdir())
        index = snap["evidence_index"]
        self.assertNotIn("session:orchard", index,
                         "a handle was minted for a project with no sessions")

    def test_a_directory_with_a_transcript_does_mint_one(self):
        # The other half, and the one that decides whether the rule above is a
        # fix or an amputation: a real session must still be citable.
        def make(d):
            d.mkdir()
            (d / "0198c1f4-1111-2222-3333-444455556666.jsonl").write_text(
                '{"type":"user"}\n', encoding="utf-8")
            set_mtime(d / "0198c1f4-1111-2222-3333-444455556666.jsonl", RECENT_MTIME)

        snap = self._sense_with_sessions(make)
        self.assertIn("session:orchard", snap["evidence_index"])
        orchard = {p["id"]: p for p in snap["projects"]}["orchard"]
        self.assertEqual(orchard["sessions"]["session_files"], 1)


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

    def test_a_file_dated_in_the_future_is_clamped_and_recorded(self):
        """A clock ahead of this one must not become negative recency.

        The recency contest picks the smallest age, so a future date wins it and
        travels on as a negative `days_since` -- which the scorer raises 0.5 to
        the power of. Clamped rather than dropped, because a file stamped
        tomorrow was almost certainly touched today and discarding the candidate
        would report "no signal" for a project that is plainly moving.

        Recorded as well as clamped: an engine that quietly corrects its input
        teaches you to trust input it has corrected.
        """
        ws = self.workspace()
        ahead = dt.datetime.combine(
            AS_OF_DATE + dt.timedelta(days=400), dt.time(12, 0)
        ).timestamp()
        set_mtime(ws / "projects" / "orchard" / "PROJECT_STATUS.md", ahead)
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        orchard = {p["id"]: p for p in snap["projects"]}["orchard"]
        self.assertGreaterEqual(orchard["evidence"]["days_since"], 0)
        recorded = [f for f in snap["parse_failed"]
                    if f["code"] == "future_dated_evidence"]
        self.assertTrue(recorded, snap["parse_failed"])

    def test_a_hand_written_lead_days_does_not_take_the_run_down(self):
        """`registry.jsonc` invites hand-editing and `check_shapes` never reaches
        this leaf, so `"lead_days": "21"` arrives here as a string and
        `0 <= days_until <= lead` raises `TypeError` out of the sense stage.

        On the unattended path that is a stack trace and no brief at all -- for
        every other project too, which is exactly what rule 6 exists to prevent.

        `null` is the one worth naming: `dl.get("lead_days", 21)` returns the
        default only when the key is *absent*, so writing it explicitly as null,
        which reads like "no lead window", is the fastest way to break the run.
        """
        ws = self.workspace()
        reg = load_jsonc(str(ws / "registry.jsonc"))
        reg["projects"][0]["deadlines"] = [
            {"label": "hand-edited", "date": "2026-04-01", "lead_days": "21"},
            {"label": "explicit null", "date": "2026-04-02", "lead_days": None},
            {"label": "nonsense", "date": "2026-04-03", "lead_days": "soon"},
        ]
        (ws / "registry.jsonc").write_text(json.dumps(reg, indent=2), encoding="utf-8")
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        codes = [f["code"] for f in snap["parse_failed"]]
        self.assertIn("bad_lead_days", codes)
        # The deadline itself survives -- the date is still a fact, and only the
        # window it was given is unreadable.
        first = {p["id"]: p for p in snap["projects"]}[reg["projects"][0]["id"]]
        self.assertEqual(len(first["deadlines"]), 3)
        for d in first["deadlines"]:
            self.assertIsInstance(d["lead_days"], int)
            self.assertIsInstance(d["in_lead_window"], bool)

    def test_a_hand_written_neglect_days_does_not_take_the_run_down(self):
        # Same leaf, different field: `classify` compares `days_since` against it
        # with `>`, so a string is a TypeError raised from inside the renderer.
        ws = self.workspace()
        reg = load_jsonc(str(ws / "registry.jsonc"))
        reg["projects"][0]["neglect_days"] = "45"
        (ws / "registry.jsonc").write_text(json.dumps(reg, indent=2), encoding="utf-8")
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        first = {p["id"]: p for p in snap["projects"]}[reg["projects"][0]["id"]]
        self.assertIsInstance(first["neglect_days"], int)

    def test_a_retired_scoring_key_is_reported_rather_than_ignored(self):
        """`scoring.tier_weight` stopped being read when `tier` split into
        `status` and `positioning`. Every number in it is now inert.

        Reported rather than migrated, because migrating would have to invent an
        answer: the old table weighed `flagship` and `active` differently and
        both are now the single status `active`.

        Reported rather than dropped, because a config file that still reads as
        though it configures the ranking, and is not, is the quietest kind of
        wrong -- nothing else in the system would ever contradict it.
        """
        ws = self.workspace()
        # Through the package's own reader: the file is JSONC, and `json.loads`
        # chokes on its first comment line.
        cfg = load_jsonc(str(ws / "config.jsonc"))
        cfg["scoring"].pop("status_weight", None)
        cfg["scoring"]["tier_weight"] = {"flagship": 1.3, "dormant": 0.4}
        (ws / "config.jsonc").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        codes = [f["code"] for f in snap["parse_failed"]]
        self.assertIn("retired_config_key", codes)

    def test_a_config_that_uses_the_current_key_is_not_nagged(self):
        # The other half of the rule, and the one that decides whether the notice
        # above is a defect: a correct config must produce no entry at all. A
        # warning that fires for a harmless reason is how a warnings column stops
        # being read.
        ws = self.workspace()
        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        codes = [f["code"] for f in snap["parse_failed"]]
        self.assertNotIn("retired_config_key", codes)

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


class TheEnginesOwnOutput(TempCase):
    """Declaring the workspace as a project of your own is supported. It only
    works if the run's own products stay out of the activity it measures."""

    def test_output_paths_are_hidden_from_a_project_holding_them(self):
        ws_dir = self.workspace()
        ws = resolve_workspace(str(ws_dir))
        globs = sense.engine_output_globs(ws, ws_dir)
        self.assertEqual(sorted(globs),
                         ["BRIEF.html", "BRIEF.md", "log/**", "state/**"])

    def test_an_ordinary_project_gets_no_implicit_exclusions(self):
        # The patterns are relative to the directory being walked, so a project
        # the engine writes nothing into must come back with an empty list --
        # otherwise every project on the list would hide its own `state/`.
        ws_dir = self.workspace()
        ws = resolve_workspace(str(ws_dir))
        self.assertEqual(sense.engine_output_globs(ws, ws_dir / "projects" / "orchard"), [])

    def test_a_split_out_directory_is_hidden_from_wherever_it_lands(self):
        # `out` is configurable, so the exclusion cannot be a fixed list of
        # names at a fixed depth -- it has to be derived from where output goes.
        ws_dir = self.workspace()
        ws = resolve_workspace(str(ws_dir))
        elsewhere = ws.__class__(root=ws.root, out=ws.root / "projects" / "orchard" / "briefs",
                                 source="test")
        self.assertEqual(sorted(sense.engine_output_globs(elsewhere, ws_dir / "projects" / "orchard")),
                         ["briefs/BRIEF.html", "briefs/BRIEF.md",
                          "briefs/log/**", "briefs/state/**"])

    def test_the_brief_does_not_count_as_activity_in_the_workspace_project(self):
        # The end-to-end version, and the one that reproduces the real bug: a
        # hand-written `ignore_globs` listed BRIEF.md and missed BRIEF.html, so
        # every night's render came back the next night as a day of work.
        #
        # The products have to be planted first. `sense` runs before `render`, so
        # on a first run they do not exist yet and the assertion below would hold
        # for the wrong reason -- which is exactly how the first draft of this
        # test passed against the unfixed engine.
        reg = base_registry()
        # `paths` is relative to `defaults.root`, so the root has to be the
        # workspace for a project to cover it -- which is the real shape: the
        # workspace lives inside the directory being scanned.
        reg["defaults"]["root"] = "."
        reg["projects"] = [{
            "id": "vault", "name": "the workspace itself", "paths": ["."],
            "git": "none", "tier": "maintenance",
        }]
        ws_dir = self.workspace(registry=reg)
        for name in ("BRIEF.md", "BRIEF.html"):
            (ws_dir / name).write_text("last night's render\n", encoding="utf-8")
        (ws_dir / "log").mkdir(exist_ok=True)
        (ws_dir / "log" / "runs.jsonl").write_text('{"ok": true}\n', encoding="utf-8")
        # The control below has to survive an eight-entry cap filled in path
        # order, so it is named to sort first rather than left to luck.
        (ws_dir / "AUDIT_NOTES.md").write_text("mine, not the engine's\n", encoding="utf-8")
        set_tree_mtime(ws_dir)

        code, _, err = capture(sense.main, ["--workspace", str(ws_dir), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)

        snap = json.loads((ws_dir / "state" / "snapshot.json").read_text(encoding="utf-8"))
        fs = snap["projects"][0]["fs"]
        touched = [p.rsplit("/", 1)[-1] for p in fs["top_changed_paths"]]
        # A control, because the interesting assertions below are all negative:
        # an engine that walked nothing at all, or one pointed at the wrong
        # directory, would satisfy every one of them. Both mistakes were made
        # while writing this test.
        self.assertIn("AUDIT_NOTES.md", touched)
        for product in ("BRIEF.md", "BRIEF.html", "runs.jsonl"):
            self.assertNotIn(product, touched,
                             "the engine's own %s was counted as project activity" % product)


if __name__ == "__main__":
    unittest.main()
