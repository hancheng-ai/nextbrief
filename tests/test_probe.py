"""External probes: the sensor that reads work which never lands on disk.

Four of these tests exist because the feature is only worth having if they hold.
A probe is the engine's first sensor pointed at somebody else's machine, so the
things that could go wrong are not the usual ones:

* **Stage 1 must stay offline.** ``test_sense_never_touches_the_network`` runs a
  whole sense with sockets amputated. If anything in stage 1 ever grows a fetch,
  this fails -- which is the only way to keep a property that is otherwise held
  by nothing but good intentions.
* **A failure must never read as a zero.** A broken sensor reports 0, and 0 is
  indistinguishable from "nothing happened" -- the single most expensive
  sentence this tool could get wrong.
* **A reading must carry its age.** An undated number is prose.
* **The boundary must be a check, not a promise.** Read-only, credential-free,
  declared URLs only -- asserted rather than documented.
"""

from __future__ import annotations

import datetime as dt
import json
import socket
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from helpers import (
    AS_OF,
    AS_OF_DATE,
    BASE_CONFIG,
    TempCase,
    base_registry,
    capture,
    make_project_entry,
    make_snapshot,
    write_snapshot,
)

from nextbrief import cli, probe, render, sense

MIA_URL = "https://mia.example.invalid/blog/knowledge.json"
WP_URL = "https://wp.example.invalid/?rest_route=/wp/v2/posts&per_page=1&orderby=modified"

# Shaped exactly like the two real responses this was built against: one hands
# you an object with an array inside it, the other an array with the count in a
# header. Between them they cover both selector styles.
MIA_BODY = {
    "tenant": "mia",
    "generated_at": "2026-03-16T00:00:00.000Z",
    "posts": [
        {"title": "Older", "published_at": "2026-02-20T09:00:00.000Z"},
        {"title": "Newest", "published_at": "2026-03-01T01:06:51.336Z"},
        {"title": "Middle", "published_at": "2026-02-28T07:48:08.533Z"},
    ],
    "tags": [{"name": "Math", "post_count": 3}],
}
WP_BODY = [{"id": 1936, "date": "2026-01-03T15:42:56", "modified": "2026-03-02T12:29:39"}]


def mia_probe(**over):
    spec = {"url": MIA_URL, "count": "posts[]", "date": "posts[].published_at",
            "label": "published posts", "ttl_days": 7}
    spec.update(over)
    return spec


def registry_with_probe(spec=None, pid="orchard"):
    reg = base_registry()
    for pr in reg["projects"]:
        if pr["id"] == pid:
            pr["evidence_probe"] = mia_probe() if spec is None else spec
    return reg


def cache_entry(**over):
    """A cache entry as ``nextbrief probe`` would have written it."""
    entry = {
        "id": "orchard", "url": MIA_URL, "label": "published posts", "ttl_days": 7,
        "sampled_at": "2026-03-15T08:00:00+00:00", "ok": True,
        "count": 3, "date": "2026-03-01",
        "http_status": 200, "error_code": None, "error_detail": None,
        "last_ok": {"sampled_at": "2026-03-15T08:00:00+00:00",
                    "count": 3, "date": "2026-03-01"},
    }
    entry.update(over)
    return entry


def write_cache(ws_root, entries):
    state = Path(ws_root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "probes.json"
    path.write_text(json.dumps({"schema": 1, "probes": entries}, indent=2) + "\n",
                    encoding="utf-8")
    return path


class _FakeResponse:
    def __init__(self, body: bytes, headers=None, status=200):
        self._body = body
        self.headers = headers or {}
        self.status = status

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(body, headers=None, status=200, capture_req=None):
    """An opener that answers one canned response and records the request.

    Patched at ``build_opener`` rather than at ``urlopen`` so the handler stack
    the module builds -- including the redirect policy -- is exercised on every
    path that does not specifically test redirects.
    """
    class _Opener:
        addheaders = []

        def open(self, req, timeout=None):
            if capture_req is not None:
                capture_req.append(req)
            payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            return _FakeResponse(payload, headers, status)

    return lambda *handlers: _Opener()


# ---------------------------------------------------------------------------
# AC #1 / #2 -- the declarative field and the two numbers it names
# ---------------------------------------------------------------------------


class RegistryField(unittest.TestCase):
    """`evidence_probe` says where a project's output actually lives."""

    def test_a_declared_probe_is_parsed(self):
        spec = probe.parse_probe({"id": "orchard", "evidence_probe": mia_probe()})
        self.assertEqual(spec["url"], MIA_URL)
        self.assertEqual(spec["count"], "posts[]")
        self.assertEqual(spec["date"], "posts[].published_at")
        self.assertEqual(spec["ttl_days"], 7)

    def test_a_project_without_one_is_simply_absent(self):
        self.assertIsNone(probe.parse_probe({"id": "kiln"}))

    def test_a_malformed_probe_is_recorded_not_swallowed(self):
        # A silently ignored probe is the worst outcome available: a sensor you
        # believe you have. Every rejection lands in parse_failed instead.
        for bad, why in (
            ({"count": "posts[]"}, "no url"),
            ({"url": MIA_URL}, "no count and no date"),
            ({"url": "http://insecure.example.invalid/x.json", "count": "a[]"}, "not https"),
            ({"url": "https://u:p@h.example.invalid/x.json", "count": "a[]"}, "credentials"),
            ({"url": MIA_URL, "count": "a[]", "ttl_days": -3}, "negative ttl"),
            ({"url": MIA_URL, "count": 7}, "non-string selector"),
        ):
            problems = []
            spec = probe.parse_probe({"id": "orchard", "evidence_probe": bad}, problems)
            self.assertIsNone(spec, why)
            self.assertEqual(len(problems), 1, why)
            self.assertEqual(problems[0]["code"], "bad_evidence_probe", why)

    def test_probes_of_is_keyed_by_project(self):
        specs = probe.probes_of(registry_with_probe())
        self.assertEqual(sorted(specs), ["orchard"])


class Selectors(unittest.TestCase):
    """The selector language stays small enough to have no surprises in it."""

    def test_array_length_is_the_count(self):
        got = probe.resolve_selector("posts[]", MIA_BODY, {})
        self.assertEqual(probe._to_count(got, "posts[]"), 3)

    def test_the_newest_date_wins_regardless_of_array_order(self):
        # The fixture deliberately lists Newest in the middle. Taking max rather
        # than first makes the reading independent of an API's ordering, which
        # not every endpoint offers and none of them promise to keep.
        got = probe.resolve_selector("posts[].published_at", MIA_BODY, {})
        self.assertEqual(probe._to_date(got, "posts[].published_at"), "2026-03-01")

    def test_a_response_header_can_be_the_count(self):
        got = probe.resolve_selector("header:X-WP-Total", WP_BODY, {"X-WP-Total": "55"})
        self.assertEqual(probe._to_count(got, "header:X-WP-Total"), 55)

    def test_header_lookup_is_case_insensitive(self):
        got = probe.resolve_selector("header:x-wp-total", WP_BODY, {"X-WP-Total": "55"})
        self.assertEqual(probe._to_count(got, "h"), 55)

    def test_top_level_array_field(self):
        got = probe.resolve_selector("[].modified", WP_BODY, {})
        self.assertEqual(probe._to_date(got, "[].modified"), "2026-03-02")

    def test_a_shape_that_is_not_there_is_a_named_failure(self):
        # This is how a site redesign announces itself. It must never arrive as a
        # count of zero.
        with self.assertRaises(probe.ProbeError) as ctx:
            probe.resolve_selector("articles[]", MIA_BODY, {})
        self.assertEqual(ctx.exception.code, "selector_no_match")

    def test_a_non_date_where_a_date_was_promised_is_a_failure(self):
        with self.assertRaises(probe.ProbeError) as ctx:
            probe._to_date(["not a date"], "posts[].published_at")
        self.assertEqual(ctx.exception.code, "bad_date")


# ---------------------------------------------------------------------------
# AC #7 -- read-only, no credentials, declared URLs only
# ---------------------------------------------------------------------------


class Boundaries(TempCase):
    """The three promises, as checks. A promise nothing tests is a comment."""

    def test_only_declared_urls_are_fetched(self):
        with self.assertRaises(probe.ProbeError) as ctx:
            probe.fetch("https://elsewhere.example.invalid/x.json",
                        declared={MIA_URL})
        self.assertEqual(ctx.exception.code, "policy")

    def test_a_url_carrying_credentials_is_refused(self):
        with self.assertRaises(probe.ProbeError) as ctx:
            probe.check_url("https://user:secret@h.example.invalid/x.json")
        self.assertEqual(ctx.exception.code, "policy")

    def test_plain_http_is_refused(self):
        with self.assertRaises(probe.ProbeError) as ctx:
            probe.check_url("http://h.example.invalid/x.json")
        self.assertEqual(ctx.exception.code, "policy")

    def test_the_request_is_a_credential_free_get(self):
        seen = []
        with mock.patch.object(urllib.request, "build_opener",
                               fake_opener(MIA_BODY, capture_req=seen)):
            probe.fetch(MIA_URL, declared={MIA_URL})
        self.assertEqual(len(seen), 1)
        req = seen[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertIsNone(req.data, "a probe never sends a body")
        # Header names arrive capitalised through Request; compare lowered.
        sent = {k.lower() for k in req.headers}
        for forbidden in ("authorization", "cookie", "proxy-authorization"):
            self.assertNotIn(forbidden, sent)

    def test_a_redirect_off_the_declared_origin_is_refused(self):
        handler = probe._StrictRedirect(probe._origin(MIA_URL))
        with self.assertRaises(probe.ProbeError) as ctx:
            handler.redirect_request(None, None, 301, "Moved", {},
                                     "https://somewhere-else.example.invalid/x.json")
        self.assertEqual(ctx.exception.code, "redirect_offsite")

    def test_an_oversized_response_is_refused_rather_than_read(self):
        big = b"x" * (probe.MAX_BYTES + 10)
        with mock.patch.object(urllib.request, "build_opener", fake_opener(big)):
            with self.assertRaises(probe.ProbeError) as ctx:
                probe.fetch(MIA_URL, declared={MIA_URL})
        self.assertEqual(ctx.exception.code, "too_large")


# ---------------------------------------------------------------------------
# AC #3 -- sense never goes online
# ---------------------------------------------------------------------------


class _NetworkAmputated:
    """Every route out of the process, closed.

    Patching ``urlopen`` alone would prove nothing: any module can import socket
    directly. Closing the socket layer itself means stage 1 cannot reach the
    network by any path, including one added later by somebody who never read
    this file.
    """

    def __init__(self):
        self._patches = []

    def _boom(self, *a, **k):
        raise AssertionError("stage 1 opened a network connection")

    def __enter__(self):
        for target, attr in ((socket, "socket"), (socket, "create_connection"),
                             (socket, "getaddrinfo"), (urllib.request, "urlopen"),
                             (urllib.request, "build_opener")):
            p = mock.patch.object(target, attr, self._boom)
            p.start()
            self._patches.append(p)
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class SenseStaysOffline(TempCase):

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(registry=registry_with_probe())
        write_cache(self.ws, {"orchard": cache_entry()})

    def _sense(self, *args):
        return capture(sense.main,
                       ["--workspace", str(self.ws), "--as-of", AS_OF] + list(args))

    def test_sense_never_touches_the_network(self):
        with _NetworkAmputated():
            code, _, err = self._sense()
        self.assertEqual(code, 0, err)

    def test_and_still_reports_the_cached_reading(self):
        # The half that makes the test above mean something. Passing with sockets
        # closed proves only that stage 1 did not call out; this proves it did
        # the work anyway, from the file on disk. Without it, a sense that
        # silently skipped probes entirely would look identical.
        with _NetworkAmputated():
            self.assertEqual(self._sense()[0], 0)
        snap = json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        p = next(x for x in snap["projects"] if x["id"] == "orchard")
        self.assertEqual(p["probe"]["count"], 3)
        self.assertEqual(p["probe"]["date"], "2026-03-01")
        self.assertTrue(p["probe"]["ok"])

    def test_a_missing_cache_is_never_sampled_not_a_failure(self):
        (self.ws / "state" / "probes.json").unlink()
        with _NetworkAmputated():
            self.assertEqual(self._sense()[0], 0)
        snap = json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        p = next(x for x in snap["projects"] if x["id"] == "orchard")
        self.assertTrue(p["probe"]["never_sampled"])
        self.assertIsNone(p["probe"]["error_code"],
                          "not having run `probe` yet is not a probe failure")


# ---------------------------------------------------------------------------
# AC #4 -- the reading reaches the snapshot with its sampling time, and the
#          evidence gate can resolve it
# ---------------------------------------------------------------------------


class SnapshotAndGate(TempCase):

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(registry=registry_with_probe())
        write_cache(self.ws, {"orchard": cache_entry()})
        capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.snap = json.loads(
            (self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))

    def test_the_reading_carries_when_it_was_sampled(self):
        p = next(x for x in self.snap["projects"] if x["id"] == "orchard")
        self.assertEqual(p["probe"]["sampled_at"], "2026-03-15T08:00:00+00:00")
        self.assertEqual(p["probe"]["age_days"], 1)

    def test_a_probe_handle_is_indexed_as_probe_grade_evidence(self):
        entry = self.snap["evidence_index"].get("probe:orchard")
        self.assertIsNotNone(entry, "the probe reading must be citable")
        self.assertIn("probe", entry["kinds"])

    def test_the_model_is_offered_the_handle_and_the_age_together(self):
        digest = json.loads((self.ws / "state" / "digest.json").read_text(encoding="utf-8"))
        p = next(x for x in digest["projects"] if x["id"] == "orchard")
        self.assertIn("probe:orchard", p["cite"])
        self.assertEqual(p["probe"]["count"], 3)
        self.assertEqual(p["probe"]["sampled_days_ago"], 1)

    def test_a_claim_citing_the_probe_passes_the_gate(self):
        rejected = []
        claim = {"text": "three posts published",
                 "evidence": [{"kind": "probe", "source": "probe:orchard"}]}
        ok = render.check_evidence(claim, self.snap["evidence_index"], BASE_CONFIG,
                                   rejected, "next_actions")
        self.assertTrue(ok, rejected)

    def test_a_file_path_cannot_be_dressed_up_as_a_probe_reading(self):
        # probe is kind-checked for the same reason commit is, only more so: its
        # facts are not on this machine, so the reader cannot go and look. A
        # claim that borrows that authority from an mtime is fabrication.
        rejected = []
        claim = {"text": "three posts published",
                 "evidence": [{"kind": "probe", "source": "orchard/README.md"}]}
        ok = render.check_evidence(claim, self.snap["evidence_index"], BASE_CONFIG,
                                   rejected, "next_actions")
        self.assertFalse(ok)
        self.assertEqual(rejected[0]["kind"], "evidence_kind_mismatch")

    def test_no_handle_is_minted_for_a_probe_that_has_never_read_anything(self):
        # Advertising a citation with no fact behind it invites the model to
        # write "9 posts published" about a project the engine has never
        # successfully read -- and the gate only checks that a source resolves.
        write_cache(self.ws, {})
        capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        snap = json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))
        self.assertNotIn("probe:orchard", snap["evidence_index"])


# ---------------------------------------------------------------------------
# AC #5 / #6 -- what the brief says when the data is old, and when it is broken
# ---------------------------------------------------------------------------


def snapshot_with_probe(**probe_over):
    """A hand-built snapshot carrying one project with a probe view on it."""
    view = {
        "declared": True, "url": MIA_URL, "label": "published posts", "ttl_days": 7,
        "never_sampled": False, "ok": True, "from_last_ok": False,
        "count": 3, "date": "2026-03-01", "sampled_at": "2026-03-15T08:00:00+00:00",
        "age_days": 1, "stale": False, "error_code": None, "error_detail": None,
        "attempted_at": "2026-03-15T08:00:00+00:00", "http_status": 200,
    }
    view.update(probe_over)
    index = {"orchard/PROJECT_STATUS.md": {"kinds": ["doc_declared", "file_mtime"],
                                           "value": "2026-03-10"},
             "orchard/README.md": {"kinds": ["file_mtime"], "value": None},
             "0000000": {"kinds": ["commit"], "value": "/example/orchard"}}
    if view.get("count") is not None or view.get("date"):
        index["probe:orchard"] = {"kinds": ["probe"], "value": view.get("date")}
    return make_snapshot(projects=[make_project_entry(probe=view)], evidence_index=index)


class BriefWording(TempCase):
    """The two sentences the author chose, asserted as sentences.

    These are user-visible strings and the whole point of the feature: a number
    that does not say how old it is, or a failure that renders as silence, is
    worth less than no probe at all.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(registry=registry_with_probe())

    def _render(self, snap):
        write_snapshot(self.ws, snap)
        code, _, err = capture(render.main, ["--workspace", str(self.ws), "--no-notify"])
        self.assertEqual(code, 0, err)
        return (self.ws / "BRIEF.md").read_text(encoding="utf-8")

    def test_a_fresh_reading_prints_the_number(self):
        text = self._render(snapshot_with_probe())
        self.assertIn("3 published posts", text)
        self.assertNotIn("probed", text, "a current reading needs no age warning")

    def test_a_stale_reading_keeps_the_number_and_says_how_old_it_is(self):
        text = self._render(snapshot_with_probe(age_days=12, stale=True))
        self.assertIn("3 published posts", text, "the number is still the best fact available")
        self.assertIn("probed 12d ago", text)

    def test_a_stale_reading_asks_to_be_re_sampled(self):
        text = self._render(snapshot_with_probe(age_days=12, stale=True))
        self.assertIn("nextbrief probe orchard", text)
        self.assertIn("12 days old", text)

    def test_a_never_sampled_probe_asks_for_a_first_reading(self):
        text = self._render(snapshot_with_probe(
            never_sampled=True, ok=False, count=None, date=None,
            sampled_at=None, age_days=None))
        self.assertIn("never been sampled", text)
        self.assertIn("nextbrief probe orchard", text)

    def test_a_failed_probe_says_probe_failed(self):
        text = self._render(snapshot_with_probe(
            ok=False, error_code="http_status", error_detail="HTTP 404",
            count=None, date=None, sampled_at=None, age_days=None))
        self.assertIn("probe failed", text.lower())
        self.assertIn("http_status", text)
        self.assertIn(MIA_URL, text)

    def test_a_failed_probe_is_never_reported_as_no_progress(self):
        # The whole reason this feature has a failure path at all. A broken
        # sensor reads 0, and 0 looks exactly like a quiet project.
        text = self._render(snapshot_with_probe(
            ok=False, error_code="not_json", error_detail="site returned HTML",
            count=None, date=None, sampled_at=None, age_days=None))
        self.assertIn("probe failed", text.lower())
        self.assertNotIn("0 published posts", text)

    def test_the_failure_reaches_BRIEF_html_too(self):
        # `nextbrief open` shows the HTML, and the HTML builds its own banners
        # rather than converting the Markdown. A warning that lands in one and
        # not the other has not been reported -- which is the mistake
        # `decision_notes` already made once, in the other direction.
        self._render(snapshot_with_probe(
            ok=False, error_code="http_status", error_detail="HTTP 404",
            count=None, date=None, sampled_at=None, age_days=None))
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertIn("probe failed", html.lower())
        self.assertIn("http_status", html)

    def test_the_re_sample_suggestion_reaches_BRIEF_html_too(self):
        self._render(snapshot_with_probe(age_days=12, stale=True))
        html = (self.ws / "BRIEF.html").read_text(encoding="utf-8")
        self.assertIn("nextbrief probe orchard", html)

    def test_a_failure_keeps_the_last_good_reading_and_labels_its_age(self):
        text = self._render(snapshot_with_probe(
            ok=False, from_last_ok=True, error_code="timeout",
            error_detail="no response in 10s", age_days=7))
        self.assertIn("3 published posts", text)
        self.assertIn("probe failed", text.lower())
        self.assertIn("7 day(s) ago", text)


# ---------------------------------------------------------------------------
# sampling and the cache
# ---------------------------------------------------------------------------


class Sampling(TempCase):

    def test_a_good_response_becomes_a_reading(self):
        with mock.patch.object(urllib.request, "build_opener", fake_opener(MIA_BODY)):
            entry = probe.sample(mia_probe(), declared={MIA_URL},
                                 now=dt.datetime(2026, 3, 16, 8, 0, tzinfo=dt.timezone.utc))
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["count"], 3)
        self.assertEqual(entry["date"], "2026-03-01")
        self.assertEqual(entry["sampled_at"], "2026-03-16T08:00:00+00:00")

    def test_the_wordpress_shape_works_too(self):
        spec = {"url": WP_URL, "count": "header:X-WP-Total", "date": "[].modified",
                "label": "posts", "ttl_days": 7}
        with mock.patch.object(urllib.request, "build_opener",
                               fake_opener(WP_BODY, headers={"X-WP-Total": "55"})):
            entry = probe.sample(spec, declared={WP_URL})
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["count"], 55)
        self.assertEqual(entry["date"], "2026-03-02")

    def test_html_where_json_was_expected_is_a_named_failure(self):
        with mock.patch.object(urllib.request, "build_opener",
                               fake_opener(b"<!doctype html><title>Not found</title>")):
            entry = probe.sample(mia_probe(), declared={MIA_URL})
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["error_code"], "not_json")
        self.assertIsNone(entry["count"], "a failed probe has no count, not a count of 0")

    def test_sample_never_raises(self):
        # The caller's job is to record what happened, not to decide whether the
        # command survives. Every outcome is a cache entry.
        class _Dead:
            addheaders = []

            def open(self, req, timeout=None):
                raise urllib.error.URLError("name resolution failed")

        with mock.patch.object(urllib.request, "build_opener", lambda *h: _Dead()):
            entry = probe.sample(mia_probe(), declared={MIA_URL})
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["error_code"], "network")

    def test_a_timeout_is_its_own_error_code(self):
        # Distinct from `network` on purpose: "the site is slow" and "the host
        # does not resolve" are different things to go and look at.
        class _Slow:
            addheaders = []

            def open(self, req, timeout=None):
                raise socket.timeout("timed out")

        with mock.patch.object(urllib.request, "build_opener", lambda *h: _Slow()):
            entry = probe.sample(mia_probe(), declared={MIA_URL})
        self.assertEqual(entry["error_code"], "timeout")

    def test_an_http_error_reports_its_status(self):
        # No cleanup on purpose. HTTPError is a response object, but only when it
        # is given a body: with `fp=None` it deliberately skips the addinfourl
        # init that would build the file wrapper, so there is nothing open to
        # close -- and calling `close()` anyway raises `KeyError: 'file'` on 3.9,
        # which is the interpreter the nightly job runs on.
        err = urllib.error.HTTPError(MIA_URL, 404, "Not Found", {}, None)

        class _NotFound:
            addheaders = []

            def open(self, req, timeout=None):
                raise err

        with mock.patch.object(urllib.request, "build_opener", lambda *h: _NotFound()):
            entry = probe.sample(mia_probe(), declared={MIA_URL})
        self.assertEqual(entry["error_code"], "http_status")
        self.assertIn("404", entry["error_detail"])

    def test_a_failure_carries_the_previous_good_reading_forward(self):
        cache = {"schema": 1, "probes": {"orchard": cache_entry()}}
        with mock.patch.object(urllib.request, "build_opener",
                               fake_opener(b"<html>oops</html>")):
            new, _ = probe.run_probes({"orchard": mia_probe()}, cache)
        entry = new["probes"]["orchard"]
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["last_ok"]["count"], 3)

    def test_a_repointed_url_drops_the_old_history(self):
        # Showing the previous site's numbers under a new URL would be a lie with
        # a timestamp on it.
        cache = {"schema": 1, "probes": {"orchard": cache_entry()}}
        moved = mia_probe(url="https://mia.example.invalid/blog/v2/knowledge.json")
        with mock.patch.object(urllib.request, "build_opener",
                               fake_opener(b"<html>oops</html>")):
            new, _ = probe.run_probes({"orchard": moved}, cache)
        self.assertIsNone(new["probes"]["orchard"]["last_ok"])

    def test_a_corrupt_cache_degrades_to_empty(self):
        path = Path(self.tmp) / "probes.json"
        path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(probe.load_cache(path), {"schema": 1, "probes": {}})


class ReadingView(unittest.TestCase):
    """`reading_for` collapses cache + spec into the four states the brief needs."""

    def test_stale_is_decided_against_the_ttl(self):
        view = probe.reading_for(mia_probe(ttl_days=7), cache_entry(), AS_OF_DATE)
        self.assertFalse(view["stale"])
        old = cache_entry(sampled_at="2026-03-01T08:00:00+00:00",
                          last_ok={"sampled_at": "2026-03-01T08:00:00+00:00",
                                   "count": 3, "date": "2026-03-01"})
        view = probe.reading_for(mia_probe(ttl_days=7), old, AS_OF_DATE)
        self.assertTrue(view["stale"])
        self.assertEqual(view["age_days"], 15)

    def test_a_failure_with_nothing_behind_it_reports_no_numbers(self):
        failed = cache_entry(ok=False, count=None, date=None, last_ok=None,
                             error_code="timeout", error_detail="no response in 10s")
        view = probe.reading_for(mia_probe(), failed, AS_OF_DATE)
        self.assertIsNone(view["count"])
        self.assertEqual(view["error_code"], "timeout")
        self.assertFalse(view["from_last_ok"])

    def test_a_future_stamp_does_not_produce_a_negative_age(self):
        ahead = cache_entry(sampled_at="2026-04-01T08:00:00+00:00",
                            last_ok={"sampled_at": "2026-04-01T08:00:00+00:00",
                                     "count": 3, "date": "2026-03-01"})
        view = probe.reading_for(mia_probe(), ahead, AS_OF_DATE)
        self.assertEqual(view["age_days"], 0)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


class Command(TempCase):

    def setUp(self):
        super().setUp()
        self.ws = self.workspace(registry=registry_with_probe())

    def _probe(self, *args):
        return capture(cli.main, ["--workspace", str(self.ws), "probe"] + list(args))

    def test_it_writes_the_cache_sense_reads(self):
        with mock.patch.object(urllib.request, "build_opener", fake_opener(MIA_BODY)):
            code, out, err = self._probe()
        self.assertEqual(code, 0, err)
        cache = json.loads((self.ws / "state" / "probes.json").read_text(encoding="utf-8"))
        self.assertEqual(cache["probes"]["orchard"]["count"], 3)
        self.assertIn(MIA_URL, out, "the URLs being contacted are named on screen")

    def test_it_names_every_url_before_contacting_it(self):
        # This command is the only outbound traffic the tool produces. Where it
        # went belongs on screen, in front of the person who typed it.
        with mock.patch.object(urllib.request, "build_opener", fake_opener(MIA_BODY)):
            _, out, _ = self._probe()
        self.assertIn("→ orchard", out)

    def test_a_failed_probe_is_reported_but_exits_zero(self):
        # "That site is down" is a fact about the world, not a broken command --
        # a non-zero exit would stop a `&&` chain over somebody else's outage.
        with mock.patch.object(urllib.request, "build_opener",
                               fake_opener(b"<html>nope</html>")):
            code, out, _ = self._probe()
        self.assertEqual(code, 0)
        self.assertIn("not_json", out)

    def test_naming_a_project_without_a_probe_is_a_usage_error(self):
        code, _, err = self._probe("kiln")
        self.assertEqual(code, 2)
        self.assertIn("kiln", err)

    def test_a_workspace_with_no_probes_says_so_and_does_nothing(self):
        ws = self.workspace(name="bare")
        code, out, _ = capture(cli.main, ["--workspace", str(ws), "probe"])
        self.assertEqual(code, 0)
        self.assertIn("evidence_probe", out)
        self.assertFalse((ws / "state" / "probes.json").exists())

    def test_it_only_requests_declared_urls(self):
        seen = []
        with mock.patch.object(urllib.request, "build_opener",
                               fake_opener(MIA_BODY, capture_req=seen)):
            self._probe()
        self.assertEqual([r.full_url for r in seen], [MIA_URL])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
