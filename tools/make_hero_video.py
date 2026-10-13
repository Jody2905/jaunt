"""Draws the looping header video for Jaunt's home page, frame by frame, then encodes it.

    py tools/make_hero_video.py          (needs Pillow, numpy and ffmpeg)

The scene is an island morning: sky, a mango sun, a hill of painted houses, a
sailboat and a moving sea. Every movement repeats a whole number of times in
the 10-second loop, so the last frame flows straight back into the first with
no visible jump. The sky on the left is kept clear for the page's headline.

Output (in jaunt/web/static/video/):  hero.mp4 (H.264), hero.webm (VP9), hero.jpg (still)
"""

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1280, 720
SS = 2                                   # draw at double size, then shrink: smooth edges
FPS, SECONDS = 24, 10
FRAMES = FPS * SECONDS
OUT = Path(__file__).resolve().parent.parent / "jaunt" / "web" / "static" / "video"

INK = (18, 48, 90)
LAGOON = (46, 196, 198)
DEEP = (22, 120, 160)
HIBISCUS = (242, 84, 125)
MANGO = (255, 182, 39)
SHUTTER = (31, 138, 112)
HILL_FAR = (120, 196, 170)
WHITE = (255, 255, 255)
SKY_TOP, SKY_LOW = (143, 216, 230), (233, 248, 246)
HORIZON = 0.64                           # where sea meets sky, as a fraction of height


def sky_and_sea(t, w, h):
    """Background pixels with numpy: sky gradient, then a sea whose bands roll sideways."""
    y = np.linspace(0, 1, h)[:, None]
    x = np.linspace(0, 1, w)[None, :]
    img = np.zeros((h, w, 3), dtype=np.float32)
    sky = y / HORIZON
    for c in range(3):
        img[..., c] = SKY_TOP[c] + (SKY_LOW[c] - SKY_TOP[c]) * np.clip(sky, 0, 1)

    sea = np.clip((y - HORIZON) / (1 - HORIZON), 0, 1)
    sea_mask = (y >= HORIZON).astype(np.float32) * np.ones_like(x)
    # Gentle light bands: each wave pattern moves a whole number of wavelengths per loop
    bands = (0.5 + 0.5 * np.sin(2 * math.pi * (x * 9 - 1 * t) + sea * 40)) * (1 - sea) * 0.18
    for c in range(3):
        base = LAGOON[c] + (DEEP[c] - LAGOON[c]) * sea ** 0.8
        lit = base + (255 - base) * bands
        img[..., c] = img[..., c] * (1 - sea_mask) + lit * sea_mask
    return Image.fromarray(img.clip(0, 255).astype(np.uint8))


def wave_line(draw, t, s, y0, amp, length, cycles, colour, width, phase=0.0):
    """One crest line across the whole width, moving `cycles` wavelengths per loop."""
    pts = []
    for i in range(0, W * s + 1, 6):
        xx = i / s
        yy = y0 + amp * math.sin(2 * math.pi * (xx / length - cycles * t) + phase)
        pts.append((i, yy * s))
    draw.line(pts, fill=colour, width=width * s, joint="curve")


def cloud(draw, cx, cy, scale, s, colour):
    for dx, dy, r in ((-60, 8, 34), (-20, -10, 46), (30, -2, 40), (70, 10, 28), (0, 16, 36)):
        x, y, rr = (cx + dx * scale) * s, (cy + dy * scale) * s, r * scale * s
        draw.ellipse((x - rr, y - rr, x + rr, y + rr), fill=colour)


def house(draw, x, y, w, h, wall, roof, s):
    """A little painted house, (x, y) = bottom-left corner."""
    draw.rectangle((x * s, (y - h) * s, (x + w) * s, y * s), fill=wall)
    draw.polygon([((x - 4) * s, (y - h) * s), ((x + w / 2) * s, (y - h - h * 0.55) * s),
                  ((x + w + 4) * s, (y - h) * s)], fill=roof)
    draw.rectangle(((x + w * 0.35) * s, (y - h * 0.55) * s, (x + w * 0.65) * s, y * s), fill=INK)


def frame(n):
    t = n / FRAMES                        # 0 .. 1 over the loop
    s = SS
    img = sky_and_sea(t, W * s, H * s)
    d = ImageDraw.Draw(img)
    hy = H * HORIZON

    # Clouds drift left, exactly one lap of the wrap distance per loop
    wrap = W + 400
    for x0, y0, sc, speed in ((300, 120, 1.0, 1), (900, 70, 0.7, 1), (1150, 190, 0.85, 2)):
        cx = (x0 - speed * wrap * t) % wrap - 200
        cloud(d, cx, y0, sc, s, (250, 253, 253))

    # Sun: a slow bob and a breathing halo
    sx, sy = W * 0.84, H * 0.20 + 6 * math.sin(2 * math.pi * t)
    halo = 92 + 7 * math.sin(2 * math.pi * 2 * t)
    d.ellipse(((sx - halo) * s, (sy - halo) * s, (sx + halo) * s, (sy + halo) * s), fill=(255, 226, 150))
    d.ellipse(((sx - 68) * s, (sy - 68) * s, (sx + 68) * s, (sy + 68) * s), fill=MANGO)

    # Far hill, then the near island with its painted houses and a palm
    d.polygon([(W * 0.48 * s, hy * s), (W * 0.62 * s, (hy - 70) * s), (W * 0.72 * s, (hy - 50) * s),
               (W * 0.80 * s, hy * s)], fill=HILL_FAR)
    island = [(W * 0.58, hy)]
    for i in range(41):
        u = i / 40
        island.append((W * (0.58 + 0.44 * u), hy - 150 * math.sin(math.pi * min(1, u * 1.15)) ** 1.4))
    island.append((W * 1.02, hy))
    d.polygon([(x * s, y * s) for x, y in island], fill=SHUTTER)
    def ground(fx):
        u = (fx - 0.58) / 0.44
        return hy - 150 * math.sin(math.pi * min(1, u * 1.15)) ** 1.4

    for (fx, wall, roof, size) in ((0.685, HIBISCUS, INK, 1.0), (0.735, MANGO, HIBISCUS, 1.15),
                                   (0.79, WHITE, LAGOON, 0.95), (0.84, LAGOON, MANGO, 1.1),
                                   (0.885, HIBISCUS, WHITE, 0.9)):
        g = max(ground(fx), ground(fx + 30 * size / W)) + 6      # sit on the lower side of the slope
        house(d, W * fx, g, 30 * size, 24 * size, wall, roof, s)
    # Palm on the lower slope, swaying twice per loop
    px = W * 0.645
    py = ground(0.645) - 30
    sway = 5 * math.sin(2 * math.pi * 2 * t)
    d.line([(px * s, (py + 40) * s), ((px + 6 + sway * 0.4) * s, (py - 30) * s)], fill=INK, width=5 * s)
    top = (px + 6 + sway * 0.4, py - 30)
    for ang in (-160, -120, -60, -20, 200):
        a = math.radians(ang + sway)
        end = (top[0] + 46 * math.cos(a), top[1] + 30 * math.sin(a) + 12)
        mid = (top[0] + 26 * math.cos(a), top[1] + 20 * math.sin(a) - 4)
        d.line([(top[0] * s, top[1] * s), (mid[0] * s, mid[1] * s), (end[0] * s, end[1] * s)],
               fill=SHUTTER, width=7 * s, joint="curve")

    # Shoreline foam where the island meets the sea
    d.rectangle((W * 0.57 * s, (hy - 2) * s, W * s, (hy + 4) * s), fill=(220, 248, 244))

    # Sea crests, near ones moving faster
    for y0, amp, length, cycles, col, width, ph in (
            (hy + 30, 3, 140, 1, (200, 244, 240), 2, 0.0),
            (hy + 70, 5, 210, 2, (190, 240, 236), 3, 1.3),
            (hy + 125, 7, 300, 2, (170, 232, 230), 4, 2.1),
            (hy + 200, 10, 420, 3, (150, 222, 226), 5, 0.6)):
        wave_line(d, t, s, y0, amp, length, cycles, col, width, ph)

    # Sailboat riding the second crest
    bx = W * 0.40
    by = hy + 70 + 5 * math.sin(2 * math.pi * (bx / 210 - 2 * t) + 1.3) - 6
    tilt = 3 * math.cos(2 * math.pi * (bx / 210 - 2 * t) + 1.3)
    hull = [(bx - 62, by - tilt), (bx + 62, by + tilt), (bx + 44, by + 22 + tilt), (bx - 44, by + 22 - tilt)]
    d.polygon([(x * s, y * s) for x, y in hull], fill=INK)
    mast_x = bx - 4
    d.line([(mast_x * s, (by - 2) * s), ((mast_x + tilt * 2) * s, (by - 150) * s)], fill=INK, width=4 * s)
    d.polygon([((mast_x + 4) * s, (by - 148) * s), ((mast_x + 82) * s, (by - 14) * s),
               ((mast_x + 4) * s, (by - 14) * s)], fill=WHITE)
    d.polygon([((mast_x + 4) * s, (by - 40) * s), ((mast_x + 64) * s, (by - 40) * s),
               ((mast_x + 82) * s, (by - 14) * s), ((mast_x + 4) * s, (by - 14) * s)], fill=HIBISCUS)
    d.polygon([((mast_x - 4) * s, (by - 120) * s), ((mast_x - 58) * s, (by - 14) * s),
               ((mast_x - 4) * s, (by - 14) * s)], fill=(250, 250, 246))

    # Sparkles on the water: each one fades in and out once per loop, at its own time
    rng = np.random.default_rng(3)
    for _ in range(26):
        x, y = rng.uniform(0, W), rng.uniform(hy + 20, H - 10)
        ph = rng.uniform(0, 1)
        a = max(0.0, math.sin(2 * math.pi * (t + ph))) ** 3
        if a > 0.05:
            ln = 6 + 10 * a
            d.line([((x - ln) * s, y * s), ((x + ln) * s, y * s)], fill=(255, 255, 255), width=2 * s)

    return img.resize((W, H), Image.LANCZOS)


def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg isn't installed")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for n in range(FRAMES):
            frame(n).save(f"{tmp}/f{n:04d}.png")
            if n % 48 == 0:
                print(f"  drawing frame {n}/{FRAMES}")
        frame(0).filter(ImageFilter.SMOOTH).save(OUT / "hero.jpg", quality=84, optimize=True, progressive=True)
        common = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{tmp}/f%04d.png"]
        subprocess.run(common + ["-c:v", "libx264", "-preset", "slow", "-crf", "27", "-pix_fmt", "yuv420p",
                                 "-movflags", "+faststart", "-an", str(OUT / "hero.mp4")], check=True)
        subprocess.run(common + ["-c:v", "libvpx-vp9", "-crf", "38", "-b:v", "0", "-row-mt", "1",
                                 "-pix_fmt", "yuv420p", "-an", str(OUT / "hero.webm")], check=True)
    for f in ("hero.mp4", "hero.webm", "hero.jpg"):
        print(f"  {f}: {(OUT / f).stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
