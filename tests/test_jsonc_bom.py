"""A UTF-8 BOM in a hand-edited JSONC file must not end the run.

``registry.jsonc`` and ``config.jsonc`` are documented as files a person opens in
an editor, and Notepad, older VS Code profiles and several Windows editors write
a BOM without ever mentioning it. Read as plain ``utf-8`` that byte survives into
``json.loads``, which reports "Expecting value: line 1 column 1" -- an accurate
message about the wrong layer, aimed at someone reading a traceback rather than
someone holding an editor.

The BOM is only ever stripped on the way in. Nothing here should make the parser
tolerant of a BOM in the *middle* of a document: that is corruption, not an
editor convention, and it must still fail.
"""

from __future__ import annotations

import unittest

from helpers import TempCase

from nextbrief.jsonc import JSONCError, load_jsonc

BOM = "﻿"


class BomTolerance(TempCase):
    def _write(self, name, text, encoding="utf-8"):
        path = self.tmp / name
        path.write_text(text, encoding=encoding)
        return path

    def test_bom_before_a_comment(self):
        """The realistic shape: a BOM, then the comment header these files open with."""
        path = self._write(
            "registry.jsonc",
            BOM + '// what the projects are\n{\n  "projects": [],\n}\n',
        )
        self.assertEqual(load_jsonc(path), {"projects": []})

    def test_bom_before_the_brace(self):
        path = self._write("config.jsonc", BOM + '{"locale": "en"}')
        self.assertEqual(load_jsonc(path), {"locale": "en"})

    def test_utf8_sig_encoder_round_trip(self):
        """What an editor actually does, rather than a hand-placed character."""
        path = self._write("config.jsonc", '{"caps": {"max_next_actions": 3}}',
                           encoding="utf-8-sig")
        self.assertEqual(load_jsonc(path), {"caps": {"max_next_actions": 3}})

    def test_no_bom_is_unaffected(self):
        path = self._write("registry.jsonc", '{\n  "a": 1,  // note\n}\n')
        self.assertEqual(load_jsonc(path), {"a": 1})

    def test_non_ascii_values_survive(self):
        """utf-8-sig is utf-8 past the first three bytes; prove nothing else shifts."""
        path = self._write("registry.jsonc", BOM + '{"owner": "张三", "note": "café"}')
        self.assertEqual(load_jsonc(path), {"owner": "张三", "note": "café"})

    def test_bom_inside_the_document_still_fails(self):
        """A BOM that is not a leading byte-order mark is corruption, not a convention."""
        path = self._write("config.jsonc", '{"a": 1}' + BOM + "{}")
        with self.assertRaises(JSONCError):
            load_jsonc(path)


if __name__ == "__main__":
    unittest.main()
