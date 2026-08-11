"""Draws assets/banner.png, the image at the top of the README.

Uses the app's own palette and tick-dial motif so the banner looks like the
thing it advertises. Every element is positioned by measuring it rather than
by hand-tuned constants, so the composition stays centred if the text changes.

Run directly to regenerate.
"""

import math
import os

from PIL import Image, ImageDraw, ImageFont

import icon_art

W, H = 1280, 400
SUPERSAMPLE = 2  # drawn large and downsampled; PIL has no anti-aliased primitives

GROUND = (13, 12, 10)
INK = (232, 225, 210)
MUTED = (125, 117, 102)
ACCENT = (242, 140, 40)
RULE = (38, 34, 28)

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
BOLD = os.path.join(FONT_DIR, "segoeuib.ttf")
REGULAR = os.path.join(FONT_DIR, "segoeui.ttf")

DIAL_TICKS = 40
LIT_FRACTION = 0.38  # partly filled reads as "in progress", not "done"


def _dial(draw, cx, cy, radius, width, rotate=0.0):
    """The same stepped tick ring the app draws for the day's total.

    `rotate` turns the whole ring so that on a dial bleeding off the right
    edge, the lit arc still falls on the visible side. Without it the two
    edge dials carry very different amounts of colour and the banner leans.
    """
    inner = radius * 0.80
    for i in range(DIAL_TICKS):
        angle = (i / DIAL_TICKS) * math.tau - math.pi / 2 + rotate
        lit = i < round(DIAL_TICKS * LIT_FRACTION)
        x1, y1 = cx + math.cos(angle) * inner, cy + math.sin(angle) * inner
        x2, y2 = cx + math.cos(angle) * radius, cy + math.sin(angle) * radius
        draw.line([(x1, y1), (x2, y2)], fill=ACCENT if lit else RULE, width=width)


def build():
    s = SUPERSAMPLE
    img = Image.new("RGB", (W * s, H * s), GROUND)
    draw = ImageDraw.Draw(img)

    title_f = ImageFont.truetype(BOLD, int(58 * s))
    tag_f = ImageFont.truetype(REGULAR, int(26 * s))
    meta_f = ImageFont.truetype(REGULAR, int(19 * s))

    # Decorative dials bleed off both edges, so the composition stays
    # symmetric instead of being weighted to one side.
    _dial(draw, int(-40 * s), int(H * s / 2), int(170 * s), int(5 * s))
    # Turned half a revolution so its lit arc faces inward, onto the canvas.
    _dial(draw, int((W + 40) * s), int(H * s / 2), int(170 * s), int(5 * s), rotate=math.pi)

    badge_px = int(112 * s)
    badge = icon_art.make_badge(badge_px)
    gap = int(34 * s)

    line1, line2 = "Screen Timer", "for Windows"
    w1 = draw.textlength(line1, font=title_f)
    w2 = draw.textlength(line2, font=title_f)
    text_w = max(w1, w2)

    # Centre the badge+text lockup as one unit
    lockup_w = badge_px + gap + text_w
    lockup_x = (W * s - lockup_w) / 2

    title_line_h = int(66 * s)
    tag_h = int(46 * s)
    block_h = title_line_h * 2 + tag_h
    # Centre the lockup in the space ABOVE the footer strip, not in the whole
    # canvas: centring against the full height leaves it sitting visibly low,
    # because the footer occupies the bottom band.
    footer_zone = int(78 * s)
    block_top = (H * s - footer_zone - block_h) / 2

    img.paste(badge, (int(lockup_x), int(block_top + (block_h - badge_px) / 2 - 6 * s)), badge)

    tx = lockup_x + badge_px + gap
    draw.text((tx, block_top), line1, font=title_f, fill=INK)
    draw.text((tx, block_top + title_line_h), line2, font=title_f, fill=ACCENT)

    tagline = "Where did your day actually go?"
    draw.text((tx, block_top + title_line_h * 2 + int(10 * s)), tagline, font=tag_f, fill=MUTED)

    # Meta strip, centred on the canvas rather than tucked in a corner
    meta = "free  ·  open source  ·  no account  ·  nothing leaves your machine"
    mw = draw.textlength(meta, font=meta_f)
    draw.text(((W * s - mw) / 2, H * s - int(46 * s)), meta, font=meta_f, fill=MUTED)

    out = img.resize((W, H), Image.LANCZOS)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "banner.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path)
    return path


if __name__ == "__main__":
    print("wrote", build())
