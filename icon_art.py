"""Draws the Screen Timer app mark: a rounded orange badge (brand F28C28)
with a white timer ring.

Run directly to (re)generate assets/icon.ico for the tray and the packaged .exe.
"""

import os

from PIL import Image, ImageDraw

from paths import user_data_path

MASTER = 1024
SUPERSAMPLE = 2  # drawn large, downsampled — PIL has no anti-aliased primitives

# Brand orange F28C28, bracketed +-15% toward white/black for the gradient.
ACCENT_TOP = (244, 157, 72)
ACCENT_BOTTOM = (206, 119, 34)

CORNER_RATIO = 0.235
RING_DIAMETER_RATIO = 0.60
RING_STROKE_RATIO = 0.125
ARC_START, ARC_END = -90, 170  # leaves a gap so the mark reads as progress, not a dot
TRACK_ALPHA = 90
TRACK_MIN_SIZE = 32  # below this the faint track muddies the glyph, so it's dropped
ICO_SIZES = [16, 20, 24, 32, 48, 64, 128, 256]

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _vertical_gradient(size, top, bottom):
    column = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        column.putpixel(
            (0, y),
            (
                round(top[0] + (bottom[0] - top[0]) * t),
                round(top[1] + (bottom[1] - top[1]) * t),
                round(top[2] + (bottom[2] - top[2]) * t),
            ),
        )
    return column.resize((size, size), Image.NEAREST)


def make_badge(size=256):
    """Returns an RGBA badge at the requested size."""
    canvas = size * SUPERSAMPLE

    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=canvas * CORNER_RATIO, fill=255
    )

    badge = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    badge.paste(_vertical_gradient(canvas, ACCENT_TOP, ACCENT_BOTTOM), (0, 0), mask)

    ring = canvas * RING_DIAMETER_RATIO
    inset = (canvas - ring) / 2
    box = (inset, inset, canvas - inset, canvas - inset)
    stroke = round(canvas * RING_STROKE_RATIO)

    # Drawn on its own layer so the semi-transparent track blends with the
    # gradient instead of punching through it.
    overlay = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)
    if size >= TRACK_MIN_SIZE:
        pen.ellipse(box, outline=(255, 255, 255, TRACK_ALPHA), width=stroke)
    pen.arc(box, ARC_START, ARC_END, fill=(255, 255, 255, 255), width=stroke)

    return Image.alpha_composite(badge, overlay).resize((size, size), Image.LANCZOS)


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


if __name__ == "__main__":
    ico = write_ico(os.path.join(_ASSETS_DIR, "icon.ico"))
    png = os.path.join(_ASSETS_DIR, "icon.png")
    make_badge(512).save(png)
    print(f"wrote {ico}\nwrote {png}")
