#!/usr/bin/env python3
"""Minimal PNG read/write on the standard library alone.

Only what the icon build needs: 8-bit non-interlaced RGB or RGBA in, 8-bit
RGBA out. No Pillow, no ImageMagick -- the point is that verifying the build
must not depend on more than the build itself does.
"""

import struct
import zlib


def read_rgba(path):
    """Return (width, height, rows) with each row a bytearray of RGBA bytes."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s: not a PNG" % path)

    pos, idat, ihdr = 8, [], None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", data[pos + 8:pos + 8 + length])
        elif ctype == b"IDAT":
            idat.append(data[pos + 8:pos + 8 + length])
        elif ctype == b"IEND":
            break
        pos += 12 + length

    if ihdr is None:
        raise ValueError("%s: no IHDR" % path)
    width, height, depth, colour, _c, _f, interlace = ihdr
    if depth != 8 or interlace != 0 or colour not in (2, 6):
        raise ValueError(
            "%s: unsupported PNG (depth=%d colour=%d interlace=%d); expected "
            "8-bit RGB or RGBA, non-interlaced" % (path, depth, colour, interlace)
        )

    nch = 4 if colour == 6 else 3
    raw = zlib.decompress(b"".join(idat))
    stride = width * nch
    rows = []
    prev = bytearray(stride)
    off = 0

    for _ in range(height):
        ftype = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        off += 1 + stride

        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(nch):
                line[i] = (line[i] + (prev[i] >> 1)) & 0xFF
            for i in range(nch, stride):
                line[i] = (line[i] + ((line[i - nch] + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(nch):
                line[i] = (line[i] + prev[i]) & 0xFF
            for i in range(nch, stride):
                a = line[i - nch]
                b = prev[i]
                c = prev[i - nch]
                p = a + b - c
                pa = p - a if p > a else a - p
                pb = p - b if p > b else b - p
                pc = p - c if p > c else c - p
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise ValueError("%s: bad filter type %d" % (path, ftype))

        prev = line
        if nch == 4:
            rows.append(line)
        else:
            rgba = bytearray(width * 4)
            rgba[3::4] = b"\xff" * width
            rgba[0::4] = line[0::3]
            rgba[1::4] = line[1::3]
            rgba[2::4] = line[2::3]
            rows.append(rgba)

    return width, height, rows


def write_rgba(path, width, height, rows):
    """Write 8-bit RGBA rows as a PNG."""
    raw = bytearray()
    for row in rows:
        raw.append(0)          # filter: None
        raw.extend(row)

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        fh.write(chunk(b"IEND", b""))
