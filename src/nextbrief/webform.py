"""The review, as a page in the browser you already have open.

`BRIEF.html` is already rendered and already opened with `webbrowser`, so the
browser is not a new dependency here -- and a form is the one input surface where
a date picker, four sets of radio buttons and a dozen projects all fit at once.

What it does need is somewhere to POST to. A page loaded over ``file://`` cannot
write to disk, so this serves the form from a loopback socket for exactly as long
as it takes to answer it: bind 127.0.0.1 on a port the OS picks, open the
browser, wait for one submission, shut down. Everything below is standard
library, like the rest of the package.

Three things make that socket boring rather than a hazard:

* it is bound to the loopback interface, so nothing off the machine can reach it
* the path carries a one-shot token generated per run, so another page open in
  the same browser cannot post to it by guessing the URL
* it serves one request and stops, so there is nothing left listening afterwards

It is opt-in (``review --web``). The default remains the editor form, which needs
no socket at all.
"""

from __future__ import annotations

import html
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Sequence

from .annotate import QUESTIONS, current_answer

__all__ = ["collect"]

# Long enough that guessing is not a strategy, short enough to sit in a URL.
TOKEN_BYTES = 16

# A person filling in a dozen projects can reasonably take a while; a person who
# has wandered off should not leave a socket open all afternoon.
TIMEOUT_SECONDS = 15 * 60

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>%(title)s</title>
<style>
 :root { color-scheme: light dark; }
 body { font: 15px/1.5 system-ui, -apple-system, sans-serif; margin: 0 auto;
        max-width: 46rem; padding: 2rem 1rem 6rem; }
 h1 { font-size: 1.3rem; margin-bottom: .2rem; }
 p.lede { color: #666; margin-top: 0; }
 section { border-top: 1px solid #8883; padding: 1.1rem 0; }
 h2 { font-size: 1rem; margin: 0 0 .1rem; }
 .id { color: #888; font-weight: normal; font-size: .85em; }
 fieldset { border: 0; margin: .7rem 0 0; padding: 0; }
 legend { font-size: .82rem; color: #666; padding: 0; margin-bottom: .25rem; }
 label { display: block; padding: .1rem 0; }
 input[type=date] { font: inherit; padding: .2rem; }
 .bar { position: fixed; bottom: 0; left: 0; right: 0; padding: .8rem;
        background: Canvas; border-top: 1px solid #8883; text-align: center; }
 button { font: inherit; padding: .5rem 1.4rem; }
</style></head><body>
<h1>%(title)s</h1>
<p class="lede">%(lede)s</p>
<form method="post" action="/%(token)s">
%(body)s
<div class="bar"><button type="submit">%(save)s</button></div>
</form></body></html>
"""


def _field_name(pid: str, field: str) -> str:
    return "%s::%s" % (pid, field)


def _render(projects: Sequence[Dict[str, Any]], cat, token: str) -> str:
    def t(key, fallback=""):
        try:
            return cat.t(key) if cat is not None else fallback or key
        except Exception:
            return fallback or key

    blocks: List[str] = []
    for proj in projects:
        pid = str(proj.get("id"))
        name = proj.get("name") or pid
        parts = ['<section><h2>%s <span class="id">%s</span></h2>'
                 % (html.escape(str(name)), html.escape(pid))]
        for q in QUESTIONS:
            have = current_answer(proj, q)
            parts.append('<fieldset><legend>%s</legend>' % html.escape(t(q.key)))
            if q.kind == "date":
                parts.append(
                    '<input type="date" name="%s" value="%s">'
                    % (html.escape(_field_name(pid, q.field)),
                       html.escape(str(have or ""))))
            else:
                for value, key in q.choices:
                    checked = " checked" if str(have) == str(value) else ""
                    parts.append(
                        '<label><input type="radio" name="%s" value="%s"%s> %s</label>'
                        % (html.escape(_field_name(pid, q.field)),
                           html.escape(str(value)), checked, html.escape(t(key))))
            parts.append("</fieldset>")
        parts.append("</section>")
        blocks.append("".join(parts))

    return PAGE % {
        "title": t("review.web.title", "nextbrief review"),
        "lede": html.escape(t("review.web.lede",
                              "Answer what you can. Anything left blank is asked again.")),
        "save": html.escape(t("review.web.save", "Save")),
        "token": html.escape(token),
        "body": "\n".join(blocks),
    }


def _parse(body: str, known) -> Dict[str, Dict[str, str]]:
    """POSTed fields back into {project: {field: raw value}}.

    Raw, deliberately: coercion and validation belong to the same code the editor
    form uses, so that two input paths cannot come to different conclusions about
    what a valid answer is.
    """
    out: Dict[str, Dict[str, str]] = {}
    for key, values in urllib.parse.parse_qs(body, keep_blank_values=False).items():
        pid, sep, field = key.partition("::")
        if not sep or pid not in known or not values:
            continue
        value = values[0].strip()
        if value:
            out.setdefault(pid, {})[field] = value
    return out


def collect(projects: Sequence[Dict[str, Any]], cat=None,
            open_browser: bool = True) -> Optional[Dict[str, Dict[str, str]]]:
    """Serve the form once and return what came back, or None.

    None means nothing was submitted -- the reader closed the tab, interrupted,
    or the wait ran out. That is not an error and does not get treated as one:
    the answers simply were not given, which is the state everything else here
    already knows how to handle.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    page = _render(projects, cat, token)
    known = {str(p.get("id")) for p in projects}
    result: Dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, text: str, ctype: str = "text/html; charset=utf-8"):
            payload = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            # This page is generated for one reader and posts secrets-adjacent
            # judgements; nothing about it should sit in a cache.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802 -- name fixed by BaseHTTPRequestHandler
            if self.path.strip("/") != token:
                self._send(404, "not found", "text/plain; charset=utf-8")
                return
            self._send(200, page)

        def do_POST(self):  # noqa: N802
            if self.path.strip("/") != token:
                self._send(404, "not found", "text/plain; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            result["answers"] = _parse(body, known)
            done = (cat.t("review.web.done") if cat is not None
                    else "Recorded. You can close this tab.")
            self._send(200, "<!doctype html><meta charset=utf-8>"
                            "<p style='font:16px system-ui;padding:2rem'>%s</p>"
                       % html.escape(done))
            # Shut down from another thread: serve_forever cannot stop itself
            # from inside a handler without deadlocking on its own loop.
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *_args):
            """Silence. The access log would be the only thing this command
            prints, and it says nothing the reader wants."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    url = "http://127.0.0.1:%d/%s" % (server.server_port, token)
    server.timeout = TIMEOUT_SECONDS

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if open_browser:
        webbrowser.open(url)
    try:
        thread.join(TIMEOUT_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return result.get("answers")


def form_url_for_test(projects, cat=None) -> str:
    """The rendered page, for tests that should not open a socket."""
    return _render(projects, cat, "test-token")
