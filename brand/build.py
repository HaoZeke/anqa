#!/usr/bin/env python3
"""Build the anqa pack from the approved truck-art painting.

The bird is cropped from ``source/approved.jpg``. The decorated word is
``source/word-ornament.png``. The typeset wordmark is outlined Fira Sans
ExtraBold (vector).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source" / "approved.jpg"
SOURCE_WORD = ROOT / "source" / "word-ornament.png"
SVG = ROOT / "svg"
PNG = ROOT / "png"
FONT = ROOT / "fonts" / "FiraSans-ExtraBold.ttf"

INK = "#0B0D0C"
INK_RGB = (11, 13, 12)
CREAM_RGB = (244, 239, 230)
CYAN_RGB = (17, 203, 189)
WHITE_RGB = (255, 255, 255)

# Split between bird (above) and painted word (below) on the 1024 master.
WORD_SPLIT_Y = 630
EYE_CENTER = (611.0, 230.0)
EYE_RADIUS = 22.0
PAPER_T0 = 22.0
PAPER_T1 = 50.0


def load_rgba() -> np.ndarray:
    """Key the paper field and un-mix cream spill on the edge."""
    rgb = np.asarray(Image.open(SOURCE).convert("RGB"), dtype=np.float32)
    border = np.concatenate(
        [
            rgb[:12].reshape(-1, 3),
            rgb[-12:].reshape(-1, 3),
            rgb[:, :12].reshape(-1, 3),
            rgb[:, -12:].reshape(-1, 3),
        ]
    )
    paper = np.median(border, axis=0)
    dist = np.linalg.norm(rgb - paper, axis=2)
    alpha = np.clip((dist - PAPER_T0) / (PAPER_T1 - PAPER_T0), 0.0, 1.0)
    # JPEG speckle keys as faint ink; keep only large opaque islands.
    coarse = dist > 40.0
    keep = _large_islands(coarse, min_px=400)
    alpha = np.where(keep, alpha, 0.0)
    am = np.maximum(alpha, 1e-5)[..., None]
    unmixed = np.clip((rgb - (1.0 - alpha)[..., None] * paper) / am, 0, 255)
    rgba = np.zeros((*rgb.shape[:2], 4), dtype=np.uint8)
    rgba[..., :3] = unmixed.astype(np.uint8)
    rgba[..., 3] = np.round(alpha * 255).astype(np.uint8)
    return rgba


def _large_islands(binary: np.ndarray, min_px: int) -> np.ndarray:
    h, w = binary.shape
    labels = np.zeros((h, w), dtype=np.int32)
    lab = 0
    keep_ids: list[int] = []
    neigh = ((-1, 0), (1, 0), (0, -1), (0, 1))
    for y in range(h):
        for x in range(w):
            if not binary[y, x] or labels[y, x]:
                continue
            lab += 1
            stack = [(y, x)]
            labels[y, x] = lab
            count = 0
            while stack:
                cy, cx = stack.pop()
                count += 1
                for dy, dx in neigh:
                    ny, nx = cy + dy, cx + dx
                    if (
                        0 <= ny < h
                        and 0 <= nx < w
                        and binary[ny, nx]
                        and labels[ny, nx] == 0
                    ):
                        labels[ny, nx] = lab
                        stack.append((ny, nx))
            if count >= min_px:
                keep_ids.append(lab)
    if not keep_ids:
        raise SystemExit("isolation found no paint islands")
    return np.isin(labels, keep_ids)


def bbox_alpha(alpha: np.ndarray, y0: int, y1: int) -> tuple[int, int, int, int]:
    sl = alpha[y0:y1]
    ys, xs = np.where(sl > 12)
    if xs.size == 0:
        raise SystemExit(f"no opaque pixels in y={y0}:{y1}")
    return int(xs.min()), y0 + int(ys.min()), int(xs.max()) + 1, y0 + int(ys.max()) + 1


def crop_pad(
    rgba: np.ndarray, box: tuple[int, int, int, int], pad: int
) -> tuple[np.ndarray, tuple[int, int]]:
    x0, y0, x1, y1 = box
    h, w = rgba.shape[:2]
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w, x1 + pad)
    y1 = min(h, y1 + pad)
    return rgba[y0:y1, x0:x1].copy(), (x0, y0)


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr, "RGBA")


def trim_image(img: Image.Image, pad: int = 4) -> Image.Image:
    arr = np.asarray(img)
    ys, xs = np.where(arr[..., 3] > 8)
    if xs.size == 0:
        return img
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(img.width, int(xs.max()) + 1 + pad)
    y1 = min(img.height, int(ys.max()) + 1 + pad)
    return img.crop((x0, y0, x1, y1))


def save_png(img: Image.Image, name: str) -> Path:
    path = PNG / name
    img.save(path, "PNG", optimize=True)
    return path


def write_svg_wrapper(name: str, png_name: str, size: tuple[int, int]) -> None:
    w, h = size
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="anqa">\n'
        f"  <title>anqa</title>\n"
        f'  <image href="../png/{png_name}" width="{w}" height="{h}" '
        f'preserveAspectRatio="xMidYMid meet"/>\n'
        f"</svg>\n"
    )
    (SVG / name).write_text(svg)


def scale_to_width(img: Image.Image, width: int) -> Image.Image:
    if img.width == width:
        return img
    height = max(1, round(img.height * (width / img.width)))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def scale_to_height(img: Image.Image, height: int) -> Image.Image:
    if img.height == height:
        return img
    width = max(1, round(img.width * (height / img.height)))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def is_cyan(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (g > 140) & (b > 120) & (g > r + 30)


def is_ink(rgb: np.ndarray) -> np.ndarray:
    return rgb.max(axis=2) < 48


def eye_mask(shape: tuple[int, int], origin: tuple[int, int]) -> np.ndarray:
    h, w = shape
    yy, xx = np.ogrid[:h, :w]
    cx = EYE_CENTER[0] - origin[0]
    cy = EYE_CENTER[1] - origin[1]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= EYE_RADIUS**2


def remap_duo(rgba: np.ndarray) -> np.ndarray:
    out = rgba.copy()
    a = out[..., 3] > 12
    cyan = is_cyan(out[..., :3]) & a
    ink = a & ~cyan
    out[ink, :3] = INK_RGB
    out[cyan, :3] = CYAN_RGB
    return out


def remap_mono(rgba: np.ndarray, origin: tuple[int, int]) -> np.ndarray:
    out = rgba.copy()
    a = out[..., 3] > 12
    eye = eye_mask(out.shape[:2], origin) & is_cyan(out[..., :3])
    out[a, :3] = INK_RGB
    out[eye, 3] = 0
    return out


def remap_reverse(rgba: np.ndarray) -> np.ndarray:
    out = rgba.copy()
    a = out[..., 3] > 12
    ink = is_ink(out[..., :3]) & a
    out[ink, :3] = WHITE_RGB
    return out


def rounded_canvas(size: int, fill: tuple[int, int, int], radius: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=(*fill, 255)
    )
    return im


def paste_centered(
    base: Image.Image, mark: Image.Image, *, max_frac: float = 0.78
) -> Image.Image:
    box = int(base.width * max_frac)
    scaled = mark.copy()
    scaled.thumbnail((box, box), Image.Resampling.LANCZOS)
    x = (base.width - scaled.width) // 2
    y = (base.height - scaled.height) // 2
    out = base.copy()
    out.alpha_composite(scaled, (x, y))
    return out


def word_paths(
    text: str, size: float, origin: tuple[float, float]
) -> tuple[str, float]:
    font = TTFont(FONT)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    upem = font["head"].unitsPerEm
    scale = size / upem
    x, y = origin
    tracking = -0.04 * size
    chunks: list[str] = []
    for i, ch in enumerate(text):
        glyph = glyph_set[cmap[ord(ch)]]
        pen = SVGPathPen(glyph_set)
        glyph.draw(TransformPen(pen, Transform(scale, 0, 0, -scale, x, y)))
        chunks.append(f'<path d="{pen.getCommands()}"/>')
        adv = glyph.width * scale
        if i + 1 < len(text):
            adv += tracking
        x += adv
    return "\n".join(chunks), x - origin[0]


def write_svg(name: str, body: str, vb: tuple[int, int]) -> Path:
    w, h = vb
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="anqa">\n'
        f"  <title>anqa</title>\n"
        f"{body}\n"
        f"</svg>\n"
    )
    path = SVG / name
    path.write_text(svg)
    return path


def render_word(text: str, size: int) -> Image.Image:
    """Rasterize the wordmark from the packed ExtraBold file."""
    font = ImageFont.truetype(str(FONT), size)
    tracking = -0.04 * size
    widths: list[float] = []
    for i, ch in enumerate(text):
        box = font.getbbox(ch)
        adv = float(box[2] - box[0])
        if i + 1 < len(text):
            adv += tracking
        widths.append(adv)
    ascent, descent = font.getmetrics()
    pad = max(16, size // 20)
    width = int(sum(widths)) + pad * 2
    height = ascent + descent + pad * 2
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = float(pad)
    y = float(pad)
    for ch, adv in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=INK)
        x += adv
    return trim_image(img, pad=8)


def compose_horizontal(mark: Image.Image, word: Image.Image, gap: int) -> Image.Image:
    height = max(mark.height, word.height)
    width = mark.width + gap + word.width
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(mark, (0, (height - mark.height) // 2))
    canvas.alpha_composite(word, (mark.width + gap, (height - word.height) // 2))
    return canvas


def compose_stacked_clean(
    mark: Image.Image, word: Image.Image, gap: int
) -> Image.Image:
    width = max(mark.width, word.width)
    height = mark.height + gap + word.height
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(mark, ((width - mark.width) // 2, 0))
    canvas.alpha_composite(word, ((width - word.width) // 2, mark.height + gap))
    return canvas


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing approved painting: {SOURCE}")
    if not SOURCE_WORD.is_file():
        raise SystemExit(f"missing decorated word: {SOURCE_WORD}")
    if not FONT.is_file():
        raise SystemExit(f"missing wordmark font: {FONT}")
    PNG.mkdir(parents=True, exist_ok=True)
    SVG.mkdir(parents=True, exist_ok=True)

    rgba = load_rgba()
    alpha = rgba[..., 3]
    bird_box = bbox_alpha(alpha, 0, WORD_SPLIT_Y)
    head, _ = crop_pad(rgba, (520, 206, 766, 452), 8)

    bird, bird_origin = crop_pad(rgba, bird_box, 16)
    mark_img = to_image(bird)
    painted_word_img = Image.open(SOURCE_WORD).convert("RGBA")
    painted_word_img = trim_image(painted_word_img, pad=8)
    head_img = to_image(head)

    save_png(mark_img, "anqa-mark.png")
    save_png(scale_to_width(mark_img, mark_img.width * 2), "anqa-mark@2x.png")
    save_png(to_image(remap_duo(bird)), "anqa-mark-duo.png")
    save_png(to_image(remap_mono(bird, bird_origin)), "anqa-mark-mono.png")
    rev = to_image(remap_reverse(bird))
    rev_on_ink = Image.new("RGBA", rev.size, (*INK_RGB, 255))
    rev_on_ink.alpha_composite(rev)
    save_png(rev_on_ink, "anqa-mark-reverse.png")
    save_png(painted_word_img, "anqa-wordmark-ornament.png")

    wp, ww = word_paths("anqa", 72, (16, 78))
    write_svg(
        "anqa-wordmark.svg",
        f'  <g fill="{INK}">{wp}</g>',
        (int(ww) + 32, 100),
    )
    word_type = render_word("anqa", 720)
    save_png(word_type, "anqa-wordmark.png")

    mark_h = scale_to_height(mark_img, 520)
    word_h = scale_to_width(word_type, int(mark_h.width * 0.92))
    clean = compose_stacked_clean(mark_h, word_h, gap=36)
    save_png(clean, "anqa-lockup-stacked-clean.png")

    word_orn = scale_to_width(painted_word_img, int(mark_h.width * 0.98))
    stacked_img = compose_stacked_clean(mark_h, word_orn, gap=28)
    save_png(stacked_img, "anqa-lockup-stacked.png")

    mark_row = scale_to_height(mark_img, 280)
    word_row = scale_to_height(word_type, 72)
    horizontal = compose_horizontal(mark_row, word_row, gap=28)
    save_png(horizontal, "anqa-lockup-horizontal.png")

    light = paste_centered(rounded_canvas(1024, CREAM_RGB, 220), mark_img)
    dark_mark = to_image(remap_reverse(bird))
    dark = paste_centered(rounded_canvas(1024, INK_RGB, 220), dark_mark)
    save_png(light, "anqa-app-icon-1024.png")
    save_png(
        light.resize((512, 512), Image.Resampling.LANCZOS), "anqa-app-icon-512.png"
    )
    save_png(
        light.resize((256, 256), Image.Resampling.LANCZOS), "anqa-app-icon-256.png"
    )
    save_png(dark, "anqa-app-icon-dark-1024.png")

    fav_base = paste_centered(
        rounded_canvas(256, CREAM_RGB, 48), head_img, max_frac=0.86
    )
    save_png(fav_base.resize((64, 64), Image.Resampling.LANCZOS), "anqa-favicon-64.png")
    save_png(fav_base.resize((32, 32), Image.Resampling.LANCZOS), "anqa-favicon-32.png")
    save_png(fav_base.resize((16, 16), Image.Resampling.LANCZOS), "anqa-favicon-16.png")

    wrappers = [
        ("anqa-mark.svg", "anqa-mark.png", mark_img.size),
        ("anqa-mark-duo.svg", "anqa-mark-duo.png", mark_img.size),
        ("anqa-mark-mono.svg", "anqa-mark-mono.png", mark_img.size),
        ("anqa-mark-reverse.svg", "anqa-mark-reverse.png", rev_on_ink.size),
        ("anqa-lockup-stacked.svg", "anqa-lockup-stacked.png", stacked_img.size),
        ("anqa-lockup-stacked-clean.svg", "anqa-lockup-stacked-clean.png", clean.size),
        ("anqa-lockup-horizontal.svg", "anqa-lockup-horizontal.png", horizontal.size),
        (
            "anqa-wordmark-ornament.svg",
            "anqa-wordmark-ornament.png",
            painted_word_img.size,
        ),
        ("anqa-app-icon.svg", "anqa-app-icon-1024.png", (1024, 1024)),
        ("anqa-app-icon-dark.svg", "anqa-app-icon-dark-1024.png", (1024, 1024)),
        ("anqa-favicon.svg", "anqa-favicon-64.png", (64, 64)),
    ]
    for svg_name, png_name, size in wrappers:
        write_svg_wrapper(svg_name, png_name, size)


if __name__ == "__main__":
    main()
