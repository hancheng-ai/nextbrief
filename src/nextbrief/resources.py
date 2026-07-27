"""Access to the package's own bundled files.

Locales, prompt templates and workspace templates ship inside the package. The
obvious way to reach them -- ``Path(__file__).parent / "locales"`` -- silently
assumes the package was unpacked onto a filesystem. It is not always: a zipapp
built with ``python -m zipapp`` keeps every module inside a single archive, and
there ``__file__`` names a path that does not exist. ``glob`` finds nothing,
``is_file`` is False, and the failure surfaces far away as "unknown locale 'en'
(available: )" rather than as "you cannot read this that way".

That matters here more than it would elsewhere, because this package has no
runtime dependencies, which makes a single-file zipapp a genuinely good way to
ship it: one download, no installer, any Python 3.9 or newer.

``importlib.resources.files()`` returns a Traversable that behaves the same for
a directory and for an archive member, so everything below works in both. It is
stdlib from 3.9, so relying on it costs no dependency.

Every reader here fails soft, returning None or an empty list. A missing bundled
file is a packaging error, and the callers already report those in terms the
reader can act on.
"""

from __future__ import annotations

from typing import List, Optional

__all__ = ["read_text", "list_names", "exists"]

_PACKAGE = "nextbrief"


def _root():
    """The package root as a Traversable, or None if it cannot be located."""
    try:
        from importlib.resources import files
    except ImportError:  # pragma: no cover - 3.9 is the floor and ships files()
        return None
    try:
        return files(_PACKAGE)
    except (ModuleNotFoundError, TypeError):  # pragma: no cover
        return None


def _locate(*parts: str):
    root = _root()
    if root is None:
        return None
    node = root
    for part in parts:
        try:
            node = node / part
        except (TypeError, ValueError):
            return None
    return node


def exists(*parts: str) -> bool:
    node = _locate(*parts)
    if node is None:
        return False
    try:
        return node.is_file()
    except (OSError, AttributeError):
        return False


def read_text(*parts: str) -> Optional[str]:
    """Contents of a bundled file, or None if it is not there or unreadable."""
    node = _locate(*parts)
    if node is None:
        return None
    try:
        if not node.is_file():
            return None
        return node.read_text(encoding="utf-8")
    except (OSError, AttributeError, UnicodeDecodeError):
        return None


def list_names(subdir: str, suffix: str = "") -> List[str]:
    """Sorted file names directly under a bundled subdirectory.

    Sorted rather than in archive order: locale discovery feeds ``--help`` text
    and error messages, and those should not vary with how the package was built.
    """
    node = _locate(subdir)
    if node is None:
        return []
    try:
        if not node.is_dir():
            return []
        names = [child.name for child in node.iterdir() if child.is_file()]
    except (OSError, AttributeError):
        return []
    if suffix:
        names = [n for n in names if n.endswith(suffix)]
    return sorted(names)
