#!/usr/bin/env bash
# Set the version in every place that carries one.
#
# There are three: pyproject.toml (what pip and PyPI see), __init__.py (what
# `nextbrief --version` prints, and the only one a zipapp can read -- there is no
# installed metadata inside an archive, so importlib.metadata is not an option
# here), and CITATION.cff (what a citation resolves to).
#
# One command rather than three edits, because the failure mode of doing it by
# hand is a package whose --version disagrees with its own metadata, and nobody
# notices until someone reports a bug against a version that was never released.
#
#   scripts/bump-version.sh 0.2.0
#   scripts/bump-version.sh 0.2.0rc1
#
# PEP 440 normalized form only: 0.2.0rc1, never 0.2.0-rc1. The release workflow
# compares the tag against pyproject.toml byte for byte and refuses on a mismatch.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEW="${1:-}"

if [ -z "$NEW" ]; then
    echo "usage: scripts/bump-version.sh <version>    e.g. 0.2.0 or 0.2.0rc1" >&2
    exit 2
fi

# Reject the shapes that would pass here and fail in CI, where it costs a tag.
if ! printf '%s' "$NEW" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?(\.dev[0-9]+)?$'; then
    echo "error: '$NEW' is not a PEP 440 normalized version." >&2
    echo "       want 0.2.0, 0.2.0rc1, or 0.2.0.dev1 -- not 0.2.0-rc1 or v0.2.0." >&2
    exit 2
fi

TODAY="$(date -u +%Y-%m-%d)"

python3 - "$ROOT" "$NEW" "$TODAY" <<'PY'
import pathlib
import re
import sys

root, new, today = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]

edits = [
    ("pyproject.toml", r'(?m)^version = "[^"]+"', 'version = "%s"' % new),
    ("src/nextbrief/__init__.py", r'(?m)^__version__ = "[^"]+"', '__version__ = "%s"' % new),
    ("CITATION.cff", r'(?m)^version: .+$', "version: %s" % new),
    ("CITATION.cff", r'(?m)^date-released: .+$', 'date-released: "%s"' % today),
]

for name, pattern, replacement in edits:
    path = root / name
    text = path.read_text(encoding="utf-8")
    text, n = re.subn(pattern, replacement, text, count=1)
    if n != 1:
        sys.exit("error: no version line matched in %s -- update this script" % name)
    path.write_text(text, encoding="utf-8")
    print("  %-28s -> %s" % (name, new))

# The version also appears in prose and URLs: badges, install commands, download
# links, the changelog heading and its link definitions, the formula's url and
# version. Updating only the three machine-readable literals left those pointing
# at the previous release -- which is what the docs-consistency tests caught, one
# tag too late. A bump has to be complete or the tests it must pass will fail.
SWEEP = ["README.md", "README.zh.md", "CHANGELOG.md", "packaging/homebrew/nextbrief.rb"]
VERSION_RE = re.compile(r"\b\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.dev\d+)?\b")

previous = None
for name in SWEEP:
    path = root / name
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8")
    if previous is None:
        # Whatever the changelog's newest heading says is the version we are
        # moving away from; deriving it beats hardcoding a pattern per file.
        head = re.search(r"(?m)^## \[(\d[^\]]*)\]", (root / "CHANGELOG.md").read_text(encoding="utf-8"))
        previous = head.group(1) if head else None
    if not previous or previous == new:
        continue
    updated = text.replace(previous, new)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        print("  %-28s -> %s (%d reference(s))" % (name, new, text.count(previous)))

if previous and previous != new:
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    # The heading was rewritten in place by the sweep above, so re-date it.
    text = re.sub(r"(?m)^## \[%s\] - .+$" % re.escape(new), "## [%s] - %s" % (new, today), text, count=1)
    changelog.write_text(text, encoding="utf-8")
PY

echo
echo "Now:"
echo "  git commit -am 'chore: release $NEW' && git tag v$NEW && git push --follow-tags"
