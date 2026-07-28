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

    def _meta(self):
        return render.classify(make_snapshot(), [], {})


if __name__ == "__main__":
    unittest.main()
