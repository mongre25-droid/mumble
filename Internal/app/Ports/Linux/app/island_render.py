#!/usr/bin/env python3
"""The island's LOOK — a pure Pillow renderer for the Golden-Black glass pill.

This module has NO tkinter / UI dependency: it turns a plain ``snapshot`` dict
into an RGBA image of the floating island, faithfully reproducing the UX-Pilot
"Voice Dictation — Island Overlay" design (liquid-glass capsule, breathing gold
rim, travelling sheen, ripple dot, audio-reactive waveform, bouncing dots,
shimmer label, per-state colour + soft drop-shadow + gold glow halo).

``overlay.py`` owns the window (a click-through, always-on-top, per-pixel-alpha
LAYERED Win32 window) and just blits the image this module produces — so the
exact same pixels can be rendered headless to a PNG for visual verification
(``python island_render.py`` writes one PNG per state to a temp folder).

PERFORMANCE: the expensive, mostly-static parts (soft shadow, gold glow halo,
glass-gradient body, rim, top highlight) are rendered ONCE and cached per
(width, style, rim-colour); each frame only the cheap animated foreground (dot,
ripple, waveform, bouncing dots, sheen, label, timer, hint) is drawn over a copy
of that cached background, then the whole thing is downsampled with LANCZOS for
smooth anti-aliasing. This keeps a live ~22 fps frame well under a few ms.
"""

import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from branding import C, MODE_COLORS

# ---- geometry (logical px; the renderer multiplies by SCALE internally) -----
# COMPACT pill (owner v4: "reduce the pill to ~half its size · clean it up"):
# noticeably smaller than the old 46-tall capsule, matching the UX-Pilot
# island-preview proportions. The label is drawn CENTRED in the pill (owner:
# "centre 'Listening' precisely — it leans right"); zones balance around it.
SCALE = 2                 # supersample factor for crisp AA, downscaled at the end
WIN_W, WIN_H = 460, 64    # fixed window canvas; the pill is centred inside it
PILL_H = 34               # capsule height (was 46 — ~half the area)
CORNER = PILL_H // 2      # full-capsule ends
PAD_X = 14                # inner left/right padding of the pill
GAP = 8                   # gap between pill segments (dot · anim · label · …)
DOT_R = 3.6               # status-dot radius
PILL_MIN_W = 118
MARGIN = 13               # min clear space from the window edge (room for glow)
# The right zone uses a fixed-width timer template so the waveform bars never
# shift as the clock ticks. The monospace font ensures constant per-char width;
# we reserve space for a worst-case M:SS timer (5 chars = "99:59").
_TIMER_TEMPLATE = "00:00"  # 5 monospace chars — the right zone never resizes

# warm glass body gradient (top → bottom) and the rim / highlight hairlines
_GLASS_TOP = "#1B1813"
_GLASS_BOT = "#0B0A09"
_PILL_DK = "#0C0B09"      # the deep pill colour dots/bars blend out of
_RIM = "#3A3320"          # default warm rim
_RIM_HI = "#6A6047"       # inner top highlight hairline
_TEXT_DIM = C.text_dim
_TEXT_MUTE = C.text_mute

_FONT_CACHE = {}
_BG_CACHE = {}            # (pw, enhanced, rim) -> (bg_img, mask, geom)
_BG_ORDER = []
_GLOW_CACHE = {}          # color -> small radial glow sprite


def _font(kind, size):
    size = int(size)
    key = (kind, size)
    f = _FONT_CACHE.get(key)
    if f is not None:
        return f
    # Try a list of FULL font paths for each weight, in preference order. The
    # Windows Segoe paths are listed first (harmless when absent) so a Windows
    # build is byte-identical; macOS and Linux candidates follow; Pillow's
    # bundled default is the final backstop so the island can never fail to
    # render on any platform.
    win = os.environ.get("WINDIR", r"C:\Windows")
    winf = lambda n: os.path.join(win, "Fonts", n)
    mac = "/System/Library/Fonts"
    mac_sup = "/System/Library/Fonts/Supplemental"
    lin = "/usr/share/fonts"
    candidates = {
        "regular": [
            winf("segoeui.ttf"),
            os.path.join(mac, "HelveticaNeue.ttc"),
            os.path.join(mac_sup, "Arial.ttf"),
            f"{lin}/truetype/dejavu/DejaVuSans.ttf",
            f"{lin}/truetype/noto/NotoSans-Regular.ttf",
            f"{lin}/truetype/liberation/LiberationSans-Regular.ttf",
            f"{lin}/dejavu/DejaVuSans.ttf",
            f"{lin}/TTF/DejaVuSans.ttf",
        ],
        "semibold": [
            winf("seguisb.ttf"), winf("segoeui.ttf"),
            os.path.join(mac, "HelveticaNeue.ttc"),
            os.path.join(mac_sup, "Arial Bold.ttf"),
            f"{lin}/truetype/dejavu/DejaVuSans-Bold.ttf",
            f"{lin}/truetype/noto/NotoSans-Medium.ttf",
            f"{lin}/truetype/liberation/LiberationSans-Bold.ttf",
            f"{lin}/dejavu/DejaVuSans-Bold.ttf",
            f"{lin}/TTF/DejaVuSans-Bold.ttf",
        ],
        "bold": [
            winf("segoeuib.ttf"), winf("seguisb.ttf"),
            os.path.join(mac_sup, "Arial Bold.ttf"),
            f"{lin}/truetype/dejavu/DejaVuSans-Bold.ttf",
            f"{lin}/truetype/noto/NotoSans-Bold.ttf",
            f"{lin}/truetype/liberation/LiberationSans-Bold.ttf",
            f"{lin}/dejavu/DejaVuSans-Bold.ttf",
            f"{lin}/TTF/DejaVuSans-Bold.ttf",
        ],
        "mono": [
            winf("consola.ttf"),
            os.path.join(mac, "Monaco.ttf"),
            os.path.join(mac_sup, "Courier New.ttf"),
            f"{lin}/truetype/dejavu/DejaVuSansMono.ttf",
            f"{lin}/truetype/liberation/LiberationMono-Regular.ttf",
            f"{lin}/dejavu/DejaVuSansMono.ttf",
            f"{lin}/TTF/DejaVuSansMono.ttf",
        ],
    }.get(kind, [
        winf("segoeui.ttf"),
        f"{lin}/truetype/dejavu/DejaVuSans.ttf",
    ])
    f = None
    for path in candidates:
        try:
            f = ImageFont.truetype(path, size)
            break
        except Exception:
            f = None
    if f is None:
        try:
            f = ImageFont.load_default()
        except Exception:
            f = None
    _FONT_CACHE[key] = f
    return f


# ---- colour helpers ---------------------------------------------------------
def _hex(h):
    if isinstance(h, (tuple, list)):
        return (int(h[0]), int(h[1]), int(h[2]))
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _blend(c1, c2, t):
    """Blend two colours (hex strings or RGB tuples) → a '#rrggbb' string."""
    a, b = _hex(c1), _hex(c2)
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rgba(c, a=255):
    c = _hex(c)
    return (c[0], c[1], c[2], int(a))


def _lighten(c, t=0.42):
    """A lighter tint of a colour (toward warm white) — same hue, brighter. Used
    for the in-hue label shimmer so a mode's colour shimmers within itself rather
    than sweeping toward gold (owner v6: each mode reads strongly as its colour)."""
    return _blend(c, "#FFF6E0", t)


def _text_w(draw, text, font):
    if not text or font is None:
        return 0
    try:
        b = draw.textbbox((0, 0), text, font=font)
        return b[2] - b[0]
    except Exception:
        return int(len(text) * font.size * 0.55) if font else 0


# bar opacity profile from the UX-Pilot waveform (swells toward the middle)
_WAVE_PROFILE = (0.55, 0.75, 1.0, 1.0, 0.85, 0.65, 0.45)

_MEASURE = ImageDraw.Draw(Image.new("RGBA", (4, 4)))


def _anim_span(state):
    """Logical width of the animation zone, matching what render() draws."""
    return {"listening": 27, "search": 27,
            "transcribing": 16, "building": 26,
            "done": 13}.get(state, 0)


# fonts used by both the measurer and the renderer (kept in lock-step)
def _lab_f():
    return _font("bold", 12 * SCALE)


def _sub_f():
    return _font("regular", 10 * SCALE)


def _mono_f():
    return _font("mono", 11 * SCALE)   # was 10 — the timer read "too small" (owner v5)


SEP_PREFIX = 13       # logical gap+separator+gap before a right-zone text
DOT_ANIM_GAP = 15     # logical gap between the status dot and the waveform/anim


def _pill_h(snap):
    """The pill's logical height — a single consistent PILL_H for every state."""
    return PILL_H


def _measure_zones(snap):
    """Measure the LEFT (dot + animation) and RIGHT (timer / hint / offline)
    zones and the label, all in supersampled px — so the label can be drawn
    dead-centre with the two zones balanced around it (owner: "centre
    'Listening' precisely · it leans right").

    The right zone uses a fixed-width timer template (_TIMER_TEMPLATE) so the
    waveform bars stay rock-steady regardless of which digits the clock shows."""
    s = SCALE
    state = snap.get("state", "idle")
    label = snap.get("label", "")
    hint = snap.get("hint", "")
    timer = snap.get("timer", "")
    offline = snap.get("offline", False)
    dr = DOT_R * s
    animW = _anim_span(state) * s
    # extra breathing room between the status dot and the waveform/animation so
    # the dot's glow never touches the "sound icon" (owner v4 §Left-Side).
    left_w = dr * 2 + (DOT_ANIM_GAP * s + animW if animW else 0)
    label_w = _text_w(_MEASURE, label, _lab_f()) if label else 0
    right_w = 0.0
    if timer:
        # Fixed-width zone: reserve space for the widest possible M:SS timer so
        # the waveform never shifts. The monospace font keeps per-character width
        # constant; we measure a 5-char template ("00:00") to cover "99:59".
        right_w = SEP_PREFIX * s + _text_w(_MEASURE, _TIMER_TEMPLATE, _mono_f())
    elif hint:
        right_w = SEP_PREFIX * s + _text_w(_MEASURE, hint, _sub_f())
    if offline:
        right_w += 9 * s
    return left_w, label_w, right_w


def _content_w(snap):
    """Supersampled width of the ACTUAL content (left zone + gap + label + gap +
    right zone), counting a gap only between zones that exist. The pill hugs THIS
    — no symmetric padding, so a state with a left zone but no right zone (e.g.
    'Building · Prompt') doesn't leave dead space on the right (owner v6)."""
    s = SCALE
    left_w, label_w, right_w = _measure_zones(snap)
    lgap = GAP * s
    w = left_w
    if label_w:
        w += (lgap if left_w else 0) + label_w
    if right_w:
        w += lgap + right_w
    return w


def pill_width(snap):
    """Logical pill width = the content width hugged (+ inner padding), clamped to
    the min pill width and the canvas. The content block is centred inside it."""
    w = _content_w(snap) / SCALE + 2 * PAD_X + 4
    return int(max(PILL_MIN_W, min(WIN_W - 2 * MARGIN, w)))


def _glow_sprite(color):
    """A small soft radial glow sprite (for the listening dot), cached per colour."""
    sp = _GLOW_CACHE.get(color)
    if sp is not None:
        return sp
    s = SCALE
    R = int(DOT_R * 2.3 * s)   # tighter than before so it never bleeds into the wave
    g = Image.new("RGBA", (R * 2, R * 2), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([R * 0.5, R * 0.5, R * 1.5, R * 1.5],
                              fill=_rgba(color, 135))
    g = g.filter(ImageFilter.GaussianBlur(R * 0.3))
    _GLOW_CACHE[color] = g
    return g


def _resize_premult(img, w, h):
    """Downscale an RGBA image WITHOUT edge fringing.

    Resizing a STRAIGHT-alpha RGBA image resamples colour and alpha
    independently, so along the pill's bottom edge the warm rim/bloom colour of
    near-transparent pixels bleeds across the alpha boundary and LANCZOS ringing
    on the alpha leaves an ultra-thin bright hairline — visible only over light
    backgrounds (the owner's "thin white line at the bottom" artifact), gone over
    dark ones. Premultiplying BEFORE the resize ties colour to alpha so the edge
    stays clean; we un-premultiply afterwards so the caller (overlay's
    UpdateLayeredWindow path) still receives a straight-alpha image to premultiply
    as it already does."""
    a = np.asarray(img, dtype=np.float32)
    al = a[:, :, 3:4] / 255.0
    a[:, :, :3] *= al                                       # → premultiplied
    pm = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")
    pm = pm.resize((w, h), Image.LANCZOS)
    out = np.asarray(pm, dtype=np.float32)
    oa = out[:, :, 3:4] / 255.0
    rgb = out[:, :, :3]
    np.divide(rgb, oa, out=rgb, where=oa > 0)               # → straight alpha
    out[:, :, :3] = np.clip(rgb, 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def _build_bg(pw, rim_c, pill_h=PILL_H):
    """Render (and cache) the ONE island's static glass background (owner v6:
    there are no longer Basic/Standard/Enhanced tiers — a single premium look):
    soft drop shadow + a soft gold bloom halo + glass-gradient body + a subtle
    state-tinted edge. Returns (bg, mask, geom).

    The pill's BOTTOM edge is anchored at its fixed PILL_H height."""
    key = (pw, rim_c, pill_h)
    cached = _BG_CACHE.get(key)
    if cached is not None:
        return cached
    s = SCALE
    W, H = WIN_W * s, WIN_H * s
    pw_s, ph = pw * s, pill_h * s
    px0 = (W - pw_s) // 2
    # Anchor the bottom at the default pill's bottom; grow upward for a taller pill.
    py1 = (H + PILL_H * s) // 2
    py0 = py1 - ph
    px1 = px0 + pw_s
    rad = CORNER * s
    cy = (py0 + py1) / 2
    geom = (px0, py0, px1, py1, rad, cy)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # soft drop shadow (always).
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [px0, py0 + 3 * s, px1, py1 + 5 * s], radius=rad, fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(6 * s))
    img.alpha_composite(sh)

    # A SOFT gold bloom behind the pill (a heavily-blurred, low-alpha FILL, so it
    # reads as a gentle halo of light, NOT a hard outline ring). Always present —
    # this is the single premium island look.
    bloom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(bloom).rounded_rectangle(
        [px0 - 3 * s, py0 - 3 * s, px1 + 3 * s, py1 + 3 * s], radius=rad,
        fill=_rgba(_blend(rim_c, C.gold, 0.6), 46))
    bloom = bloom.filter(ImageFilter.GaussianBlur(9 * s))
    img.alpha_composite(bloom)

    # glass body: vertical gradient (smoothstep, gold-tinted top) masked to capsule
    ys = np.linspace(0.0, 1.0, ph)
    te = ys * ys * (3 - 2 * ys)
    top, bot, gold = (np.array(_hex(_GLASS_TOP), float),
                      np.array(_hex(_GLASS_BOT), float),
                      np.array(_hex(C.gold), float))
    base = top[None, :] + (bot - top)[None, :] * te[:, None]
    tint = ((1.0 - ys) ** 2 * 0.07)[:, None]
    rows = np.clip(base * (1 - tint) + gold[None, :] * tint, 0, 255).astype("uint8")
    arr = np.concatenate([rows[:, None, :],
                          np.full((ph, 1, 1), 250, "uint8")], axis=2)
    grad = Image.fromarray(arr, "RGBA").resize((W, ph))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([px0, py0, px1, py1],
                                           radius=rad, fill=255)
    body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    body.paste(grad, (0, py0))
    img.paste(body, (0, 0), mask)

    # SUBTLE edge — a LIT-FROM-ABOVE hairline, NOT a uniform ring. A flat rim
    # reads as a hard, "heavier" bright LINE along the BOTTOM edge: the warm
    # stroke contrasts maximally against the dark pill underside + drop shadow
    # there, while everywhere else the gold-tinted glass top and bloom hide it
    # (owner v6: "thin white line along the bottom… heavier than the rest of the
    # border"). So the rim is brightest along the TOP arc (a faint warm highlight,
    # the premium glass "light from above") and FADES to near-nothing at the
    # bottom — no bottom line, no inconsistency. A strong, clearly-visible outline
    # is still reserved for the ARMED / Prompt indicator (drawn in render()).
    rim_cov = Image.new("L", (W, H), 0)
    ImageDraw.Draw(rim_cov).rounded_rectangle(
        [px0, py0, px1 - 1, py1 - 1], radius=rad, outline=255, width=max(1, s))
    cov = np.asarray(rim_cov, dtype=np.float32) / 255.0            # stroke coverage
    yy = np.clip((np.arange(H, dtype=np.float32) - py0) /
                 max(1.0, float(py1 - py0)), 0.0, 1.0)             # 0=top … 1=bottom
    afac = 0.15 + 0.95 * (1.0 - yy) ** 1.6                         # bright top → faint bottom
    lift = (1.0 - yy) ** 2 * 0.5                                   # warm top highlight
    base_rgb = np.array(_hex(rim_c), np.float32)
    hi_rgb = np.array(_hex(_RIM_HI), np.float32)
    rim_rgb = base_rgb[None, :] * (1 - lift)[:, None] + hi_rgb[None, :] * lift[:, None]
    rim_arr = np.zeros((H, W, 4), np.uint8)
    for ch in range(3):
        rim_arr[:, :, ch] = np.clip(rim_rgb[:, ch], 0, 255).astype(np.uint8)[:, None]
    rim_arr[:, :, 3] = np.clip(cov * (70.0 * afac)[:, None], 0, 255).astype(np.uint8)
    img.alpha_composite(Image.fromarray(rim_arr, "RGBA"))

    _BG_CACHE[key] = (img, mask, geom)
    _BG_ORDER.append(key)
    while len(_BG_ORDER) > 8:
        _BG_CACHE.pop(_BG_ORDER.pop(0), None)
    return _BG_CACHE[key]


def _gold_absorb(d, geom, frame, n=16):
    """Gold motes STREAM inward from around the pill's perimeter and are absorbed
    at its edge — the context-gathering motion (owner v6: it must visibly MOVE
    toward the inside). Each mote is a short directional streak (faint outer tail →
    bright inner head) travelling from ~16px outside the capsule boundary to the
    edge, shrinking + fading as it's absorbed. The tail makes the inward direction
    obvious even in a single frame. Cheap: n cos/sin streaks per frame, no filters."""
    s = SCALE
    px0, py0, px1, py1, rad, cy = geom
    cx = (px0 + px1) / 2.0
    rx = (px1 - px0) / 2.0
    ry = (py1 - py0) / 2.0
    period = 38.0
    gr, gg, gb = _hex(C.gold_hi)
    for i in range(n):
        ang = 2 * math.pi * i / n + frame * 0.02       # gentle swirl
        # flight progress 0→1; +frame each tick → the motes keep moving inward
        p = ((frame + i * period / n) % period) / period
        ca, sa = math.cos(ang), math.sin(ang)
        bx = cx + rx * ca                              # boundary point on the
        by = cy + ry * sa                              # capsule-ish ellipse
        a = int(215 * math.sin(math.pi * p))           # fade in, absorbed at edge
        if a <= 6:
            continue
        out_head = 15 * s * (1.0 - p)                  # head: outside → edge
        out_tail = out_head + 6.5 * s                  # tail trails further out
        hx, hy = bx + ca * out_head, by + sa * out_head
        tx, ty = bx + ca * out_tail, by + sa * out_tail
        d.line([(tx, ty), (hx, hy)], fill=(gr, gg, gb, int(a * 0.45)),
               width=max(1, int(1.2 * s)))            # inward streak (tail→head)
        rr = 1.5 * s * (0.55 + 0.45 * (1.0 - p))       # head shrinks as absorbed
        d.ellipse([hx - rr, hy - rr, hx + rr, hy + rr], fill=(gr, gg, gb, a))


def render(snap):
    """Render one island frame to an RGBA Image (WIN_W×WIN_H logical, SCALE× AA).

    snapshot keys (all optional): frame, state, level(0..1), armed, suggest,
      offline, gathering, label, hint, timer, dot, rim, label_color,
      colors[hex...] (mode ramp for building/done)."""
    s = SCALE
    reduced_motion = bool(snap.get("reduced_motion", False))
    frame = 0 if reduced_motion else snap.get("frame", 0)
    # There is ONE island now (owner v6) — no Basic/Standard/Enhanced tiers. The
    # full premium look (gold bloom + travelling sheen + listening ripple) always
    # renders.
    state = snap.get("state", "idle")
    level = max(0.0, min(1.0, snap.get("level", 0.0)))
    armed = snap.get("armed", False)
    offline = snap.get("offline", False)
    # `gathering` = context is being pulled in (a History/Deck job): the island
    # shows the gold-particle ABSORPTION motion instead of a dedicated colour.
    gathering = snap.get("gathering", False)
    fade = max(0.0, min(1.0, snap.get("fade", 1.0)))   # whole-pill dissolve
    label = snap.get("label", "")
    hint = snap.get("hint", "")
    timer = snap.get("timer", "")
    dot_c = snap.get("dot", C.gold)
    rim_c = snap.get("rim", _RIM)
    label_c = snap.get("label_color", _TEXT_DIM)
    colors = snap.get("colors") or [dot_c]

    pw = pill_width(snap)
    bg, mask, geom = _build_bg(pw, rim_c, PILL_H)
    px0, py0, px1, py1, rad, cy = geom
    W, H = WIN_W * s, WIN_H * s
    img = bg.copy()
    d = ImageDraw.Draw(img)
    lab_f, sub_f, mono_f = _lab_f(), _sub_f(), _mono_f()
    # ALL island text shares ONE baseline so the label, timer and hint visually
    # line up (owner v5: each was centred on its OWN bbox, so "Listening"'s 'g'
    # descender made its body sit high vs the timer). The baseline sits a touch
    # below the geometric centre so the glyph bodies are vertically centred.
    base_y = cy + 0.30 * lab_f.size

    # ---- travelling glass sheen (always — the one premium look, clipped) ----
    if not reduced_motion:
        span = (px1 - px0) + 120 * s
        sx = px0 - 60 * s + ((frame * 3.0 * s) % span)
        sheen_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sheen_img)
        bandw = 18 * s
        for k in range(-bandw, bandw, 2 * s):
            a = int(20 * (1 - abs(k) / bandw))
            xx = int(sx + k)
            sd.line([(xx + 10 * s, py0), (xx - 10 * s, py1)],
                    fill=(255, 252, 240, a), width=2 * s)
        sheen_img.putalpha(Image.composite(sheen_img.getchannel("A"),
                                           Image.new("L", (W, H), 0), mask))
        img.alpha_composite(sheen_img)

    # ---- gold-particle ABSORPTION (context gathering) — gold motes stream in
    # from around the pill's perimeter and are drawn INTO it, fading as they're
    # absorbed: "information is being gathered and fed into the system" (owner v6,
    # replacing the old dedicated context colour). Cheap: ~14 cos/sin dots/frame.
    if gathering and not reduced_motion and state in ("building", "transcribing"):
        _gold_absorb(d, geom, frame)

    # ---- PROMPT-MODE indicator (the Big Shift): when the sticky Prompt toggle is
    # ON, ring the pill in its vivid PURPLE so it's unmistakable you're crafting a
    # prompt (not the old "right-shift held" gold ring — that input mechanism is
    # gone; `armed` now mirrors the persistent Prompt toggle). The quiet rest-state
    # rim is kept so this pops. ----
    if armed and state in ("listening", "transcribing", "building"):
        ap = 0.62 + 0.38 * (math.sin(frame * 0.20) + 1) / 2
        # ITEM 3: ring in the ACTIVE mode's colour (echoes the mode deck above),
        # defaulting to Prompt purple for the plain Prompt toggle.
        _pc = snap.get("armed_color") or MODE_COLORS["prompt"]
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle(
            [px0 - 2 * s, py0 - 2 * s, px1 + 2 * s, py1 + 2 * s], radius=rad,
            outline=_rgba(_pc, int(150 * ap)), width=4 * s)
        glow = glow.filter(ImageFilter.GaussianBlur(3 * s))
        img.alpha_composite(glow)
        d.rounded_rectangle([px0, py0, px1 - 1, py1 - 1], radius=rad,
                            outline=_rgba(_pc, int(235 * ap)), width=2 * s)

    # ===================== CONTENT (left→right, block-centred) =====================
    # Lay the content out left→right (dot+anim · label · timer/hint) and centre the
    # WHOLE block in the pill. Because the pill hugs the content (pill_width), there
    # is no phantom padding: "Listening · 0:04" balances naturally (both zones), and
    # "Building · Prompt" is compact (no empty right zone) — owner v6.
    left_w, label_w, right_w = _measure_zones(snap)
    lgap = GAP * s
    content_w = _content_w(snap)
    lx = (px0 + px1) / 2.0 - content_w / 2.0      # block start (centred)
    cursor = lx + left_w
    if label_w:
        cursor += (lgap if left_w else 0)
        label_left = cursor
        cursor += label_w
    else:
        label_left = cursor
    if right_w:
        cursor += lgap
    rx = cursor

    # ---- status dot (+ glow + ripple while listening), first in the left zone
    dr = DOT_R * s
    dotx = lx + dr
    pulse = 0.55 + 0.45 * (math.sin(frame * 0.16) + 1) / 2
    if state in ("listening", "search"):
        gs = _glow_sprite(dot_c)
        img.alpha_composite(gs, (int(dotx - gs.width / 2), int(cy - gs.height / 2)))
        rp = (frame % 36) / 36.0
        rr = dr + 2 * s + rp * 7 * s
        # The expanding "listening" ripple. Drawn as a FILLED ANNULUS (an outer disc
        # with an inner hole punched out) — NOT ImageDraw.ellipse(outline=…, width=…).
        # PIL's wide-outline rasteriser lays down UNEVEN stroke coverage around the
        # curve, and the 2×→1× downscale turns that into a visibly lighter/darker arc
        # (owner: "one section of the white ring is a different shade"). Two FILLED
        # ellipses at a single alpha give a perfectly uniform-thickness ring. The hole
        # is punched on a private layer so it never erases the dot glow underneath.
        ring_w = max(1.0, s)
        R = int(rr + ring_w + 2)
        if not reduced_motion:
            ripple = Image.new("RGBA", (2 * R, 2 * R), (0, 0, 0, 0))
            rd = ImageDraw.Draw(ripple)
            a_ring = int(170 * (1 - rp))
            rd.ellipse([R - rr - ring_w / 2, R - rr - ring_w / 2,
                        R + rr + ring_w / 2, R + rr + ring_w / 2],
                       fill=_rgba(dot_c, a_ring))
            rd.ellipse([R - rr + ring_w / 2, R - rr + ring_w / 2,
                        R + rr - ring_w / 2, R + rr - ring_w / 2], fill=(0, 0, 0, 0))
            img.alpha_composite(ripple, (int(round(dotx - R)), int(round(cy - R))))
        d.ellipse([dotx - dr, cy - dr, dotx + dr, cy + dr], fill=_rgba(dot_c))
    else:
        # keep the dot strongly its own colour even at the pulse trough (owner v6:
        # don't blend so far toward near-black that the mode hue reads muddy).
        dot_col = _rgba(_blend(_PILL_DK, dot_c, 0.62 + 0.38 * pulse))
        d.ellipse([dotx - dr, cy - dr, dotx + dr, cy + dr], fill=dot_col)

    # ---- animation zone (right after the dot, with extra breathing room) ----
    ax = lx + 2 * dr + DOT_ANIM_GAP * s
    if state in ("listening", "search"):
        n, bw, gap = 7, 2.6 * s, 4.5 * s
        maxh = (PILL_H - 14) * s
        wave_color = dot_c if state == "search" else C.gold_hi
        for i in range(n):
            ph_i = (math.sin(frame * 0.4 + i * 0.8) + 1) / 2
            hh = 3 * s + (maxh - 3 * s) * level * (0.5 + 0.5 * ph_i)
            xi = ax + i * gap
            col = _rgba(_blend(_PILL_DK, wave_color,
                               _WAVE_PROFILE[i] * (0.55 + 0.45 * ph_i)))
            d.rounded_rectangle([xi - bw / 2, cy - hh / 2, xi + bw / 2,
                                 cy + hh / 2], radius=bw / 2, fill=col)
    elif state == "transcribing":
        n, gap, r = 3, 8 * s, 2.4 * s
        for i in range(n):
            sv = math.sin(frame * 0.45 - i * 0.9)
            ph_i = (sv + 1) / 2
            yo = -3 * s * max(0.0, sv)
            col = _rgba(_blend(_PILL_DK, C.amber, 0.55 + 0.45 * ph_i))
            xi = ax + i * gap
            d.ellipse([xi - r, cy - r + yo, xi + r, cy + r + yo], fill=col)
    elif state == "building":
        n, gap, r = 5, 6.5 * s, 2.3 * s
        for i in range(n):
            ph_i = (math.sin(frame * 0.26 - i * 0.7) + 1) / 2
            # A single mode hue (no rainbow). A multi-colour ramp is only used
            # when colors[] genuinely carries >1 mode (a true hybrid transition);
            # at rest each mode is strongly ITS colour, not blended toward black.
            if len(colors) >= 2:
                t = i / (n - 1)
                idx = min(int(t * (len(colors) - 1)), len(colors) - 2)
                lt = (t * (len(colors) - 1)) - idx
                base = _blend(colors[idx], colors[idx + 1], lt)
            else:
                base = colors[0]
            col = _rgba(_blend(_PILL_DK, base, 0.58 + 0.42 * ph_i))
            xi = ax + i * gap
            d.ellipse([xi - r, cy - r, xi + r, cy + r], fill=col)
    elif state == "done":
        ac = colors[0] if colors else dot_c
        lw = max(2, int(2.2 * s))
        d.line([(ax, cy + 1 * s), (ax + 4 * s, cy + 4.5 * s)],
               fill=_rgba(ac), width=lw, joint="curve")
        d.line([(ax + 4 * s, cy + 4.5 * s), (ax + 12 * s, cy - 5 * s)],
               fill=_rgba(ac), width=lw, joint="curve")

    # ---- label (left-anchored at label_left, baseline-aligned) ----
    if label:
        if state == "building":
            # shimmer WITHIN the mode's own hue (mode colour ↔ a lighter tint of
            # itself), not toward gold — so e.g. Prompt reads strongly PURPLE
            # while it builds, instead of being swept to gold (owner v6).
            mc = colors[0] if colors else dot_c
            _gradient_text(img, label, lab_f, label_left, base_y,
                           mc, _lighten(mc, 0.5), frame)
        else:
            d.text((label_left, base_y), label, font=lab_f,
                   fill=_rgba(label_c), anchor="ls")

    # ---- right zone: separator + timer / hint (right-aligned), then offline ----
    rxx = rx
    if timer:
        # Fixed-width right zone — the waveform never shifts because the zone
        # size is computed from a static template (_TIMER_TEMPLATE), not the
        # live timer text. The actual timer is right-aligned within that zone
        # so the separator bar is always in the same position.
        d.line([(rxx + 5 * s, cy - 5 * s), (rxx + 5 * s, cy + 5 * s)],
               fill=_rgba(_RIM, 200), width=max(1, s))
        timer_w = _text_w(d, timer, mono_f)
        zone_w = _text_w(_MEASURE, _TIMER_TEMPLATE, mono_f)  # fixed zone width
        timer_x = rxx + 13 * s + zone_w - timer_w             # right-aligned
        d.text((timer_x, base_y), timer, font=mono_f,
               fill=_rgba(C.gold), anchor="ls")
        rxx += SEP_PREFIX * s + zone_w
    elif hint:
        d.line([(rxx + 5 * s, cy - 5 * s), (rxx + 5 * s, cy + 5 * s)],
               fill=_rgba(_RIM, 200), width=max(1, s))
        hc = label_c if state == "hint" else _TEXT_MUTE
        d.text((rxx + 13 * s, base_y), hint, font=sub_f, fill=_rgba(hc), anchor="ls")
        rxx += SEP_PREFIX * s + _text_w(d, hint, sub_f)
    if offline:
        oxc = rxx + 4 * s
        d.ellipse([oxc - 3 * s, cy - 3 * s, oxc + 3 * s, cy + 3 * s],
                  outline=_rgba(_TEXT_DIM, 220), width=max(1, s))

    # ---- prompt-mode "✦" indicator (subtle, inline, right after content) ----
    # When the sticky Prompt toggle is ON, a faint "✦" glyph sits at the far
    # right of the pill so you can see Prompt mode without a separate bar.
    # The purple ring (above) is the primary indicator; this is a secondary cue.
    if armed and state in ("listening", "transcribing", "building"):
        _pc = snap.get("armed_color") or MODE_COLORS["prompt"]
        ap = 0.55 + 0.45 * (math.sin(frame * 0.18) + 1) / 2
        prom_x = rxx + 6 * s
        d.text((prom_x, base_y), "✦", font=sub_f,
               fill=_rgba(_pc, int(180 * ap)), anchor="ls")

    if s != 1:
        img = _resize_premult(img, WIN_W, WIN_H)   # premultiplied: no bottom hairline
    if fade < 0.999:
        # dissolve the WHOLE pill (shadow, glow, body, content) uniformly
        img.putalpha(img.getchannel("A").point(lambda v: int(v * fade)))
    return img


# ---- companion rail ---------------------------------------------------------
# Status and controls are separate native windows so the first can stay click-
# through. Visually this is a quiet rail docked below the island: direct mode
# chips, the configured language, then Deck.
BAR_H = 40                # accessible 36-44px companion action surface
BAR_PAD_X = 12            # inner left/right padding of the control rail
BAR_MIN_W = 176           # enough room for the smallest useful control group
BAR_WIN_W = WIN_W         # same window width as the island (for alignment)
BAR_WIN_H = 54            # rail (40) + room for its restrained shadow
BAR_CORNER = BAR_H // 2
BAR_CHIP_PAD = 8          # inner padding inside a mode/language chip
BAR_CHIP_GAP = 3          # gap between adjacent mode chips
BAR_SEP_W = 13            # separator zone before the Deck action
BAR_GROUP_GAP = 7         # gap between related control groups
BAR_CHIP_H = 30           # ordinary mode chip height (logical)
BAR_STOP_CHIP_H = 36      # prominent labelled Stop action (Issue #23)


def _bar_font():
    return _font("semibold", 10 * SCALE)


def bar_layout(snap):
    """Return drawing and hit-test geometry for the companion control rail.

    The floating surface has only two direct processing lanes, so both stay
    visible. This removes the previous one-item dropdown/chevron state and makes
    the rail's width and click behaviour predictable. ``caret`` and ``expanded``
    remain in the result only for compatibility with older callers.
    """
    s = SCALE
    bf = _bar_font()
    modes = snap.get("modes") or [("prompt", "Prompt")]
    show_foreign = bool(snap.get("show_foreign"))
    correction_available = bool(snap.get("correction_available"))
    control_review = bool(snap.get("control_review_available"))
    stop_enabled = bool(snap.get("stop_enabled"))

    def w(txt):
        return _text_w(_MEASURE, txt, bf) / s   # logical text width

    if stop_enabled:
        stop_label = str(snap.get("stop_label") or "Stop")[:64]
        stop_w = w(stop_label) + 2 * BAR_CHIP_PAD + 10
        pill_w = max(BAR_MIN_W, stop_w + 2 * BAR_PAD_X)
        px0 = (BAR_WIN_W - pill_w) / 2.0
        stop = (px0 + BAR_PAD_X, px0 + BAR_PAD_X + stop_w)
        stop_bounds = (stop[0], BAR_WIN_H - BAR_H, stop[1], BAR_WIN_H)
        return {"pill": (px0, px0 + pill_w), "chips": [], "caret": None,
                "sep_x": None, "deck": None, "correction": None,
                "correction_dismiss": None, "foreign": None,
                "control_review": None, "control_cancel": None,
                "stop": stop, "stop_label": stop_label,
                "stop_bounds": stop_bounds,
                "stop_target_height": BAR_H, "processing_cancel": None,
                "expanded": False}

    if control_review:
        review_w = w("Review plan") + 2 * BAR_CHIP_PAD
        cancel_w = w("Cancel") + 2 * BAR_CHIP_PAD
        inner = review_w + BAR_GROUP_GAP + cancel_w
        pill_w = min(max(BAR_MIN_W, inner + 2 * BAR_PAD_X), BAR_WIN_W - 8)
        px0 = (BAR_WIN_W - pill_w) / 2.0
        cursor = px0 + BAR_PAD_X
        review = (cursor, cursor + review_w)
        cursor += review_w + BAR_GROUP_GAP
        cancel = (cursor, cursor + cancel_w)
        return {"pill": (px0, px0 + pill_w), "chips": [], "caret": None,
                "sep_x": None, "deck": None, "correction": None,
                "correction_dismiss": None, "foreign": None,
                "control_review": review,
                "control_cancel": cancel, "expanded": False}

    # A detected correction is a focused decision, not another permanent mode.
    # Remove unrelated dictation controls while it is pending and offer one clear
    # review path plus an explicit dismissal.
    if correction_available:
        review_w = w("Review") + 2 * BAR_CHIP_PAD
        dismiss_w = w("Dismiss") + 2 * BAR_CHIP_PAD
        inner = review_w + BAR_GROUP_GAP + dismiss_w
        pill_w = min(max(BAR_MIN_W, inner + 2 * BAR_PAD_X), BAR_WIN_W - 8)
        px0 = (BAR_WIN_W - pill_w) / 2.0
        cursor = px0 + BAR_PAD_X
        review = (cursor, cursor + review_w)
        cursor += review_w + BAR_GROUP_GAP
        dismiss = (cursor, cursor + dismiss_w)
        return {"pill": (px0, px0 + pill_w), "chips": [], "caret": None,
                "sep_x": None, "deck": None, "correction": review,
                "correction_dismiss": dismiss, "foreign": None,
                "control_review": None, "control_cancel": None,
                "expanded": False}

    deck_w = w("Deck") + 16
    language_label = str(snap.get("foreign_label") or "Language")[:18]
    foreign_w = (w(language_label) + 2 * BAR_CHIP_PAD + 9) if show_foreign else 0.0
    chip_ws = [(k, lbl, w(lbl) + 2 * BAR_CHIP_PAD) for k, lbl in modes]

    inner = sum(cw for _, _, cw in chip_ws) + BAR_CHIP_GAP * max(0, len(chip_ws) - 1)
    if show_foreign:
        inner += BAR_GROUP_GAP + foreign_w
    inner += BAR_SEP_W + deck_w
    pill_w = max(BAR_MIN_W, inner + 2 * BAR_PAD_X)
    pill_w = min(pill_w, BAR_WIN_W - 8)
    px0 = (BAR_WIN_W - pill_w) / 2.0

    cursor = px0 + BAR_PAD_X
    chips = []
    for k, _lbl, cw in chip_ws:
        chips.append((k, cursor, cursor + cw))
        cursor += cw + BAR_CHIP_GAP
    if chip_ws:
        cursor -= BAR_CHIP_GAP
    foreign = None
    if show_foreign:
        cursor += BAR_GROUP_GAP
        foreign = (cursor, cursor + foreign_w)
        cursor += foreign_w
    sep_x = cursor + BAR_SEP_W / 2.0
    cursor += BAR_SEP_W
    deck = (cursor, cursor + deck_w)
    return {"pill": (px0, px0 + pill_w), "chips": chips, "caret": None,
            "sep_x": sep_x, "deck": deck, "correction": None,
            "correction_dismiss": None, "foreign": foreign,
            "control_review": None, "control_cancel": None,
            "expanded": False}


def _finish_bar(img, snap):
    """Downsample and apply the island's entrance/exit opacity to the whole bar."""
    if SCALE != 1:
        img = _resize_premult(img, BAR_WIN_W, BAR_WIN_H)
    fade = max(0.0, min(1.0, float(snap.get("fade", 1.0))))
    if fade < 0.999:
        img.putalpha(img.getchannel("A").point(lambda value: int(value * fade)))
    return img


def render_bar(snap):
    """Render the companion mode-deck as a glass pill matching the island's design
    language. The rail sits at the bottom of its transparent canvas; overlay.py
    docks the visible rail just below the status island.

    snapshot keys:
      modes [(key,label)...]  — the processing modes to offer (default Prompt only)
      active (key | None)     — the currently-selected mode (None = plain dictation)
      foreign_on (bool)       — language-assist toggle state
      show_foreign (bool)     — whether the language toggle is shown at all
      foreign_label (str)     — configured language name/count
      correction_available    — show a short-lived, explicit correction review
      frame (int)             — accepted for snapshot compatibility
    Returns an RGBA Image (BAR_WIN_W × BAR_WIN_H logical, SCALE× AA)."""
    s = SCALE
    active = snap.get("active")
    foreign_on = bool(snap.get("foreign_on"))
    show_foreign = bool(snap.get("show_foreign"))
    correction_available = bool(snap.get("correction_available"))
    mode_labels = dict(snap.get("modes") or [("prompt", "Prompt")])

    layout = bar_layout(snap)
    lpx0, lpx1 = layout["pill"]

    W_c, H_c = BAR_WIN_W * s, BAR_WIN_H * s
    ph = BAR_H * s
    rad = BAR_CORNER * s
    px0, px1 = lpx0 * s, lpx1 * s
    py0, py1 = H_c - ph, H_c
    cy = (py0 + py1) / 2.0
    bf = _bar_font()
    base_y = cy + 0.30 * bf.size
    ch = BAR_CHIP_H * s / 2.0   # half chip height

    img = Image.new("RGBA", (W_c, H_c), (0, 0, 0, 0))

    # ---- restrained shadow: this rail should not compete with status ----
    sh = Image.new("RGBA", (W_c, H_c), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [px0, py0 - 1 * s, px1, py1], radius=rad, fill=(0, 0, 0, 105))
    sh = sh.filter(ImageFilter.GaussianBlur(3 * s))
    img.alpha_composite(sh)

    # ---- soft gold bloom behind the pill ----
    bloom = Image.new("RGBA", (W_c, H_c), (0, 0, 0, 0))
    ImageDraw.Draw(bloom).rounded_rectangle(
        [px0 - 2 * s, py0 - 2 * s, px1 + 2 * s, py1 + 2 * s], radius=rad,
        fill=_rgba(_blend(_RIM, C.gold, 0.45), 18))
    bloom = bloom.filter(ImageFilter.GaussianBlur(5 * s))
    img.alpha_composite(bloom)

    # ---- glass body (vertical gradient, gold-tinted top) ----
    ys = np.linspace(0.0, 1.0, ph)
    te = ys * ys * (3 - 2 * ys)
    top, bot, gold = (np.array(_hex(_GLASS_TOP), float),
                      np.array(_hex(_GLASS_BOT), float),
                      np.array(_hex(C.gold), float))
    cells = top[None, :] + (bot - top)[None, :] * te[:, None]
    tint = ((1.0 - ys) ** 2 * 0.06)[:, None]
    rows = np.clip(cells * (1 - tint) + gold[None, :] * tint, 0, 255).astype("uint8")
    arr = np.concatenate([rows[:, None, :],
                          np.full((ph, 1, 1), 236, "uint8")], axis=2)
    grad = Image.fromarray(arr, "RGBA").resize((W_c, ph))
    mask = Image.new("L", (W_c, H_c), 0)
    ImageDraw.Draw(mask).rounded_rectangle([px0, py0, px1, py1],
                                           radius=rad, fill=255)
    body = Image.new("RGBA", (W_c, H_c), (0, 0, 0, 0))
    body.paste(grad, (0, py0))
    img.paste(body, (0, 0), mask)

    # ---- lit-from-above rim (bright top, fades to bottom) ----
    rim_cov = Image.new("L", (W_c, H_c), 0)
    ImageDraw.Draw(rim_cov).rounded_rectangle(
        [px0, py0, px1 - 1, py1 - 1], radius=rad, outline=255, width=max(1, s))
    cov = np.asarray(rim_cov, dtype=np.float32) / 255.0
    yy = np.clip((np.arange(H_c, dtype=np.float32) - py0) /
                 max(1.0, float(py1 - py0)), 0.0, 1.0)
    afac = 0.12 + 0.95 * (1.0 - yy) ** 1.6
    lift = (1.0 - yy) ** 2 * 0.5
    base_rgb = np.array(_hex(_RIM), np.float32)
    hi_rgb = np.array(_hex(_RIM_HI), np.float32)
    rim_rgb = base_rgb[None, :] * (1 - lift)[:, None] + hi_rgb[None, :] * lift[:, None]
    rim_arr = np.zeros((H_c, W_c, 4), np.uint8)
    for cc in range(3):
        rim_arr[:, :, cc] = np.clip(rim_rgb[:, cc], 0, 255).astype(np.uint8)[:, None]
    rim_arr[:, :, 3] = np.clip(cov * (65.0 * afac)[:, None], 0, 255).astype(np.uint8)
    img.alpha_composite(Image.fromarray(rim_arr, "RGBA"))

    d = ImageDraw.Draw(img)

    def _chip(x0, x1, color, on, label, dim_fg, icon=None):
        """One selectable chip: filled+ringed in `color` when on, quiet text else.
        Label centred in the chip; returns nothing."""
        cx0, cx1 = x0 * s, x1 * s
        if on:
            fillimg = Image.new("RGBA", (W_c, H_c), (0, 0, 0, 0))
            ImageDraw.Draw(fillimg).rounded_rectangle(
                [cx0, cy - ch, cx1, cy + ch], radius=ch, fill=_rgba(color, 42))
            img.alpha_composite(fillimg)
            ImageDraw.Draw(img).rounded_rectangle(
                [cx0, cy - ch, cx1, cy + ch], radius=ch,
                outline=_rgba(color, 205), width=max(1, s))
            fg = _rgba(_lighten(color, 0.4))
        else:
            fg = dim_fg
        lw = _text_w(_MEASURE, label, bf)
        icon_w = 9 * s if icon else 0
        tx = (cx0 + cx1) / 2 - (lw + icon_w) / 2 + icon_w
        if icon == "status":
            gx = tx - 5.5 * s
            gr = 2.2 * s
            if on:
                d.ellipse([gx - gr, cy - gr, gx + gr, cy + gr], fill=fg)
            else:
                d.ellipse([gx - gr, cy - gr, gx + gr, cy + gr],
                          outline=fg, width=max(1, s))
        ImageDraw.Draw(img).text((tx, base_y), label, font=bf, fill=fg, anchor="ls")

    if layout.get("control_review"):
        rx0, rx1 = layout["control_review"]
        cx0, cx1 = layout["control_cancel"]
        _chip(rx0, rx1, C.gold, True, "Review plan", _rgba(_TEXT_DIM, 205))
        _chip(cx0, cx1, "#DF655D", False, "Cancel", _rgba("#FFAAA4", 230))
        return _finish_bar(img, snap)

    if layout.get("stop"):
        sx0, sx1 = layout["stop"]
        ch = BAR_STOP_CHIP_H * s / 2.0
        _chip(
            sx0, sx1, "#DF655D", True, layout["stop_label"],
            _rgba("#FFAAA4", 230)
        )
        return _finish_bar(img, snap)

    if correction_available and layout.get("correction"):
        rx0, rx1 = layout["correction"]
        dx0, dx1 = layout["correction_dismiss"]
        _chip(rx0, rx1, C.gold, True, "Review", _rgba(_TEXT_DIM, 205))
        _chip(dx0, dx1, "#8C8171", False, "Dismiss", _rgba(_TEXT_MUTE, 220))
        return _finish_bar(img, snap)

    # ---- direct mode chips: no hidden dropdown state ----
    for key, lx0, lx1 in layout["chips"]:
        mc = MODE_COLORS.get(key, C.gold)
        _chip(lx0, lx1, mc, key == active, mode_labels.get(key, key.title()),
              _rgba(_TEXT_DIM, 205))

    # ---- optional Foreign toggle, grouped with dictation modes ----
    if show_foreign and layout["foreign"]:
        fx0, fx1 = layout["foreign"]
        language_label = str(snap.get("foreign_label") or "Language")[:18]
        _chip(fx0, fx1, MODE_COLORS.get("foreign", C.gold), foreign_on,
              language_label, _rgba(_TEXT_MUTE, 215), icon="status")

    # ---- separator before the Deck action ----
    sep_x = layout["sep_x"] * s
    d.line([(sep_x, cy - 7 * s), (sep_x, cy + 7 * s)],
           fill=_rgba(_RIM, 160), width=max(1, s))

    # ---- Deck action (gold, distinct from the selector chips) ----
    dx0, dx1 = layout["deck"]
    deck_label = "Deck"
    dlw = _text_w(_MEASURE, deck_label, bf)
    dcx = (dx0 + dx1) / 2 * s
    # a tiny drawn "stack" glyph to the left of the word (font glyphs render as
    # tofu on some builds, so draw it with primitives — always crisp)
    gx = dcx - dlw / 2 - 9 * s
    for row in range(3):
        gy = cy - 4 * s + row * 4 * s
        d.line([(gx, gy), (gx + 6 * s, gy)], fill=_rgba(C.gold, 220), width=max(1, s))
    d.text((dcx - dlw / 2, base_y), deck_label, font=bf, fill=_rgba(C.gold), anchor="ls")

    return _finish_bar(img, snap)


def _gradient_text(img, text, font, x, base_y, c1, c2, frame):
    """Draw `text` filled with an animated c1↔c2 horizontal sweep (the UX-Pilot
    background-clip:text shimmer), with its BASELINE at `base_y` so it lines up
    with the other island text. Returns the new x."""
    s = SCALE
    tw = _text_w(_MEASURE, text, font)
    if tw <= 0:
        return x
    asc, desc = font.getmetrics()
    th = asc + desc
    gw = tw + 4 * s
    tm = Image.new("L", (gw, th), 0)
    ImageDraw.Draw(tm).text((0, asc), text, font=font, fill=255, anchor="ls")  # baseline at y=asc
    off = (frame * 0.06) % 1.0
    xs = np.arange(gw) / max(1, tw) + off
    tt = np.abs((xs % 1.0) * 2 - 1)
    a, b = np.array(_hex(c1), float), np.array(_hex(c2), float)
    cols = np.clip(a[None, :] + (b - a)[None, :] * tt[:, None], 0, 255).astype("uint8")
    grad_arr = np.concatenate([cols[None, :, :].repeat(th, 0),
                               np.full((th, gw, 1), 255, "uint8")], axis=2)
    img.paste(Image.fromarray(grad_arr, "RGBA"),
              (int(x), int(base_y - asc)), tm)
    return x + tw


# ---- self-test: render every state to PNGs for visual verification ----------
def _demo():
    import tempfile
    out = os.path.join(tempfile.gettempdir(), "mumble_island_preview")
    os.makedirs(out, exist_ok=True)

    def rim(c, t=0.4):
        return _blend("#3A3320", c, t)

    snaps = {
        "idle": dict(state="idle", label="Idle", hint="Ctrl+Win to start",
                     dot=C.gold, rim="#3A3320", label_color=C.text_dim),
        "listening": dict(state="listening", label="Listening", timer="0:04",
                          level=0.85, dot=C.gold, rim=rim(C.gold),
                          label_color=C.gold),
        "listening_armed": dict(state="listening", label="Listening", timer="0:02",
                                level=0.6, armed=True, dot=C.gold,
                                rim=rim(C.gold), label_color=C.gold),
        "transcribing": dict(state="transcribing", label="Transcribing",
                             dot=C.amber, rim=rim(C.amber, 0.35), label_color=C.amber),
        "building_prompt": dict(state="building", label="Building · Prompt",
                                dot=MODE_COLORS["prompt"], colors=[MODE_COLORS["prompt"]],
                                rim=rim(MODE_COLORS["prompt"]),
                                label_color=MODE_COLORS["prompt"]),
        "building_email": dict(state="building", label="Building · Email",
                               dot=MODE_COLORS["email"], colors=[MODE_COLORS["email"]],
                               rim=rim(MODE_COLORS["email"]),
                               label_color=MODE_COLORS["email"]),
        "gathering": dict(state="building", label="Gathering · Email",
                          gathering=True, dot=MODE_COLORS["email"],
                          colors=[MODE_COLORS["email"]], rim=rim(MODE_COLORS["email"]),
                          label_color=MODE_COLORS["email"]),
        "done": dict(state="done", label="Pasted!", hint="Email",
                     colors=[MODE_COLORS["email"]], dot=MODE_COLORS["email"],
                     rim=rim(MODE_COLORS["email"]), label_color=MODE_COLORS["email"]),
        "saved": dict(state="done", label="Saved", hint="Reply",
                      colors=[MODE_COLORS["reply"]], dot=MODE_COLORS["reply"],
                      rim=rim(MODE_COLORS["reply"]), label_color=MODE_COLORS["reply"]),
        "done_offline": dict(state="done", label="Pasted!", hint="Text",
                             colors=[C.gold], dot=C.gold, offline=True,
                             rim=rim(C.gold), label_color=C.gold),
        "hint": dict(state="hint", label="Ctrl + Alt + D", dot=C.gold,
                     rim="#3A3320", label_color=C.gold_hi),
        "suggest": dict(state="hint", label="Which mode?", suggest=True,
                        dot=MODE_COLORS["prompt"], rim=rim(MODE_COLORS["prompt"]),
                        label_color=MODE_COLORS["prompt"]),
        "error": dict(state="error", label="No audio", hint="Check mic",
                      dot=C.red, rim=rim(C.red), label_color=C.red),
    }
    for name, snap in snaps.items():
        snap.setdefault("frame", 7)
        im = render(snap)
        tile = Image.new("RGBA", (WIN_W, WIN_H), (22, 22, 26, 255))
        tile.alpha_composite(im)
        tile.convert("RGB").save(os.path.join(out, name + ".png"))
    names = list(snaps)
    cols = 2
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (WIN_W * cols, WIN_H * rows), (18, 18, 22))
    for i, name in enumerate(names):
        sheet.paste(Image.open(os.path.join(out, name + ".png")),
                    ((i % cols) * WIN_W, (i // cols) * WIN_H))
    sheet.save(os.path.join(out, "_contact_sheet.png"))

    # Companion mode-deck renders for visual verification (ITEM 3/4).
    MODES = [("prompt", "Prompt"), ("email", "Email")]   # the real cloud lanes
    bar_cases = [
        ("bar_none", dict(modes=MODES, active=None, show_foreign=False)),
        ("bar_prompt", dict(modes=MODES, active="prompt", show_foreign=False)),
        ("bar_email", dict(modes=MODES, active="email", show_foreign=False)),
        ("bar_foreign", dict(modes=MODES, active="email",
                             show_foreign=True, foreign_on=True,
                             foreign_label="Arabic")),
        ("bar_correction", dict(correction_available=True)),
    ]
    bar_sheet = Image.new("RGB", (BAR_WIN_W, BAR_WIN_H * len(bar_cases)), (18, 18, 22))
    for i, (label, snap) in enumerate(bar_cases):
        snap["frame"] = 7
        im = render_bar(snap)
        tile = Image.new("RGBA", (BAR_WIN_W, BAR_WIN_H), (22, 22, 26, 255))
        tile.alpha_composite(im)
        tile.convert("RGB").save(os.path.join(out, label + ".png"))
        bar_sheet.paste(tile.convert("RGB"), (0, i * BAR_WIN_H))
    bar_sheet.save(os.path.join(out, "_bar_sheet.png"))

    # Actual docked composition: status first, quieter controls underneath.
    stack = Image.new("RGBA", (WIN_W, 92), (22, 22, 26, 255))
    stack.alpha_composite(render(dict(
        state="listening", label="Listening", timer="0:04", level=0.72,
        dot=C.gold, rim=rim(C.gold), label_color=C.gold, frame=7,
    )))
    rail_y = int((WIN_H + PILL_H) / 2 + 5 - (BAR_WIN_H - BAR_H))
    stack.alpha_composite(render_bar(dict(
        modes=MODES, active=None, show_foreign=True, foreign_on=False,
        foreign_label="Arabic", frame=7,
    )), (0, rail_y))
    stack.convert("RGB").save(os.path.join(out, "_stack_preview.png"))

    # Gathering motion: a few frames of the gold-particle absorption so the
    # motes-streaming-inward effect can be eyeballed across time.
    gsheet = Image.new("RGB", (WIN_W, WIN_H * 4), (18, 18, 22))
    for i, fr in enumerate((3, 14, 26, 38)):
        snap = dict(state="building", label="Gathering · Email", gathering=True,
                    dot=MODE_COLORS["email"], colors=[MODE_COLORS["email"]],
                    rim=rim(MODE_COLORS["email"]), label_color=MODE_COLORS["email"],
                    frame=fr)
        tile = Image.new("RGBA", (WIN_W, WIN_H), (22, 22, 26, 255))
        tile.alpha_composite(render(snap))
        gsheet.paste(tile.convert("RGB"), (0, i * WIN_H))
    gsheet.save(os.path.join(out, "_gathering_sheet.png"))
    print(out)


if __name__ == "__main__":
    _demo()
