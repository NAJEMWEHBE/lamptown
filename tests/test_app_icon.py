"""The app icon: the committed files match tools/make_icon.py, and each format is well formed."""

from __future__ import annotations

import importlib.util
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "lamptown" / "app" / "assets"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


make_icon = load("lamptown_make_icon", ROOT / "tools" / "make_icon.py")


def png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def png_pixels(data: bytes) -> tuple:
    """(IHDR, chunk tags, inflated image data) of one PNG: what it shows, not how it was
    compressed (zlib, zlib-ng and others deflate the same pixels to different bytes)."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    pos, tags, ihdr, idat = 8, [], b"", b""
    while pos < len(data):
        length, tag = struct.unpack(">I", data[pos:pos + 4])[0], data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        assert zlib.crc32(tag + body) & 0xFFFFFFFF == crc, f"bad CRC in {tag!r}"
        tags.append(tag)
        if tag == b"IHDR":
            ihdr = body
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    assert pos == len(data)
    return ihdr, tuple(tags), zlib.decompress(idat)


def frames(name: str, data: bytes) -> list[tuple]:
    """Every image in an icon file with its header fields (offsets and lengths left out)."""
    if name.endswith(".png"):
        return [png_pixels(data)]
    out = []
    if name.endswith(".ico"):
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        out.append((reserved, kind, count))
        for i in range(count):
            w, h, c, r, planes, bpp, length, off = struct.unpack(
                "<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
            out.append((w, h, c, r, planes, bpp, png_pixels(data[off:off + length])))
        return out
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    pos = 8
    while pos < len(data):
        kind, length = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        out.append((kind, png_pixels(data[pos + 8:pos + length])))
        pos += length
    return out


@pytest.mark.parametrize("name", ["lamptown.ico", "lamptown.icns", "lamptown.png"])
def test_committed_icons_match_the_generator(name):
    want = frames(name, make_icon.outputs()[name])
    got = frames(name, (ASSETS / name).read_bytes())
    assert len(got) == len(want), "run: python -I tools/make_icon.py"
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        assert g == w, f"{name}: image #{i} differs; run: python -I tools/make_icon.py"


def test_generator_is_reproducible():
    assert make_icon.outputs() == make_icon.outputs()


def test_ico_frames():
    data = (ASSETS / "lamptown.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(make_icon.ICO_SIZES))
    for i, size in enumerate(make_icon.ICO_SIZES):
        w, h, _c, _r, planes, bpp, length, off = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert (w or 256, h or 256) == (size, size) and (planes, bpp) == (1, 32)
        assert png_size(data[off:off + length]) == (size, size)


def test_icns_entries():
    data = (ASSETS / "lamptown.icns").read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    pos, seen = 8, []
    while pos < len(data):
        kind, length = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        seen.append((kind, png_size(data[pos + 8:pos + length])[0]))
        pos += length
    assert pos == len(data)
    assert seen == list(make_icon.ICNS_TYPES)


def test_png_and_no_text_chunks():
    data = (ASSETS / "lamptown.png").read_bytes()
    assert png_size(data) == (make_icon.PNG_SIZE, make_icon.PNG_SIZE)
    for tag in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"):
        assert tag not in data


def test_every_size_has_a_crisp_sprite():
    for size in (*make_icon.ICO_SIZES, *(s for _t, s in make_icon.ICNS_TYPES)):
        art = make_icon.sprite_for(size)
        assert size % len(art) == 0
    with pytest.raises(ValueError):
        make_icon.sprite_for(24)


def test_the_app_finds_its_icons():
    from lamptown.app.window import asset, icon_for_platform

    assert asset("lamptown.icns") is not None
    assert icon_for_platform("win32").name == "lamptown.ico"
    assert icon_for_platform("linux").name == "lamptown.png"
    assert asset("nope.png") is None
