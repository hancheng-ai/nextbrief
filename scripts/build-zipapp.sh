#!/usr/bin/env bash
#
# Build dist/nextbrief.pyz -- the whole program as one executable file.
#
# Why this exists: nextbrief has zero runtime dependencies and is stdlib-only,
# which makes a zipapp an unusually honest distribution channel. One download,
# no installer, no virtualenv, no PATH surgery, any Python 3.9 or newer. For
# someone who just wants tomorrow's brief, `curl -LO && chmod +x` is a shorter
# conversation than explaining pipx.
#
# Why the smoke test below is not optional: inside an archive there is no
# filesystem, so the package reaches its own locales, prompts and templates
# through importlib.resources (see src/nextbrief/resources.py). Get that wrong
# and *the build still succeeds* -- zipapp only zips files -- and `--version`
# still prints, because argparse never opens a catalog. The breakage surfaces
# only when something reads a bundled file, and then it reads as "unknown locale
# 'en' (available: )" rather than as "you cannot read this that way". So the
# test renders a real brief and insists the output carry text that can only have
# come from the packaged zh catalog.
#
# Stdlib only, like the package: no pip install, no third-party builder.
# Usable locally and from CI. Set PYTHON=... to build with another interpreter.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
OUT="$ROOT/dist/nextbrief.pyz"

WORK=""
cleanup() {
    if [ -n "$WORK" ]; then
        rm -rf "$WORK"
    fi
}
trap cleanup EXIT

WORK="$(mktemp -d)"

# ---------------------------------------------------------------------------
# stage
# ---------------------------------------------------------------------------

# Copied to a clean directory rather than zipped in place: src/nextbrief is a
# working tree and picks up caches, editor droppings and half-finished files
# that must not end up inside something people download.
STAGE="$WORK/stage"
mkdir -p "$STAGE"
cp -R "$ROOT/src/nextbrief" "$STAGE/nextbrief"

# A .pyc compiled by whichever interpreter last ran the tests would ship inside
# the archive and, on the paths where it is honoured, be preferred over the
# source next to it. That turns "works on my machine" into a shipped artifact.
find "$STAGE" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$STAGE" -type f -name '*.pyc' -delete

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

mkdir -p "$ROOT/dist"
rm -f "$OUT"

# `/usr/bin/env python3` rather than a hard-coded interpreter: the file has to
# run on a machine whose Python is somewhere this build never saw.
"$PYTHON" -m zipapp "$STAGE" \
    --main "nextbrief.cli:main" \
    --python "/usr/bin/env python3" \
    --compress \
    --output "$OUT"
chmod +x "$OUT"

echo "built $OUT"

# ---------------------------------------------------------------------------
# smoke test -- against the artifact, not against the source tree
# ---------------------------------------------------------------------------

SMOKE="$WORK/smoke"
# A throwaway HOME as well as a throwaway workspace. `init` writes a pointer
# file under $XDG_CONFIG_HOME naming the default workspace, and building an
# artifact must never repoint the workspace of whoever ran the build.
# The workspace sits one level down so its parent is empty and project
# discovery has nothing local to wander into.
mkdir -p "$SMOKE/home" "$SMOKE/box/ws"
WS="$SMOKE/box/ws"

run_pyz() {
    env -u NEXTBRIEF_WORKSPACE \
        HOME="$SMOKE/home" \
        XDG_CONFIG_HOME="$SMOKE/home/.config" \
        "$OUT" "$@"
}

# Executed directly, not as `python3 nextbrief.pyz`, because the shebang and the
# execute bit are part of what is being shipped and are equally able to be wrong.
echo -n "smoke: --version ... "
version="$(run_pyz --version)"
echo "$version"

# The precise failure resources.py exists to prevent, asserted early so that a
# packaging mistake reads as itself instead of as a confusing empty brief. The
# archive is put on sys.path so zipimport is exercised the same way it is when
# the file is run.
echo -n "smoke: locales readable from inside the archive ... "
PYTHONPATH="$OUT" "$PYTHON" - <<'PY'
import sys

from nextbrief.i18n import available_locales

found = available_locales()
if len(found) < 2 or "en" not in found or "zh" not in found:
    sys.exit(
        "the zipapp cannot read its own locales (found: %r). "
        "Something bypassed nextbrief.resources and used __file__ or a glob."
        % (found,)
    )
print(", ".join(found))
PY

# init reads the packaged workspace templates and the packaged prompts; without
# them it stops with "packaged templates are missing".
echo -n "smoke: init a throwaway workspace ... "
init_log="$WORK/init.log"
if ! run_pyz init "$WS" --yes --no-scan >"$init_log" 2>&1; then
    echo "FAILED"
    cat "$init_log"
    exit 1
fi
if [ ! -f "$WS/registry.jsonc" ]; then
    echo "FAILED"
    cat "$init_log"
    echo "init reported success but wrote no registry.jsonc" >&2
    exit 1
fi
echo "ok"

# The real exercise: sense + render, in Chinese, writing a brief.
echo "smoke: v0 --locale zh"
run_pyz --workspace "$WS" --locale zh v0 --no-notify

if [ ! -f "$WS/BRIEF.md" ]; then
    echo "v0 exited 0 but produced no BRIEF.md in $WS" >&2
    exit 1
fi

# Existence alone would pass even with every catalog unreadable, because every
# string in the renderer carries its English original as a fallback. CJK can
# only have come from the packaged zh catalog, so that is what is asserted --
# and matching a character range rather than a phrase keeps this from breaking
# the next time someone rewords a translation.
"$PYTHON" - "$WS/BRIEF.md" <<'PY'
import re
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    text = fh.read()

if not re.search("[一-鿿]", text):
    sys.exit(
        "BRIEF.md was rendered with --locale zh but contains no Chinese, so the "
        "zh catalog inside the archive was not read and the renderer silently "
        "fell back to English."
    )
print("smoke: BRIEF.md ok (%d lines, zh catalog applied)" % len(text.splitlines()))
PY

# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

# Computed with Python rather than sha256sum/shasum/stat, whose names and flags
# differ between GNU and BSD. The interpreter is already a hard requirement.
"$PYTHON" - "$OUT" <<'PY'
import hashlib
import os
import sys

path = sys.argv[1]
with open(path, "rb") as fh:
    data = fh.read()

print()
print("%s  %s" % (hashlib.sha256(data).hexdigest(), os.path.basename(path)))
print("%d bytes (%.1f KB)" % (len(data), len(data) / 1024.0))
PY
