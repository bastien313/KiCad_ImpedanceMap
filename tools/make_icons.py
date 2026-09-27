"""Génère les icônes PNG du plugin (24 et 48 px, thèmes clair et sombre) dans icons/."""

import pathlib

from PIL import Image, ImageDraw

OUT = pathlib.Path(__file__).resolve().parents[1] / "icons"


def draw(size: int, dark: bool, clear: bool = False) -> Image.Image:
    s = 4  # sur-échantillonnage
    S = size * s
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fg = (230, 230, 225, 255) if dark else (40, 40, 38, 255)
    lw = max(2, S // 12)
    # piste (trait horizontal) colorée par tronçons : bleu / gris / rouge
    y = int(S * 0.72)
    cols = [(42, 120, 214, 255), (160, 159, 152, 255), (227, 73, 72, 255)]
    for i, c in enumerate(cols):
        d.line([(int(S * (0.08 + 0.28 * i)), y), (int(S * (0.08 + 0.28 * (i + 1))), y)], fill=c, width=lw * 2)
    # « Z »
    x0, x1, y0, y1 = int(S * 0.22), int(S * 0.78), int(S * 0.12), int(S * 0.52)
    d.line([(x0, y0), (x1, y0), (x0, y1), (x1, y1)], fill=fg, width=lw, joint="curve")
    if clear:
        r = (int(S * 0.55), int(S * 0.45), int(S * 0.98), int(S * 0.98))
        d.ellipse(r, fill=(227, 73, 72, 255))
        m = int(S * 0.1)
        d.line([(r[0] + m, r[1] + m), (r[2] - m, r[3] - m)], fill=(255, 255, 255, 255), width=lw)
        d.line([(r[0] + m, r[3] - m), (r[2] - m, r[1] + m)], fill=(255, 255, 255, 255), width=lw)
    return im.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(exist_ok=True)
    for clear in (False, True):
        base = "impedance_map_clear" if clear else "impedance_map"
        for size in (24, 48):
            draw(size, False, clear).save(OUT / f"{base}_{size}.png")
            draw(size, True, clear).save(OUT / f"{base}_dark_{size}.png")
    print("icônes écrites dans", OUT)


if __name__ == "__main__":
    main()
