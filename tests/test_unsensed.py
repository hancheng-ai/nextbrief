"""Projects the filesystem cannot measure, and declarations that have broken.

Two failures that used to produce the same sentence, and that call for opposite
actions:

* a declared directory has moved, so every number about the project is the
  absence of a directory -- one line of the registry fixes it;
* the work genuinely does not land on this disk, so file silence is not a
  finding at all -- somebody has to say what happened.

Both used to render as "cold", alongside the projects that really had gone
quiet. The tests here are written against that: each one asserts what the row
must NOT say as well as what it must.
"""

from __future__ import annotations

import json
import shutil
import unittest

from helpers import (
    AS_OF,
    RECENT_MTIME,
    TempCase,
    base_registry,
    capture,
    set_tree_mtime,
)

from nextbrief import cli, render, sense
from nextbrief.i18n import load_catalog

CAT = load_catalog("en")

# The three signal words this feature exists to keep off these rows. Read from
# the catalog rather than spelled out, so a reworded label cannot make the
# assertion pass by no longer matching anything.
COLD_WORDS = tuple(CAT.t(k) for k in ("signal.cold", "signal.dormant", "signal.unknown"))


class Pipeline(TempCase):
    """Both deterministic stages over a real workspace, with no model."""

    def sense(self, ws, *args):
        code, _out, err = capture(
            sense.main, ["--workspace", str(ws), "--as-of", AS_OF] + list(args))
        self.assertEqual(code, 0, err)
        return json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))

    def brief(self, ws):
        code, _out, err = capture(
            render.main, ["--workspace", str(ws), "--no-notify"])
        self.assertEqual(code, 0, err)
        return (ws / "BRIEF.md").read_text(encoding="utf-8")

    def run_both(self, ws):
        self.sense(ws)
        return self.brief(ws)

    def row(self, brief_text, name):
        """The project's line of the table, or None."""
        for line in brief_text.splitlines():
            if line.startswith("| %s |" % name):
                return line
        return None

    def project(self, snap, pid):
        return next(p for p in snap["projects"] if p["id"] == pid)


class BrokenDeclaration(Pipeline):
    """★ NA-0061 AC #1 and #4. ★

    `fs.missing_paths` has been collected since the walk was written and read by
    nothing, so a moved directory and an abandoned project were one sentence.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)

    def _move_orchard_away(self):
        """Exactly the accident this was found by: a reorganisation.

        The directory is moved rather than deleted, because that is the case
        with a right answer in it -- `check` can see where it went.
        """
        src = self.ws / "projects" / "orchard"
        dest = self.ws / "projects" / "cowork" / "orchard"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        set_tree_mtime(self.ws / "projects", RECENT_MTIME)

    def test_the_brief_changes_and_does_not_call_it_cold(self):
        """★ The criterion that goes red. ★

        Point a project at a directory that is not there and the brief must
        change -- and the change must not be that it went cold. Both halves are
        load-bearing: a version of this that only asserted "the brief changed"
        passes on the old code, where what changed was the signal word.
        """
        before = self.run_both(self.ws)
        before_row = self.row(before, "Orchard")
        self.assertIsNotNone(before_row, before)

        self._move_orchard_away()
        after = self.run_both(self.ws)
        after_row = self.row(after, "Orchard")

        self.assertNotEqual(before, after, "a broken declaration changed nothing")
        self.assertIsNotNone(after_row, after)
        self.assertNotEqual(before_row, after_row)
        for word in COLD_WORDS:
            self.assertNotIn(word, after_row,
                             "a moved directory is being reported as %r" % word)
        self.assertIn(CAT.t("brief.signal.declaration_broken"), after_row)

    def test_it_says_so_above_the_fold_and_names_the_path(self):
        self._move_orchard_away()
        after = self.run_both(self.ws)
        self.assertIn("orchard", after)
        banner = [ln for ln in after.splitlines()
                  if ln.startswith(">") and "declared path is not there" in ln]
        self.assertTrue(banner, "no banner announced the broken declaration:\n%s" % after)
        self.assertIn("nextbrief check", banner[0])

    def test_the_evidence_cell_stops_claiming_no_signal(self):
        """"No signal since <date>" is a claim about the project. What is
        actually known is a claim about the registry."""
        self._move_orchard_away()
        after = self.run_both(self.ws)
        row = self.row(after, "Orchard")
        self.assertIn("declared path is not there", row)
        self.assertNotIn(CAT.t("evidence.no_signal_since", date="").strip(), row)

    def test_the_snapshot_carries_the_flag_the_page_reads(self):
        self._move_orchard_away()
        snap = self.sense(self.ws)
        p = self.project(snap, "orchard")
        self.assertEqual(p["fs"]["missing_paths"], ["orchard"])
        self.assertTrue(p["declaration_broken"])
        digest = json.loads((self.ws / "state" / "digest.json").read_text(encoding="utf-8"))
        d = next(x for x in digest["projects"] if x["id"] == "orchard")
        self.assertTrue(d["declaration_broken"])
        self.assertEqual(d["missing_paths"], ["orchard"])

    def test_a_present_declaration_sets_nothing(self):
        snap = self.sense(self.ws)
        for p in snap["projects"]:
            self.assertFalse(p["declaration_broken"], p["id"])
            self.assertEqual(p["fs"]["missing_paths"], [])


class CheckNamesCandidates(Pipeline):
    """★ NA-0061 AC #2 -- and the half of it that is a promise: it never edits. ★"""

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        dest = self.ws / "projects" / "cowork" / "orchard"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.ws / "projects" / "orchard"), str(dest))

    def check(self):
        return capture(cli.main, ["--workspace", str(self.ws), "check"])

    def test_it_names_where_the_directory_turned_up(self):
        _code, _out, err = self.check()
        self.assertIn("orchard", err)
        self.assertIn("cowork/orchard", err.replace("\\", "/"),
                      "check did not name the candidate:\n%s" % err)

    def test_it_does_not_touch_the_registry(self):
        before = (self.ws / "registry.jsonc").read_bytes()
        self.check()
        self.assertEqual((self.ws / "registry.jsonc").read_bytes(), before,
                         "check re-pointed a project by itself")

    def test_with_nowhere_to_look_it_says_that_instead_of_guessing(self):
        shutil.rmtree(str(self.ws / "projects" / "cowork"))
        _code, _out, err = self.check()
        self.assertIn("no directory of that name", err)

    def test_a_healthy_workspace_says_nothing_about_paths(self):
        shutil.move(str(self.ws / "projects" / "cowork" / "orchard"),
                    str(self.ws / "projects" / "orchard"))
        _code, _out, err = self.check()
        self.assertNotIn("is gone", err)


class ReportedProjects(Pipeline):
    """★ NA-0061 AC #3. ★

    `status` answers what phase a project is in. This answers a different
    question -- what counts as evidence here -- and the two are orthogonal:
    `maintenance` says "it is meant to be quiet", while these projects expect a
    great deal to happen and none of it on this disk.
    """

    def _ws(self, **evidence):
        reg = base_registry()
        reg["projects"][0]["evidence"] = evidence
        return self.workspace(registry=reg, with_git=False)

    def test_an_overdue_report_replaces_the_counts_with_its_own_age(self):
        ws = self._ws(kind="reported", last_report="2026-02-01", cadence_days=14)
        brief = self.run_both(ws)
        row = self.row(brief, "Orchard")
        self.assertIsNotNone(row, brief)
        # 2026-02-01 -> 2026-03-16 is 43 days.
        self.assertIn(CAT.t("evidence.no_report_since", date="2026-02-01", days=43), row)
        self.assertIn(CAT.t("brief.signal.report_due", days=43), row)
        for word in COLD_WORDS:
            self.assertNotIn(word, row,
                             "a hand-reported project is being called %r" % word)

    def test_the_file_counts_it_disowned_do_not_appear(self):
        """The fixture's files are two days old, so a sensed Orchard would be
        loudly hot. That is the whole point: the numbers are real, and they are
        not about this project's progress."""
        sensed = self.row(self.run_both(self.workspace(with_git=False)), "Orchard")
        self.assertIn("files/7d", sensed)

        ws = self._ws(kind="reported", last_report="2026-02-01")
        row = self.row(self.run_both(ws), "Orchard")
        self.assertNotIn("files/7d", row)
        self.assertNotIn("active days", row)

    def test_a_fresh_report_reads_as_a_report_not_as_a_sensor(self):
        ws = self._ws(kind="reported", last_report="2026-03-14", cadence_days=14)
        row = self.row(self.run_both(ws), "Orchard")
        self.assertIn(CAT.t("evidence.reported_on", date="2026-03-14"), row)
        self.assertIn(CAT.t("brief.signal.reported", days=2), row)

    def test_a_declaration_with_no_report_behind_it_says_so(self):
        ws = self._ws(kind="reported")
        row = self.row(self.run_both(ws), "Orchard")
        self.assertIn(CAT.t("evidence.no_report_yet"), row)
        self.assertIn(CAT.t("brief.signal.report_never"), row)

    def test_the_string_shorthand_means_the_same_thing(self):
        reg = base_registry()
        reg["projects"][0]["evidence"] = "reported"
        ws = self.workspace(registry=reg, with_git=False)
        snap = self.sense(ws)
        self.assertTrue(self.project(snap, "orchard")["reported"]["declared"])

    def test_sensed_is_the_default_written_out(self):
        reg = base_registry()
        reg["projects"][0]["evidence"] = "sensed"
        ws = self.workspace(registry=reg, with_git=False)
        snap = self.sense(ws)
        self.assertIsNone(self.project(snap, "orchard")["reported"])
        self.assertEqual(self.project(snap, "orchard")["evidence"]["best_kind"],
                         "file_mtime")

    def test_the_report_date_is_what_the_signal_is_measured_from(self):
        ws = self._ws(kind="reported", last_report="2026-02-01")
        snap = self.sense(ws)
        ev = self.project(snap, "orchard")["evidence"]
        self.assertEqual(ev["best_kind"], "human")
        self.assertEqual(ev["best_date"], "2026-02-01")
        self.assertEqual(ev["days_since"], 43)

    def test_the_model_is_not_handed_the_numbers_it_may_not_quote(self):
        """★ The enforcement, not the phrasing. ★

        A count in the digest is a count the model will use, and a citation
        handle is a licence for the gate to let the sentence through. Both are
        withdrawn together, so "12 files changed" about a hand-reported project
        cannot be written *or* pass.
        """
        ws = self._ws(kind="reported", last_report="2026-02-01")
        self.sense(ws)
        digest = json.loads((ws / "state" / "digest.json").read_text(encoding="utf-8"))
        d = next(x for x in digest["projects"] if x["id"] == "orchard")
        self.assertEqual(d["evidence_basis"], "reported")
        self.assertEqual(d["reported"]["last_report"], "2026-02-01")
        self.assertTrue(d["reported"]["overdue"])
        self.assertEqual(set(d["facts"].values()), {None},
                         "a disowned sensor still handed the model its numbers")
        self.assertNotIn("orchard", d["cite"])
        self.assertIn("report:orchard", d["cite"])

        snap = json.loads((ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        self.assertNotIn("orchard", snap["evidence_index"])
        self.assertIn("report:orchard", snap["evidence_index"])

    def test_it_does_not_caveat_timestamps_it_never_quoted(self):
        ws = self._ws(kind="reported", last_report="2026-02-01")
        snap = self.sense(ws)
        self.assertIsNone(self.project(snap, "orchard")["evidence"]["caveat"])

    def test_a_bad_date_costs_one_row_not_the_whole_brief(self):
        ws = self._ws(kind="reported", last_report="the sixth of August")
        snap = self.sense(ws)
        self.assertIsNone(self.project(snap, "orchard")["reported"])
        codes = {f.get("code") for f in snap["parse_failed"]}
        self.assertIn("bad_evidence", codes)

    def test_an_unknown_kind_is_a_parse_failure_not_a_silent_default(self):
        ws = self._ws(kind="vibes")
        snap = self.sense(ws)
        self.assertIsNone(self.project(snap, "orchard")["reported"])
        self.assertIn("bad_evidence", {f.get("code") for f in snap["parse_failed"]})


class BothAtOnce(Pipeline):
    """A hand-reported project whose directory also moved. The broken
    declaration wins the signal cell: until the registry points at something
    real, nothing else on the row was measured."""

    def test_the_broken_declaration_outranks_the_report(self):
        reg = base_registry()
        reg["projects"][0]["evidence"] = {"kind": "reported", "last_report": "2026-03-14"}
        reg["projects"][0]["paths"] = ["orchard-moved-away"]
        ws = self.workspace(registry=reg, with_git=False)
        row = self.row(self.run_both(ws), "Orchard")
        self.assertIn(CAT.t("brief.signal.declaration_broken"), row)
        self.assertIn("declared path is not there", row)


if __name__ == "__main__":
    unittest.main()
