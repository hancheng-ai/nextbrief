#!/bin/bash
# Rebuild nextbrief.iconset and nextbrief.icns from nextbrief.svg.
#
# Uses only what ships with macOS:
#   qlmanage   QuickLook -- rasterises the SVG through WebKit (via render-svg.py)
#   python3    render-svg.py / verify-iconset.py, standard library only
#   iconutil   packs the .iconset directory into an .icns
#
# ImageMagick is deliberately not used. Its built-in SVG renderer silently
# drops stroked paths and gradients -- asked to render one of these files it
# produced three dots on a flat black square, no error.

set -euo pipefail
cd "$(dirname "$0")"

SRC=nextbrief.svg
SET=nextbrief.iconset
ICNS=nextbrief.icns

[ -f "$SRC" ] || { echo "missing $SRC" >&2; exit 1; }

rm -rf "$SET" "$ICNS"
mkdir -p "$SET"

# These ten members cover 16, 32, 64, 128, 256, 512 and 1024 pixels. 64px is
# carried by icon_32x32@2x; there is no icon_64x64 member in the format and
# iconutil rejects that name.
echo "rendering $SRC -> $SET"
python3 render-svg.py "$SRC" 16   "$SET/icon_16x16.png"
python3 render-svg.py "$SRC" 32   "$SET/icon_16x16@2x.png"
cp "$SET/icon_16x16@2x.png" "$SET/icon_32x32.png"
python3 render-svg.py "$SRC" 64   "$SET/icon_32x32@2x.png"
python3 render-svg.py "$SRC" 128  "$SET/icon_128x128.png"
python3 render-svg.py "$SRC" 256  "$SET/icon_128x128@2x.png"
cp "$SET/icon_128x128@2x.png" "$SET/icon_256x256.png"
python3 render-svg.py "$SRC" 512  "$SET/icon_256x256@2x.png"
cp "$SET/icon_256x256@2x.png" "$SET/icon_512x512.png"
python3 render-svg.py "$SRC" 1024 "$SET/icon_512x512@2x.png"

echo
echo "verifying $SET"
python3 verify-iconset.py "$SET"

echo
iconutil -c icns "$SET" -o "$ICNS"
echo "built $ICNS ($(wc -c < "$ICNS" | tr -d ' ') bytes)"
