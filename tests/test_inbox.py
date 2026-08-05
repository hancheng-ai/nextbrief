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

from helpers import TempCase

from nextbrief import annotate, inbox

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


if __name__ == "__main__":
    unittest.main()
