#!/usr/bin/env python3
"""Check that a built .iconset actually contains the nextbrief artwork.

Why this exists
---------------
The renderer is QuickLook. When QuickLook cannot render a file it does not
fail -- it returns its *generic document thumbnail*: right size, right format,
completely wrong picture. And QuickLook flattens onto opaque white, so an icon
can come out looking correct on a white background and carry a white square
everywhere else. Both failures produce ten plausible PNGs. A build step that
asks "did a PNG appear?" cannot tell either one from success.

What is checked
---------------
Two absolutes, then four proportions measured per size:

  corner    the canvas corner lies outside the rounded sheet, so it must be
            fully transparent. Catches the generic thumbnail and a lost
            alpha channel.
  ground    a point in the lower left must be the sheet colour.
  sheet     opaque share of the canvas -- scale and corner radius.
  ink       dark share of the sheet -- the two rules and the check.
  top       how much of that dark share sits above mid-height -- where the
            rules are, not just how many.
  teal      teal share -- the check.

Per size, not one global band. The ratios drift systematically from 16px to
1024px as antialiasing stops thinning the 1px rules, and that drift is the
same size as the signal from a real defect: deleting the second rule takes ink
from .2083 to .1771 at 16px, a 15% drop, while the honest 16px-to-1024px
spread is .2039 to .2177. One band wide enough for every size is too wide to
catch anything.
The drift is systematic and reproducible, so each size gets its own expected
value and a relative tolerance.

Each ratio earns its place -- this was checked by breaking the artwork four
ways and seeing which numbers moved:

    deleting the check     moves ink, top and teal.
    ink-ing the check      moves teal only. Graphite and deep teal are both
                           "dark", so ink and top do not notice at all.
    deleting a rule        moves ink and top.
    a wrong render         moves corner, ground and sheet.

The second one is the argument for measuring colour as well as darkness: a
check that silently lost its teal would pass every other number in this file.
It is also the reason to re-derive this table rather than widen it when the
artwork changes on purpose.

Usage:  verify-iconset.py <iconset-dir>
Exit:   0 all members pass, 1 otherwise, with a per-file reason on stderr.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pngkit  # noqa: E402

EXPECTED_SIZE = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

# size -> (sheet, ink, top, teal), measured from the reference build.
# Re-derive these if the artwork changes; see the README.
REFERENCE = {
      16: (0.7500, 0.2083, 0.4750, 0.0521),
      32: (0.7422, 0.2039, 0.5097, 0.1237),
      64: (0.7393, 0.2107, 0.4890, 0.1367),
     128: (0.7375, 0.2072, 0.4960, 0.1322),
     256: (0.7363, 0.2162, 0.4846, 0.1416),
     512: (0.7358, 0.2154, 0.4869, 0.1407),
    1024: (0.7357, 0.2177, 0.4844, 0.1417),
}
RATIO_NAMES = ("sheet", "ink", "top", "teal")

# Relative tolerance. Repeated runs on one machine are bit-identical, so this
# is headroom for a different macOS build of WebKit, not for run-to-run noise.
# It is also comfortably tighter than every defect measured above: the
# smallest of those moves a ratio by 15%. The 16px teal value is the most
# antialiasing-sensitive entry -- the check is only 2px wide there, so most of
# its pixels are blends -- which is precisely why these are per-size.
TOLERANCE = 0.12

# Lower left: below the second rule and clear of the check's vertex.
GROUND_AT = (0.15, 0.84)
GROUND_RGB = (0xE9, 0xDD, 0xC4)
GROUND_TOL = 10

INK_LUMA = 0.42
# Manhattan distance in RGB. A graphite rule pixel blended a little way toward
# the sheet passes within 66 of the teal check -- at tolerance 70 the rules'
# antialiased edges were being counted as check, and deleting a rule moved the
# teal ratio by 15%. 55 admits teal blended up to about 11% and no graphite at
# all.
TEAL_RGB, TEAL_TOL = (0x14, 0x58, 0x4F), 55


def measure(rows, size):
    dark = opaque = dark_top = teal = 0
    midpoint = size / 2.0
    ink_cut = INK_LUMA * 255.0
    for y in range(size):
        row = rows[y]
        above = y < midpoint
        for x in range(size):
            i = x * 4
            if row[i + 3] < 128:
                continue
            opaque += 1
            R, G, B = row[i], row[i + 1], row[i + 2]
            if (0.2126 * R + 0.7152 * G + 0.0722 * B) < ink_cut:
                dark += 1
                if above:
                    dark_top += 1
            if abs(R - TEAL_RGB[0]) + abs(G - TEAL_RGB[1]) + abs(B - TEAL_RGB[2]) < TEAL_TOL:
                teal += 1
    px = float(size * size)
    return (opaque / px,
            (dark / float(opaque)) if opaque else 0.0,
            (dark_top / float(dark)) if dark else 0.0,
            (teal / float(opaque)) if opaque else 0.0)


def check(path, size):
    problems = []
    width, height, rows = pngkit.read_rgba(path)
    if (width, height) != (size, size):
        return ["is %dx%d, expected %dx%d" % (width, height, size, size)], None

    inset = max(0, round(size * 0.02))
    corner_a = rows[inset][inset * 4 + 3]
    if corner_a > 8:
        problems.append(
            "corner pixel has alpha %d, expected 0. Either this is QuickLook's "
            "generic document thumbnail, or the alpha channel was flattened "
            "onto a background." % corner_a
        )

    gx = min(size - 1, round(size * GROUND_AT[0]))
    gy = min(size - 1, round(size * GROUND_AT[1]))
    i = gx * 4
    r, g, b, a = rows[gy][i], rows[gy][i + 1], rows[gy][i + 2], rows[gy][i + 3]
    if a < 250:
        problems.append("sheet is not opaque at (15%%, 84%%): alpha %d" % a)
    elif max(abs(r - GROUND_RGB[0]), abs(g - GROUND_RGB[1]), abs(b - GROUND_RGB[2])) > GROUND_TOL:
        problems.append("ground at (15%%, 84%%) is #%02X%02X%02X, expected #%02X%02X%02X"
                        % ((r, g, b) + GROUND_RGB))

    got = measure(rows, size)
    ref = REFERENCE.get(size)
    if ref is None:
        problems.append("no reference measurements recorded for %dpx" % size)
        return problems, got

    for name, actual, expect in zip(RATIO_NAMES, got, ref):
        lo, hi = expect * (1 - TOLERANCE), expect * (1 + TOLERANCE)
        if not (lo <= actual <= hi):
            problems.append(
                "%s ratio %.4f is outside [%.4f, %.4f] (reference %.4f "
                "+/-%d%%)" % (name, actual, lo, hi, expect, TOLERANCE * 100)
            )
    return problems, got


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: verify-iconset.py <iconset-dir>\n")
        return 2
    root = sys.argv[1]

    failed = False
    for name in sorted(EXPECTED_SIZE, key=lambda n: (EXPECTED_SIZE[n], n)):
        path = os.path.join(root, name)
        if not os.path.exists(path):
            sys.stderr.write("FAIL %-22s missing\n" % name)
            failed = True
            continue
        try:
            problems, got = check(path, EXPECTED_SIZE[name])
        except Exception as exc:
            sys.stderr.write("FAIL %-22s %s\n" % (name, exc))
            failed = True
            continue
        if problems:
            failed = True
            for p in problems:
                sys.stderr.write("FAIL %-22s %s\n" % (name, p))
        else:
            print("  ok %-22s %4dpx  sheet %.3f  ink %.4f  top %.4f  teal %.4f"
                  % ((name, EXPECTED_SIZE[name]) + got))

    if failed:
        sys.stderr.write("\niconset verification FAILED\n")
        return 1
    print("  all %d members verified" % len(EXPECTED_SIZE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
