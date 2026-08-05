"""Corrections dropped by the page, and the four things that stop one landing.

Every test plants a file the ingest must refuse and checks both halves: that it
did not apply, and that the refusal was COUNTED. A correction that silently does
nothing is the worst outcome available here -- the person believes they told the
engine something and the engine believes nothing happened -- so a refusal that
is not surfaced is only half a guard.
"""

from __future__ import annotations

import datetime as dt
import json
import unittest

from helpers import TempCase, make_project_entry, make_snapshot

from nextbrief import annotate, cli, html, inbox
from nextbrief.i18n import load_catalog
from nextbrief.jsonc import load_jsonc
from nextbrief.paths import resolve_workspace

AS_OF = dt.date(2026, 3, 16)
KNOWN = ("orchard", "kiln")
CONTRADICTED = ("orchard",)


class InboxCase(TempCase):
    def setUp(self):
        super().setUp()
        self.drop = self.tmp / "drops"
        self.drop.mkdir(parents=True, exist_ok=True)

    def put(self, name="nextbrief-adjust-2026-03-16-orchard.json", **over):
        payload = {"project": "orchard", "field": "status", "value": "maintenance",
                   "from_as_rendered": AS_OF.isoformat()}
        payload.update(over)
        (self.drop / name).write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def read(self, **kw):
        return inbox.read_adjustments(
            self.drop, annotate.QUESTIONS, KNOWN, CONTRADICTED,
            as_of=kw.pop("as_of", AS_OF), **kw)


class WhatIsAccepted(InboxCase):
    def test_a_correction_to_a_contradicted_project_lands(self):
        self.put()
        accepted, refused = self.read()
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["project"], "orchard")
        self.assertEqual(accepted[0]["value"], "maintenance")
        self.assertEqual(sum(refused.values()), 0)

    def test_it_shapes_into_what_record_answers_expects(self):
        self.put()
        accepted, _ = self.read()
        self.assertEqual(inbox.apply_adjustments(accepted),
                         {"orchard": {"status": "maintenance"}})

    def test_the_later_file_wins_for_the_same_field(self):
        """They clicked twice because the second click is what they meant."""
        self.put(name="nextbrief-adjust-2026-03-16-orchard-1.json", value="maintenance")
        self.put(name="nextbrief-adjust-2026-03-16-orchard-2.json", value="frozen")
        accepted, _ = self.read()
        self.assertEqual(inbox.apply_adjustments(accepted),
                         {"orchard": {"status": "frozen"}})

    def test_an_empty_drop_directory_is_not_an_error(self):
        accepted, refused = self.read()
        self.assertEqual(accepted, [])
        self.assertEqual(sum(refused.values()), 0)

    def test_a_missing_drop_directory_is_not_an_error(self):
        got, refused = inbox.read_adjustments(
            self.tmp / "nope", annotate.QUESTIONS, KNOWN, CONTRADICTED, as_of=AS_OF)
        self.assertEqual(got, [])
        self.assertEqual(sum(refused.values()), 0)


class WhatIsRefused(InboxCase):
    def test_a_stale_tab_is_refused(self):
        """A tab left open for three days answers a question about a state that
        has since changed. Applying it attributes to the person a statement about
        facts they never saw."""
        self.put(from_as_rendered="2026-03-13")
        accepted, refused = self.read()
        self.assertEqual(accepted, [])
        self.assertEqual(refused["stale"], 1)

    def test_a_project_the_engine_did_not_contradict_is_refused(self):
        """The never-originate rule, enforced here rather than trusted to the
        page. The page decides which controls to DRAW; a file on disk is not the
        page, and anything can write to a downloads directory."""
        self.put(project="kiln")
        accepted, refused = self.read()
        self.assertEqual(accepted, [])
        self.assertEqual(refused["not_contradicted"], 1)

    def test_a_field_outside_the_allowlist_is_refused(self):
        """Impact and positioning are RELATIVE judgements -- they need the whole
        portfolio in one view and cannot be made one project at a time. A control
        offering them inline would be a form, not a correction."""
        for field in ("impact", "positioning", "deadline", "goal_one_line"):
            self.put(field=field, value="4")
            accepted, refused = self.read()
            self.assertEqual(accepted, [], field)
            self.assertGreaterEqual(refused["field_not_adjustable"]
                                    + refused["value_not_offered"], 1, field)

    def test_a_value_the_question_does_not_offer_is_refused(self):
        self.put(value="whatever-i-typed")
        accepted, refused = self.read()
        self.assertEqual(accepted, [])
        self.assertEqual(refused["value_not_offered"], 1)

    def test_an_unknown_project_is_refused(self):
        self.put(project="../../etc/passwd")
        accepted, refused = self.read()
        self.assertEqual(accepted, [])
        self.assertEqual(refused["unknown_project"], 1)

    def test_malformed_json_costs_that_file_and_not_the_run(self):
        (self.drop / "nextbrief-adjust-2026-03-16-broken.json").write_text(
            "{not json", encoding="utf-8")
        self.put(name="nextbrief-adjust-2026-03-16-good.json")
        accepted, refused = self.read()
        self.assertEqual(len(accepted), 1, "one bad file stopped a good one")
        self.assertEqual(refused["unreadable"], 1)

    def test_a_json_document_that_is_not_an_object_is_refused(self):
        for body in ("[]", '"a string"', "42", "null"):
            (self.drop / "nextbrief-adjust-2026-03-16-x.json").write_text(
                body, encoding="utf-8")
            accepted, refused = self.read()
            self.assertEqual(accepted, [], body)
            self.assertEqual(refused["unreadable"], 1, body)

    def test_files_that_are_not_ours_are_left_alone(self):
        """A downloads directory is full of other people's files. Reading one is
        at best noise and at worst a way to be handed something."""
        (self.drop / "invoice.json").write_text('{"project": "orchard"}', encoding="utf-8")
        (self.drop / "nextbrief-report.txt").write_text("x", encoding="utf-8")
        accepted, refused = self.read()
        self.assertEqual(accepted, [])
        self.assertEqual(sum(refused.values()), 0, "a foreign file was even parsed")


class TheAllowlistIsNarrowOnPurpose(unittest.TestCase):
    def test_exactly_one_field_is_adjustable(self):
        """Widening this is a design change, not a configuration change: the rule
        is that inline may correct a claim the brief printed and may never
        originate a judgement, and only `status` is ever printed next to an
        observation that contradicts it."""
        self.assertEqual(inbox.ADJUSTABLE_FIELDS, ("status",))

    def test_the_adjustable_field_is_one_review_actually_asks(self):
        """Otherwise a correction lands in a field nothing reads, and the person
        is told their answer was recorded when it was recorded nowhere."""
        asked = {q.field for q in annotate.QUESTIONS}
        for field in inbox.ADJUSTABLE_FIELDS:
            self.assertIn(field, asked)


class TheControlThePageDraws(TempCase):
    """The emitting half. What matters is WHERE it appears, not that it works.

    A control beside a verdict is answering a question the page already asked. A
    control anywhere else is a form, and a form in a document nobody opened to
    fill in is answered carelessly or not at all.
    """

    def _p(self, pid, days, status="active", **over):
        got = make_project_entry(pid=pid, ice={"impact": 4})
        got["status"] = status
        got["evidence"] = dict(got["evidence"], days_since=days)
        got.update(over)
        return got

    def _html(self, projects):
        snap = make_snapshot(projects=projects)
        return html.render_html(snap, {}, [], {}, {}, load_catalog("en"),
                                {"conflicts": []})

    def test_a_contradicted_project_gets_the_control(self):
        """Neglected is only ever said of an ACTIVE project, and means it has
        been quiet far longer than active implies. The engine is saying "this
        does not look like what you told me"."""
        got = self._html([self._p("quiet", days=400)])
        self.assertIn("adj(this,'quiet'", got)

    def test_a_healthy_project_gets_no_control(self):
        """Rule 8, applied to a widget. A control on every row is furniture, and
        furniture is not read."""
        got = self._html([self._p("busy", days=1)])
        self.assertNotIn("adj(this,", got)

    def test_the_payload_carries_the_briefs_own_date(self):
        """The staleness guard is only as good as the stamp. If the page emitted
        today's date instead of the brief's, a three-day-old tab would look
        current and the guard would never fire."""
        got = self._html([self._p("quiet", days=400)])
        self.assertIn("adj(this,'quiet','2026-03-16')", got)

    def test_the_values_offered_are_the_ones_review_asks(self):
        """A phase corrected here and a phase answered in `review` have to be the
        same statement, or the ingest refuses its own page's output."""
        got = self._html([self._p("quiet", days=400)])
        for value, _label in [q for q in annotate.QUESTIONS
                              if q.field == "status"][0].choices:
            self.assertIn('value="%s"' % value, got)

    def test_a_hostile_project_name_is_still_escaped(self):
        """Adding markup to a row must not disturb the escaping around it.

        Scoped to what it actually proves: the NAME cell. The signal cell is also
        escaped, and that escaping is defence in depth rather than a live guard --
        `sig` is built from catalog strings with numeric interpolation, so a
        mutation removing it passes this suite. Claiming otherwise here would be
        a guard that cannot fail dressed as one that can.
        """
        got = self._html([self._p("quiet", days=400,
                                  name="<script>alert(1)</script>")])
        self.assertNotIn("<script>alert(1)</script>", got)
        self.assertIn("&lt;script&gt;", got)
        self.assertIn("adj(this,'quiet'", got, "the control was lost entirely")

    def test_the_control_writes_a_file_the_reader_will_look_for(self):
        """Two halves of one contract, declared in different modules. If the page
        names its download anything else, the ingest never sees it -- and the
        failure is silent on both sides."""
        got = self._html([self._p("quiet", days=400)])
        prefix = inbox.DROP_GLOB.split("*")[0]
        self.assertIn("'" + prefix, got)


class TheRoundTrip(TempCase):
    """A correction dropped by yesterday's brief, folded in by today's run.

    Both halves have their own tests. This is the seam between them, which is
    where a contract declared in two modules actually breaks.
    """

    def setUp(self):
        super().setUp()
        self.ws_root = self.workspace()
        self.ws = resolve_workspace(str(self.ws_root))
        self.drop = self.tmp / "drops"
        self.drop.mkdir(parents=True, exist_ok=True)
        cfg = load_jsonc(self.ws.config_path)
        cfg["review"] = {"drop_dir": str(self.drop)}
        (self.ws_root / "config.jsonc").write_text(
            json.dumps(cfg, indent=2), encoding="utf-8")

    def _run_record(self, as_of="2026-03-16", contradicted=("orchard",)):
        (self.ws_root / "log").mkdir(parents=True, exist_ok=True)
        (self.ws_root / "log" / "runs.jsonl").write_text(
            json.dumps({"at": as_of + "T21:30:00", "as_of": as_of, "ok": True,
                        "status_contradicted": list(contradicted)}) + "\n",
            encoding="utf-8")

    def _drop(self, stamp="2026-03-16", project="orchard", value="maintenance"):
        (self.drop / ("nextbrief-adjust-%s-%s.json" % (stamp, project))).write_text(
            json.dumps({"project": project, "field": "status", "value": value,
                        "from_as_rendered": stamp}), encoding="utf-8")

    def test_a_correction_reaches_the_overlay(self):
        self._run_record()
        self._drop()
        cli._ingest_adjustments(self.ws)
        got = annotate.load_annotations(self.ws)
        self.assertEqual((got.get("orchard") or {}).get("status"), "maintenance")

    def test_it_is_stamped_only_on_the_field_it_answered(self):
        """The inline path must not refresh the impact clock. Correcting a phase
        is not restating a strategy, and laundering one into the other is how a
        field goes quietly dead while reporting itself fresh."""
        self._run_record()
        self._drop()
        cli._ingest_adjustments(self.ws)
        stamps = (annotate.load_annotations(self.ws).get("orchard") or {}).get("asked_on")
        self.assertEqual(set(stamps or {}), {"status"},
                         "an inline correction stamped a field it did not answer")

    def test_a_correction_for_a_brief_that_is_no_longer_current_is_ignored(self):
        """The tab was open across several runs. The state has moved on since
        they looked, so the answer is about facts that no longer hold."""
        self._run_record(as_of="2026-03-16")
        self._drop(stamp="2026-03-10")
        cli._ingest_adjustments(self.ws)
        self.assertEqual(annotate.load_annotations(self.ws), {})

    def test_a_correction_for_a_project_the_brief_did_not_flag_is_ignored(self):
        """The never-originate rule, at the seam rather than in the unit.

        `kiln` is a real project in the registry, so it passes the
        known-projects check -- and the earlier round-trip tests cannot see this
        at all, because they only ever drop for a project that is both known and
        contradicted. Handing the whole registry through instead of the flagged
        set is invisible until one is not the other.
        """
        self._run_record(contradicted=("orchard",))
        self._drop(project="kiln")
        cli._ingest_adjustments(self.ws)
        self.assertEqual(annotate.load_annotations(self.ws), {},
                         "a project the brief never flagged was corrected anyway")

    def test_nothing_rendered_yet_means_nothing_to_apply(self):
        """No run record: there is no brief anybody could have clicked, so a file
        in the drop directory did not come from one."""
        self._drop()
        cli._ingest_adjustments(self.ws)
        self.assertEqual(annotate.load_annotations(self.ws), {})

    def test_an_unreadable_drop_never_costs_the_run(self):
        """`cmd_run` catches, but the ingest should not be the thing that throws.
        A brief that fails to build because somebody's downloads folder had an
        odd file in it is a brief that stops being trusted."""
        self._run_record()
        (self.drop / "nextbrief-adjust-2026-03-16-x.json").write_text(
            "\x00\xff not json", encoding="latin-1")
        cli._ingest_adjustments(self.ws)
        self.assertEqual(annotate.load_annotations(self.ws), {})


if __name__ == "__main__":
    unittest.main()
