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
# links, the formula's url and version. Updating only the three machine-readable
# literals left those pointing at the previous release -- which is what the
# docs-consistency tests caught, one tag too late.
#
# CHANGELOG.md is deliberately NOT in this list. A changelog is append-only
# history: the version strings in it are statements about releases that already
# happened, and a blanket replace rewrites the last one's heading into this one's
# -- deleting a release from the record and stranding everything under
# [Unreleased]. It gets its own handling below.
SWEEP = ["README.md", "README.zh.md", "packaging/homebrew/nextbrief.rb"]

# ...and the same reasoning applies *inside* a swept file, which is the part this
# script missed. README.md carries a release-history table, and a row in it is
# the same kind of statement a changelog heading is: this version, this anchor,
# this publication date. The replace below used to be unbounded, so every bump
# rewrote the newest row's version into the release being cut while leaving that
# row's anchor and date pointing at the release it used to describe. The row
# still parsed, the table still rendered, CI stayed green, and a release
# disappeared from the public record. It happened twice and was fixed by hand
# twice, which is how long a defect survives when its symptom is that nothing
# looks wrong.
#
# The boundary is a pair of markers in the document:
#
#     <!-- bump-version:skip:begin -->
#     ...append-only history...
#     <!-- bump-version:skip:end -->
#
# Not a heading: it is "## Release history" in English, something else in
# README.zh.md, and either can be renamed by someone who has never opened this
# script. Not "the file that has the table" either -- README.zh.md is swept as
# well and has no table today, so the boundary has to already be correct on the
# day it gains one. tests/test_bump_version.py holds both ends of this: the
# behaviour, and the fact that no swept file in this repository files release
# history outside the markers.
SKIP_BEGIN = "<!-- bump-version:skip:begin -->"
SKIP_END = "<!-- bump-version:skip:end -->"


def sweep(text, name):
    """Replace `previous` with `new` outside the skip markers.

    Returns (text, swept, kept). A file with no markers behaves exactly as it
    did before, which is what keeps the formula and the Chinese README swept.
    """
    out, swept, kept, pos = [], 0, 0, 0
    while True:
        begin = text.find(SKIP_BEGIN, pos)
        if begin < 0:
            break
        end = text.find(SKIP_END, begin)
        if end < 0:
            # Refused rather than guessed. An unclosed marker has two readings
            # -- "everything below is history" and "the author forgot the close"
            # -- and quietly picking one is the habit that cost two releases.
            sys.exit("error: %s in %s is never closed by %s"
                     % (SKIP_BEGIN, name, SKIP_END))
        end += len(SKIP_END)
        live, frozen = text[pos:begin], text[begin:end]
        swept += live.count(previous)
        kept += frozen.count(previous)
        out.append(live.replace(previous, new))
        out.append(frozen)
        pos = end
    tail = text[pos:]
    swept += tail.count(previous)
    out.append(tail.replace(previous, new))
    return "".join(out), swept, kept


changelog = root / "CHANGELOG.md"
changelog_text = changelog.read_text(encoding="utf-8")

# The newest dated heading is the release we are moving away from. `[Unreleased]`
# does not match, because the bracket must open with a digit.
head = re.search(r"(?m)^## \[(\d[^\]]*)\]", changelog_text)
previous = head.group(1) if head else None

for name in SWEEP:
    path = root / name
    if not path.is_file() or not previous or previous == new:
        continue
    text = path.read_text(encoding="utf-8")
    updated, swept, kept = sweep(text, name)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        # The kept count is printed rather than dropped: a marker that is doing
        # its job and a marker somebody deleted look identical in a log that
        # only reports what changed, and this defect's whole character is that
        # it looks fine.
        print("  %-28s -> %s (%d reference(s)%s)"
              % (name, new, swept, ", %d left in history" % kept if kept else ""))

# --- the changelog -------------------------------------------------------
#
# Open a dated section for this version and move everything that accumulated
# under [Unreleased] into it, leaving [Unreleased] empty for the next cycle.
# Nothing already written is edited: previous headings and their link
# definitions are history and stay exactly as they are.
if previous == new:
    print("  %-28s already at %s, left alone" % ("CHANGELOG.md", new))
elif re.search(r"(?m)^## \[%s\]" % re.escape(new), changelog_text):
    print("  %-28s already has a %s section, left alone" % ("CHANGELOG.md", new))
else:
    opener = re.search(r"(?m)^## \[Unreleased\][ \t]*$", changelog_text)
    if not opener:
        sys.exit("error: CHANGELOG.md has no '## [Unreleased]' heading -- update this script")

    body_start = opener.end()
    following = re.search(r"(?m)^## ", changelog_text[body_start:])
    body_end = body_start + following.start() if following else len(changelog_text)
    body = changelog_text[body_start:body_end].strip("\n")

    section = "\n\n## [%s] - %s\n" % (new, today)
    if body:
        section += "\n%s\n" % body
    changelog_text = changelog_text[:body_start] + section + "\n" + changelog_text[body_end:]

    # Link definitions: retarget the Unreleased comparison and add this tag's
    # own line just below it. The URL shape is copied from the previous entry
    # rather than hardcoded, so a repository move needs no edit here.
    def _retarget(match):
        return match.group(0).replace(previous, new)

    changelog_text = re.sub(r"(?m)^\[Unreleased\]: .+$", _retarget, changelog_text, count=1)

    prior = re.search(r"(?m)^\[%s\]: (\S+)$" % re.escape(previous), changelog_text)
    if prior:
        line = "[%s]: %s" % (new, prior.group(1).replace(previous, new))
        anchor = re.search(r"(?m)^\[Unreleased\]: .+$", changelog_text)
        if anchor:
            changelog_text = (changelog_text[:anchor.end()] + "\n" + line
                              + changelog_text[anchor.end():])

    changelog.write_text(changelog_text, encoding="utf-8")
    print("  %-28s -> new [%s] section%s"
          % ("CHANGELOG.md", new, " with the unreleased entries" if body else " (empty)"))
PY

echo
echo "Now:"
echo "  git commit -am 'chore: release $NEW' && git tag v$NEW && git push --follow-tags"
