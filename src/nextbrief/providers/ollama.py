"""A local model via ``ollama run <model>``, prompt fed on stdin.

Not an agent: there are no tools, so this runner cannot read the digest or write
the brief. The caller must inline the digest into the prompt and persist the
reply -- see ``AGENTIC`` in the package docstring.

Worth having anyway. A local model costs nothing per run, which changes what the
cost table is arguing about, and it keeps the whole portfolio on the machine for
anyone who would rather no project directory ever leave it. Expect weaker
judgement: the evidence gate in stage 3 is what makes that survivable, since a
claim nobody can source is dropped whatever produced it.
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
    unfence,
)

NAME = "ollama"
AGENTIC = False

DEFAULT_BIN = "ollama"


def probe(cfg: Any = None) -> bool:
    opts = provider_options(cfg, NAME)
    return resolve_binary(str(opts.get("bin") or DEFAULT_BIN)) is not None


def run(cfg: Any, prompt: str, ws) -> ProviderResult:
    opts = provider_options(cfg, NAME)
    exe: Optional[str] = resolve_binary(str(opts.get("bin") or DEFAULT_BIN))
    if exe is None:
        return ProviderResult(
            False, "", "ollama not found on PATH; set model.ollama.bin or pick another runner"
        )

    # No default tag on purpose. Naming a model this package cannot know is
    # installed turns a config mistake into a confusing runtime error; asking
    # for it by name turns it into one sentence.
    model = opts.get("model")
    if not model:
        return ProviderResult(
            False, "", "no local model configured; set model.ollama.model (e.g. `ollama list`)"
        )

    argv = [exe, "run", str(model)] + extra_args_of(opts)
    # stdin rather than an argv element: local prompts carry the whole digest and
    # will happily exceed a comfortable command-line length.
    rc, out, err = run_cli(argv, cwd=ws.root, timeout=timeout_of(opts), stdin_text=prompt)
    if rc != 0:
        return ProviderResult(False, out, "ollama exited %d: %s" % (rc, tail(err)))
    return ProviderResult(True, unfence(out), "", 1)
