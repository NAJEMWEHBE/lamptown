# The room kit `K`

Every room draws with one shared kit, `K`. It is what keeps a room you write looking like it
belongs in the same town: one pixel grid, one outline colour, one font, five state colours.

`K` is a frozen object, available to every room script as a global. It has a drawing half
(`web/kit.js`) and a navigation half (`web/town.js`). How a room plugs in is in
[rooms.md](rooms.md#bring-your-own-room).

Unpack what you need at the top of your room:

```js
const { W, H, OL, ST, R, P, text, textC, layer, glow, glowSp, sfx } = K;
```

## The canvas

| Name | Value | Meaning |
|---|---|---|
| `W`, `H` | `400`, `225` | The size of the world in native pixels. The page scales it up by a whole number, so pixels stay crisp. |
| `OL` | `'#090a16'` | The outline colour every sprite shares. Outline your shapes with it. |

`draw(g, t)` gets the 2D context `g`. The primitives below draw into the current context, so you
rarely need `g` yourself. Coordinates are rounded to whole pixels.

## The five states: `ST`

`ST[state]` gives the colours and words for `ok`, `waiting`, `working`, `error` and `missing`:

| Key | Use |
|---|---|
| `lamp` | The lamp colour: green, amber, cyan, red, grey. |
| `hi` | A light highlight of the lamp colour. |
| `txt` | Text in that state's colour. |
| `num` | Numbers on plates. |
| `glow` | Colour for `glowSp` (`null` for `missing`: a missing thing doesn't glow). |
| `shaft` | Colour for a light shaft on the floor (`null` for `missing`). |
| `word` | `'running fine'`, `'needs you'`, `'running now'`, `'something is wrong'`, `"can't see it"`. |

Use `ST[row.state] || ST.missing` so unknown states fall back to grey.

## Shapes

| Function | Draws |
|---|---|
| `R(x, y, w, h, colour)` | A filled rectangle. |
| `P(x, y, colour)` | One pixel. |
| `sprite(rows, x, y, map)` | A sprite from strings: `sprite(['.#.', '###'], x, y, { '#': '#ffd36e' })`. Characters not in `map` are clear. |
| `disc(cx, cy, r, colour)` | A filled pixel circle. |
| `ellipse(cx, cy, rx, ry, colour)` | A filled pixel ellipse. |
| `worker(x, y, dir, phase, carry, shirt)` | The town's 3 × 7 worker in a hard hat. `dir` is `1` or `-1`, `phase` steps the walk, `carry` holds a note. |

## Text

There is one font: a hand-drawn 3 × 5 pixel font, upper case only. Characters it lacks print as a
space. Every word on screen uses it.

| Function | Does |
|---|---|
| `text(s, x, y, colour)` | Draws text directly (slow; fine for a few words). |
| `textL(s, x, y, colour)` | Draws text from a cache, left-aligned. Use this in `draw`. |
| `textC(s, cx, y, colour)` | The same, centred on `cx`. |
| `textW(s)` | The width of `s` in pixels. |
| `fitText(s, maxW)` | `s` cut to fit `maxW` pixels, with a `.` when cut. |
| `wrapText(s, maxW)` | `s` broken into lines no wider than `maxW`. |
| `label(s, colour)` | The cached canvas behind `textL`, to blit yourself. |
| `plural(n, word)` | `'3 JOBS'`, `'1 JOB'`. |

## Light

Glows are pixel-stepped and additive, so a lamp lights what is near it.

| Function | Does |
|---|---|
| `glowSp(colour, rx, ry, alpha)` | A cached glow sprite. Make it once per colour and size. |
| `glow(sprite, x, y, k)` | Draws a glow centred on `x, y` at strength `k` (0 to 1). |
| `mkGlow(rx, ry, colour, alpha)` | A new, uncached glow sprite. Prefer `glowSp`. |

```js
if (row.state === 'working') glow(glowSp(ST.working.glow, 14, 10, 0.4), x, y, 0.7);
```

## Speed

| Function | Does |
|---|---|
| `layer(w, h, fn)` | Runs `fn(g)` into a new offscreen canvas and returns it. Build static parts (walls, shelves, signs) once in `enter`, then `g.drawImage(layer, 0, 0)` each frame. |

The town draws at 20 fps at most, and not at all while the window is hidden. In still mode
(reduced motion, or `?t=`), it redraws only when something changes. Keep `draw` to a few
milliseconds: cache with `layer` and `textL`, and don't allocate in the frame loop.

## Colour and noise

| Function | Does |
|---|---|
| `hash(x, y, seed)` | A stable pseudo-random number from 0 to 1. Use it for texture (grain, stars, moss) that looks the same every frame. |
| `hexRGB(hex)` | `'#ff8800'` to `[255, 136, 0]`. |
| `mixHex(a, b, u)` | A colour `u` of the way from `a` to `b`. |
| `rgba(hex, alpha)` | An `rgba(...)` string. |

## Time

The room data holds absolute times only. These turn them into words against the page's clock, so
the text stays right without a rebuild.

| Function | Gives |
|---|---|
| `nowMs()` | The page's "now" in milliseconds. Use it instead of `Date.now()`: tests and screenshots pin the clock with `?at=` or `--now`. |
| `dayText(iso)` | `'TUE 10 NOV'`. |
| `timeText(iso)` | `'TUE 07:15'`. |
| `untilText(iso)` | `'IN 3D 21H'`, `'IN 12M'`, `'DUE NOW'`, `'2H 5M AGO'`. |

## Sound: `sfx`

Rooms may make small, synthesised sounds. The town itself is silent. Sound starts after the first
click or key (browsers allow nothing sooner), and the user's mute choice (key `M`) is remembered.

| Member | Does |
|---|---|
| `ctx()` | The `AudioContext`, or `null` when muted or not yet allowed. Check it before scheduling. |
| `out()` | The room's output node. Connect your own nodes to it. |
| `tone(t0, freq, dur, vol, type)` | One note at audio time `t0`. `type` is an oscillator type (`'sine'`, `'square'`, `'triangle'`...). |
| `noise(t0, dur, vol, freq, filterType)` | A filtered noise burst (a rustle, a thud). |
| `click()` | A small UI click. |
| `muted` | Whether the user muted sound. |

```js
const ac = sfx.ctx(); if (ac) sfx.tone(ac.currentTime + 0.02, 660, 0.15, 0.2, 'triangle');
```

Leaving a room fades out whatever still plays. Keep sounds short and quiet, and tie each one to
something that happened.

## Navigation

| Member | Does |
|---|---|
| `leave()` | Walk back to town. |
| `card()` | Open the building's card: its status in words and the link to the details page. |
| `open(href)` | Open a link in a new tab. Only `https:`, `http:` and `file:` links open; anything else is ignored. |
| `toast(message)` | Show a short message at the bottom of the room. Screen readers hear it too. |
| `part(id)` | Another building's town entry (`lamp`, `state`, `name`, `num`, `links`), or `null`. |
| `still` | `true` when the scene is still (reduced motion, and no `?motion=1`). |
| `demo` | `true` when the page was opened with `?demo=1`. |

## Accessibility you get for free

Return your clickable things from `targets()` and the town handles the rest: hover tooltips, Tab
and Shift+Tab focus with a visible ring, Enter and Space to use, and a screen-reader line for each
target (`label`, else its `tip`). The town page also has a text-only
[details page](../README.md#how-it-works) with the same data.
