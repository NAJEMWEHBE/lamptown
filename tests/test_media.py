"""The README pictures in docs/media (made by tools/make_media.py) show what their alt texts say.

``live-town.png`` is ``town.png``'s moment with one Lamplighter's card pinned open. The README and
docs/live.md say it shows the harbour, two boats at their berths and the lighthouse, so the card
must open somewhere else: that corner of the two pictures is the same, pixel for pixel. Read with
the standard library only (no Pillow), so the check runs everywhere the other tests do.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

MEDIA = Path(__file__).resolve().parents[1] / "docs" / "media"
WHARF = (360, 290, 800, 450)  # x0, y0, x1, y1 at 800 x 450: the harbour, the berths, the lighthouse


def rgb(path: Path) -> tuple[int, int, list[bytes]]:
    """(width, height, rows of RGB bytes) of an 8-bit, non-interlaced RGB or palette PNG."""
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), path
    pos, idat, palette, w, h, kind = 8, b"", b"", 0, 0, 0
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if tag == b"IHDR":
            w, h, depth, kind, _c, _f, lace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and kind in (2, 3) and lace == 0, (path, depth, kind, lace)
        elif tag == b"PLTE":
            palette = body
        elif tag == b"IDAT":
            idat += body
    raw, bpp = zlib.decompress(idat), 3 if kind == 2 else 1
    stride, rows, prev = w * bpp, [], bytearray(w * bpp)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        rows.append(bytes(line) if kind == 2 else b"".join(palette[3 * v:3 * v + 3] for v in line))
    return w, h, rows


def box(rows: list[bytes], x0: int, y0: int, x1: int, y1: int) -> list[bytes]:
    return [r[3 * x0:3 * x1] for r in rows[y0:y1]]


def test_the_pinned_card_leaves_the_wharf_in_the_picture():
    town, live = rgb(MEDIA / "town.png"), rgb(MEDIA / "live-town.png")
    assert town[:2] == live[:2] == (800, 450)
    off = [y + WHARF[1] for y, (a, b) in enumerate(zip(box(live[2], *WHARF), box(town[2], *WHARF),
                                                       strict=True)) if a != b]
    assert not off, ("live-town.png's pinned card covers the harbour, the berths or the "
                     f"lighthouse (rows {off[0]}-{off[-1]} differ from town.png)")
    assert live[2] != town[2], "live-town.png has no card pinned open"
