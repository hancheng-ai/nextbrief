"""JSONC parsing.

Every assertion here is on the *parsed value*, never on the stripped text. The
bug this module was rewritten to fix produced perfectly well-formed JSON with a
quietly different string in it, which an assertion about whitespace or shape
would have sailed straight past.
"""

from __future__ import annotations

import unittest

from helpers import FIXTURES, TempCase

from nextbrief.jsonc import JSONCError, load_jsonc, loads_jsonc, strip_jsonc


class StripComments(unittest.TestCase):
    def test_line_comment(self):
        text = """
        // leading note
        {
          "a": 1,   // trailing note
          "b": 2
        }
        """
        self.assertEqual(loads_jsonc(text), {"a": 1, "b": 2})

    def test_block_comment(self):
        text = """
        /* a block
           spanning lines */
        {"a": /* inline */ 1, "b": 2}
        """
        self.assertEqual(loads_jsonc(text), {"a": 1, "b": 2})

    def test_block_comment_containing_a_brace(self):
        # A naive scanner that tracked nesting outside string/comment state would
        # see this as an unbalanced document.
        text = '{"a": 1 /* { not a real brace } */ , "b": [1, 2] /* ] */ }'
        self.assertEqual(loads_jsonc(text), {"a": 1, "b": [1, 2]})

    def test_line_numbers_survive_stripping(self):
        # Whitespace and newlines are preserved so a json error still points at
        # the line the human wrote.
        text = '{\n  // note\n  "a": 1\n}'
        self.assertEqual(strip_jsonc(text).count("\n"), text.count("\n"))


class TrailingCommas(unittest.TestCase):
    def test_object(self):
        self.assertEqual(loads_jsonc('{"a": 1, "b": 2,}'), {"a": 1, "b": 2})

    def test_array(self):
        self.assertEqual(loads_jsonc("[1, 2, 3,]"), [1, 2, 3])

    def test_nested_and_multiline(self):
        text = """
        {
          "outer": {
            "inner": [1, 2,],
            "map": {"k": "v",},
          },
          "list": [
            {"x": 1,},
          ],
        }
        """
        self.assertEqual(
            loads_jsonc(text),
            {"outer": {"inner": [1, 2], "map": {"k": "v"}}, "list": [{"x": 1}]},
        )

    def test_double_comma_still_fails(self):
        # Repairing this would be the parser deciding what the author meant.
        with self.assertRaises(ValueError):
            loads_jsonc('{"a": 1,, "b": 2}')


class StringsAreNeverTouched(unittest.TestCase):
    """The regression suite proper: string literals that *look* like syntax."""

    def test_comma_before_bracket_inside_a_string(self):
        # The exact document the old regex-based stripper corrupted: it produced
        # {"a": "foo]bar"} -- valid JSON, wrong value, no error anywhere.
        self.assertEqual(loads_jsonc('{"a": "foo, ]bar"}'), {"a": "foo, ]bar"})

    def test_comma_before_brace_inside_a_string(self):
        self.assertEqual(loads_jsonc('{"a": "foo, }bar"}'), {"a": "foo, }bar"})

    def test_double_slash_inside_a_string(self):
        value = loads_jsonc('{"url": "https://example.invalid/docs?a=1//2"}')
        self.assertEqual(value["url"], "https://example.invalid/docs?a=1//2")

    def test_block_comment_opener_inside_a_string(self):
        self.assertEqual(loads_jsonc('{"a": "/* not a comment */"}'), {"a": "/* not a comment */"})

    def test_escaped_quotes(self):
        self.assertEqual(
            loads_jsonc(r'{"a": "she said \"foo, ]bar\" out loud"}'),
            {"a": 'she said "foo, ]bar" out loud'},
        )

    def test_escaped_backslash_before_quote(self):
        # The escape state machine must not treat the closing quote of "c:\\" as
        # escaped, or everything after it is swallowed as string content.
        self.assertEqual(loads_jsonc(r'{"a": "back\\", "b": 2,}'), {"a": "back\\", "b": 2})


class LoadFromFile(TempCase):
    def test_reads_a_file(self):
        path = self.tmp / "conf.jsonc"
        path.write_text('// note\n{"a": [1, 2,],}\n', encoding="utf-8")
        self.assertEqual(load_jsonc(path), {"a": [1, 2]})

    def test_missing_file_reports_the_path(self):
        with self.assertRaises(JSONCError) as caught:
            load_jsonc(self.tmp / "absent.jsonc")
        self.assertIn("absent.jsonc", str(caught.exception))

    def test_malformed_file_reports_path_and_position(self):
        path = self.tmp / "bad.jsonc"
        path.write_text('{\n  "a": 1\n  "b": 2\n}\n', encoding="utf-8")
        with self.assertRaises(JSONCError) as caught:
            load_jsonc(path)
        message = str(caught.exception)
        self.assertIn("bad.jsonc", message)
        self.assertIn("line 3", message)

    def test_the_hostile_fixture_parses_to_exactly_the_values_written_in_it(self):
        # One document containing every trap at once. The reduced case from any
        # future "my config will not load" report belongs in that file.
        data = load_jsonc(FIXTURES / "hostile.jsonc")
        self.assertEqual(data["trailing_comma_object"], {"a": 1, "b": 2})
        self.assertEqual(data["trailing_comma_array"], [1, 2, 3])
        self.assertEqual(data["nested"], {"deeper": [{"k": "v"}]})
        self.assertEqual(data["comma_then_bracket"], "foo, ]bar")
        self.assertEqual(data["comma_then_brace"], "foo, }bar")
        self.assertEqual(data["url"], "https://example.invalid/handbook?a=1//2")
        self.assertEqual(data["looks_like_a_block_comment"], "/* not a comment */")
        self.assertEqual(data["escaped_quotes"], 'she said "foo, ]bar" out loud')
        self.assertEqual(data["escaped_backslash"], "ends with a backslash \\")
        self.assertEqual(data["unicode"], "潮汐 · タイド · tide")
        self.assertEqual(data["after_a_comment"], 42)

    def test_shipped_examples_parse(self):
        # The example workspace is documentation people copy from; a broken one
        # is worse than none.
        example = self.copy_example()
        self.assertIsInstance(load_jsonc(example / "registry.jsonc"), dict)
        self.assertIsInstance(load_jsonc(example / "config.jsonc"), dict)


if __name__ == "__main__":
    unittest.main()
