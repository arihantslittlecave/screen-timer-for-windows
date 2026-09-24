"""Draws the Screen Timer app mark: a flat black badge with one bold blue
arc — a timer three-quarters of the way round.

Run directly to (re)generate assets/icon.ico for the tray and the packaged .exe.
"""

import math
import os

from PIL import Image, ImageDraw

from paths import user_data_path

SUPERSAMPLE = 4  # drawn large, downsampled — PIL has no anti-aliased primitives

# Flat black badge, brand blue 5AA3FF arc — same pairing as the in-app mark.
BADGE_COLOR = (0, 0, 0)
RING_COLOR = (90, 163, 255)

CORNER_RATIO = 0.235
RING_DIAMETER_RATIO = 0.60
# Heavy on purpose: the mark lives mostly at 16-24px (tray, taskbar,
# notifications), where a thin ring or a faint second tone blurs to mush.
RING_STROKE_RATIO = 0.135
# Degrees, clockwise from 3 o'clock: starts at 12, stops at 9 — a quarter
# gap, so it reads as time running rather than as a letter O.
ARC_START, ARC_END = -90, 180
ICO_SIZES = [16, 20, 24, 32, 48, 64, 128, 256]

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def make_badge(size=256):
    """Returns an RGBA badge at the requested size."""
    canvas = size * SUPERSAMPLE

    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=canvas * CORNER_RATIO, fill=255
    )

    badge = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    flat = Image.new("RGB", (canvas, canvas), BADGE_COLOR)
    badge.paste(flat, (0, 0), mask)

    ring = canvas * RING_DIAMETER_RATIO
    inset = (canvas - ring) / 2
    box = (inset, inset, canvas - inset, canvas - inset)
    stroke = round(canvas * RING_STROKE_RATIO)

    pen = ImageDraw.Draw(badge)
    pen.arc(box, ARC_START, ARC_END, fill=RING_COLOR, width=stroke)

    # Round caps: PIL's arc ends square, which at small sizes reads as a
    # rendering glitch. A dot on the stroke's centre line at each end rounds
    # them, matching the in-app SVG's stroke-linecap="round".
    centre = canvas / 2
    mid_radius = ring / 2 - stroke / 2
    cap = stroke / 2
    for angle in (ARC_START, ARC_END):
        x = centre + mid_radius * math.cos(math.radians(angle))
        y = centre + mid_radius * math.sin(math.radians(angle))
        pen.ellipse((x - cap, y - cap, x + cap, y + cap), fill=RING_COLOR)

    return badge.resize((size, size), Image.LANCZOS)


def write_ico(path=None):
    # Defaults to the persistent data dir, not assets/: the AUMID's IconUri
    # registry value outlives the process, and in a one-file build the bundle
    # dir it would otherwise point into is deleted on exit.
    path = path or user_data_path("icon.ico")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Each size is rendered at its own scale so small frames stay crisp rather
    # than being downscaled from one master by the ICO writer.
    frames = [make_badge(s) for s in ICO_SIZES]
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1])
    return path


def write_png(path=None, size=256):
    """A single flat PNG for consumers that load one image at face value —
    the toast notification and the AUMID registry IconUri — rather than
    picking a frame out of a multi-size .ico. Handing those a plain PNG is
    what keeps the notification badge crisp instead of upscaled from
    whichever frame Windows happened to grab."""
    path = path or user_data_path("icon.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    make_badge(size).save(path)
    return path


if __name__ == "__main__":
    ico = write_ico(os.path.join(_ASSETS_DIR, "icon.ico"))
    png = os.path.join(_ASSETS_DIR, "icon.png")
    make_badge(512).save(png)
    print(f"wrote {ico}\nwrote {png}")
