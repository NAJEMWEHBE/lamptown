#!/usr/bin/env python3
"""Draw the Lamptown app icon in code and write it in every format the shortcuts need.

The icon is pixel art: a street lamp lit against the night with a little house behind it, its
window warm and its status lamp green. Two hand-placed sprites (16 x 16 for the smallest size,
32 x 32 for everything else) are scaled up by whole pixels only, so every size stays crisp.

Writes into ``src/lamptown/app/assets/`` (committed as package data):

* ``lamptown.ico``  (Windows: 16, 32, 48, 64, 128, 256 px, PNG frames)
* ``lamptown.icns`` (macOS: 16 to 1024 px, PNG entries)
* ``lamptown.png``  (Linux and the window icon: 256 px)

Standard library only, and the output is byte-for-byte reproducible (no timestamps, no text
chunks). ``tests/test_app_icon.py`` checks the committed files match this script.

Usage: python -I tools/make_icon.py [--out DIR] [--preview FILE.png]
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "lamptown" / "app" / "assets"

PAL = {
    "x": (0, 0, 0, 0),           # transparent (the rounded corners)
    ".": (16, 20, 46, 255),      # night sky
    ",": (24, 31, 66, 255),      # lower sky
    "s": (220, 226, 255, 255),   # star
    "m": (255, 233, 176, 255),   # moon
    "y": (255, 211, 110, 255),   # lamp light
    "Y": (255, 246, 214, 255),   # lamp light, hottest
    "k": (12, 14, 30, 255),      # outline
    "p": (74, 84, 140, 255),     # lamp post
    "P": (124, 136, 196, 255),   # lamp post highlight
    "r": (200, 68, 60, 255),     # roof
    "R": (138, 42, 42, 255),     # roof shadow
    "w": (107, 74, 46, 255),     # wall
    "W": (78, 52, 32, 255),      # wall shadow
    "o": (255, 211, 110, 255),   # lit window
    "d": (52, 34, 20, 255),      # door
    "g": (61, 220, 122, 255),    # status lamp (green: all fine)
    "c": (43, 51, 106, 255),     # cobbles
    "C": (30, 36, 78, 255),      # cobble gaps
}


def _mix(rgb: tuple[int, ...], amount: float, light=(255, 178, 74)) -> tuple[int, int, int, int]:
    """``rgb`` lit by the lamp: blended ``amount`` of the way towards its warm light."""
    return (*(round(c + (lc - c) * amount) for c, lc in zip(rgb[:3], light, strict=True)), 255)


# The lamp's light, in rings: sky pixels within each radius take the ring's tint, and so do the
# cobbles inside the pool of light below the lamp.
RINGS = (("1", 0.34), ("2", 0.20), ("3", 0.09))
for _key, _amt in RINGS:
    PAL[_key + "."] = _mix(PAL["."], _amt)
    PAL[_key + ","] = _mix(PAL[","], _amt)
PAL["q"] = _mix(PAL["c"], 0.22)
PAL["Q"] = _mix(PAL["C"], 0.22)
POOL = {"c": "q", "C": "Q"}

ART32 = [
    "xxx..........................xxx",
    "xx.......................mmm...x",
    "x..s....................mm......",
    "..............s.........mm......",
    ".........k..............mm......",
    "........kkk..............mmm....",
    ".......kpppk....................",
    "......kkkkkkk...................",
    "......kYYYYYk...................",
    "......kYYYYYk...........s.......",
    "......kyYYYyk..............RR...",
    "......kyyyyyk..........rr..RR...",
    "......kkkkkkk.........rrrr.RR...",
    "........kpk..........rrrrrrRR...",
    ".........P..........rrrrrrrr....",
    ".........P.........rrrrrrrrrr...",
    ".........P.......RRRRRRRRRRRRRR.",
    ".........P........wwwwwwwwwwww..",
    ".........P........wooowwwwooow..",
    ".........P........woYowwwwoYow..",
    ",,,,,,,,,P,,,,,,,,wooowggwooow,,",
    ",,,,,,,,,P,,,,,,,,wwwwwddwwwww,,",
    ",,,,,,,,,P,,,,,,,,wwwwwddwwwww,,",
    ",,,,,,,,,P,,,,,,,,WwwwwddwwwwW,,",
    ",,,,,,,,ppp,,,,,,,WWWWWddWWWWW,,",
    ",,,,,,,kkkkk,,,,,,,,,,,,,,,,,,,,",
    "cCcccCcccCcccCcccCcccCcccCcccCcc",
    "cccCcccCcccCcccCcccCcccCcccCcccC",
    "CcccCcccCcccCcccCcccCcccCcccCccc",
    "ccCcccCcccCcccCcccCcccCcccCcccCc",
    "xcccCcccCcccCcccCcccCcccCcccCccx",
    "xxxcCcccCcccCcccCcccCcccCcccCxxx",
]

ART16 = [
    "xx............xx",
    "x...........mm.x",
    "....k.......m...",
    "...kkk......mm..",
    "..kYYYk.........",
    "..kyyyk...rr....",
    "..kkkkk..rrrr...",
    "....P...rrrrrr..",
    "....P..RRRRRRRR.",
    "....P...wowwow..",
    ",,,,P,,,wwggww,,",
    ",,,,P,,,wwddww,,",
    ",,,ppp,,WWddWW,,",
    "cCccCccCccCccCcc",
    "xcCccCccCccCccCx",
    "xxccCccCccCccCxx",
]

ICO_SIZES = (16, 32, 48, 64, 128, 256)
# icns entry types that hold PNG data: (type, pixel size)
ICNS_TYPES = (
    (b"icp4", 16), (b"ic11", 32), (b"icp5", 32), (b"icp6", 64), (b"ic12", 64), (b"ic07", 128),
    (b"ic08", 256), (b"ic13", 256), (b"ic09", 512), (b"ic14", 512), (b"ic10", 1024),
)
PNG_SIZE = 256


# Where each sprite's lamp light sits: (x, y, ring radii from the inside out, pool x, pool y,
# pool half-width, pool half-height), in sprite pixels.
LIGHT = {
    32: (9, 9.5, (4.4, 5.6, 6.8), 9, 27.5, 6.5, 2.2),
    16: (4, 4.5, (2.9,), 4, 13.5, 3.2, 1.2),
}


def lit(art: list[str]) -> list[list[str]]:
    """The sprite as palette keys, with its lamp's glow and pool of light painted in."""
    x0, y0, radii, px, py, pw, ph = LIGHT[len(art)]
    out = []
    for y, line in enumerate(art):
        row = []
        for x, ch in enumerate(line):
            key = ch
            if ch in ".,":
                d = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
                for (ring, _amt), r in zip(RINGS, radii, strict=False):
                    if d <= r:
                        key = ring + ch
                        break
            elif ch in POOL and ((x - px) / pw) ** 2 + ((y - py) / ph) ** 2 <= 1:
                key = POOL[ch]
            row.append(key)
        out.append(row)
    return out


def check_art() -> None:
    for name, art, n in (("ART32", ART32, 32), ("ART16", ART16, 16)):
        if len(art) != n or any(len(r) != n for r in art):
            raise SystemExit(f"{name} must be {n} x {n}")
        bad = {ch for r in art for ch in r} - set(PAL)
        if bad:
            raise SystemExit(f"{name} uses colours not in PAL: {sorted(bad)}")


def sprite_for(size: int) -> list[str]:
    """The sprite that scales to ``size`` by a whole factor, preferring the detailed one."""
    for art in (ART32, ART16):
        if size % len(art) == 0 and size >= len(art):
            return art
    raise ValueError(f"no sprite scales to {size} px by a whole factor")


def png(size: int, art: list[str] | None = None) -> bytes:
    art = lit(art or sprite_for(size))
    k = size // len(art)
    rows = []
    for line in art:
        row = b"".join(bytes(PAL[ch]) * k for ch in line)
        rows.extend([b"\x00" + row] * k)
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def ico(sizes=ICO_SIZES) -> bytes:
    frames = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(frames))
    off = 6 + 16 * len(frames)
    entries, body = b"", b""
    for s, data in zip(sizes, frames, strict=True):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data),
                               off + len(body))
        body += data
    return head + entries + body


def icns(types=ICNS_TYPES) -> bytes:
    body = b"".join(t + struct.pack(">I", 8 + len(d)) + d
                    for t, d in ((t, png(s)) for t, s in types))
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def outputs() -> dict[str, bytes]:
    check_art()
    return {"lamptown.ico": ico(), "lamptown.icns": icns(), "lamptown.png": png(PNG_SIZE)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ASSETS, help="folder to write into")
    ap.add_argument("--preview", type=Path, help="also write a 512 px PNG preview here")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in outputs().items():
        (args.out / name).write_bytes(data)
        print(f"wrote {args.out / name} ({len(data)} bytes)")
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        args.preview.write_bytes(png(512))
        small = args.preview.with_name(args.preview.stem + "-16x" + args.preview.suffix)
        small.write_bytes(png(16 * 16, ART16))
        print(f"wrote {args.preview} and {small} (the 16 px sprite, 16x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
