#!/usr/bin/env python3
"""Rasterise an SVG to an RGBA PNG at a given size, using only macOS built-ins.

The renderer is `qlmanage`, which is QuickLook, which is WebKit. It renders
SVG correctly -- but it flattens the result onto opaque white, so a naive
`qlmanage -t` of a rounded app icon gives you a white square with the icon
inside it. That looks fine on a white Notification Center card and badly
broken on a dark one, which is the sort of defect that survives a review
because the place you happen to look at it is the place it works.

So render twice and solve for the alpha that was thrown away. For a pixel
with true colour C and coverage a, compositing over white and over black
gives

    Pw = C*a + (1 - a)
    Pb = C*a

from which

    a = 1 - (Pw - Pb)      and      C = Pb / a

exactly, per pixel. This assumes nothing about the artwork -- no knowledge of
where the rounded corners are -- so it keeps working when the artwork changes.

The recovered image is then re-composited over white and compared against the
white pass; any pixel that does not reproduce means the assumption above has
broken, and the build stops rather than shipping the difference.

That round-trip check is necessary and not sufficient, which cost an hour to
learn. An SVG with `--` inside an XML comment is not well-formed, and WebKit
renders malformed XML as a *parse error page* -- a white sheet with red text.
Both passes rendered the same error page, so Pw == Pb everywhere, so the
algebra reported alpha=1, colour=Pb, and a perfect round trip. The check
validated the arithmetic, which was fine; it could not see that the input was
not the icon. So there are two more checks, aimed at the cause rather than the
symptom:

  * the SVG is parsed with ElementTree before it is rendered, which catches
    malformed XML with a line number instead of a silent wrong picture;
  * if the recovered image has no transparent pixels at all then the backdrop
    made no difference, which for icon artwork means the render is not what we
    think it is. Pass --allow-opaque for genuinely full-bleed sources.

Usage:  render-svg.py [--allow-opaque] <source.svg> <pixels> <out.png>
"""

import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pngkit  # noqa: E402

# Largest allowed disagreement, in 8-bit levels, when the recovered image is
# re-composited over white and compared with the white pass. Rounding alone
# accounts for 1; anything beyond that means the two passes disagree about
# something other than the background.
RECOMPOSITE_TOL = 2

_SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)


def _with_backdrop(svg_text, colour):
    """Insert a full-canvas rect of `colour` as the first child of <svg>."""
    m = _SVG_OPEN.search(svg_text)
    if not m:
        raise ValueError("no <svg> element found")
    # -10%/120% rather than 0/100% so the backdrop cannot leave a hairline of
    # unpainted canvas at the edge if the viewBox is fractional.
    rect = ('<rect x="-10%%" y="-10%%" width="120%%" height="120%%" '
            'fill="%s"/>' % colour)
    return svg_text[:m.end()] + rect + svg_text[m.end():]


def _quicklook(svg_path, size, workdir):
    out = os.path.join(workdir, os.path.basename(svg_path) + ".png")
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(
        ["qlmanage", "-t", "-s", str(size), "-o", workdir, svg_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if not os.path.exists(out):
        raise RuntimeError("qlmanage produced no thumbnail for %s at %dpx"
                           % (svg_path, size))
    return out


def render(src, size, dest, allow_opaque=False):
    with open(src) as fh:
        svg = fh.read()

    # Parse before rendering. WebKit does not refuse malformed XML, it draws
    # an error page, and an error page rasterises just as happily as an icon.
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        raise RuntimeError(
            "%s is not well-formed XML: %s. WebKit would render this as a "
            "parse-error page rather than failing, so the build stops here."
            % (os.path.basename(src), exc)
        ) from exc

    with tempfile.TemporaryDirectory() as tmp:
        passes = {}
        for name, colour in (("white", "#FFFFFF"), ("black", "#000000")):
            path = os.path.join(tmp, "%s.svg" % name)
            with open(path, "w") as fh:
                fh.write(_with_backdrop(svg, colour))
            png = _quicklook(path, size, tmp)
            passes[name] = pngkit.read_rgba(png)

    (ww, wh, wrows) = passes["white"]
    (bw, bh, brows) = passes["black"]
    if (ww, wh) != (bw, bh):
        raise RuntimeError("passes disagree on size: %dx%d vs %dx%d"
                           % (ww, wh, bw, bh))
    if (ww, wh) != (size, size):
        raise RuntimeError("qlmanage returned %dx%d, wanted %dx%d"
                           % (ww, wh, size, size))

    out_rows = []
    worst = 0
    transparent = 0
    for y in range(wh):
        wrow, brow = wrows[y], brows[y]
        orow = bytearray(ww * 4)
        for x in range(ww):
            i = x * 4
            wr, wg, wb = wrow[i], wrow[i + 1], wrow[i + 2]
            br, bg, bb = brow[i], brow[i + 1], brow[i + 2]

            # alpha is one value for the pixel; average the three channel
            # estimates so per-channel rounding noise cancels
            a = 255 - ((wr - br) + (wg - bg) + (wb - bb)) / 3.0
            if a < 0:
                a = 0.0
            elif a > 255:
                a = 255.0

            if a < 0.5:
                transparent += 1
                orow[i:i + 4] = b"\x00\x00\x00\x00"
                # a fully transparent pixel must have been white on the white
                # pass; if it was not, the algebra has broken
                if max(abs(wr - 255), abs(wg - 255), abs(wb - 255)) > RECOMPOSITE_TOL:
                    worst = 255
                continue

            scale = 255.0 / a
            cr = int(br * scale + 0.5)
            cg = int(bg * scale + 0.5)
            cb = int(bb * scale + 0.5)
            cr = 255 if cr > 255 else cr
            cg = 255 if cg > 255 else cg
            cb = 255 if cb > 255 else cb
            ai = int(a + 0.5)
            orow[i], orow[i + 1], orow[i + 2], orow[i + 3] = cr, cg, cb, ai

            # re-composite over white and compare with the white pass
            k = ai / 255.0
            inv = 255.0 * (1.0 - k)
            for got, want in ((cr * k + inv, wr), (cg * k + inv, wg), (cb * k + inv, wb)):
                d = abs(got - want)
                if d > worst:
                    worst = d
        out_rows.append(orow)

    if worst > RECOMPOSITE_TOL:
        raise RuntimeError(
            "alpha recovery did not round-trip at %dpx (worst channel error "
            "%.1f levels, tolerance %d). The two passes disagree about "
            "something other than the backdrop." % (size, worst, RECOMPOSITE_TOL)
        )

    if transparent == 0 and not allow_opaque:
        raise RuntimeError(
            "nothing came out transparent at %dpx, so swapping the backdrop "
            "from white to black changed no pixel. The render is not the "
            "artwork -- an error page or a fallback thumbnail will do exactly "
            "this. Pass --allow-opaque if the source really is full-bleed."
            % size
        )

    pngkit.write_rgba(dest, ww, wh, out_rows)
    return worst, transparent / float(ww * wh)


def main():
    argv = [a for a in sys.argv[1:] if a != "--allow-opaque"]
    allow_opaque = "--allow-opaque" in sys.argv[1:]
    if len(argv) != 3:
        sys.stderr.write(
            "usage: render-svg.py [--allow-opaque] <source.svg> <pixels> <out.png>\n")
        return 2
    src, size, dest = argv[0], int(argv[1]), argv[2]
    try:
        worst, clear = render(src, size, dest, allow_opaque)
    except Exception as exc:
        sys.stderr.write("render failed: %s\n" % exc)
        return 1
    print("  %-22s %4dpx  round-trip %.1f  clear %.3f"
          % (os.path.basename(dest), size, worst, clear))
    return 0


if __name__ == "__main__":
    sys.exit(main())
