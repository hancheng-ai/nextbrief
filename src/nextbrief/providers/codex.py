"""Codex CLI as the stage-2 runner.

Same shape as the claude provider -- an agent that reads the digest and writes
the brief itself -- with the one structural difference that the fence is the
runner's own sandbox mode rather than a settings file in the workspace. The
default is the narrowest mode that can still write: the pass must be able to
update ``state/brief.json`` and backlog notes, and must not be able to do
anything else.

Effort/reasoning knobs are deliberately not modelled here. The runner spells
them differently across versions, and the honest place for a flag this package
does not track is ``extra_args``.
"""

from __future__ import annotations

from typing import Any, Optional

from . import (
    ProviderResult,
    extra_args_of,
    provider_options,
    resolve_binary,
    run_cli,
    tail,
    timeout_of,
)

NAME = "codex"
AGENTIC = True

DEFAULT_BIN = "codex"
DEFAULT_SANDBOX = "workspace-write"


def probe(cfg: Any = None) -> bool:
    opts = provider_options(cfg, NAME)
    return resolve_binary(str(opts.get("bin") or DEFAULT_BIN)) is not None


def run(cfg: Any, prompt: str, ws) -> ProviderResult:
    opts = provider_options(cfg, NAME)
    exe: Optional[str] = resolve_binary(str(opts.get("bin") or DEFAULT_BIN))
    if exe is None:
        return ProviderResult(
            False,
            "",
            "codex not found on PATH; set model.codex.bin to its full path, "
            "or set model.provider to another runner",
        )

    argv = [exe, "exec"]
    model = opts.get("model")
    # No default model: the runner's own configured default is a better guess
    # than anything this package could hardcode, and hardcoding one would go
    # stale silently.
    if model:
        argv += ["--model", str(model)]
    sandbox = opts.get("sandbox", DEFAULT_SANDBOX)
    if sandbox:
        argv += ["--sandbox", str(sandbox)]
    # A workspace is usually not a git repo of its own, and refusing to run in
    # one would make the common case fail for a reason unrelated to the task.
    if opts.get("skip_git_repo_check", True):
        argv.append("--skip-git-repo-check")
    argv += extra_args_of(opts)
    argv.append(prompt)  # single argv element: no shell, no interpolation

    rc, out, err = run_cli(argv, cwd=ws.root, timeout=timeout_of(opts))
    if rc != 0:
        return ProviderResult(False, out, "codex exited %d: %s" % (rc, tail(err)))
    return ProviderResult(True, out)
