"""Locale catalogs for rendered output.

Both ``en`` and ``zh`` are first-class: neither is a machine translation of the
other, and CI asserts the two catalogs have identical key sets so a new string
cannot land in one language only.

Only *rendered* strings live here. Diagnostics aimed at developers (exception
messages, ``--help``) stay in English so that pasted stack traces are searchable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

__all__ = ["Catalog", "load_catalog", "available_locales", "DEFAULT_LOCALE"]

DEFAULT_LOCALE = "en"
_LOCALE_DIR = Path(__file__).resolve().parent / "locales"


def available_locales() -> list:
    return sorted(p.stem for p in _LOCALE_DIR.glob("*.json"))


class Catalog:
    """A loaded locale. ``t()`` never raises -- a missing key renders as the key
    itself, which shows up loudly in output instead of crashing the nightly run."""

    __slots__ = ("locale", "_strings", "_fallback")

    def __init__(self, locale: str, strings: Dict[str, str], fallback=None):
        self.locale = locale
        self._strings = strings
        self._fallback = fallback

    def t(self, key: str, **kwargs) -> str:
        s = self._strings.get(key)
        if s is None and self._fallback is not None:
            s = self._fallback._strings.get(key)
        if s is None:
            return key
        if not kwargs:
            return s
        try:
            return s.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return s

    def has(self, key: str) -> bool:
        return key in self._strings

    def keys(self):
        return self._strings.keys()

    def __repr__(self) -> str:
        return "Catalog(%r, %d strings)" % (self.locale, len(self._strings))


def _read(locale: str) -> Dict[str, str]:
    path = _LOCALE_DIR / ("%s.json" % locale)
    if not path.is_file():
        raise FileNotFoundError(
            "unknown locale %r (available: %s)" % (locale, ", ".join(available_locales()))
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_catalog(locale=None) -> Catalog:
    """Load a catalog. Precedence: argument > ``$NEXTBRIEF_LOCALE`` > default."""
    name = locale or os.environ.get("NEXTBRIEF_LOCALE") or DEFAULT_LOCALE
    name = str(name).replace("-", "_").split(".")[0]
    if name not in available_locales():
        base = name.split("_")[0]
        name = base if base in available_locales() else DEFAULT_LOCALE
    fallback = None
    if name != DEFAULT_LOCALE:
        fallback = Catalog(DEFAULT_LOCALE, _read(DEFAULT_LOCALE))
    return Catalog(name, _read(name), fallback)
