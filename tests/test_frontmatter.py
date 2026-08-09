"""The YAML-subset frontmatter parser and its single writer.

Two contracts are load-bearing and are what most of this file is about: parsing
never raises (one bad backlog entry must not end the nightly run), and writing
touches the frontmatter block only (the prose underneath is a human's, and no
amount of malformed input justifies rewriting it).
"""

from __future__ import annotations

import unittest

from helpers import TempCase, fixture

from nextbrief.frontmatter import (
    format_value,
    parse_frontmatter,
    remove_fields,
    rewrite_fields,
)

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


class CarriageReturns(unittest.TestCase):
    """The same document with CRLF endings has to parse to the same thing.

    Not reachable through a file read, which is the trap: every ``read_text`` in
    the package gets Python's universal-newline translation for free, so a CRLF
    file on disk arrives here already normalised. A test that wrote a CRLF file
    and read it back would pass without the fix and prove nothing.

    The caller that does hand this function untranslated bytes is
    ``render._baseline_by_id``, which parses ``git show HEAD:<item>`` straight
    off the subprocess pipe. A repository checked out on Windows -- or anywhere
    with ``core.autocrlf`` on -- has CRLF in that blob, and the write-permission
    gate compares what comes back against a ``read_text`` parse of the same
    file. Two parses of one file that disagree about line endings is the whole
    problem, so the property asserted here is the parser's own: the result must
    not depend on which ending the caller happened to have.
    """

    def setUp(self):
        self.lf = parse_frontmatter(DOC)
        self.crlf = parse_frontmatter(DOC.replace("\n", "\r\n"))

    def test_the_body_does_not_start_with_the_closing_markers_own_newline(self):
        # `text[end + 4:]` hardcodes len("\n---"), so under CRLF the slice
        # starts on the delimiter's own \r, and `.lstrip("\n")` cannot remove
        # it. The body arrives with the marker's tail welded to the front.
        _fields, body = self.crlf
        self.assertTrue(body.startswith("# Body"), repr(body[:24]))

    def test_a_block_scalar_does_not_keep_its_carriage_returns(self):
        # The accumulator appends line content verbatim and joins with "\n", so
        # every line but the last keeps the \r that split("\n") left behind.
        # `.strip()` only reaches the two ends, so an interior one survives.
        fields, _body = self.crlf
        self.assertEqual(
            fields["notes"],
            "Two paragraphs of context that belong to the field,\nnot to the body.",
        )

    def test_the_parse_is_identical_either_way(self):
        self.assertEqual(self.crlf, self.lf)


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


class Removing(TempCase):
    """The other writer, and the more dangerous one: it deletes lines out of a
    file a person owns. Taking the wrong line is worse than the illegal write it
    is reverting, so what it refuses to touch matters as much as what it removes.
    """

    def setUp(self):
        super().setUp()
        self.path = self.tmp / "NA-0001.md"
        self.path.write_text(DOC, encoding="utf-8")
        self.original = self.path.read_bytes()

    def _fields(self):
        return parse_frontmatter(self.path.read_text(encoding="utf-8"))[0]

    def test_a_scalar_key_goes_and_nothing_else_moves(self):
        self.assertTrue(remove_fields(self.path, ["priority"]))
        after = self.path.read_text(encoding="utf-8")
        self.assertNotIn("priority", after)
        fields, body = parse_frontmatter(after)
        self.assertEqual(fields["id"], "NA-0001")
        self.assertEqual(fields["is_next_action"], True)
        self.assertEqual(body, parse_frontmatter(DOC)[1])

    def test_several_keys_at_once(self):
        self.assertTrue(remove_fields(self.path, ["priority", "human_confirmed"]))
        fields = self._fields()
        self.assertNotIn("priority", fields)
        self.assertNotIn("human_confirmed", fields)
        self.assertEqual(fields["estimate_min"], 45)

    def test_a_key_owning_a_nested_block_is_refused(self):
        """★ Removing the header would orphan its body into the previous key. ★

        `automation:` owns four indented lines. Delete the header and the parser
        reads them as belonging to `estimate_min`, which is not a smaller version
        of the original edit -- it is a different, wrong document.
        """
        self.assertFalse(remove_fields(self.path, ["automation"]),
                         "a key owning indented lines was removed by its header alone")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self._fields()["automation"]["tier"], "hook")

    def test_a_block_scalar_is_refused_too(self):
        # `notes: |` owns the two indented lines under it, for the same reason.
        self.assertFalse(remove_fields(self.path, ["notes"]),
                         "a block scalar was removed by its header alone")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_a_nested_key_is_not_reachable_from_the_top_level(self):
        # `tier` exists only inside `automation`. A top-level removal must not
        # reach into the block -- the mirror of the same rule in `rewrite_fields`.
        self.assertFalse(remove_fields(self.path, ["tier"]))
        self.assertEqual(self._fields()["automation"]["tier"], "hook")

    def test_removing_a_key_that_is_not_there_changes_nothing(self):
        self.assertFalse(remove_fields(self.path, ["deferred_until"]))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_file_without_frontmatter_is_left_alone(self):
        plain = self.tmp / "plain.md"
        plain.write_text("# No frontmatter\n", encoding="utf-8")
        self.assertFalse(remove_fields(plain, ["status"]))
        self.assertEqual(plain.read_text(encoding="utf-8"), "# No frontmatter\n")


if __name__ == "__main__":
    unittest.main()
