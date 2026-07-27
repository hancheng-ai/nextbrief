"""No model at all.

Running without an interpretation stage is a supported mode, not a degraded one.
Stage 1 gathers evidence and stage 3 renders it under caps and an evidence gate;
between them the model adds ranking, phrasing and the automation split -- useful,
and strictly optional. Without it you still get a brief: every project's real
signal, what is stale, what is blocked, what has gone quiet. Zero tokens, zero
hallucination, and a fixed point to compare the model's version against.

It is a provider rather than an ``if`` in the pipeline for the same reason: the
no-model path should exercise exactly the same code as every other path, or it
quietly rots until the day you actually need it.

``ok=True`` matters. Not calling a model is not a failure, and reporting it as
one would put a red line in the log every night for a working configuration.
"""

from __future__ import annotations

from typing import Any

from . import ProviderResult

NAME = "none"
AGENTIC = False


def probe(cfg: Any = None) -> bool:
    """Always usable -- that is the point of having it."""
    return True


def run(cfg: Any, prompt: str, ws) -> ProviderResult:
    return ProviderResult(True, "", "")
