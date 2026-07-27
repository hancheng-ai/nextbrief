"""The YAML-subset frontmatter parser and its single writer.

Two contracts are load-bearing and are what most of this file is about: parsing
never raises (one bad backlog entry must not end the nightly run), and writing
touches the frontmatter block only (the prose underneath is a human's, and no
amount of malformed input justifies rewriting it).
"""

from __future__ import annotations

import unittest

from helpers import TempCase, fixture

from nextbrief.frontmatter import format_value, parse_frontmatter, rewrite_fields

# The document a user actually writes, kept in tests/fixtures/ so the parser test
# and the launch test cannot drift onto two different ideas of the schema.
ITEM = fixture("backlog-item.md")

DOC = """\
---
id: NA-0001
title: Split the tenancy latency report per tenant
project: orchard
status: open
priority: 2
is_next_action: true
human_confirmed: false
estimate_min: 45
blocked_by: null
tags: [latency, tenancy, bench]
empty_list: []
automation:
  tier: hook
  what_needs_human: Decide whether the tail matters more than the mean
  next_probe: Re-run the harness with per-tenant grouping
source:
  doc: orchard/docs/BENCH_NOTES.md
  anchor: "## Results"
notes: |
  Two paragraphs of context that belong to the field,
  not to the body.
---

# Body

Everything below the closing marker belongs to the human.

- [ ] Per-tenant p95 is reported separately
"""


class Parsing(unittest.TestCase):
    def setUp(self):
        self.fields, self.body = parse_frontmatter(DOC)

    def test_scalars(self):
        self.assertEqual(self.fields["id"], "NA-0001")
        self.assertEqual(self.fields["priority"], 2)
        self.assertEqual(self.fields["estimate_min"], 45)
        self.assertIs(self.fields["is_next_action"], True)
        self.assertIs(self.fields["human_confirmed"], False)
        self.assertIsNone(self.fields["blocked_by"])

    def test_inline_lists(self):
        self.assertEqual(self.fields["tags"], ["latency", "tenancy", "bench"])
        self.assertEqual(self.fields["empty_list"], [])

    def test_nesting(self):
        self.assertEqual(self.fields["automation"]["tier"], "hook")
        self.assertEqual(
            self.fields["automation"]["what_needs_human"],
            "Decide whether the tail matters more than the mean",
        )
        self.assertEqual(self.fields["source"]["doc"], "orchard/docs/BENCH_NOTES.md")
        # Quoted values keep their content and lose only the quotes.
        self.assertEqual(self.fields["source"]["anchor"], "## Results")

    def test_block_scalar(self):
        self.assertEqual(
            self.fields["notes"],
            "Two paragraphs of context that belong to the field,\nnot to the body.",
        )

    def test_body_starts_after_the_closing_marker(self):
        self.assertTrue(self.body.startswith("# Body"))
        self.assertIn("- [ ] Per-tenant p95", self.body)
        self.assertNotIn("id: NA-0001", self.body)


class TheDocumentedSchema(unittest.TestCase):
    """The shared fixture, parsed as the rest of the engine will parse it."""

    def test_every_field_the_engine_reads_survives_a_round_trip(self):
        fields, body = parse_frontmatter(ITEM)
        self.assertEqual(fields["id"], "NA-0001")
        self.assertEqual(fields["priority"], 2)
        self.assertIs(fields["is_next_action"], True)
        self.assertIs(fields["human_confirmed"], False)
        self.assertEqual(fields["tags"], ["latency", "tenancy", "bench"])
        self.assertEqual(fields["automation"]["tier"], "hook")
        self.assertEqual(fields["source"]["doc"], "orchard/PROJECT_STATUS.md")
        self.assertEqual(fields["source"]["source_last_updated_declared"], "2026-03-10")
        # Acceptance criteria live in the body, and the body is nobody's data but
        # the human's.
        self.assertIn("- [ ] p95 is reported per tenant", body)


class MalformedInput(unittest.TestCase):
    """Failing open is a project-wide contract, so it is tested as one."""

    def test_no_frontmatter_at_all(self):
        text = "# Just a document\n\nNo frontmatter here.\n"
        self.assertEqual(parse_frontmatter(text), (None, text))

    def test_unterminated_block(self):
        text = "---\nid: NA-0002\ntitle: never closed\n"
        self.assertEqual(parse_frontmatter(text), (None, text))

    def test_empty_string(self):
        self.assertEqual(parse_frontmatter(""), (None, ""))

    def test_garbage_lines_are_skipped_not_fatal(self):
        text = "---\nid: NA-0003\n%%% not a key %%%\nstatus: open\n---\nbody\n"
        fields, body = parse_frontmatter(text)
        self.assertEqual(fields["id"], "NA-0003")
        self.assertEqual(fields["status"], "open")
        self.assertEqual(body, "body\n")


class FormatValue(unittest.TestCase):
    def test_round_trip_of_the_types_the_schema_uses(self):
        self.assertEqual(format_value(None), "null")
        self.assertEqual(format_value(True), "true")
        self.assertEqual(format_value(False), "false")
        self.assertEqual(format_value(3), "3")
        self.assertEqual(format_value(["a", "b"]), "[a, b]")


class Rewriting(TempCase):
    def setUp(self):
        super().setUp()
        self.path = self.tmp / "NA-0001.md"
        self.path.write_text(DOC, encoding="utf-8")
        self.original = self.path.read_bytes()

    def _body_bytes(self, raw):
        # Everything from the closing marker onwards, compared as bytes so that a
        # re-encoding or a line-ending change would fail too.
        return raw[raw.index(b"\n---", 3):]

    def test_changes_only_the_frontmatter(self):
        self.assertTrue(rewrite_fields(self.path, {"status": "done"}))
        after = self.path.read_bytes()
        self.assertEqual(self._body_bytes(after), self._body_bytes(self.original))
        fields, body = parse_frontmatter(after.decode("utf-8"))
        self.assertEqual(fields["status"], "done")
        self.assertEqual(body, parse_frontmatter(DOC)[1])

    def test_nested_keys_are_not_matched_by_a_top_level_write(self):
        # `tier` exists only inside `automation`; writing it must append a new
        # top-level key rather than reach into the nested block.
        self.assertTrue(rewrite_fields(self.path, {"tier": "skill"}))
        fields, _ = parse_frontmatter(self.path.read_text(encoding="utf-8"))
        self.assertEqual(fields["tier"], "skill")
        self.assertEqual(fields["automation"]["tier"], "hook")

    def test_unchanged_value_does_not_rewrite_the_file(self):
        self.assertFalse(rewrite_fields(self.path, {"status": "open"}))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_absent_key_is_appended_inside_the_block(self):
        self.assertTrue(rewrite_fields(self.path, {"updated_date": "2026-03-16"}))
        fields, body = parse_frontmatter(self.path.read_text(encoding="utf-8"))
        self.assertEqual(fields["updated_date"], "2026-03-16")
        self.assertNotIn("updated_date", body)

    def test_file_without_frontmatter_is_left_alone(self):
        plain = self.tmp / "plain.md"
        plain.write_text("# No frontmatter\n", encoding="utf-8")
        self.assertFalse(rewrite_fields(plain, {"status": "done"}))
        self.assertEqual(plain.read_text(encoding="utf-8"), "# No frontmatter\n")


if __name__ == "__main__":
    unittest.main()
