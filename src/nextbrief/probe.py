"""Declarative read-only probes: evidence for work that never lands on disk.

The engine's three original senses -- commits, file timestamps, agent sessions --
all answer the same question: *what happened in this filesystem?* That is the
wrong question for a growing share of real work. A blog whose posts live in a
SQLite file behind an admin editor, a WordPress site holding a finished
migration, a deck on Canva: the project is moving and the repository is silent.
Read only the filesystem and you get more blind as your user gets more modern.

A probe closes exactly that gap and nothing wider. It fetches one URL the
registry names, pulls **two numbers** out of the response -- a count and a date
-- and stops. Those are the same two numbers a commit already supplies, so the
result plugs into machinery that exists rather than starting a subsystem.

Four boundaries, all of them load-bearing:

**`sense` never calls this module's network path.** Fetching happens only in the
explicit ``nextbrief probe`` command. A sensor that reaches the internet
unattended every night at 21:30 converts three of somebody else's problems into
yours: a network blip becomes a failed brief, a site redesign becomes local
noise, and a daily outbound request becomes a thing you have to explain. Reading
only your own disk is one of the most valuable properties this engine has, and a
number is not worth trading it for. So the probe writes ``state/probes.json`` and
``sense`` reads that file like any other file.

**The reading is therefore born old, and must say so.** A probe value with no
sampling time attached is indistinguishable from hand-written prose, which is
precisely the failure mode the engine exists to prevent. Every cached reading
carries ``sampled_at``; everything downstream carries its age.

**A failed probe is never a zero.** A broken sensor reads 0, and 0 looks exactly
like "nothing happened" -- the two most costly words this tool could get wrong.
Failures are stored as failures, with a code, and are rendered as failures.

**Read-only, credential-free, declared URLs only.** GET, no auth header, no
cookies, https only, no userinfo in the URL, no cross-origin redirects, and the
URL must be one the registry declared. Anything needing a login is out of scope
by construction -- that is a human's job to report, not a probe's.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "ProbeError", "parse_probe", "resolve_selector", "fetch", "sample",
    "run_probes", "load_cache", "reading_for", "CACHE_SCHEMA",
    "DEFAULT_TTL_DAYS", "DEFAULT_TIMEOUT", "MAX_BYTES", "MAX_REDIRECTS",
]

CACHE_SCHEMA = 1

DEFAULT_TTL_DAYS = 7
DEFAULT_TIMEOUT = 10.0
# Generous for two numbers, small enough that a misconfigured URL pointing at a
# video cannot fill the disk. The two production probes are 6KB and 4KB.
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3

# https only. Not a purity argument: a plain-http probe is a URL somebody on the
# path can rewrite, and this reading is allowed to influence what the brief says
# about whether a project is alive.
ALLOWED_SCHEMES = ("https",)

USER_AGENT = "nextbrief-probe (+https://github.com/hancheng-ai/nextbrief)"


class ProbeError(Exception):
    """A probe that could not produce a reading. ``code`` is a stable vocabulary
    the renderer switches on; ``detail`` is for the human reading the brief."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__("%s: %s" % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# the registry field
# ---------------------------------------------------------------------------

def parse_probe(pr: dict, problems: Optional[List[dict]] = None) -> Optional[dict]:
    """Normalise one project's ``evidence_probe`` block, or return None.

    Every rejection is recorded rather than raised. A malformed probe on one
    project must not cost you the other eleven, and a silently ignored one is a
    sensor you believe you have.
    """
    def fail(why: str):
        if problems is not None:
            problems.append({"path": str(pr.get("id") or "?"),
                             "code": "bad_evidence_probe", "why": why})
        return None

    spec = pr.get("evidence_probe")
    if not spec:
        return None
    if not isinstance(spec, dict):
        return fail("evidence_probe must be an object, got %s" % type(spec).__name__)

    url = spec.get("url")
    if not url or not isinstance(url, str):
        return fail("evidence_probe.url is required and must be a string")
    try:
        check_url(url)
    except ProbeError as exc:
        return fail(str(exc))

    count_sel = spec.get("count")
    date_sel = spec.get("date")
    if not count_sel and not date_sel:
        return fail("evidence_probe needs at least one of `count` or `date`")
    for name, sel in (("count", count_sel), ("date", date_sel)):
        if sel is not None and not isinstance(sel, str):
            return fail("evidence_probe.%s must be a string selector" % name)

    ttl = spec.get("ttl_days", DEFAULT_TTL_DAYS)
    try:
        ttl = int(ttl)
        if ttl < 0:
            raise ValueError
    except (TypeError, ValueError):
        return fail("evidence_probe.ttl_days must be a non-negative integer")

    return {
        "id": str(pr.get("id") or ""),
        "url": url,
        "count": count_sel or None,
        "date": date_sel or None,
        "label": str(spec.get("label") or "").strip() or None,
        "ttl_days": ttl,
    }


def probes_of(reg: dict, problems: Optional[List[dict]] = None) -> Dict[str, dict]:
    """Every declared probe in the registry, keyed by project id.

    This is also the allowlist. Nothing else in this module accepts a URL that
    did not come through here.
    """
    out: Dict[str, dict] = {}
    for pr in reg.get("projects", []) or []:
        spec = parse_probe(pr, problems)
        if spec and spec["id"]:
            out[spec["id"]] = spec
    return out


# ---------------------------------------------------------------------------
# the selector language
# ---------------------------------------------------------------------------
#
# Deliberately tiny and non-executable. The registry is a hand-edited file and
# the response is somebody else's JSON; a selector language with any evaluation
# in it would turn "a site changed its output" into "a site runs code here".
#
#   header:X-WP-Total     a response header
#   posts[]               the array at `posts`      -> as a count, its length
#   posts[].published_at  that field of each element -> as a date, the newest
#   [].modified           same, for a top-level array
#   total                 a plain scalar
#
# Any shape the selector does not find is `selector_no_match`, which is how a
# site redesign reports itself: as a named failure, never as a zero.

_HEADER_PREFIX = "header:"
_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def resolve_selector(selector: str, payload: Any, headers: Dict[str, str]) -> Any:
    """Resolve one selector against a parsed body and the response headers.

    Returns a scalar, or a list of scalars when the selector iterated an array.
    """
    sel = (selector or "").strip()
    if not sel:
        raise ProbeError("selector_no_match", "empty selector")

    if sel.lower().startswith(_HEADER_PREFIX):
        name = sel[len(_HEADER_PREFIX):].strip()
        # Header names are case-insensitive; the registry should not have to
        # guess whether a server spells it X-WP-Total or x-wp-total.
        lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
        if name.lower() not in lowered:
            raise ProbeError("selector_no_match", "no response header %r" % name)
        return lowered[name.lower()]

    node: Any = payload
    multi = False
    for token in sel.split("."):
        token = token.strip()
        if not token:
            raise ProbeError("selector_no_match", "empty path segment in %r" % sel)
        iterate = token.endswith("[]")
        key = token[:-2] if iterate else token

        if key:
            if multi:
                collected = []
                for el in node:
                    if not isinstance(el, dict) or key not in el:
                        raise ProbeError("selector_no_match",
                                         "%r missing on an element of %r" % (key, sel))
                    collected.append(el[key])
                node = collected
            else:
                if not isinstance(node, dict) or key not in node:
                    raise ProbeError("selector_no_match",
                                     "%r not found (selector %r)" % (key, sel))
                node = node[key]

        if iterate:
            if multi:
                raise ProbeError("selector_type", "nested [] is not supported: %r" % sel)
            if not isinstance(node, list):
                raise ProbeError("selector_type",
                                 "%r is %s, not an array" % (token, type(node).__name__))
            multi = True

    return node


def _to_count(value: Any, selector: str) -> int:
    """A count is an integer. An array yields its length; anything numeric-looking
    yields itself; everything else is a failure with a name."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, bool):
        raise ProbeError("bad_count", "%r resolved to a boolean" % selector)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise ProbeError("bad_count", "%r resolved to %r, which is not a count"
                     % (selector, _clip(value)))


def _to_date(value: Any, selector: str) -> str:
    """A date is the newest ISO date among the resolved values.

    The *newest* rather than the first: a probe reports when this project last
    produced something, and the URL controls the ordering only when its API
    happens to offer one. Taking the max makes the reading independent of that.
    """
    values = value if isinstance(value, list) else [value]
    if not values:
        raise ProbeError("selector_no_match", "%r resolved to an empty array" % selector)
    dates = []
    for v in values:
        if not isinstance(v, str):
            raise ProbeError("bad_date", "%r resolved to %r, which is not a date string"
                             % (selector, _clip(v)))
        m = _ISO_DATE.match(v.strip())
        if not m:
            raise ProbeError("bad_date", "%r is not an ISO date (selector %r)"
                             % (_clip(v), selector))
        try:
            dates.append(dt.date.fromisoformat(m.group(1)))
        except ValueError:
            raise ProbeError("bad_date", "%r is not a real date (selector %r)"
                             % (m.group(1), selector)) from None
    return max(dates).isoformat()


def _clip(value, n: int = 60) -> str:
    s = repr(value)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# the network boundary
# ---------------------------------------------------------------------------

def _origin(url: str) -> Tuple[str, str, Optional[int]]:
    u = urllib.parse.urlsplit(url)
    return (u.scheme.lower(), (u.hostname or "").lower(), u.port)


def check_url(url: str) -> None:
    """Everything this module refuses to fetch, in one place.

    Called both when the registry is parsed -- so a bad URL is a config error you
    see before any request -- and again immediately before the request, so no
    later code path can smuggle one in.
    """
    if not isinstance(url, str) or not url.strip():
        raise ProbeError("policy", "url is empty")
    u = urllib.parse.urlsplit(url)
    if u.scheme.lower() not in ALLOWED_SCHEMES:
        raise ProbeError("policy", "only %s is fetched, got %r"
                         % ("/".join(ALLOWED_SCHEMES), u.scheme or "no scheme"))
    if not u.hostname:
        raise ProbeError("policy", "url has no host")
    # user:pass@host is a credential, and this module does not carry credentials.
    if u.username or u.password:
        raise ProbeError("policy", "url carries credentials in its userinfo")


class _StrictRedirect(urllib.request.HTTPRedirectHandler):
    """Follow same-origin redirects; refuse to leave the declared origin.

    A probe that follows a redirect off-site is a crawler with extra steps, and
    the URL the registry approved is no longer the URL being read.
    """

    max_redirections = MAX_REDIRECTS

    def __init__(self, origin):
        self._origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != self._origin:
            raise ProbeError("redirect_offsite",
                             "refused redirect to %s" % urllib.parse.urlsplit(newurl).netloc)
        check_url(newurl)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def fetch(url: str, timeout: float = DEFAULT_TIMEOUT,
          declared: Optional[set] = None) -> Tuple[int, Dict[str, str], bytes]:
    """One GET. No credentials, no cookies, no off-origin redirect, size-capped.

    ``declared`` is the set of URLs the registry named. Passing it makes "only
    URLs you declared" a check rather than a convention -- the difference between
    a promise and a test.
    """
    if declared is not None and url not in declared:
        raise ProbeError("policy", "url is not declared in the registry")
    check_url(url)

    # A bare opener: no cookie processor, no auth handler, no proxy-auth. The
    # default global opener is avoided because anything in this process could
    # have installed handlers into it.
    opener = urllib.request.build_opener(_StrictRedirect(_origin(url)))
    opener.addheaders = []
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*;q=0.5"},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            headers = dict(resp.headers.items())
            # +1 so a body sitting exactly on the cap is still detected as over.
            body = resp.read(MAX_BYTES + 1)
    except ProbeError:
        raise
    except urllib.error.HTTPError as exc:
        # An error response still has headers and a body, but it is not a
        # reading. Reported by status so "the route moved" and "the site is
        # down" do not arrive as the same sentence.
        raise ProbeError("http_status", "HTTP %s" % exc.code) from None
    except socket.timeout:
        raise ProbeError("timeout", "no response in %gs" % timeout) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout):
            raise ProbeError("timeout", "no response in %gs" % timeout) from None
        raise ProbeError("network", str(reason)[:200]) from None
    except OSError as exc:
        raise ProbeError("network", str(exc)[:200]) from None

    if len(body) > MAX_BYTES:
        raise ProbeError("too_large", "response exceeds %d bytes" % MAX_BYTES)
    return status, headers, body


def sample(spec: dict, timeout: float = DEFAULT_TIMEOUT,
           declared: Optional[set] = None, now: Optional[dt.datetime] = None) -> dict:
    """Fetch one probe and reduce it to {count, date}. Never raises.

    The return value is a cache entry either way: ``ok`` true with numbers, or
    ``ok`` false with a code. There is no third outcome and no exception path,
    because the caller's job is to record what happened, not to decide whether
    the run continues.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    entry: Dict[str, Any] = {
        "id": spec.get("id"),
        "url": spec.get("url"),
        "label": spec.get("label"),
        "ttl_days": spec.get("ttl_days", DEFAULT_TTL_DAYS),
        "sampled_at": _stamp(now),
        "ok": False,
        "count": None,
        "date": None,
        "http_status": None,
        "error_code": None,
        "error_detail": None,
    }
    try:
        status, headers, body = fetch(spec["url"], timeout=timeout, declared=declared)
        entry["http_status"] = status
        try:
            payload = json.loads(body.decode("utf-8", "replace")) if body.strip() else None
        except ValueError as exc:
            # The single most likely shape of "the site changed": a route that
            # used to serve JSON now serves an HTML page, with a 200 attached.
            raise ProbeError("not_json", str(exc)[:160]) from None
        if spec.get("count"):
            entry["count"] = _to_count(
                resolve_selector(spec["count"], payload, headers), spec["count"])
        if spec.get("date"):
            entry["date"] = _to_date(
                resolve_selector(spec["date"], payload, headers), spec["date"])
        entry["ok"] = True
    except ProbeError as exc:
        entry["error_code"] = exc.code
        entry["error_detail"] = exc.detail or str(exc)
    return entry


def _stamp(when: dt.datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# the cache -- the only thing `sense` ever sees
# ---------------------------------------------------------------------------

def load_cache(path) -> Dict[str, Any]:
    """Read ``state/probes.json``. A missing or corrupt cache is an empty one.

    Fail-open on purpose: the probe cache is an optional extra sense, and a bad
    one must degrade to "no probe readings", never to a failed brief.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": CACHE_SCHEMA, "probes": {}}
    if not isinstance(data, dict):
        return {"schema": CACHE_SCHEMA, "probes": {}}
    probes = data.get("probes")
    if not isinstance(probes, dict):
        probes = {}
    return {"schema": data.get("schema", CACHE_SCHEMA),
            "probes": {k: v for k, v in probes.items() if isinstance(v, dict)}}


def run_probes(specs: Dict[str, dict], cache: Dict[str, Any],
               timeout: float = DEFAULT_TIMEOUT,
               now: Optional[dt.datetime] = None) -> Tuple[Dict[str, Any], List[dict]]:
    """Sample every spec given and fold the results into ``cache``.

    Returns the new cache and the list of fresh entries, in the order sampled.
    The last *successful* reading is carried forward across a failure, so a brief
    can show an aged number next to an explicit failure line rather than losing
    a fact because a request timed out once.
    """
    declared = {s["url"] for s in specs.values()}
    probes = dict(cache.get("probes") or {})
    results = []
    for pid in sorted(specs):
        spec = specs[pid]
        entry = sample(spec, timeout=timeout, declared=declared, now=now)
        prev = probes.get(pid) or {}
        if entry["ok"]:
            entry["last_ok"] = {"sampled_at": entry["sampled_at"],
                                "count": entry["count"], "date": entry["date"]}
        else:
            # Only carried forward when it describes the same URL. A registry
            # that was repointed at a different endpoint has no history here;
            # showing the old site's numbers under the new URL would be a lie
            # with a timestamp on it.
            carried = prev.get("last_ok") if prev.get("url") == entry["url"] else None
            entry["last_ok"] = carried
        probes[pid] = entry
        results.append(entry)
    return {"schema": CACHE_SCHEMA, "probes": probes}, results


def reading_for(spec: Optional[dict], cached: Optional[dict], as_of: dt.date) -> Optional[dict]:
    """The view `sense` puts in the snapshot: one project's probe state.

    Four states, and the brief says something different for each, because
    collapsing any two of them is how a broken sensor starts reading as news:

    ``never_sampled``  declared, but ``nextbrief probe`` has not run yet
    ``ok``             a reading; ``stale`` says whether it is past its TTL
    ``failed`` + aged  the last attempt failed, an older reading survives
    ``failed`` alone   the last attempt failed and there is nothing to show
    """
    if not spec:
        return None
    ttl = int(spec.get("ttl_days", DEFAULT_TTL_DAYS))
    view: Dict[str, Any] = {
        "declared": True,
        "url": spec["url"],
        "label": spec.get("label"),
        "ttl_days": ttl,
        "never_sampled": False,
        "ok": False,
        "from_last_ok": False,
        "count": None,
        "date": None,
        "sampled_at": None,
        "age_days": None,
        "stale": False,
        "error_code": None,
        "error_detail": None,
    }
    if not cached or cached.get("url") != spec["url"]:
        # A repointed URL is treated as never sampled rather than as a failure:
        # nothing is broken, the question simply changed.
        view["never_sampled"] = True
        return view

    view["error_code"] = cached.get("error_code")
    view["error_detail"] = cached.get("error_detail")
    view["http_status"] = cached.get("http_status")
    view["attempted_at"] = cached.get("sampled_at")

    if cached.get("ok"):
        source = cached
        view["ok"] = True
    else:
        source = cached.get("last_ok") or {}
        view["from_last_ok"] = bool(source)
        if not source:
            view["age_days"] = _age_days(cached.get("sampled_at"), as_of)
            return view

    view["count"] = source.get("count")
    view["date"] = source.get("date")
    view["sampled_at"] = source.get("sampled_at")
    view["age_days"] = _age_days(view["sampled_at"], as_of)
    if view["age_days"] is not None:
        view["stale"] = view["age_days"] > ttl
    return view


def _age_days(sampled_at, as_of: dt.date) -> Optional[int]:
    if not sampled_at or not isinstance(sampled_at, str):
        return None
    m = _ISO_DATE.match(sampled_at)
    if not m:
        return None
    try:
        sampled = dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    # Clamped at zero: a sample stamped in the future is a clock difference, and
    # "sampled -2 days ago" reads as a defect in the brief rather than in a clock.
    return max(0, (as_of - sampled).days)
