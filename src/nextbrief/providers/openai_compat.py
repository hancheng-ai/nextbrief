"""Any OpenAI-compatible chat-completions endpoint, over ``urllib`` only.

This is the escape hatch: a hosted API, a gateway, a self-hosted server -- if it
speaks ``/chat/completions`` it works here, with no dependency added to a package
that has none.

**The key is never in the workspace.** Config names an environment variable; the
value is read from the environment at call time. A workspace is a directory of
Markdown and JSONC that people put under version control and hand to agents, and
a secret that lives there will eventually be committed, synced, or read aloud by
some other tool. If the variable is unset we fail with the variable's name, which
is the one piece of information that makes it fixable.

Not an agent: no tools, so the caller inlines the digest into the prompt and
persists the reply.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from . import ProviderResult, provider_options, tail, timeout_of, unfence

NAME = "openai_compat"
AGENTIC = False

DEFAULT_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_PATH = "/chat/completions"
# The brief is a structured summary of fixed facts. Sampling variance here shows
# up as the same situation being described differently on consecutive days, which
# is exactly the drift the deterministic stages were built to avoid.
DEFAULT_TEMPERATURE = 0.2

# Probed without config so `available()` stays argument-free. The authoritative
# variable name lives in config, so this is a hint, not a verdict.
_COMMON_KEY_ENVS = ("NEXTBRIEF_API_KEY", "OPENAI_API_KEY")


def _key_env(opts: Dict[str, Any]) -> str:
    return str(opts.get("api_key_env") or DEFAULT_KEY_ENV)


def probe(cfg: Any = None) -> bool:
    opts = provider_options(cfg, NAME)
    if not opts.get("base_url"):
        return False
    names = [_key_env(opts)] if opts.get("api_key_env") else list(_COMMON_KEY_ENVS)
    return any(os.environ.get(n) for n in names)


def _endpoint(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith(path.rstrip("/")):
        return base  # the config already pointed at the full endpoint
    return base + path


def run(cfg: Any, prompt: str, ws) -> ProviderResult:
    # Imported here, not at module scope: urllib.request is a heavyweight import
    # and most runs never reach this provider. The package is loaded on every
    # invocation of a program that runs on a timer.
    import urllib.error
    import urllib.request

    opts = provider_options(cfg, NAME)
    base_url = str(opts.get("base_url") or "").strip()
    if not base_url:
        return ProviderResult(
            False, "", "no endpoint configured; set model.openai_compat.base_url"
        )
    model = str(opts.get("model") or "").strip()
    if not model:
        return ProviderResult(False, "", "no model configured; set model.openai_compat.model")

    key_env = _key_env(opts)
    api_key = os.environ.get(key_env, "")
    if not api_key:
        return ProviderResult(
            False,
            "",
            "$%s is not set; export the key in the environment that runs nextbrief "
            "(it is deliberately never read from the workspace)" % key_env,
        )

    body: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(opts.get("temperature", DEFAULT_TEMPERATURE)),
        "stream": False,
    }
    if opts.get("max_tokens"):
        body["max_tokens"] = int(opts["max_tokens"])
    # Off by default: strict endpoints reject fields they do not know, and a
    # request refused for an unrecognised key is a maddening thing to debug.
    if opts.get("json_mode"):
        body["response_format"] = {"type": "json_object"}
    extra_body = opts.get("extra_body")
    if isinstance(extra_body, dict):
        body.update(extra_body)

    url = _endpoint(base_url, str(opts.get("path") or DEFAULT_PATH))
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % api_key,
            "User-Agent": "nextbrief",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_of(opts)) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # The body carries the actual reason (bad model name, no credit, wrong
        # key); the status alone almost never does.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return ProviderResult(False, "", "HTTP %s from %s: %s" % (exc.code, url, tail(detail, 300)))
    except urllib.error.URLError as exc:
        return ProviderResult(False, "", "cannot reach %s: %s" % (url, exc.reason))
    except (OSError, ValueError) as exc:
        return ProviderResult(False, "", "request to %s failed: %s" % (url, exc))

    try:
        payload = json.loads(raw)
        choice = payload["choices"][0]
        text = choice.get("message", {}).get("content") or choice.get("text") or ""
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return ProviderResult(False, "", "unexpected response shape from %s: %s" % (url, tail(raw, 300)))

    if not str(text).strip():
        # An empty completion is a failure even at HTTP 200: usually a length cap
        # or a refusal, and silently returning "" would render an empty brief.
        return ProviderResult(False, "", "endpoint returned an empty completion")

    # Token usage is reported but not priced: pricing is per-vendor, changes
    # without notice, and a stale table shipped in a package is worse than no
    # number at all. Callers that care can read usage from their own dashboard.
    return ProviderResult(True, unfence(str(text)), "", 1)
