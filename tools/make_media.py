#!/usr/bin/env python3
"""Make the README media from the fictional demo world.

Builds ``lamptown demo`` at a fixed moment (a full-moon night), photographs it with headless
Chrome, and writes into ``docs/media/`` (the only folder the privacy gate allows images in):

* ``hero.gif``       the town for a few seconds, then a walk into three rooms and back out
* ``town.png``       the town, still, with its live town: Lamplighters, the lighthouse, berths
* ``live-town.png``  the same moment with one Lamplighter's card pinned open (a click)
* ``room-<id>.png``  one still per room (every building in the demo has one, the lighthouse
                     too)
* ``made-on.txt``    which OS made the set (windows, macos or linux): Chrome's canvas pixels
                     may differ between systems, so tests/test_media_fresh.py re-renders and
                     compares only on that one (written by a full run, not by ``--only``)

Every picture is the page's own 400 x 225 pixel art, scaled up by whole pixels (2x), so it
stays crisp. Every machine makes the same pictures: the build time, the page clock (``?at=``)
and the animation time (``?t=``) are pinned to one moment in UTC (+00:00), the sky to fixed
fictional coordinates, and the media copy of the page runs its clock as if the machine were on
UTC (browsers ignore $TZ on some systems), so the machine's own time zone never shows. No file
gets a timestamp or text chunk.

Needs Pillow (``pip install pillow``) and Chrome, Chromium or Edge ($CHROME, or the usual
install places). Run from anywhere:

    python tools/make_media.py [--out DIR] [--jobs N] [--only NAME ...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # run from a checkout without installing

from lamptown.build import build  # noqa: E402
from lamptown.config import load_config  # noqa: E402
from lamptown.demo import make_world  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover - a helpful message instead of a traceback
    sys.exit("make_media needs Pillow: pip install pillow")

W, H = 400, 225               # the town's native pixels (kit.js)
SHOT = 4                      # Chrome draws the page at 4x (1600 x 900); one pixel per block
SCALE = 2                     # the published size: 800 x 450
WHEN = dt.datetime(2026, 11, 24, 21, 30, tzinfo=dt.UTC)   # a full moon, an evening at work
AT = urllib.parse.quote(WHEN.isoformat(), safe=":")          # 2026-11-24T21:30:00+00:00
LAT, LON = 45.0, -8.0         # the sky's place: open sea, made up for the demo town
T = 300.0                     # animation seconds the stills freeze at
FPS = 10
TOWN_SECONDS = 3.0            # the town before the first walk-in
ROOM_SECONDS = 1.6            # time spent inside each room in the GIF
WALK_STEPS = 6                # frames for a walk-in (the page's zoom takes 0.6 s)
HERO_ROOMS = ("notes", "harbour", "contest")
MADE_ON = "made-on.txt"


def this_os() -> str:
    """The OS family written to MADE_ON (tests/test_media_fresh.py has the same rule)."""
    if sys.platform == "win32":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def find_chrome() -> str | None:
    """$CHROME, else the usual install places on each OS, else a browser on PATH."""
    env = os.environ.get("CHROME")
    if env:
        return env if Path(env).is_file() or shutil.which(env) else None
    cands: list[Path] = []
    if sys.platform == "win32":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                cands += [Path(base, "Google", "Chrome", "Application", "chrome.exe"),
                          Path(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    elif sys.platform == "darwin":
        cands += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    for c in cands:
        if c.is_file():
            return str(c)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


class Camera:
    """Headless Chrome screenshots of one town page, read back at native size."""

    def __init__(self, chrome: str, page: Path, scratch: Path):
        self.chrome, self.url, self.scratch = chrome, page.resolve().as_uri(), scratch

    def __call__(self, key: str, query: str, frag: str = "") -> Image.Image:
        work = self.scratch / key
        work.mkdir(parents=True)
        png = work / "shot.png"
        cmd = [self.chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--no-first-run", "--no-default-browser-check", "--mute-audio",
               f"--user-data-dir={work / 'profile'}", f"--window-size={W * SHOT},{H * SHOT}",
               "--force-device-scale-factor=1", "--virtual-time-budget=8000",
               f"--screenshot={png}", self.url + query + frag]
        root = hasattr(os, "geteuid") and os.geteuid() == 0  # CI containers run as root
        if root or (sys.platform.startswith("linux") and os.environ.get("CI")):
            cmd.insert(1, "--no-sandbox")  # Linux CI runners can't start Chrome's sandbox
        if sys.platform == "darwin":  # a real keychain can block a headless run on a prompt
            cmd.insert(1, "--use-mock-keychain")
        subprocess.run(cmd, capture_output=True, timeout=180, check=False)
        if not png.is_file():
            raise RuntimeError(f"Chrome made no screenshot for {key} ({query}{frag})")
        with Image.open(png) as im:
            big = im.convert("RGB")
        if big.size != (W * SHOT, H * SHOT):
            raise RuntimeError(f"screenshot {key} is {big.size}, not {(W * SHOT, H * SHOT)}")
        small = big.resize((W, H), Image.Resampling.NEAREST)  # the centre pixel of each block
        if small.resize(big.size, Image.Resampling.NEAREST).tobytes() != big.tobytes():
            raise RuntimeError(f"screenshot {key} is not whole {SHOT}x pixels; the page moved?")
        return small


# Rooms scatter a few sparks and notes with Math.random; a fixed seed makes them land in the
# same place every run. The page reads its clock in local time (getHours, getTimezoneOffset, ...);
# the second part makes local time UTC, so no machine's time zone reaches a picture. Only the
# media copy of the page gets this, never the real town.
SEED = ("<script>(() => { let s = 0x2545f491; Math.random = () => { s ^= s << 13; "
        "s ^= s >>> 17; s ^= s << 5; return (s >>> 0) / 4294967296; }; })();</script>"
        "<script>(() => { const D = Date, P = D.prototype;"
        " const LOCAL = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}([.][0-9]+)?)?$/;"
        " const utc = v => typeof v === 'string' && LOCAL.test(v.trim()) ? v.trim() + 'Z' : v;"
        " function UTCDate(...a) { if (!new.target) return new D().toString();"
        " return a.length > 1 ? new D(D.UTC(...a)) : new D(...a.map(utc)); }"
        " UTCDate.prototype = P; UTCDate.now = D.now; UTCDate.UTC = D.UTC;"
        " UTCDate.parse = v => D.parse(utc(v));"
        " for (const k of ['FullYear', 'Month', 'Date', 'Day', 'Hours', 'Minutes', 'Seconds',"
        " 'Milliseconds']) { P['get' + k] = P['getUTC' + k];"
        " if (k !== 'Day') P['set' + k] = P['setUTC' + k]; }"
        " P.getTimezoneOffset = () => 0; window.Date = UTCDate; })();</script>")


def seeded(html: str) -> str:
    head = html.find("<head>")
    if head < 0:
        raise RuntimeError("the built town has no <head>")
    return html[: head + 6] + SEED + html[head + 6:]


def query(t: float, zoom: float | None = None) -> str:
    q = f"?motion=1&t={t:.2f}&at={AT}"
    return q + (f"&zoom={zoom:.3f}" if zoom is not None else "")


def hero_plan() -> list[tuple[str, str, str]]:
    """(key, query, fragment) for every GIF frame, in order."""
    plan: list[tuple[str, str, str]] = []
    t = T
    step = 1 / FPS

    def add(key, q, frag=""):
        plan.append((f"{len(plan):03d}-{key}", q, frag))

    for _ in range(round(TOWN_SECONDS * FPS)):
        add("town", query(t))
        t += step
    for rid in HERO_ROOMS:
        frag = f"#room={rid}"
        for i in range(WALK_STEPS):
            add(f"{rid}-in", query(t, i / WALK_STEPS), frag)
            t += step
        for _ in range(round(ROOM_SECONDS * FPS)):
            add(rid, query(t), frag)
            t += step
        for i in reversed(range(WALK_STEPS)):
            add(f"{rid}-out", query(t, i / WALK_STEPS), frag)
            t += step
        for _ in range(round(0.6 * FPS)):
            add("town", query(t))
            t += step
    return plan


def clean(im: Image.Image) -> Image.Image:
    """A copy with no metadata at all, scaled up to the published size."""
    out = Image.frombytes("RGB", im.size, im.tobytes())
    return out.resize((W * SCALE, H * SCALE), Image.Resampling.NEAREST)


def save_png(im: Image.Image, path: Path) -> None:
    buf = io.BytesIO()
    clean(im).save(buf, "PNG", optimize=True)
    path.write_bytes(buf.getvalue())


CLEAR = 255   # the GIF's transparent index: "this pixel did not change since the last frame"


def gif_palette(frames: list[Image.Image]) -> tuple[list[tuple[int, int, int]], dict[bytes, int]]:
    """255 colours shared by every frame, and each colour's index.

    The pixel art usually fits as it is (an exact palette). When the glows push it over, an
    octree picks 192 colours and the rest go, one by one, to the colour drawn worst so far
    (weighted by how often it is drawn), so a small red alarm light keeps its own red. Every
    colour then maps to its nearest. Done by hand because Pillow's own palette mapping rounds
    close colours together, which would shift the night sky."""
    count: Counter[tuple[int, int, int]] = Counter()
    for f in frames:
        for n, rgb in f.getcolors(maxcolors=W * H):
            count[rgb] += n

    def d2(a, b):
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2

    if len(count) <= CLEAR:
        pal = sorted(count)
    else:
        sheet = Image.new("RGB", (W, H * len(frames)))
        for i, f in enumerate(frames):
            sheet.paste(f, (0, H * i))
        flat = sheet.quantize(192, method=Image.Quantize.FASTOCTREE,
                              dither=Image.Dither.NONE).getpalette()[: 192 * 3]
        pal = sorted({tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)})
        worst = {c: min(d2(c, p) for p in pal) for c in count}
        while len(pal) < CLEAR:
            c = max(sorted(count), key=lambda c: worst[c] * math.log2(1 + count[c]))
            if not worst[c]:
                break
            pal.append(c)
            worst = {k: min(v, d2(k, c)) for k, v in worst.items()}
        pal.sort()

    def nearest(c):
        return min(range(len(pal)), key=lambda i: d2(c, pal[i]))

    return pal, {bytes(c): nearest(c) for c in count}


def save_gif(frames: list[Image.Image], path: Path) -> None:
    """One shared palette, no dithering, endless loop. Each frame after the first stores only
    the pixels that changed (the rest are transparent over the frame before), which keeps a
    town full of small moving things small."""
    pal, index = gif_palette(frames)
    flat = [v for c in pal for v in c]
    flat += [0] * (768 - len(flat))
    pics, prev = [], None
    for f in frames:
        rgb = f.tobytes()
        idx = bytes(index[rgb[i:i + 3]] for i in range(0, len(rgb), 3))
        if prev is None:
            data = idx
        else:
            data = bytes(CLEAR if a == b else a for a, b in zip(idx, prev, strict=True))
        prev = idx
        p = Image.frombytes("P", (W, H), data)
        p.putpalette(flat)
        p = p.resize((W * SCALE, H * SCALE), Image.Resampling.NEAREST)
        p.info["transparency"] = CLEAR
        pics.append(p)
    buf = io.BytesIO()
    pics[0].save(buf, "GIF", save_all=True, append_images=pics[1:], duration=round(1000 / FPS),
                 loop=0, optimize=False, disposal=1, transparency=CLEAR)
    path.write_bytes(buf.getvalue())


def build_media_town(root: Path):
    """The demo world at WHEN, with the sky at LAT, LON (the demo itself leaves them unset). The
    lighthouse's days are UTC days too, so no machine's zone moves an event in its book."""
    cfg = make_world(root / "world", WHEN, tz=dt.UTC)
    text = cfg.read_text(encoding="utf-8")
    if text.count("\n[town]\n") != 1:
        raise RuntimeError("the demo world's config has no single [town] table")
    cfg.write_text(text.replace("\n[town]\n", f"\n[town]\nlatitude = {LAT}\nlongitude = {LON}\n"),
                   encoding="utf-8")
    return build(load_config(cfg), out_dir=root / "town", now=WHEN, tz=dt.UTC)


LIVE_JS = "town-live.js"
# live-town.png: a Lamplighter at the notes vault, its card pinned open. Its card opens into the
# open grass, so the wharf (the berths), the lighthouse and its sign stay in the picture; the
# waiting agent's card (Otter's) would open over the wharf. tests/test_media.py checks that.
LIVE_PIN = "puffin"


def live_push(town_dir: Path) -> str:
    """The live town for the media copy of the page. The page shows the Lamplighters, the
    lighthouse and the berths only while the live payload is fresh by the *real* clock (under
    30 s old), and a Watcher's file on disk ages while Chrome works. So the payload is pushed in
    the page itself, as the desktop app does (``lamptownLive(p)``), stamped with the page's own
    moment; the file is taken away, so the page's poll can't swap in the stale one. The page
    ages an agent from the payload's ``at`` to its ``since``, so every ``since`` moves with
    ``at`` by the same amount: "waiting 12 min" stays 12 min on any machine and any day.
    Everything else in it (the agents' states) is the build's, pinned to WHEN."""
    path = town_dir / LIVE_JS
    text = path.read_text(encoding="utf-8").strip()
    head = "lamptownLive("
    if not (text.startswith(head) and text.endswith(");")):
        raise RuntimeError(f"{LIVE_JS} is not a lamptownLive(...) call")
    payload = text[len(head):-2]
    path.unlink()
    # the sinces are the build's (WHEN), the file's `at` is the clock on the wall: move from WHEN
    when = WHEN.isoformat().replace("+00:00", "Z")
    return ("<script>(() => { const p = " + payload + ", now = Date.now(),"
            f" off = now - Date.parse('{when}'), move = s => new Date(Date.parse(s) + off)"
            ".toISOString();"
            " for (const a of p.agents || []) if (a.since) a.since = move(a.since);"
            " p.at = new Date(now).toISOString(); lamptownLive(p); })();</script>")


# ``?pin=<agent id>`` (the media copy only): click that Lamplighter once it stands still, as a
# reader would, so its card stays pinned in the picture. The click goes in only at a point whose
# tooltip is that agent's own card (its first line is the agent's title), so a miss can never
# fall through to the town and walk into a building.
PIN = ("<script>(() => { const id = new URLSearchParams(location.search).get('pin');"
       " if (!id) return; let tries = 0;"
       " const go = () => { if (++tries > 400) return;"
       # SEEN is the page's top-level const (not a window property)
       " const f = (SEEN.agents || []).find(e => e.id === id && !e.small);"
       " const a = ((SEEN.live || {}).agents || []).find(e => e.id === id);"
       " if (!f || !a || f.mode === 'walk') return setTimeout(go, 25);"
       " for (let dy = -24; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {"
       " const x = f.x + dx, y = f.y + dy, tp = tipAt(x, y);"
       " if (!tp || tp.tip[0] !== String(a.title)) continue;"
       " const r = cvs.getBoundingClientRect();"
       " stage.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true,"
       " clientX: r.left + (x + 0.5) / W * r.width, clientY: r.top + (y + 0.5) / H * r.height }));"
       # one click, then a look 300 ms on (the click redraws at once): no card yet, try again
       " return setTimeout(() => { if (!SEEN.agentCard) go(); }, 300); }"
       " setTimeout(go, 25); };"
       " addEventListener('load', go); })();</script>")


def media_page(html: str, push: str) -> str:
    """The media copy of the town: seeded (above), fed the live payload (live_push), and able to
    pin one agent's card (PIN)."""
    out = seeded(html)
    end = out.rfind("</body>")
    if end < 0:
        raise RuntimeError("the built town has no </body>")
    return out[:end] + push + PIN + out[end:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "media", help="default: docs/media")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 2),
                    help="screenshots taken at once")
    ap.add_argument("--only", nargs="+", metavar="NAME",
                    help="make only these files, named without the extension (town, live-town, "
                         "room-<id>, hero); tests/test_media_fresh.py uses it")
    args = ap.parse_args(argv)
    chrome = find_chrome()
    if not chrome:
        print("make_media: no Chrome/Chromium/Edge found (set $CHROME)", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lamptown-media-") as tmp:
        demo = build_media_town(Path(tmp) / "demo")
        rooms = [bid for bid, room in demo.data["rooms"].items() if isinstance(room, dict)]
        page = demo.town.with_name("town-media.html")   # beside town.html, so its files resolve
        page.write_text(media_page(demo.town.read_text(encoding="utf-8"),
                                   live_push(demo.town.parent)), encoding="utf-8")
        cam = Camera(chrome, page, Path(tmp) / "shots")
        stills = [("town", query(T), ""), ("live-town", query(T) + f"&pin={LIVE_PIN}", "")]
        stills += [(f"room-{r}", query(T), f"#room={r}") for r in rooms]
        hero = True
        if args.only:
            names = [key for key, _, _ in stills] + ["hero"]
            unknown = sorted(set(args.only) - set(names))
            if unknown:
                print(f"make_media: no such file {', '.join(unknown)} (choose from "
                      f"{', '.join(names)})", file=sys.stderr)
                return 2
            stills = [s for s in stills if s[0] in args.only]
            hero = "hero" in args.only
        jobs = stills + (hero_plan() if hero else [])
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            pics = list(pool.map(lambda j: cam(*j), jobs))
        made = []
        for (key, _, _), im in zip(stills, pics[: len(stills)], strict=True):
            save_png(im, args.out / f"{key}.png")
            made.append(args.out / f"{key}.png")
        if hero:
            save_gif(pics[len(stills):], args.out / "hero.gif")
            made.append(args.out / "hero.gif")
        if not args.only:
            (args.out / MADE_ON).write_text(this_os() + "\n", encoding="utf-8", newline="\n")
            made.append(args.out / MADE_ON)
    for p in sorted(made):
        print(f"{p.name:24} {p.stat().st_size / 1024:8.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
