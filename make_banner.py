"""Draws assets/banner.png, the image at the top of the README.

Uses the app's own palette and its tick-dial motif so the banner looks like the
thing it is advertising. Run directly to regenerate.
"""

import os

from PIL import Image, ImageDraw, ImageFont

import icon_art

W, H = 1280, 440
SUPERSAMPLE = 2  # drawn large and downsampled; PIL has no anti-aliased primitives

GROUND = (13, 12, 10)
INK = (232, 225, 210)
MUTED = (132, 124, 109)
ACCENT = (242, 140, 40)
RULE = (43, 38, 32)

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
BOLD = os.path.join(FONT_DIR, "segoeuib.ttf")
REGULAR = os.path.join(FONT_DIR, "segoeui.ttf")

DIAL_TICKS = 40
LIT_FRACTION = 0.38  # a partly-filled dial reads as "in progress", not "done"


def _dial(draw, cx, cy, radius, scale):
    """The same stepped tick ring the app draws for the day's total."""
    import math

    inner = radius * 0.80
    for i in range(DIAL_TICKS):
        angle = (i / DIAL_TICKS) * math.tau - math.pi / 2
        lit = i < round(DIAL_TICKS * LIT_FRACTION)
        x1, y1 = cx + math.cos(angle) * inner, cy + math.sin(angle) * inner
        x2, y2 = cx + math.cos(angle) * radius, cy + math.sin(angle) * radius
        draw.line(
            [(x1, y1), (x2, y2)],
            fill=ACCENT if lit else RULE,
            width=int(5 * scale),
        )


def build():
    scale = SUPERSAMPLE
    img = Image.new("RGB", (W * scale, H * scale), GROUND)
    draw = ImageDraw.Draw(img)

    title_font = ImageFont.truetype(BOLD, int(64 * scale))
    tag_font = ImageFont.truetype(REGULAR, int(28 * scale))
    meta_font = ImageFont.truetype(REGULAR, int(21 * scale))

    # Badge, vertically centred on the left
    badge_px = int(150 * scale)
    badge = icon_art.make_badge(badge_px)
    bx, by = int(96 * scale), (H * scale - badge_px) // 2
    img.paste(badge, (bx, by), badge)

    text_x = bx + badge_px + int(52 * scale)
    draw.text((text_x, int(150 * scale)), "Screen Timer", font=title_font, fill=INK)
    draw.text(
        (text_x, int(228 * scale)),
        "for Windows",
        font=title_font,
        fill=ACCENT,
    )
    draw.text(
        (text_x, int(316 * scale)),
        "Where did your day actually go?",
        font=tag_font,
        fill=MUTED,
    )

    # Large dial motif bleeding off the right edge, echoing the app's own dial
    _dial(draw, int(1140 * scale), int(H * scale / 2), int(150 * scale), scale)

    # Hairline along the bottom, the same divider language as the UI
    draw.line(
        [(0, H * scale - int(2 * scale)), (W * scale, H * scale - int(2 * scale))],
        fill=RULE,
        width=int(3 * scale),
    )

    draw.text(
        (int(96 * scale), H * scale - int(58 * scale)),
        "free  ·  open source  ·  no account  ·  nothing leaves your machine",
        font=meta_font,
        fill=MUTED,
    )

    out = img.resize((W, H), Image.LANCZOS)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "banner.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path)
    return path


if __name__ == "__main__":
    print("wrote", build())
