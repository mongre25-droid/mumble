#!/usr/bin/env python3
"""Generate Mumble's app icon — a gold waveform on a black tile, rendered at high
resolution and downscaled with LANCZOS so it stays crisp at every size."""

import os

from PIL import Image, ImageDraw

import branding


def _render(S):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = S * 0.05
    rad = int(S * 0.225)
    # warm-black rounded tile
    d.rounded_rectangle([m, m, S - m, S - m], radius=rad, fill=(13, 12, 10, 255))
    # subtle gold rim
    d.rounded_rectangle([m, m, S - m, S - m], radius=rad,
                        outline=(150, 122, 48, 255), width=max(1, int(S * 0.014)))
    # gold waveform
    bars = [0.34, 0.58, 0.95, 0.64, 0.40]
    n = len(bars)
    bw = S * 0.082
    total = S * 0.46
    gap = (total - n * bw) / (n - 1)
    x = (S - total) / 2 + bw / 2
    cy = S / 2
    gold = (212, 175, 55, 255)
    for h in bars:
        half = (S * 0.30) * h
        d.rounded_rectangle([x - bw / 2, cy - half, x + bw / 2, cy + half],
                            radius=bw / 2, fill=gold)
        x += bw + gap
    return img


def make_icon(path=None):
    path = path or branding.ICON_ICO
    # A caller may request a filename in the current directory. makedirs("")
    # raises FileNotFoundError even though no directory needs creating.
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    big = _render(1024).resize((256, 256), Image.LANCZOS)
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    big.save(path, format="ICO", sizes=sizes)
    try:
        big.save(os.path.join(branding.ASSETS_DIR, "mumble.png"))
    except Exception:
        pass
    return path


if __name__ == "__main__":
    print("icon written:", make_icon())
