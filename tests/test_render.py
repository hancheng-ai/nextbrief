"""Stage 3: rendering, idempotence, and where the engine is allowed to write.

Two properties, and they are related. Identical inputs must produce byte-identical
output *and* must not rewrite files whose content did not change -- because the
workspace is itself a sensed tree, so a pointless write registers as activity and
makes the next run's snapshot differ from this one's for no reason at all.
"""

from __future__ import annotations

import json
import os
import re
import unittest

from helpers import (
    AS_OF,
    TempCase,
    capture,
    make_project_entry,
    make_snapshot,
    read_jsonl,
    set_tree_mtime,
    tree_state,
    write_backlog_item,
    write_brief_json,
    write_snapshot,
)

from nextbrief import render, sense
from nextbrief.i18n import load_catalog


class RenderCase(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace(with_git=False)
        write_snapshot(self.ws, make_snapshot())

    def render(self, *args):
        return capture(render.main, ["--workspace", str(self.ws), "--no-notify"] + list(args))


class Idempotence(RenderCase):
    def setUp(self):
        super().setUp()
        write_backlog_item(self.ws, "NA-0001", title="An open item")
        write_brief_json(
            self.ws,
            {
                "next_actions": [
                    {
                        "title": "Split the tenancy report per tenant",
                        "project": "orchard",
                        "estimate": "45m",
                        "who": "me",
                        "evidence_line": "PROJECT_STATUS.md declares 2026-03-10",
                        "evidence": [
                            {"kind": "doc_declared", "source": "orchard/PROJECT_STATUS.md"}
                        ],
                    }
                ]
            },
        )

    def test_two_renders_are_byte_identical(self):
        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        first_md = (self.ws / "BRIEF.md").read_bytes()
        first_html = (self.ws / "BRIEF.html").read_bytes()

        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.ws / "BRIEF.md").read_bytes(), first_md)
        self.assertEqual((self.ws / "BRIEF.html").read_bytes(), first_html)

    def test_unchanged_output_is_not_rewritten(self):
        self.assertEqual(self.render()[0], 0)
        before = (self.ws / "BRIEF.md").stat().st_mtime_ns
        self.assertEqual(self.render()[0], 0)
        self.assertEqual((self.ws / "BRIEF.md").stat().st_mtime_ns, before)

    def test_a_second_render_of_the_same_snapshot_is_the_same_run(self):
        # Run records are stamped with the snapshot's generated_at, so the "last
        # run" line in the header cannot change under a re-render.
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self.render()[0], 0)
        records = read_jsonl(self.ws / "log" / "runs.jsonl")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["at"], records[1]["at"])
        self.assertIn("first run", (self.ws / "BRIEF.md").read_text(encoding="utf-8"))

    def test_the_day_log_appends_rather_than_rewrites(self):
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self.render()[0], 0)
        log = (self.ws / "log" / ("%s.md" % AS_OF)).read_text(encoding="utf-8")
        # A second run on the same day adds a section; it never overwrites the
        # first, because the day log is the record and BRIEF.md is only the view.
        self.assertIn("## run 1", log)
        self.assertIn("## run 2", log)
        self.assertEqual(log.count("# %s" % AS_OF), 1)

    def test_dry_run_writes_nothing(self):
        code, out, err = self.render("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("Daily brief", out)
        self.assertFalse((self.ws / "BRIEF.md").exists())
        self.assertFalse((self.ws / "log" / "runs.jsonl").exists())


class OptionalTools(RenderCase):
    """External tools are optional by contract: sensing records what was missing
    and carries on with a documented proxy. That promise is only worth anything
    if the renderer can then render the record."""

    def _snapshot_with_missing_tools(self):
        snap = make_snapshot()
        snap["tool_missing"] = [
            {"tool": "scc", "why": "cyclomatic complexity is unavailable; line count is used as the proxy"},
            {"tool": "ccusage", "why": "per-run cost is unavailable"},
        ]
        write_snapshot(self.ws, snap)

    def test_a_missing_optional_tool_does_not_break_the_render(self):
        # These entries are dicts. Joining them as strings raised TypeError, so
        # the one code path whose entire purpose is to survive a missing tool was
        # the path that aborted the run -- and only on a machine that lacked the
        # tool, which is never the machine the author is working on.
        self._snapshot_with_missing_tools()
        code, _, err = self.render()
        self.assertEqual(code, 0, err)

    def test_the_brief_names_the_tool_and_the_consequence(self):
        # Naming the tool without naming what degrades leaves the reader unable
        # to judge whether the brief they are reading is weaker than usual.
        self._snapshot_with_missing_tools()
        self.assertEqual(self.render()[0], 0)
        brief = (self.ws / "BRIEF.md").read_text(encoding="utf-8")
        self.assertIn("scc", brief)
        self.assertIn("ccusage", brief)
        self.assertIn("line count", brief)

    def test_the_order_is_stable(self):
        # Reminder text feeds the byte-identical guarantee, so it cannot depend
        # on the order sensing happened to append in.
        self._snapshot_with_missing_tools()
        self.assertEqual(self.render()[0], 0)
        first = (self.ws / "BRIEF.md").read_text(encoding="utf-8")

        snap = make_snapshot()
        snap["tool_missing"] = [
            {"tool": "ccusage", "why": "per-run cost is unavailable"},
            {"tool": "scc", "why": "cyclomatic complexity is unavailable; line count is used as the proxy"},
        ]
        write_snapshot(self.ws, snap)
        self.assertEqual(self.render()[0], 0)
        self.assertEqual((self.ws / "BRIEF.md").read_text(encoding="utf-8"), first)


class Contents(RenderCase):
    def test_v0_says_that_nothing_has_interpreted_the_facts(self):
        # Without a model the brief is still a brief; it just has to be honest
        # about which half of the pipeline produced it.
        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        brief = (self.ws / "BRIEF.md").read_text(encoding="utf-8")
        self.assertIn("v0", brief)
        self.assertIn("Orchard", brief)
        self.assertEqual(read_jsonl(self.ws / "log" / "runs.jsonl")[-1]["mode"], "v0")

    def test_evidence_phrase_names_the_kind_of_signal(self):
        cat = load_catalog("en")
        with_git = render.evidence_phrase(make_project_entry(), cat)
        self.assertIn("commit", with_git)
        no_git = render.evidence_phrase(
            make_project_entry(
                has_git=False,
                git=None,
                git_declared="none",
                evidence={
                    "best_kind": "file_mtime",
                    "best_date": "2026-03-14",
                    "days_since": 2,
                    "signal": "hot",
                    "caveat_code": "no_git",
                    "caveat": "no git here",
                },
            ),
            cat,
        )
        self.assertNotIn("commit", no_git)
        self.assertIn("file", no_git)

    def test_the_two_artifacts_agree(self):
        write_brief_json(
            self.ws,
            {
                "next_actions": [
                    {
                        "title": "A verifiable action",
                        "project": "orchard",
                        "evidence": [
                            {"kind": "doc_declared", "source": "orchard/PROJECT_STATUS.md"}
                        ],
                    }
                ]
            },
        )
        self.assertEqual(self.render()[0], 0)
        md = (self.ws / "BRIEF.md").read_text(encoding="utf-8")
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertIn("A verifiable action", md)
        self.assertIn("A verifiable action", html)
        self.assertIn("<!doctype html>", html)
        # Self-contained: no network, no CDN, readable on a plane.
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))

    def test_html_escapes_project_text(self):
        # Project documents are data, never markup we execute.
        write_snapshot(
            self.ws,
            make_snapshot(projects=[make_project_entry(name="<script>alert(1)</script>")]),
        )
        self.assertEqual(self.render()[0], 0)
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_missing_snapshot_is_a_usage_error(self):
        os.remove(str(self.ws / "state" / "snapshot.json"))
        code, _, err = self.render()
        self.assertEqual(code, 2)
        self.assertIn("nextbrief sense", err)

    def test_a_snapshot_without_a_run_block_is_refused(self):
        write_snapshot(self.ws, make_snapshot(run={}))
        code, _, err = self.render()
        self.assertEqual(code, 2)
        self.assertIn("as_of_date", err)


class TableCells(RenderCase):
    """Every value in the project table is interpolated into Markdown.

    Project names come from a registry a human hand-edits and from directory
    names on disk, so a ``|`` or a newline is an ordinary accident -- and it
    corrupts the table for the *reader*, who cannot tell a broken row from a
    missing project. ANSI escapes are worse: they pass through into the file and
    then into the terminal of whoever cats it.
    """

    HOSTILE = "Orchard | prod\nsecond line\x1b[31mred\x1b[0m"

    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot(projects=[make_project_entry(name=self.HOSTILE)]))

    def _row(self):
        rows = [ln for ln in (self.ws / "BRIEF.md").read_text(encoding="utf-8").splitlines()
                if ln.startswith("| Orchard")]
        self.assertEqual(len(rows), 1, "the project produced %d rows, not one" % len(rows))
        return rows[0]

    def test_a_pipe_in_a_project_name_does_not_split_the_row(self):
        self.assertEqual(self.render()[0], 0)
        row = self._row()
        # Four columns means five unescaped delimiters and no more.
        self.assertEqual(len(re.findall(r"(?<!\\)\|", row)), 5)
        self.assertIn(r"Orchard \| prod", row)

    def test_a_newline_in_a_project_name_stays_on_one_row(self):
        self.assertEqual(self.render()[0], 0)
        self.assertIn("second line", self._row())

    def test_ansi_escapes_never_reach_the_file(self):
        self.assertEqual(self.render()[0], 0)
        self.assertNotIn("\x1b", (self.ws / "BRIEF.md").read_text(encoding="utf-8"))

    def test_the_line_cap_never_leaves_a_dangling_escape(self):
        # A row long enough to be cut, with a pipe positioned so that the cut
        # lands on its escape. A trailing backslash is a hard line break in
        # Markdown -- the corruption the escaping exists to prevent.
        name = "A" * 197 + "|B"
        write_snapshot(self.ws, make_snapshot(projects=[make_project_entry(name=name)]))
        self.assertEqual(self.render()[0], 0)
        for line in (self.ws / "BRIEF.md").read_text(encoding="utf-8").splitlines():
            self.assertFalse(line.endswith("\\"), "dangling escape: %r" % line[-20:])

    def test_the_escaper_leaves_ordinary_text_alone(self):
        # Bold and code markers are how the rest of the brief is written; an
        # escaper that mangled them would be worse than the bug it fixes.
        self.assertEqual(render.md_cell("**bold** and `code`"), "**bold** and `code`")
        self.assertEqual(render.md_cell(None), "")


class OverdueDeadlines(RenderCase):
    """"-125 days out" under a heading that reads "Tightest on time" is a
    sentence nobody parses as "you missed this four months ago"."""

    def setUp(self):
        super().setUp()
        write_snapshot(self.ws, make_snapshot(projects=[make_project_entry(
            deadlines=[{"date": "2025-11-11", "label": "cutover", "days_until": -125,
                        "lead_days": 14, "hard": True, "in_lead_window": False,
                        "overdue": True}],
        )]))

    def test_an_overdue_deadline_does_not_render_as_negative_days_out(self):
        code, _, err = self.render()
        self.assertEqual(code, 0, err)
        brief = (self.ws / "BRIEF.md").read_text(encoding="utf-8")
        self.assertIn("125 days overdue", brief)
        self.assertNotIn("-125 days out", brief)

    def test_a_deadline_still_ahead_keeps_the_days_out_wording(self):
        write_snapshot(self.ws, make_snapshot(projects=[make_project_entry(
            deadlines=[{"date": "2026-03-20", "label": "cutover", "days_until": 4,
                        "lead_days": 14, "hard": True, "in_lead_window": True,
                        "overdue": False}],
        )]))
        self.assertEqual(self.render()[0], 0)
        brief = (self.ws / "BRIEF.md").read_text(encoding="utf-8")
        self.assertIn("4 days out", brief)
        self.assertNotIn("overdue", brief)


class LocalisedOutput(RenderCase):
    def test_the_two_artifacts_print_the_same_signal_word_in_zh(self):
        # BRIEF.md read signal.* and BRIEF.html read signal.short.*, and only the
        # second set had been translated -- so the same fact printed as "🔥 hot"
        # in one artifact and "🔥 热" in the other.
        code, _, err = self.render("--locale", "zh")
        self.assertEqual(code, 0, err)
        md = (self.ws / "BRIEF.md").read_text(encoding="utf-8")
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertIn("🔥 热", md)
        self.assertIn("🔥 热", html)
        self.assertNotIn("🔥 hot", md)


class FirstClause(unittest.TestCase):
    """Clause splitting is a property of the text, not of the interface language.

    Keyed to the UI locale, an English render split on every '.' -- including the
    one inside a filename, which is exactly the kind of string this field holds.
    """

    def test_a_filename_survives_an_english_render(self):
        self.assertEqual(
            render._first_clause("rotate config.json before the run; then redeploy"),
            "rotate config.json before the run",
        )

    def test_a_sentence_still_ends_at_a_full_stop(self):
        self.assertEqual(render._first_clause("Approve the spend. Then tell finance."),
                         "Approve the spend")

    def test_chinese_enders_split_in_an_english_render(self):
        self.assertEqual(render._first_clause("先批预算。再通知财务"), "先批预算")

    def test_empty_input_is_empty_output(self):
        self.assertEqual(render._first_clause(None), "")


class FirstBriefAdvice(RenderCase):
    """The empty-backlog reminder is the only actionable instruction a brand new
    brief gives. It named `nextbrief bootstrap`, which has never existed."""

    def _reminder(self):
        self.assertEqual(self.render()[0], 0)
        brief = (self.ws / "BRIEF.md").read_text(encoding="utf-8")
        self.assertIn("The backlog is still empty", brief)
        return brief

    def test_it_does_not_name_a_subcommand_that_does_not_exist(self):
        self.assertNotIn("nextbrief bootstrap", self._reminder())

    def test_every_command_it_names_is_one_the_cli_accepts(self):
        from nextbrief import cli

        known = set()
        for action in cli.build_parser()._subparsers._group_actions:
            known.update(action.choices)
        self.assertIn("run", known)  # sanity: the scrape found real subcommands
        named = set(re.findall(r"`nextbrief ([a-z0-9-]+)", self._reminder()))
        self.assertTrue(named, "the brief names no command at all")
        self.assertEqual(sorted(named - known), [])


class Ranking(unittest.TestCase):
    def test_the_decay_floor_keeps_the_avoided_work_visible(self):
        cfg = {}
        fresh = make_project_entry()
        stale = make_project_entry(
            evidence={"best_kind": "commit", "best_date": "2025-06-01", "days_since": 288,
                      "signal": "dormant", "caveat_code": None, "caveat": None}
        )
        # Pure exponential decay would bury the stale project entirely; the floor
        # is what stops the tool from quietly agreeing with your avoidance.
        self.assertGreater(render.score_project(stale, cfg), 0.0)
        self.assertGreater(render.score_project(fresh, cfg), render.score_project(stale, cfg))
        self.assertGreater(
            render.score_project(stale, cfg) / render.score_project(fresh, cfg), 0.25
        )

    def test_no_readable_evidence_does_not_score_as_freshly_worked(self):
        # `days_since is None` means no commit, no file mtime, no session -- an
        # empty or unreadable directory. It used to take decay = 1.0, the maximum
        # freshness term, so absence of evidence read as evidence of activity and
        # an untouched directory ranked level with one worked on this morning.
        # Near-unreachable while every project was declared by hand; ordinary once
        # discovery started adopting whatever sits in the root.
        cfg = {}
        blank = make_project_entry(
            evidence={"best_kind": None, "best_date": None, "days_since": None,
                      "signal": "unknown", "caveat_code": None, "caveat": None}
        )
        fresh = make_project_entry(
            evidence={"best_kind": "commit", "best_date": "2026-03-16", "days_since": 0,
                      "signal": "hot", "caveat_code": None, "caveat": None}
        )
        self.assertLess(render.score_project(blank, cfg), render.score_project(fresh, cfg))
        # Still on the page, though: the floor exists so that what you are
        # avoiding stays visible, and "unreadable" is not a reason to vanish.
        self.assertGreater(render.score_project(blank, cfg), 0.0)

    def test_a_future_dated_project_cannot_outrank_a_worked_one(self):
        # `days_since` is an age, and an age is not negative. It could be: sense
        # subtracted a file's date from `as_of` with no floor, and the recency
        # contest picks the SMALLEST age -- so one file dated in the future won
        # the contest outright and carried a negative age into the scorer.
        #
        # `0.5 ** (days / half_life)` is bounded by 1.0 for days >= 0 and
        # unbounded below it. A file a year ahead scored 477911 against a normal
        # maximum of 4: five orders of magnitude, from a wrong clock on a NAS or
        # an archive unpacked with its original timestamps. Silent, because
        # nothing else in the brief reads `days_since` as a number.
        cfg = {}
        worked_today = make_project_entry(
            evidence={"best_kind": "commit", "best_date": "2026-03-16", "days_since": 0,
                      "signal": "hot", "caveat_code": None, "caveat": None}
        )
        ceiling = render.score_project(worked_today, cfg)
        for ahead in (-1, -7, -365, -100000):
            future = make_project_entry(
                evidence={"best_kind": "file_mtime", "best_date": "2027-03-16",
                          "days_since": ahead, "signal": "hot",
                          "caveat_code": None, "caveat": None}
            )
            self.assertLessEqual(
                render.score_project(future, cfg), ceiling,
                "days_since=%s scored above a project worked on today" % ahead)

    def test_the_decay_term_is_bounded_for_every_shape_days_since_can_take(self):
        # The property, stated once: nothing `days_since` can hold makes the score
        # exceed what impact alone allows. Tomorrow's parser bug is caught here
        # rather than in a brief that puts the wrong project first for a week.
        cfg = {}
        # Read from the entry rather than written here: impact is the base the
        # decay term multiplies, so the ceiling is whatever the fixture declares.
        # Hard-coding it makes the test assert on the fixture instead of on the
        # property.
        impact = float(make_project_entry()["ice"]["impact"])
        for days in (None, -100000, -365, -1, 0, 1, 21, 365, 100000,
                     "not a number", True, float("nan")):
            p = make_project_entry(
                evidence={"best_kind": "commit", "best_date": "2026-03-16",
                          "days_since": days, "signal": "hot",
                          "caveat_code": None, "caveat": None}
            )
            score = render.score_project(p, cfg)
            self.assertGreaterEqual(score, 0.0, "days_since=%r went negative" % (days,))
            self.assertLessEqual(score, impact, "days_since=%r exceeded impact" % (days,))

    def test_a_partial_status_weight_does_not_delete_the_other_statuses(self):
        # A flat dict update replaced the whole nested table, so naming one
        # status silently reset the other three to the 1.0 fallback -- invisible
        # in the config file, which still reads as though it changed one number.
        cfg = {"scoring": {"status_weight": {"frozen": 0.5}}}
        merged = render.scoring_of(cfg)
        self.assertEqual(merged["status_weight"]["frozen"], 0.5)
        self.assertEqual(merged["status_weight"]["maintenance"], 0.6)
        self.assertEqual(merged["status_weight"]["done"], 0.0)
        # Scalars still replace wholesale.
        self.assertEqual(render.scoring_of({"scoring": {"decay_floor": 0.1}})["decay_floor"], 0.1)

    def test_a_retired_tier_weight_changes_nothing_and_is_not_resurrected(self):
        # It used to sit in SCORING_DEFAULTS under a comment promising it was
        # "read only when `status_weight` is absent". No line of code kept that
        # promise: `scoring_of` merges the defaults first, so `status_weight` was
        # never absent and the branch could not be reached.
        #
        # Nor can the promise be kept. The old table weighed `flagship` 1.3 and
        # `active` 1.0 and both migrate to the one status `active`, so there is
        # no weight a translation could pick -- which is the ambiguity that split
        # `tier` in the first place. `sense` reports the key instead; see
        # `test_sense.RetiredConfigKeys`.
        merged = render.scoring_of({"scoring": {"tier_weight": {"flagship": 99.0}}})
        self.assertNotIn("tier_weight", render.SCORING_DEFAULTS)
        self.assertEqual(merged["status_weight"]["active"], 1.0)

    def test_an_overdue_deadline_outranks_a_fresher_project(self):
        cfg = {}
        overdue = make_project_entry(
            tier="active",
            evidence={"best_kind": "commit", "best_date": "2026-01-01", "days_since": 74,
                      "signal": "dormant", "caveat_code": None, "caveat": None},
            deadlines=[{"date": "2026-03-01", "label": "cutover", "days_until": -15,
                        "lead_days": 14, "hard": True, "in_lead_window": False,
                        "overdue": True}],
        )
        self.assertGreater(
            render.score_project(overdue, cfg), render.score_project(make_project_entry(tier="active"), cfg)
        )

    def test_ranking_ties_break_on_id_not_on_dict_order(self):
        a = make_project_entry(pid="alpha")
        b = make_project_entry(pid="beta")
        snap = make_snapshot(projects=[b, a])
        meta = render.classify(snap, [], {})
        self.assertEqual([p["id"] for p in meta["ranked"]], ["alpha", "beta"])


class Containment(TempCase):
    """A full run writes inside the workspace and nowhere else.

    Asserted by snapshotting a sibling tree -- and the redirected home directory,
    which is where the workspace pointer and the agent's session logs live -- and
    comparing sizes and mtimes across the run.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        self.outside = self.tmp / "outside"
        (self.outside / "nested").mkdir(parents=True)
        (self.outside / "a.txt").write_text("untouched\n", encoding="utf-8")
        (self.outside / "nested" / "b.txt").write_text("also untouched\n", encoding="utf-8")
        (self.outside / "registry.jsonc").write_text("{}\n", encoding="utf-8")
        set_tree_mtime(self.outside)

    def test_a_full_run_touches_nothing_outside_the_workspace(self):
        before_outside = tree_state(self.outside)
        before_home = tree_state(self.home)

        code, _, err = capture(
            sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF]
        )
        self.assertEqual(code, 0, err)
        code, _, err = capture(
            render.main, ["--workspace", str(self.ws), "--no-notify"]
        )
        self.assertEqual(code, 0, err)

        self.assertTrue((self.ws / "BRIEF.md").is_file())
        self.assertEqual(tree_state(self.outside), before_outside)
        self.assertEqual(tree_state(self.home), before_home)

    def test_the_output_directory_can_be_split_from_the_inputs(self):
        # A read-only or version-controlled registry with artifacts written
        # elsewhere is a supported layout, and both halves count as "inside".
        out = self.tmp / "artifacts"
        out.mkdir()
        before_outside = tree_state(self.outside)
        code, _, err = capture(
            sense.main, ["--workspace", str(self.ws), "--out", str(out), "--as-of", AS_OF]
        )
        self.assertEqual(code, 0, err)
        code, _, err = capture(
            render.main, ["--workspace", str(self.ws), "--out", str(out), "--no-notify"]
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((out / "BRIEF.md").is_file())
        self.assertFalse((self.ws / "BRIEF.md").exists())
        self.assertEqual(tree_state(self.outside), before_outside)

    def test_write_text_refuses_a_path_outside_the_workspace(self):
        # The structural half of the guarantee: not a rule to remember, a
        # precondition on the only function that writes.
        from nextbrief.paths import WorkspaceError, resolve_workspace

        ws = resolve_workspace(str(self.ws))
        with self.assertRaises(WorkspaceError):
            render.write_text(ws, self.outside / "escaped.md", "nope")
        self.assertFalse((self.outside / "escaped.md").exists())

    def test_append_jsonl_refuses_a_path_outside_the_workspace(self):
        from nextbrief.paths import WorkspaceError, resolve_workspace

        ws = resolve_workspace(str(self.ws))
        # Log appends are fail-open about the *environment* -- a full disk or a
        # read-only mount costs a log line, never the run -- but not about the
        # target. A path outside the workspace is a caller bug, and a bug that
        # returns False is a bug that ships.
        with self.assertRaises(WorkspaceError):
            render.append_jsonl(ws, self.outside / "escaped.jsonl", {"a": 1})
        self.assertFalse((self.outside / "escaped.jsonl").exists())

    def test_a_log_append_survives_an_unwritable_target(self):
        # The other half of that split: inside the workspace, an OSError is
        # swallowed. The run has a brief to finish.
        from nextbrief.paths import resolve_workspace

        ws = resolve_workspace(str(self.ws))
        blocked = self.ws / "log" / "blocked"
        blocked.mkdir(parents=True, exist_ok=True)
        # A directory where the log line expects a file: open(..., "a") raises
        # IsADirectoryError, which is an OSError.
        self.assertFalse(render.append_jsonl(ws, blocked, {"a": 1}))


class Notification(RenderCase):
    """The silence rule: a system that reports "nothing happened" punctually every
    day gets muted in week three, and after that it can never tell you anything."""

    def test_first_run_always_notifies(self):
        do_notify, why = render.should_notify({}, make_snapshot(), None, self._meta(), {})
        self.assertTrue(do_notify)
        self.assertIn("first run", why)

    def test_an_unchanged_portfolio_stays_quiet(self):
        snap = make_snapshot()
        cfg = {"notify": {"only_if": ["change"]}}
        do_notify, why = render.should_notify(cfg, snap, json.loads(json.dumps(snap)),
                                              self._meta(), {})
        self.assertFalse(do_notify)
        self.assertIn("nothing changed", why)

    def test_a_dropped_claim_always_breaks_the_silence(self):
        snap = make_snapshot()
        cfg = {"notify": {"only_if": ["change"]}}
        do_notify, _ = render.should_notify(cfg, snap, json.loads(json.dumps(snap)),
                                            self._meta(), {"dropped_claims": 1})
        self.assertTrue(do_notify)

    def _meta(self, neglected=(), stalled=()):
        meta = render.classify(make_snapshot(), [], {})
        meta["neglected"] = [{"id": i} for i in neglected]
        meta["stalled"] = [{"id": i} for i in stalled]
        meta["neglected_ids"] = set(neglected)
        meta["stalled_ids"] = set(stalled)
        return meta

    def test_a_neglected_project_does_not_notify_every_single_day(self):
        """The defect this class's own docstring describes, in the code below it.

        `neglect` and `new_stalled` were state tests -- `if want and
        meta["neglected"]` -- while the two branches directly above them diff
        against the previous snapshot. So a project you already know is neglected
        interrupted you again every morning until you fixed it, which is the
        precise behaviour the docstring calls fatal, sitting four lines under the
        docstring.

        Nothing new to say is the whole definition of a quiet morning.
        """
        snap = make_snapshot()
        prev = json.loads(json.dumps(snap))
        cfg = {"notify": {"only_if": ["neglect"]}}
        meta = self._meta(neglected=["orchard"])

        first, why = render.should_notify(cfg, snap, prev, meta, {}, prev_run=None)
        self.assertTrue(first, "the first sighting must break the silence")
        self.assertIn("neglected", why)

        again, why = render.should_notify(
            cfg, snap, prev, meta, {}, prev_run={"neglected_ids": ["orchard"]})
        self.assertFalse(again, "the same neglected project notified twice")
        self.assertIn("nothing changed", why)

    def test_a_newly_neglected_project_still_breaks_the_silence(self):
        # The half that decides whether the above is a fix or an amputation.
        snap = make_snapshot()
        prev = json.loads(json.dumps(snap))
        cfg = {"notify": {"only_if": ["neglect"]}}
        do_notify, why = render.should_notify(
            cfg, snap, prev, self._meta(neglected=["orchard", "kiln"]), {},
            prev_run={"neglected_ids": ["orchard"]})
        self.assertTrue(do_notify)
        self.assertIn("neglected", why)

    def test_a_project_that_comes_back_notifies_again(self):
        # Re-arming falls out of set difference rather than needing a timer, and
        # is better than one: a timer re-fires about a project you already know
        # about, whereas this fires only when something is genuinely new again.
        snap = make_snapshot()
        prev = json.loads(json.dumps(snap))
        cfg = {"notify": {"only_if": ["neglect"]}}
        do_notify, _ = render.should_notify(
            cfg, snap, prev, self._meta(neglected=["orchard"]), {},
            prev_run={"neglected_ids": []})
        self.assertTrue(do_notify)

    def test_a_stalled_project_is_edge_triggered_too(self):
        snap = make_snapshot()
        prev = json.loads(json.dumps(snap))
        cfg = {"notify": {"only_if": ["new_stalled"]}}
        meta = self._meta(stalled=["kiln"])
        self.assertTrue(render.should_notify(cfg, snap, prev, meta, {}, prev_run=None)[0])
        self.assertFalse(render.should_notify(
            cfg, snap, prev, meta, {}, prev_run={"stalled_ids": ["kiln"]})[0])

    def test_an_upgrade_from_a_record_without_the_sets_fires_once(self):
        # Older run records carry no id lists. Read as "nothing was known", which
        # makes everything currently in the set look new: one notification on the
        # first run after an upgrade, and quiet after that. The alternative --
        # treating absence as "everything already reported" -- would swallow a
        # genuinely new neglected project on exactly the run where the reader has
        # least reason to expect a gap.
        snap = make_snapshot()
        prev = json.loads(json.dumps(snap))
        cfg = {"notify": {"only_if": ["neglect"]}}
        do_notify, _ = render.should_notify(
            cfg, snap, prev, self._meta(neglected=["orchard"]), {},
            prev_run={"at": "2026-03-15T21:30:00", "ok": True})
        self.assertTrue(do_notify)

    def _unused(self):
        return render.classify(make_snapshot(), [], {})


if __name__ == "__main__":
    unittest.main()


class WhatIsNotRanked(TempCase):
    """A score multiplies a human's stated importance. Where none was stated
    there is no number that stands in for it, so the project is not ranked --
    but it is still listed, because vanishing from the page is the failure this
    whole split exists to avoid.
    """

    def test_a_project_with_no_impact_is_not_ranked(self):
        judged = make_project_entry(pid="judged", tier="active", ice={"impact": 4})
        blank = make_project_entry(pid="blank", tier=None, ice=None)
        meta = render.classify(make_snapshot([judged, blank]), [], {})
        self.assertEqual([p["id"] for p in meta["ranked"]], ["judged"])
        self.assertEqual([p["id"] for p in meta["unjudged"]], ["blank"])

    def test_it_is_still_on_the_page(self):
        """Ranking it would assert an importance nobody gave. Dropping it would
        hide a project, which is worse than either."""
        judged = make_project_entry(pid="judged", tier="active", ice={"impact": 4})
        blank = make_project_entry(pid="blank", tier=None, ice=None)
        snap = make_snapshot([judged, blank])
        md, _ = render.render_brief(snap, {}, [], {}, {}, load_catalog("en"),
                                    {"conflicts": []})
        self.assertIn("Blank", md)
        self.assertIn("Judged", md)

    def test_an_impact_only_answer_is_judged(self):
        """`review` asks importance and nothing else. Requiring confidence or
        effort as well would leave every reviewed project permanently unranked."""
        p = make_project_entry(pid="reviewed", tier=None, ice={"impact": 3})
        self.assertTrue(render.is_judged(p))

    def test_a_declared_tier_is_not_required(self):
        self.assertTrue(render.is_judged(
            make_project_entry(pid="x", tier=None, ice={"impact": 1})))

    def test_scoring_no_longer_invents_an_impact(self):
        """The whole point. Absent impact must not read as the midpoint."""
        blank = make_project_entry(pid="blank", tier="active", ice=None)
        blank["evidence"] = dict(blank["evidence"], days_since=0)
        self.assertEqual(render.score_project(blank, {}), 0.0)

    def test_an_unrated_project_says_so_rather_than_showing_a_signal(self):
        blank = make_project_entry(pid="blank", tier=None, ice=None)
        snap = make_snapshot([make_project_entry(pid="judged", tier="active",
                                                ice={"impact": 4}), blank])
        md, _ = render.render_brief(snap, {}, [], {}, {}, load_catalog("en"),
                                    {"conflicts": []})
        row = [ln for ln in md.splitlines() if ln.startswith("| ") and "Blank" in ln]
        self.assertTrue(row, "the unrated project has no table row")
        self.assertIn("not rated", row[0])

    def test_a_deadline_still_counts_on_an_unrated_project(self):
        """A date is a fact, not a judgement: the most overdue thing you own can
        be something nobody has rated."""
        blank = make_project_entry(pid="blank", tier=None, ice=None)
        blank["deadlines"] = [{"label": "handover", "date": "2026-03-01",
                               "days_until": -15, "overdue": True}]
        snap = make_snapshot([blank])
        md, _ = render.render_brief(snap, {}, [], {}, {}, load_catalog("en"),
                                    {"conflicts": []})
        self.assertIn("handover", md)

    def test_a_hand_edited_impact_degrades_instead_of_raising(self):
        """`registry.jsonc` invites hand-editing and `check_shapes` never sees
        `ice`, so a string reaches the scorer. Raising there costs the whole
        brief on the unattended path, which is the opposite of fail-open."""
        for bad in ("high", float("nan"), float("inf"), True, []):
            p = make_project_entry(pid="x", tier="active", ice={"impact": bad})
            self.assertFalse(render.is_judged(p), bad)
            self.assertEqual(render.score_project(p, {}), 0.0)

    def test_nan_never_reaches_the_sort_key(self):
        """NaN compares false against everything, so one of them makes the
        ordering depend on where the comparison started -- and this package
        guarantees byte-identical output for identical input."""
        self.assertIsNone(render.declared_impact(
            make_project_entry(pid="x", ice={"impact": float("nan")})))

    def test_an_old_snapshot_still_produces_verdicts(self):
        """`render` re-reads an existing snapshot without re-sensing. A snapshot
        written before `status` existed carries only `tier`, and without the
        migration every verdict would silently stop firing on any workspace that
        had not re-sensed yet."""
        old = make_project_entry(pid="old", tier="active", ice={"impact": 4})
        old.pop("status", None)
        old["evidence"] = dict(old["evidence"], days_since=99)
        meta = render.classify(make_snapshot([old]), [], {})
        self.assertEqual([p["id"] for p in meta["neglected"]], ["old"])

    def test_maintenance_is_never_reported_neglected(self):
        """It is the declaration that a project is meant to be quiet. Warning
        about a thing doing exactly what was asked of it is how a warning column
        stops being read."""
        quiet = make_project_entry(pid="quiet", ice={"impact": 4})
        quiet["status"] = "maintenance"
        quiet["evidence"] = dict(quiet["evidence"], days_since=200)
        meta = render.classify(make_snapshot([quiet]), [], {})
        self.assertEqual(meta["neglected"], [])

    def test_hot_and_maintenance_are_independent(self):
        """Activity is observed, phase is declared. A busy project can be one
        that has finished evolving, and the brief has to be able to say so."""
        busy = make_project_entry(pid="busy", ice={"impact": 4})
        busy["status"] = "maintenance"
        busy["evidence"] = dict(busy["evidence"], days_since=0, signal="hot")
        meta = render.classify(make_snapshot([busy]), [], {})
        self.assertEqual(meta["neglected"], [])
        self.assertEqual(busy["evidence"]["signal"], "hot")

    def test_a_done_project_leaves_the_ranking(self):
        done = make_project_entry(pid="done", ice={"impact": 5})
        done["status"] = "done"
        done["evidence"] = dict(done["evidence"], days_since=0)
        self.assertEqual(render.score_project(done, {}), 0.0)
