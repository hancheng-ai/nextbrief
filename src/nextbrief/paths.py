"""Workspace resolution.

nextbrief separates the **engine** (this package, installable and public) from the
**workspace** (your registry, backlog, state and logs -- private, never shipped).
The relationship is the one Obsidian has with a vault: one program, many possible
vaults, and the program contains none of your content.

Every path the engine touches hangs off a resolved :class:`Workspace`. Nothing is
derived from where the code happens to live, which is what makes a pip install
possible at all.

Resolution order, first hit wins:

1. an explicit ``--workspace DIR``
2. ``$NEXTBRIEF_WORKSPACE``
3. the pointer file written by ``nextbrief init`` (``~/.config/nextbrief/workspace``)
4. the current directory, or the nearest ancestor, containing ``registry.jsonc``

If none match we raise. A workspace that silently defaults to an empty directory
would render a clean, plausible, entirely content-free brief -- which reads as
"nothing is happening" rather than as "you are not configured".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

__all__ = ["Workspace", "resolve_workspace", "WorkspaceError", "config_home", "pointer_file"]

REGISTRY_NAME = "registry.jsonc"
CONFIG_NAME = "config.jsonc"
ENV_WORKSPACE = "NEXTBRIEF_WORKSPACE"
ENV_OUT = "NEXTBRIEF_OUT"


class WorkspaceError(RuntimeError):
    """No usable workspace could be resolved, or the resolved one is unusable."""


def config_home() -> Path:
    """XDG config dir for nextbrief itself (not for a workspace)."""
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "nextbrief"


def pointer_file() -> Path:
    """One-line file holding the default workspace path."""
    return config_home() / "workspace"


def expand(p) -> Path:
    """``~`` and ``$VAR`` expansion. Applied at every boundary where a path
    arrives from a human-authored file, which the original code never did."""
    return Path(os.path.expandvars(str(p))).expanduser()


@dataclass(frozen=True)
class Workspace:
    """A resolved vault. ``root`` holds inputs; ``out`` receives generated files.

    ``out`` defaults to ``root``. Splitting them lets you keep a read-only or
    version-controlled registry while writing artifacts elsewhere.
    """

    root: Path
    out: Path
    source: str  # how we resolved it, for diagnostics

    # -- inputs ---------------------------------------------------------------
    @property
    def registry_path(self) -> Path:
        return self.root / REGISTRY_NAME

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_NAME

    @property
    def backlog(self) -> Path:
        return self.root / "backlog"

    @property
    def prompts(self) -> Path:
        return self.root / "prompts"

    # -- outputs --------------------------------------------------------------
    @property
    def state(self) -> Path:
        return self.out / "state"

    @property
    def log(self) -> Path:
        return self.out / "log"

    @property
    def brief_md(self) -> Path:
        return self.out / "BRIEF.md"

    @property
    def brief_html(self) -> Path:
        return self.out / "BRIEF.html"

    @property
    def snapshot(self) -> Path:
        return self.state / "snapshot.json"

    @property
    def snapshot_prev(self) -> Path:
        return self.state / "snapshot.prev.json"

    @property
    def digest(self) -> Path:
        return self.state / "digest.json"

    @property
    def brief_json(self) -> Path:
        return self.state / "brief.json"

    @property
    def probes(self) -> Path:
        """Cached probe readings. Written only by ``nextbrief probe``; ``sense``
        reads it like any other file on disk, which is what keeps stage 1
        offline."""
        return self.state / "probes.json"

    def ensure_dirs(self) -> None:
        for d in (self.state, self.log, self.backlog):
            d.mkdir(parents=True, exist_ok=True)

    def contains(self, path) -> bool:
        """True if `path` is inside this workspace. The engine writes nowhere else."""
        try:
            target = Path(path).resolve()
        except OSError:
            return False
        for base in {self.root.resolve(), self.out.resolve()}:
            try:
                target.relative_to(base)
                return True
            except ValueError:
                continue
        return False


def _looks_like_workspace(d: Path) -> bool:
    return (d / REGISTRY_NAME).is_file()


def _search_upward(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if _looks_like_workspace(candidate):
            return candidate
    return None


def resolve_workspace(
    explicit=None,
    out=None,
    cwd=None,
    require_registry: bool = True,
) -> Workspace:
    """Resolve a workspace, or raise :class:`WorkspaceError` explaining how to fix it."""
    root: Optional[Path] = None
    source = ""

    if explicit:
        root, source = expand(explicit), "--workspace"
    elif os.environ.get(ENV_WORKSPACE):
        root, source = expand(os.environ[ENV_WORKSPACE]), "$" + ENV_WORKSPACE
    else:
        ptr = pointer_file()
        if ptr.is_file():
            text = ptr.read_text(encoding="utf-8").strip()
            if text:
                root, source = expand(text), str(ptr)
        if root is None:
            found = _search_upward(Path(cwd) if cwd else Path.cwd())
            if found is not None:
                root, source = found, "discovered from cwd"

    if root is None:
        raise WorkspaceError(
            "no workspace found.\n"
            "  Run `nextbrief init <dir>` to create one, or point at an existing one:\n"
            "    nextbrief --workspace ~/my-workspace run\n"
            "    export %s=~/my-workspace" % ENV_WORKSPACE
        )

    if not root.is_dir():
        raise WorkspaceError("workspace %s (from %s) is not a directory" % (root, source))
    if require_registry and not _looks_like_workspace(root):
        raise WorkspaceError(
            "%s (from %s) has no %s.\n"
            "  Run `nextbrief init %s` to scaffold one." % (root, source, REGISTRY_NAME, root)
        )

    out_dir = expand(out) if out else (
        expand(os.environ[ENV_OUT]) if os.environ.get(ENV_OUT) else root
    )
    return Workspace(root=root.resolve(), out=out_dir.resolve(), source=source)
