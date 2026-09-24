"""Draws the Screen Timer app mark: a black badge with one bold blue arc — a
timer three-quarters of the way round. The arc brightens toward its start
and, at sizes big enough to see it, the badge gets a soft glow and a
lit top edge; at tray size it stays flat, where detail would only blur.

Run directly to (re)generate assets/icon.ico for the tray and the packaged .exe.
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter

from paths import user_data_path

SUPERSAMPLE = 4  # drawn large, downsampled — PIL has no anti-aliased primitives

# Black badge, brand blue 5AA3FF arc — same pairing as the in-app mark.
BADGE_COLOR = (0, 0, 0)
BADGE_TOP = (30, 30, 36)  # the badge lightens toward its top at large sizes
RING_COLOR = (90, 163, 255)
RING_LIGHT = (170, 212, 255)  # where the arc starts, at 12 o'clock
RING_DEEP = (52, 128, 255)  # where it ends, at 9 o'clock
DETAIL_FROM = 48  # below this, flat colours only

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


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def make_badge(size=256):
    """Returns an RGBA badge at the requested size."""
    canvas = size * SUPERSAMPLE
    detailed = size >= DETAIL_FROM

    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=canvas * CORNER_RATIO, fill=255
    )

    fill = Image.new("RGB", (canvas, canvas), BADGE_COLOR)
    if detailed:
        shade = ImageDraw.Draw(fill)
        for y in range(0, canvas, SUPERSAMPLE):
            t = min(1, y / (canvas * 0.75))
            shade.rectangle((0, y, canvas, y + SUPERSAMPLE), fill=_mix(BADGE_TOP, BADGE_COLOR, t))
    badge = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    badge.paste(fill, (0, 0), mask)

    ring = canvas * RING_DIAMETER_RATIO
    inset = (canvas - ring) / 2
    box = (inset, inset, canvas - inset, canvas - inset)
    stroke = round(canvas * RING_STROKE_RATIO)
    centre = canvas / 2
    mid_radius = ring / 2 - stroke / 2
    cap = stroke / 2

    arc = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    pen = ImageDraw.Draw(arc)
    span = ARC_END - ARC_START
    colour_at = (lambda t: _mix(RING_LIGHT, RING_DEEP, t)) if detailed else (lambda t: RING_COLOR)
    # Drawn as short overlapping pieces so the colour can run along the arc;
    # PIL has no gradient stroke.
    for step in range(span):
        pen.arc(box, ARC_START + step, ARC_START + step + 1.6, fill=colour_at(step / span), width=stroke)

    # Round caps: PIL's arc ends square, which at small sizes reads as a
    # rendering glitch. A dot on the stroke's centre line at each end rounds
    # them, matching the in-app SVG's stroke-linecap="round".
    for angle, t in ((ARC_START, 0), (ARC_END, 1)):
        x = centre + mid_radius * math.cos(math.radians(angle))
        y = centre + mid_radius * math.sin(math.radians(angle))
        pen.ellipse((x - cap, y - cap, x + cap, y + cap), fill=colour_at(t))

    if detailed:
        # A soft blue light under the arc, kept inside the badge.
        glow = arc.filter(ImageFilter.GaussianBlur(canvas * 0.05))
        glow.putalpha(glow.getchannel("A").point(lambda a: a * 55 // 100))
        clipped = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        clipped.paste(glow, (0, 0), mask)
        badge.alpha_composite(clipped)
    badge.alpha_composite(arc)

    if detailed:
        # A hairline of light along the top edge, fading out down the sides.
        rim = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        ImageDraw.Draw(rim).rounded_rectangle(
            (0, 0, canvas - 1, canvas - 1), radius=canvas * CORNER_RATIO,
            outline=(255, 255, 255, 60), width=max(SUPERSAMPLE, canvas // 128),
        )
        fade = Image.linear_gradient("L").resize((canvas, canvas)).point(lambda v: 255 - min(255, v * 2))
        rim.putalpha(Image.composite(rim.getchannel("A"), Image.new("L", rim.size, 0), fade))
        badge.alpha_composite(rim)

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
