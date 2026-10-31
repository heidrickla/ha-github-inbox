"""Generate the in-repo brand images.

Since Home Assistant 2026.3 a custom integration carries its own brand images and no pull request against
`home-assistant/brands` is needed. HACS reads `custom_components/<domain>/brand/` first.

Sizes:

    icon.png       256x256 exactly
    icon@2x.png    512x512 exactly
    logo.png       shortest side 128-256
    logo@2x.png    shortest side 256-512

The icons use brand/icon-source.png. The wide logos retain the tray and issue marks.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

BG = (0, 0, 0, 0)
PAD_FRAC = 0.06
# Glyphs (0.44 of the square) above the tray (0.50 to 0.90).
MARK_H_FRAC = 0.90
ISSUE = (57, 135, 229, 255)
PULL = (217, 89, 38, 255)
TRAY = (88, 100, 122, 255)
SLOT = (146, 160, 184, 255)


def ring(
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    width: int,
    fill: tuple[int, ...],
) -> None:
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=width)


def draw(size: tuple[int, int], pad_frac: float) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, BG)
    d = ImageDraw.Draw(img)
    side = min(w, h)
    pad = side * pad_frac
    b = side - 2 * pad
    x0 = (w - b) / 2
    y0 = (h - b * MARK_H_FRAC) / 2

    def x(f: float) -> float:
        return x0 + b * f

    def y(f: float) -> float:
        return y0 + b * f

    stroke = max(3, round(b * 0.05))

    # Open issue: a ring with a dot.
    r = b * 0.22
    ring(d, x(0.22), y(0.22), r, stroke, ISSUE)
    dot = b * 0.06
    d.ellipse([x(0.22) - dot, y(0.22) - dot, x(0.22) + dot, y(0.22) + dot], fill=ISSUE)

    # Pull request: a stem between two nodes, and a branch from a third node back over the top.
    node = b * 0.065
    lx, rx = x(0.60), x(0.935) - node
    top, bottom = y(0.0) + node, y(0.44) - node
    d.line([(lx, top + node), (lx, bottom - node)], fill=PULL, width=stroke)
    ring(d, lx, top, node, stroke, PULL)
    ring(d, lx, bottom, node, stroke, PULL)
    ring(d, rx, bottom, node, stroke, PULL)
    turn = y(0.10)
    d.line([(rx, bottom - node), (rx, turn)], fill=PULL, width=stroke)
    d.line([(rx + stroke / 2, turn), (lx + node * 2.4, turn)], fill=PULL, width=stroke)
    head = b * 0.07
    tip = lx + node * 1.6
    d.polygon(
        [(tip, turn), (tip + head, turn - head), (tip + head, turn + head)], fill=PULL
    )

    # The tray, with its slot.
    ty = y(0.50)
    d.rounded_rectangle([x(0.0), ty, x(1.0), y(0.90)], radius=b * 0.08, fill=TRAY)
    d.polygon(
        [(x(0.26), ty), (x(0.34), y(0.65)), (x(0.66), y(0.65)), (x(0.74), ty)],
        fill=SLOT,
    )
    return img


def main() -> None:
    out = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "custom_components",
        "github_inbox",
        "brand",
    )
    os.makedirs(out, exist_ok=True)
    specs = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
        "logo.png": (512, 256),
        "logo@2x.png": (1024, 512),
    }
    source = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "brand", "icon-source.png"
    )
    with Image.open(source) as original:
        approved_icon = original.convert("RGBA")
    for name, size in specs.items():
        image = (
            approved_icon.resize(size, Image.LANCZOS)
            if name.startswith("icon")
            else draw(size, pad_frac=PAD_FRAC)
        )
        image.save(os.path.join(out, name), "PNG")
        print(f"  {name:14s} {size[0]}x{size[1]}")

    # Checked against the published rules: the sizes, a transparent background (the corners), and a mark that
    # fills the square.
    ok = True
    for name, (want_w, want_h) in specs.items():
        with Image.open(os.path.join(out, name)) as im:
            w, h = im.size
            alpha = im.convert("RGBA").getchannel("A")
        if name.startswith("icon"):
            good = (w, h) == (want_w, want_h)
        else:
            short = min(w, h)
            good = (256 <= short <= 512) if "@2x" in name else (128 <= short <= 256)
        corners = [
            alpha.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
        ]
        transparent = all(a == 0 for a in corners)
        left, top, right, bottom = alpha.getbbox() or (0, 0, 0, 0)
        box = min(w, h) * (1 - 2 * PAD_FRAC)
        filled = (right - left) >= box - 2 and (bottom - top) >= box * MARK_H_FRAC - 2
        verdict = "OK" if good and transparent and filled else "FAILS THE RULE"
        print(
            f"  check {name:14s} {w}x{h} transparent={transparent} filled={filled} {verdict}"
        )
        ok &= good and transparent and filled
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
