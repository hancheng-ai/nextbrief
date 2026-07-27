"""Claude Code as the stage-2 runner.

This is the invocation the tool was built around, now parameterised. Two of its
choices are load-bearing and stay as defaults:

**effort: low.** Stage 2 is structured summarisation over facts that stage 1 has
already computed -- not problem solving. Measured over one identical digest:
reading each backlog file separately took 36 rounds and $4.37 a run; reading the
single digest took 9 rounds and $1.09; the same digest at low effort took 7
rounds and $0.74. Most output tokens are reasoning, and reasoning buys almost
nothing here. Raise it if you like, but you are paying about triple for the
privilege.

**cwd is the workspace.** The runner is started inside the workspace so that the
workspace's own permission fence (its agent settings file) is the one in effect.
Directory grants are additive on top of that, never a substitute for it: the
model gets read access to the portfolio the registry declares, and write access
only where the fence allows.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import (
    ProviderResult,
    extra_args_of,
    granted_dirs,
    provider_options,
    resolve_binary,
    run_cli,
    tail,
    timeout_of,
)

NAME = "claude"
AGENTIC = True

DEFAULT_BIN = "claude"
DEFAULT_MODEL = "sonnet"
DEFAULT_EFFORT = "low"
DEFAULT_PERMISSION_MODE = "acceptEdits"
# Stage 2 reads the digest and writes the brief and backlog notes. Nothing here
# needs a shell or the network, and not granting them is the cheapest possible
# guarantee that a prompt-injected line in some project file cannot act.
DEFAULT_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write"]


def probe(cfg: Any = None) -> bool:
    opts = provider_options(cfg, NAME)
    return resolve_binary(str(opts.get("bin") or DEFAULT_BIN)) is not None


def _parse_json_envelope(stdout: str):
    """``(text, rounds, cost)`` from ``--output-format json``, or ``None``.

    Only used when the workspace opts into JSON output; the default stays text
    so the runner's own console rendering is what you see when debugging.
    """
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    text = payload.get("result") or payload.get("text") or ""
    rounds = payload.get("num_turns") or payload.get("rounds") or 0
    cost = payload.get("total_cost_usd") or payload.get("cost_usd") or 0.0
    try:
        return str(text), int(rounds), float(cost)
    except (TypeError, ValueError):
        return str(text), 0, 0.0


def run(cfg: Any, prompt: str, ws) -> ProviderResult:
    opts = provider_options(cfg, NAME)
    exe: Optional[str] = resolve_binary(str(opts.get("bin") or DEFAULT_BIN))
    if exe is None:
        return ProviderResult(
            False,
            "",
            "claude not found on PATH; set model.claude.bin to its full path, "
            "or set model.provider to another runner",
        )

    model = str(opts.get("model") or DEFAULT_MODEL)
    effort = opts.get("effort", DEFAULT_EFFORT)
    permission_mode = str(opts.get("permission_mode") or DEFAULT_PERMISSION_MODE)
    output_format = str(opts.get("output_format") or "text")
    tools = opts.get("allowed_tools", DEFAULT_TOOLS)
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]

    argv = [exe, "-p", "--model", model]
    # An empty effort omits the flag entirely, for runner builds that predate it.
    if effort:
        argv += ["--effort", str(effort)]
    argv += ["--permission-mode", permission_mode]
    for directory in granted_dirs(ws):
        argv += ["--add-dir", str(directory)]
    if tools:
        argv += ["--allowedTools", ",".join(str(t) for t in tools)]
    argv += ["--output-format", output_format]
    argv += extra_args_of(opts)
    # The prompt travels as a single argv element: no shell, no interpolation,
    # so nothing a project file contains can become part of the command line.
    argv.append(prompt)

    rc, out, err = run_cli(argv, cwd=ws.root, timeout=timeout_of(opts))
    if rc != 0:
        return ProviderResult(False, out, "claude exited %d: %s" % (rc, tail(err)))

    if output_format == "json":
        parsed = _parse_json_envelope(out)
        if parsed is not None:
            text, rounds, cost = parsed
            return ProviderResult(True, text, "", rounds, cost)
        # Exit code said success, so treat an unrecognised envelope as text
        # rather than manufacturing a failure the pipeline would have to explain.
    return ProviderResult(True, out)
