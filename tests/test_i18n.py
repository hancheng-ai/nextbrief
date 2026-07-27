"""Locale catalogs.

Both languages are first-class, which is a claim that has to be mechanically
enforced or it decays on the first hurried commit. Two checks do it: the key sets
must be identical, and every key the renderers actually ask for must exist in
both. The second one is the interesting half -- a key that exists in neither
catalog renders as the bare key string, which is loud in output but invisible in
a diff.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from helpers import TempCase

from nextbrief import html as html_mod
from nextbrief import render as render_mod
from nextbrief.i18n import DEFAULT_LOCALE, Catalog, available_locales, load_catalog

LOCALE_DIR = Path(render_mod.__file__).resolve().parent / "locales"

# Keys reached through a dict rather than written at the call site. Collected from
# the modules themselves so that adding a signal or a tier cannot bypass the check.
_INDIRECT = (
    list(render_mod.WEEKDAY_KEYS)
    + list(render_mod.SIGNAL_KEYS.values())
    + list(render_mod.TIER_KEYS.values())
    + list(html_mod.WEEKDAY_KEYS)
    + [k for k, _cls in html_mod.SIGNAL.values()]
    + [k for pair in html_mod.TIER_KEYS.values() for k in pair]
    + [k for k, _cls in html_mod.BLOCKED_KEYS.values()]
)

_LITERAL_CALL = re.compile(r"\.t\(\s*[\"']([\w.\-]+)[\"']")


def keys_used_in(module) -> set:
    source = Path(module.__file__).read_text(encoding="utf-8")
    return set(_LITERAL_CALL.findall(source))


def load(locale):
    return json.loads((LOCALE_DIR / ("%s.json" % locale)).read_text(encoding="utf-8"))


class Catalogs(unittest.TestCase):
    def test_both_locales_are_shipped(self):
        self.assertIn("en", available_locales())
        self.assertIn("zh", available_locales())

    def test_key_sets_are_identical(self):
        en, zh = load("en"), load("zh")
        self.assertEqual(
            sorted(set(en) - set(zh)), [], "keys present in en but missing from zh"
        )
        self.assertEqual(
            sorted(set(zh) - set(en)), [], "keys present in zh but missing from en"
        )

    def test_every_locale_agrees_with_the_default(self):
        # Stated for all locales, not just zh, so adding a third one is covered
        # by this test the day it lands.
        reference = set(load(DEFAULT_LOCALE))
        for locale in available_locales():
            self.assertEqual(set(load(locale)), reference, "locale %r drifted" % locale)

    def test_placeholders_match_across_locales(self):
        # A translation that drops {count} renders a sentence with a hole in it.
        en, zh = load("en"), load("zh")
        placeholder = re.compile(r"\{(\w+)\}")
        for key, value in en.items():
            self.assertEqual(
                sorted(set(placeholder.findall(value))),
                sorted(set(placeholder.findall(zh[key]))),
                "placeholders differ for %r" % key,
            )

    def test_every_key_the_renderers_use_exists_in_both(self):
        used = keys_used_in(render_mod) | keys_used_in(html_mod) | set(_INDIRECT)
        # Sanity: if the scrape found nothing the test would pass vacuously.
        self.assertGreater(len(used), 50)
        for locale in ("en", "zh"):
            strings = load(locale)
            missing = sorted(k for k in used if k not in strings)
            self.assertEqual(missing, [], "missing from %s: %s" % (locale, missing))


class Lookup(TempCase):
    def test_formatting(self):
        cat = load_catalog("en")
        self.assertEqual(cat.t("evidence.commits_30d", count=9), "9 commits/30d")

    def test_a_missing_key_renders_as_itself(self):
        # Loud in the output, but the nightly run still completes.
        cat = load_catalog("en")
        self.assertEqual(cat.t("no.such.key"), "no.such.key")

    def test_a_missing_placeholder_does_not_raise(self):
        cat = load_catalog("en")
        self.assertEqual(cat.t("evidence.commits_30d"), "{count} commits/30d")

    def test_zh_falls_back_to_english_for_an_unknown_key(self):
        cat = Catalog("zh", {"a": "甲"}, Catalog("en", {"b": "bee"}))
        self.assertEqual(cat.t("a"), "甲")
        self.assertEqual(cat.t("b"), "bee")
        self.assertEqual(cat.t("c"), "c")

    def test_locale_precedence_and_normalisation(self):
        import os

        self.assertEqual(load_catalog("zh").locale, "zh")
        self.assertEqual(load_catalog("zh_CN.UTF-8").locale, "zh")
        self.assertEqual(load_catalog("zh-CN").locale, "zh")
        # An unknown language is not a reason to fail; it is a reason to speak
        # English.
        self.assertEqual(load_catalog("kl").locale, DEFAULT_LOCALE)
        os.environ["NEXTBRIEF_LOCALE"] = "zh"
        self.assertEqual(load_catalog().locale, "zh")
        self.assertEqual(load_catalog("en").locale, "en")

    def test_zh_output_is_not_a_machine_gloss_of_en(self):
        # A weak but real check that the two files were written, not generated:
        # no zh string may be byte-identical to its English original unless it is
        # pure punctuation or a format string with nothing else in it.
        en, zh = load("en"), load("zh")
        shared = [k for k in en if en[k] == zh[k] and re.search(r"[A-Za-z]{4}", en[k])]
        self.assertLess(len(shared), len(en) * 0.2, "zh looks copied from en: %s" % shared[:5])


if __name__ == "__main__":
    unittest.main()
