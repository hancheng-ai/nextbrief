"""The two ways of answering the review, and the rule that they agree.

Four heterogeneous questions across a dozen projects is the shape a prompt loop
handles worst: fixed order, one project visible at a time, no way back, and a
free-text date made as awkward as a menu. So the editor form is the default and
the browser form is opt-in — which means there are now three code paths writing
the same answers, and the thing most worth pinning is that they cannot come to
different conclusions about what a valid answer is.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
import urllib.parse
import urllib.request

from helpers import TempCase

from nextbrief import webform
from nextbrief.annotate import (
    QUESTIONS,
    coerce_answer,
    parse_review_form,
    render_review_form,
)
from nextbrief.i18n import load_catalog

PROJECTS = [
    {"id": "alpha", "name": "Alpha", "ice": {"impact": 4}},
    {"id": "beta", "name": "Beta"},
]


def _is_stdlib(name: str) -> bool:
    """Did this interpreter get `name` from its own standard library?

    Works on every version the package supports, which `sys.stdlib_module_names`
    does not -- see `test_it_adds_no_dependency`. The site-packages exclusion is
    not redundant with the prefix test: a virtualenv built with
    `--system-site-packages` can place site-packages underneath the stdlib
    prefix, and without the second test every installed package there would read
    as standard library.
    """
    import importlib.util
    import os
    import sysconfig

    if name in sys.builtin_module_names:
        return True
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False        # not importable at all is certainly not stdlib
    if spec is None:
        return False
    if spec.origin in ("built-in", "frozen"):
        return True
    if spec.origin is None:
        return False        # a namespace package, which the stdlib does not use
    stdlib = os.path.realpath(sysconfig.get_paths()["stdlib"])
    origin = os.path.realpath(spec.origin)
    return (origin.startswith(stdlib + os.sep)
            and "site-packages" not in origin
            and "dist-packages" not in origin)


class TheEditorForm(unittest.TestCase):
    def form(self):
        return render_review_form(PROJECTS, load_catalog("en"))

    def test_it_shows_every_project_and_every_question(self):
        text = self.form()
        for pid in ("alpha", "beta"):
            self.assertIn("[%s]" % pid, text)
        for q in QUESTIONS:
            self.assertIn(q.field + ":", text)

    def test_an_existing_answer_is_shown_rather_than_blanked(self):
        """The point of a form over a prompt: what is already known stays
        visible while the rest is filled in."""
        self.assertIn("impact:      4", self.form())

    def test_a_filled_form_round_trips(self):
        text = """[alpha]
impact: 5
positioning: flagship
status: frozen
deadline: 2026-12-01
"""
        got = parse_review_form(text, known={"alpha"})
        self.assertEqual(got["alpha"]["ice"], {"impact": 5})
        self.assertEqual(got["alpha"]["positioning"], "flagship")
        self.assertEqual(got["alpha"]["status"], "frozen")
        self.assertEqual(got["alpha"]["deadlines"][0]["date"], "2026-12-01")

    def test_one_bad_line_costs_that_line_and_nothing_else(self):
        """The file is hand-edited. A mistyped date must not discard eleven
        other projects' answers."""
        text = """[alpha]
impact: not-a-number
positioning: nonsense
status: active
deadline: 31/12/2026
"""
        got = parse_review_form(text, known={"alpha"})
        self.assertEqual(got["alpha"], {"status": "active"})

    def test_a_section_for_an_unknown_project_is_dropped(self):
        """Otherwise a typo in a header records an answer that is never shown
        and never explained."""
        got = parse_review_form("[ghost]\nimpact: 5\n", known={"alpha"})
        self.assertEqual(got, {})

    def test_blank_means_unanswered(self):
        got = parse_review_form("[alpha]\nimpact:\nstatus:   \n", known={"alpha"})
        self.assertEqual(got, {})


class TheBrowserForm(unittest.TestCase):
    def page(self):
        return webform.form_url_for_test(PROJECTS, load_catalog("en"))

    def test_every_choice_becomes_a_radio_and_the_date_a_date_input(self):
        page = self.page()
        choices = sum(len(q.choices) for q in QUESTIONS) * len(PROJECTS)
        self.assertEqual(page.count('type="radio"'), choices)
        self.assertEqual(page.count('type="date"'), len(PROJECTS))

    def test_an_existing_answer_is_pre_selected(self):
        self.assertIn('value="4" checked', self.page())

    def test_it_adds_no_dependency(self):
        """`webbrowser` is already used to open BRIEF.html and `http.server` is
        standard library, so the browser form costs a socket rather than a
        package. The zero-dependency rule is load-bearing for the unattended
        path, and this is the module most likely to tempt someone away from it.

        Resolved rather than looked up in a name list. `sys.stdlib_module_names`
        would be the obvious tool and cannot be used here: it arrived in 3.10, so
        on the 3.9 floor -- the interpreter this rule exists to protect -- it
        raises `AttributeError` and the assertion below never runs. A guard that
        cannot fail on the only platform it is for is worse than no guard, and
        this one shipped that way: green on 3.11 and 3.13, red on 3.9, for two
        commits.

        Asking the import system where a module actually lives is also the
        stronger question. A name list says `json` is standard library; it cannot
        say whether *this* interpreter's `json` came from the standard library or
        from something earlier on `sys.path`.
        """
        import ast
        import pathlib

        source = pathlib.Path(webform.__file__).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(sorted(n for n in imported if not _is_stdlib(n)), [])

    def test_the_dependency_check_can_actually_fail(self):
        """The guard above is only worth its line count if it can say no.

        Its predecessor could not: it raised before asserting on 3.9 and was
        skipped by the runner, which looks exactly like passing. So the negative
        case is pinned here rather than left to be true.

        `nextbrief` is the honest probe -- importable in every environment the
        suite runs in, since this file imports it, and about as far from the
        standard library as a module gets.
        """
        self.assertFalse(_is_stdlib("nextbrief"))
        self.assertFalse(_is_stdlib("nextbrief_no_such_module"))
        self.assertTrue(_is_stdlib("webbrowser"))
        self.assertTrue(_is_stdlib("sys"))


class TheBrowserFormOverASocket(TempCase):
    """One real round-trip. The parsing above is unit-testable; that the socket
    binds to loopback, refuses a wrong token and stops after one answer is not.
    """

    def setUp(self):
        super().setUp()
        self.opened = {}
        self._real_open = webform.webbrowser.open
        webform.webbrowser.open = lambda url: self.opened.setdefault("url", url)
        self.addCleanup(setattr, webform.webbrowser, "open", self._real_open)

    def _serve(self):
        result = {}
        thread = threading.Thread(
            target=lambda: result.update(answers=webform.collect(PROJECTS, load_catalog("en"))),
            daemon=True)
        thread.start()
        for _ in range(100):
            if "url" in self.opened:
                break
            time.sleep(0.02)
        self.assertIn("url", self.opened, "the form never started")
        return result, thread, self.opened["url"]

    def test_it_binds_to_loopback_only(self):
        result, thread, url = self._serve()
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        urllib.request.urlopen(urllib.request.Request(
            url, data=urllib.parse.urlencode({"alpha::status": "done"}).encode()))
        thread.join(5)

    def test_a_wrong_token_is_refused(self):
        """The page is served to one reader for one purpose. Another tab open in
        the same browser should not be able to post to it by guessing."""
        result, thread, url = self._serve()
        base = url.rsplit("/", 1)[0]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/not-the-token")
        self.assertEqual(caught.exception.code, 404)
        urllib.request.urlopen(urllib.request.Request(
            url, data=urllib.parse.urlencode({"alpha::status": "done"}).encode()))
        thread.join(5)

    def test_a_submission_comes_back_and_the_server_stops(self):
        result, thread, url = self._serve()
        body = urllib.parse.urlencode({
            "alpha::impact": "5", "alpha::positioning": "flagship",
            "alpha::deadline": "2026-09-01", "beta::status": "done",
            "ghost::impact": "5",
        }).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=body))
        thread.join(5)
        self.assertFalse(thread.is_alive(), "the server outlived its one answer")

        got = result["answers"]
        self.assertEqual(got["alpha"]["impact"], "5")
        self.assertEqual(got["beta"]["status"], "done")
        self.assertNotIn("ghost", got, "a project nobody asked about was accepted")


class BothPathsAgree(unittest.TestCase):
    def test_the_same_coercion_decides_both(self):
        """The browser posts strings and the editor form parses strings, so both
        go through `coerce_answer`. Two validators would eventually disagree, and
        the disagreement would surface as an answer that records from one input
        and vanishes from the other."""
        by_field = {q.field: q for q in QUESTIONS}
        cases = [("impact", "5", 5), ("impact", "3", None),
                 ("status", "frozen", "frozen"), ("status", "asleep", None),
                 ("deadline", "2026-01-31", "2026-01-31"), ("deadline", "soon", None)]
        for field, raw, want in cases:
            self.assertEqual(coerce_answer(by_field[field], raw), want,
                             "%s=%r" % (field, raw))


if __name__ == "__main__":
    unittest.main()
