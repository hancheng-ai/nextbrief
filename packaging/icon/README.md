# nextbrief icon

A warm ivory sheet carrying two short rules and a deep teal check: a brief, and
the gate every claim passes before it prints.

Designed at 16×16 first, because that is where it lives — a Notification
Center banner and a menu bar. Everything larger is the same drawing at more
pixels.

## Files

| File | What it is |
|---|---|
| `nextbrief.svg` | **Canonical source.** Everything else is generated from it. |
| `nextbrief.icns` | Built artifact. 10 members, 16–1024px. |
| `nextbrief.iconset/` | The PNGs `iconutil` packs into the `.icns`. |
| `nextbrief-mono.svg` | One-colour mark in `currentColor`, no sheet. For checking the silhouette holds without the palette. |
| `nextbrief-menubar-template.svg` | macOS **template image**: black on transparency, no tile. Not generated from `nextbrief.svg` — see below. |
| `build-icon.sh` | Rebuilds the iconset and the `.icns`. |
| `render-svg.py` | SVG → RGBA PNG at one size. |
| `verify-iconset.py` | Checks a built iconset against the artwork. |
| `pngkit.py` | Standard-library PNG read/write shared by the two above. |

## Rebuilding

```bash
./build-icon.sh
```

About eight seconds, and it needs nothing macOS does not already ship:
`qlmanage`, `python3` (standard library only), `iconutil`. Edit
`nextbrief.svg` and re-run; do not hand-edit the PNGs.

The ten iconset members cover 16, 32, 64, 128, 256, 512 and 1024 pixels. There
is no `icon_64x64.png` — 64px is carried by `icon_32x32@2x.png`, and `iconutil`
rejects the other name.

## How it is rendered, and why that way

**ImageMagick is not used.** Its built-in SVG renderer silently drops stroked
paths and gradients — asked to render one of these files it produced three dots
on a flat black square, no error, exit code 0. If you have librsvg,
`rsvg-convert -w N -h N` is a correct substitute for the `qlmanage` call in
`render-svg.py`.

The renderer is `qlmanage`, which is QuickLook, which is WebKit. It renders SVG
properly, but it **flattens onto opaque white**. A naive `qlmanage -t` of a
rounded app icon gives a white square with the icon inside it: fine on a white
Notification Center card, obviously broken on a dark one.

So `render-svg.py` renders each size twice — once over white, once over black —
and solves for the alpha that was discarded. For a pixel of true colour `C` and
coverage `a`:

```
Pw = C·a + (1 − a)        Pb = C·a
⇒   a = 1 − (Pw − Pb)     C = Pb / a
```

Exact, per pixel, assuming nothing about where the transparent regions are.

## What the build checks

| Check | Catches |
|---|---|
| SVG parses as XML before rendering | Malformed source. WebKit does not refuse bad XML — it draws a **parse-error page**, which rasterises as happily as an icon. |
| Recovered image re-composites onto the white pass | The alpha algebra going wrong. |
| Something came out transparent | Both passes returning the *same wrong image*. |
| Canvas corner fully transparent | QuickLook's generic document thumbnail; a lost alpha channel. |
| Ground colour in the lower left | Wrong palette or wrong file. |
| Four ratios, per size | A changed composition. |

The four ratios are `sheet` (opaque share of canvas), `ink` (dark share), `top`
(how much of the ink sits above mid-height) and `teal` (the check's colour).
They are compared against **per-size** reference values in
`verify-iconset.py`, not one global band, because the ratios drift
systematically from 16px to 1024px as antialiasing stops thinning the 1px
rules — and that drift is the same size as the signal from a real defect.
Deleting the second rule takes `ink` from .2083 to .1771 at 16px, a 15% drop,
while the honest 16px-to-1024px spread is .2039 to .2177. One band wide enough
for every size is too wide to catch anything.

Each ratio earns its place. The artwork was broken three ways to see which
numbers actually moved:

| Defect | `ink` | `top` | `teal` |
|---|---|---|---|
| check deleted | 66% | 111% | 100% |
| check recoloured to graphite | 2% | 2% | **100%** |
| one rule deleted | 15% | 20% | — |

The middle row is the argument for measuring colour as well as darkness: a
check that silently lost its teal moves nothing else by more than 2%, well
inside tolerance. It would pass every other number in this file.

Three cautionary notes, all from checks that passed while something was wrong:

- An early `nextbrief-menubar-template.svg` had `--` inside an XML comment,
  which is not well-formed. Both render passes drew WebKit's error page, so
  `Pw == Pb` everywhere, so the algebra reported `alpha = 1` and a *perfect*
  round trip. The check was verifying its own arithmetic. Hence the XML parse
  and transparency checks, both confirmed to fail on the broken file before
  being kept. (That bug has since been reintroduced twice by editing these
  comments. The parse check caught it both times.)
- An earlier single `ink` band of `[0.170, 0.240]` was documented as catching a
  dropped rule. It did not: the broken build measured 0.176–0.187, inside the
  band. Per-size references and the `top` ratio replaced it.
- The `teal` classifier first used a tolerance of 70, and deleting a *rule*
  moved the teal ratio by 15% — a graphite rule pixel blended ~10% toward the
  sheet lands within 66 of the teal check, so the rules' antialiased edges were
  being counted as check. Tolerance is now 55: teal blended up to ~11% counts,
  graphite never does.

## Geometry

Laid out on a 16×16 pixel grid and multiplied by 64, so the `viewBox` is
`0 0 1024 1024` and **one device pixel at 16px is 64 user units**. Both rule
edges are whole multiples of 64 in y — on half-pixel boundaries the rules smear
into a single grey block at 16px, which an early round demonstrated.

- Sheet: 16px `x=1..15, y=1..15`, corner radius 3, plus a ¼px rim so the
  silhouette survives on a white card.
- Rules: 1px tall at `y=3` and `y=5`; lengths 8.5px and 6px. A ragged right
  edge reads as text rather than as a chart or a hamburger menu.
- Check: 2px stroke, from `(4, 10)` through `(6.5, 12.5)` to `(12.5, 6.5)`.

The check's ascending arm rises into the notch left by the shorter second rule.
That one relationship is what makes the three marks read as a single shape
rather than a stack — a three-rule version pushed the check down and away from
the text and lost it.

`nextbrief-menubar-template.svg` is **re-drawn on its own 16px grid**, not
scaled. A menu bar item has no tile, so the mark fills the canvas. AppKit
ignores a template image's colour and recolours its alpha to match the menu
bar, which is how one asset serves both light and dark menu bars — and why an
`.icns`, being full-colour, cannot serve the menu bar at all. Hence two files.

## Palette

Two materials, ivory and graphite. Graphite ink on an ivory sheet rather than
the reverse: the light ground is what makes it read as *morning*.

| Role | Hex |
|---|---|
| Sheet | `#F7F2E6` → `#E9DCC2` |
| Rim | `#CBBA96` |
| Ink (rules) | `#2A303C` |
| Check | `#14584F` |

The light ground is also the practical choice: it is the only one of the tried
directions that keeps a defined edge against *both* a white and a near-black
Notification Center card. Dark-ground concepts lost their lower-left corner
into `#1D1D1F`.

## Directions considered

Three were drawn and tested at 16px before this one was refined:

- **The sheet** — a page that has been checked. Chosen.
- **Registration** — two plates offset, only the overlap printing. Handsome at
  128px, mush at 16px: two 1.5px outlines blur into a grey ring.
- **The horizon** — a warm disc rising behind a rule. Calm and distinct, but
  silent about verification, and its dark ground failed the light/dark test.

Also built and set aside: a sun rising over a stack of graduated rules, with
and without beams and priority colours. Beams dissolve into fuzz at 16px and
force the disc smaller to fit inside the rim; four contiguous priority-coloured
bars read as a bar chart, and red reads as an alert.

Deliberately avoided throughout: bells, charts, gauges, anything that reads as
an alert, and any radial or starburst form. `cc-notify` has no icon of its own
— it re-badges a copy of `terminal-notifier` with the icon of whichever app
launched the session — so in practice its banners wear **Claude's coral burst**
or Terminal's black tile. Those are what this had to be told apart from.

## Licence

Everything in this directory is original work created for nextbrief and is
licensed under **Apache-2.0**, the same as the rest of the package.

- The artwork was drawn from scratch as SVG geometry — two rectangles, one
  polyline and one linear gradient. No icon set, no template, no traced or
  derivative source.
- No trademarked marks, no third-party icon-set glyphs, and no font or
  letterform of any kind (the icon contains no text).
- The palette is the author's own; colour values are not copyrightable in any
  case.
- `pngkit.py`, `render-svg.py` and `verify-iconset.py` are original,
  standard-library-only Python, Apache-2.0.
- `qlmanage`, `iconutil` and `python3` are Apple-supplied macOS tools invoked
  as external programs; nothing from them is redistributed here.

### A note on the mark

Apache-2.0 grants the right to make and distribute *derivative works*, this
artwork included, while its section 6 withholds trademark rights. That is a
deliberate choice here rather than an oversight: keeping one licence across the
whole package means downstream packagers can redistribute the icon without
having to reason about mixed terms, which a no-derivatives licence on the
artwork would have cost. (Debian, for one, treats no-derivatives licences as
non-free and would strip or replace the icon.)

What the licence does not do is stop a fork shipping a lightly altered version
of this mark. So, as a request rather than a legal restriction:

> **The nextbrief name and this mark identify the project.** If you fork
> nextbrief and distribute it under another name, please use your own mark, so
> that a user seeing this icon in Notification Center can tell whose software
> produced the notification. Verbatim redistribution as part of nextbrief —
> packaging, mirroring, bundling — is exactly what it is for and needs no
> permission.

No trademark licence is granted by Apache-2.0 or by this file.
