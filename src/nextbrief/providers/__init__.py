"""Model providers -- the one place in nextbrief that spends money.

Stage 1 (``sense``) and stage 3 (``render``) are deterministic and free. Between
them sits exactly one model call, whose job is to turn precomputed facts into a
handful of judgements. This package is the shim around that call, so that the
choice of runner is configuration rather than a rewrite.

Two properties are load-bearing and must not be traded away:

**Failure is soft.** Every entry point here returns a :class:`ProviderResult`
with ``ok=False`` instead of raising. The original shell driver ended the model
step with ``|| echo "that step failed -- still rendering"`` and then rendered
anyway. That is the product: the deterministic brief is what you rely on, and
the model layer is an accessory that is allowed to be missing, broken, offline,
or unpaid for.

**Discovery is a plain dict.** No ``importlib.metadata`` entry points, no plugin
scanning. Entry-point discovery costs tens of milliseconds of interpreter
startup and drags in the packaging machinery, and this program runs unattended
on a schedule under a system Python; a five-key dict costs nothing and is
readable in one screen. Adding a provider means adding a module and one line
below -- a deliberately unglamorous extension point.

A provider module exposes::

    NAME: str
    AGENTIC: bool                       # can it read/write files by itself?
    probe(cfg=None) -> bool             # is it usable on this machine?
    run(cfg, prompt, ws) -> ProviderResult

``AGENTIC`` matters to the caller. An agent runner (claude, codex) is handed a
prompt telling it to read ``state/digest.json`` and write ``state/brief.json``
itself, and ``ProviderResult.text`` is only a transcript. A plain completion
endpoint (ollama, openai_compat) has no tools: the caller must inline the digest
into the prompt and persist ``ProviderResult.text`` as the brief. Getting that
backwards produces a run that reports success and writes nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..jsonc import JSONCError, load_jsonc
from ..paths import expand

__all__ = [
    "ProviderResult",
    "ProviderError",
    "PROVIDERS",
    "DEFAULT_PROVIDER",
    "DEFAULT_TIMEOUT",
    "available",
    "resolve",
    "run_provider",
    "provider_name",
    "provider_options",
    "is_agentic",
    "granted_dirs",
    "resolve_binary",
    "run_cli",
    "RC_TIMEOUT",
    "RC_NOT_FOUND",
]

# Long enough that a real stage-2 pass never trips it, short enough that a hung
# runner does not silently cost you tomorrow's brief as well as today's. The
# shell version had no timeout at all and relied on the scheduler to reap it.
DEFAULT_TIMEOUT = 900

DEFAULT_PROVIDER = "auto"

# Shell conventions, so the numbers still mean something in a log line.
RC_TIMEOUT = 124
RC_NOT_FOUND = 127

# Preference order for ``provider: "auto"``. Agent runners first: they can read
# the digest and write the brief themselves, which is the shape the daily prompt
# was written for.
AUTO_ORDER = ("claude", "codex", "ollama", "openai_compat", "none")


@dataclass(frozen=True)
class ProviderResult:
    """Outcome of one stage-2 call.

    ``rounds`` and ``cost_usd`` are best-effort: only some runners report them.
    They are carried anyway because the digest -- and the ``effort: low``
    default -- exist precisely because someone measured these two numbers. Zero
    means "not reported", not "free".
    """

    ok: bool
    text: str
    error: str = ""
    rounds: int = 0
    cost_usd: float = 0.0


class ProviderError(ValueError):
    """Unknown or unusable provider name. Raised only by :func:`resolve`;
    :func:`run_provider` turns it into a failed result."""


# --- configuration ---------------------------------------------------------
#
# Config shape (every key optional)::
#
#     "model": {
#       "provider": "auto",          // auto | claude | codex | ollama |
#                                    // openai_compat | none
#       "timeout_seconds": 900,      // scalars here are shared by all providers
#       "claude": { "model": "sonnet", "effort": "low" },
#       "ollama": { "model": "some-local-model" }
#     }
#
# The rule is just: a nested object is a per-provider block, a scalar is a
# shared default. That keeps the common case ("everyone gets the same timeout")
# to one line and needs no schema to explain.


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _model_section(cfg: Any) -> Dict[str, Any]:
    """The ``model`` block, whether we were handed the whole config or just it."""
    cfg = _as_dict(cfg)
    section = _as_dict(cfg.get("model"))
    if section:
        return section
    # Tolerate a caller that already drilled down. Cheap, and it makes providers
    # usable from a test without building a whole config.
    if "provider" in cfg or any(k in cfg for k in PROVIDERS):
        return cfg
    return {}


def _provider_entry(cfg: Any) -> Any:
    """The raw ``provider`` value, wherever it was written."""
    section = _model_section(cfg)
    if "provider" in section:
        return section["provider"]
    return _as_dict(cfg).get("provider")


def provider_name(cfg: Any) -> str:
    """Which provider the config asks for; ``auto`` when it says nothing.

    Two spellings are accepted, because both are in the wild:

        "provider": "claude"
        "provider": { "name": "claude", "model": "sonnet", "effort": "low" }

    The mapping form is what the shipped template and every generated workspace
    use. This function previously did ``str(value)`` on whatever it found, so the
    mapping form stringified to ``"{'name': 'claude', ...}"`` -- an unknown
    provider. Stage 2 then failed soft, exactly as designed, and the run
    degraded to v0 and exited 0. Every config produced by ``nextbrief init``
    silently skipped the model, and the scheduler reported success.
    """
    raw = _provider_entry(cfg)
    if isinstance(raw, dict):
        raw = raw.get("name")
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    return str(raw).strip().lower() if raw else DEFAULT_PROVIDER


def provider_options(cfg: Any, name: str) -> Dict[str, Any]:
    """Options for one provider: stage defaults, then shared scalars, then the
    provider's own block. Later wins."""
    cfg_d = _as_dict(cfg)
    section = _model_section(cfg)
    opts: Dict[str, Any] = {}

    # ``model_by_stage.daily`` predates the provider split and still carries the
    # measured defaults (a mid-tier model at low effort). Honour it as the
    # weakest layer so an existing workspace keeps its cost profile.
    stage = _as_dict(_as_dict(cfg_d.get("model_by_stage")).get("daily"))
    for key, value in stage.items():
        if value is not None:
            opts[key] = value

    for key, value in section.items():
        if key != "provider" and not isinstance(value, dict):
            opts[key] = value

    # The mapping form carries its options inline -- {"name": "claude", "model":
    # "sonnet", "effort": "low"} -- so those have to be read here or a workspace
    # written from the shipped template would resolve the right provider and then
    # run it with none of the settings sitting next to the name.
    entry = _provider_entry(cfg)
    if isinstance(entry, dict):
        for key, value in entry.items():
            if key != "name" and not isinstance(value, dict):
                opts[key] = value
        opts.update(_as_dict(entry.get(name)))

    opts.update(_as_dict(section.get(name)))
    return opts


def timeout_of(opts: Dict[str, Any]) -> Optional[float]:
    """``timeout_seconds`` from options; zero or negative means no limit."""
    try:
        seconds = float(opts.get("timeout_seconds", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return float(DEFAULT_TIMEOUT)
    return seconds if seconds > 0 else None


def extra_args_of(opts: Dict[str, Any]) -> List[str]:
    """Escape hatch for flags this package does not model.

    CLI runners change flags faster than a package can ship releases, and a
    workspace should never be stuck one flag away from working.
    """
    extra = opts.get("extra_args")
    if isinstance(extra, str):
        return extra.split()
    if isinstance(extra, (list, tuple)):
        return [str(x) for x in extra]
    return []


# --- shared plumbing -------------------------------------------------------


def resolve_binary(exe: str) -> Optional[str]:
    """Locate a runner. Accepts a bare name (PATH lookup) or an explicit path.

    Explicit paths are checked directly because a GUI-launched scheduled job
    inherits a minimal PATH -- the same reason nothing else in this codebase
    assumes a login shell's environment.
    """
    if not exe:
        return None
    if os.path.sep in exe:
        candidate = str(expand(exe))
        return candidate if os.access(candidate, os.X_OK) else None
    return shutil.which(exe)


def run_cli(
    argv: Sequence[str],
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
    stdin_text: Optional[str] = None,
) -> Tuple[int, str, str]:
    """Run a child process and return ``(returncode, stdout, stderr)``.

    Every failure becomes a return code rather than an exception, so callers
    stay one ``if rc:`` away from a soft failure.

    Two details here are not incidental. Encoding is pinned to UTF-8 rather than
    the locale's, because an unattended job has no locale and a brief in a
    non-ASCII language would otherwise die at the decode step. And stdin is
    ``/dev/null`` when there is nothing to feed, because a runner that decides
    to prompt would otherwise block forever on an inherited, headless stdin.
    """
    kwargs: Dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if stdin_text is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = stdin_text

    try:
        proc = subprocess.run(list(argv), timeout=timeout, **kwargs)
    except FileNotFoundError:
        return RC_NOT_FOUND, "", "%s: not found" % (argv[0] if argv else "?")
    except subprocess.TimeoutExpired:
        return RC_TIMEOUT, "", "timed out after %ss" % timeout
    except OSError as exc:  # permissions, exec format, ...
        return RC_NOT_FOUND, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def tail(text: str, limit: int = 400) -> str:
    """The end of a runner's stderr, so a failure fits in one log line."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def granted_dirs(ws) -> List[Path]:
    """Directories an agent runner is allowed to read.

    The workspace itself, plus the portfolio root the registry declares as
    ``defaults.root``. Reading it from the registry instead of hardcoding a path
    is what lets the same engine serve someone else's layout -- and it keeps the
    grant honest: the model sees exactly the tree the registry already claims to
    describe, no more.
    """
    dirs: List[Path] = []
    try:
        dirs.append(Path(ws.root))
    except (AttributeError, TypeError):
        return []
    try:
        registry = load_jsonc(ws.registry_path)
        declared = _as_dict(_as_dict(registry).get("defaults")).get("root")
    except (JSONCError, OSError, AttributeError, ValueError):
        declared = None  # fail open: grant only the workspace
    if declared:
        try:
            root = expand(declared)
            if not root.is_absolute():
                # Same rule as everywhere else a human-authored root is read: it
                # is relative to the workspace, not to the process's directory.
                root = Path(ws.root) / root
            root = root.resolve()
            if root.is_dir():
                dirs.append(root)
        except OSError:
            pass

    unique: List[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def unfence(text: str) -> str:
    """Strip one wrapping ``` fence.

    Completion endpoints wrap JSON in Markdown fences however the prompt is
    phrased. Removing one whole-reply fence is a normalisation, not a parse:
    everything else is left exactly as the model wrote it, so a malformed reply
    still fails loudly at the caller's ``json.loads`` instead of being quietly
    repaired into something plausible.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2:
        return text
    return "\n".join(lines[1:-1]).strip()


# --- registry --------------------------------------------------------------
#
# Imported last on purpose: the provider modules import the helpers above from
# this package, so those names must already exist when these imports execute.

from . import claude as _claude  # noqa: E402
from . import codex as _codex  # noqa: E402
from . import none as _none  # noqa: E402
from . import ollama as _ollama  # noqa: E402
from . import openai_compat as _openai_compat  # noqa: E402

PROVIDERS = {
    "claude": _claude,
    "codex": _codex,
    "ollama": _ollama,
    "openai_compat": _openai_compat,
    "none": _none,
}

# Spellings people actually type.
ALIASES = {
    "openai": "openai_compat",
    "openai-compat": "openai_compat",
    "compat": "openai_compat",
    "off": "none",
    "v0": "none",
    "": DEFAULT_PROVIDER,
}


def canonical(name: Optional[str]) -> str:
    key = (name or "").strip().lower().replace(" ", "")
    return ALIASES.get(key, key)


def available(cfg: Any = None) -> Dict[str, bool]:
    """``{name: usable_here}``. Cheap enough to call before every run: it is a
    PATH lookup and an environment read, never a network call."""
    out: Dict[str, bool] = {}
    for name, module in PROVIDERS.items():
        try:
            out[name] = bool(module.probe(cfg))
        except Exception:  # a broken probe must not take down the CLI
            out[name] = False
    return out


def is_agentic(name: str) -> bool:
    """True if the runner can read the digest and write the brief by itself."""
    module = PROVIDERS.get(canonical(name))
    return bool(getattr(module, "AGENTIC", False))


def resolve(name: Optional[str], cfg: Any = None) -> Callable[..., ProviderResult]:
    """Return the ``run`` callable for ``name``.

    ``None`` means "ask the config". ``auto`` takes the first provider that
    probes usable, which is how a machine with no model runner at all still gets
    a brief: it lands on ``none`` and the deterministic pipeline carries on.
    """
    key = canonical(name if name else provider_name(cfg))
    if key == "auto":
        probes = available(cfg)
        key = next((n for n in AUTO_ORDER if probes.get(n)), "none")
    module = PROVIDERS.get(key)
    if module is None:
        raise ProviderError(
            "unknown provider %r; known: %s" % (name, ", ".join(sorted(PROVIDERS)))
        )
    return module.run


def run_provider(name: Optional[str], cfg: Any, prompt: str, ws) -> ProviderResult:
    """Run stage 2. Never raises -- see the module docstring."""
    if not (prompt or "").strip():
        return ProviderResult(False, "", "empty prompt; nothing to ask the model")
    try:
        runner = resolve(name, cfg)
    except ProviderError as exc:
        return ProviderResult(False, "", str(exc))
    try:
        result = runner(cfg, prompt, ws)
    except Exception as exc:  # a provider bug must not cost you the brief
        return ProviderResult(False, "", "%s: %s" % (type(exc).__name__, exc))
    if not isinstance(result, ProviderResult):
        return ProviderResult(
            False, "", "provider %r returned %s" % (name, type(result).__name__)
        )
    return result
