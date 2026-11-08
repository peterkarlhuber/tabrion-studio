"""Derive the Tabrion Studio brand assets in this folder from Peter's logo master.

The "Tabrion STUDIO" lockup ``tabrion-studio-logo-master-2026-11-08.png`` (1046 x 154 px, RGB on white) is the
bottom lockup of the full Tabrion logo master ``tabrion-logo-master-2026-11-08.png``, cropped and cleaned by
``tabrion/assets/branding/build.py --studio`` (``tests/unit/test_branding.py`` checks that the two agree pixel for
pixel). The shared brand kit script ``derive_branding.py`` (vendored unchanged from ``nodestro-studio/scripts/``, like
``social_preview.py``) expects a 2048 x 640 transparent master with the symbol left and a pure black wordmark right,
plus the same artwork flattened on white. This wrapper prepares those two files, then does what the Tabrion wrapper
does: run ``derive_branding.py``, keep the files this repository uses, render the 1280 x 640 social preview and copy
the web assets to where the docs and the frontend serve them.

Preparing the transparent master follows ``tabrion/assets/branding/build.py`` step for step (nothing is redrawn;
every step is a pixel operation on the supplied artwork):

1. Clean the background: every pixel at or above ``BACKGROUND`` (the paper noise is 247-255) is white. The Studio
   lockup was cropped from the full master with a box that leaves out the faint ghost of old letters right of the
   top wordmark; ``studio_crop`` repeats that crop for the check.
2. Upscale with Lanczos so the lockup fills the 2048 x 640 canvas.
3. Knock out the white. The symbol as in Tabrion: each pixel takes the colour of the nearest solid area of its own
   layer and the alpha that reproduces its grey value over white. The wordmark is pure black with alpha, as
   ``derive_branding.py`` requires: "Tabrion" is opaque, the grey "STUDIO" (grey value about 69) is black at the alpha
   that gives the same grey over white (about 0.73). On dark surfaces the reversed wordmark keeps that difference:
   "Tabrion" white, "STUDIO" white at 73 %.

The gap between symbol and wordmark is narrower in the Studio lockup than in the other brands (about 51 px in the
2048-px master), so the brand kit's ``SPLIT_GAP`` is set to 40 px at run time, like the product and theme tables. The
largest gap inside the wordmark is about 24 px.

Run from the repository root with Pillow and NumPy::

    uv run --with pillow==12.3.0 --with numpy==2.5.3 python assets/branding/build.py
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DOCS = ROOT / "docs" / "assets" / "branding"
FRONTEND = ROOT / "frontend" / "public" / "branding"

sys.path.insert(0, str(HERE))
import derive_branding  # noqa: E402  (vendored next to this file)
import social_preview  # noqa: E402  (vendored next to this file)

PRODUCT = "tabrion-studio"
FULL_MASTER = HERE / "tabrion-logo-master-2026-11-08.png"
SUPPLIED = HERE / "tabrion-studio-logo-master-2026-11-08.png"

#: Crop box (left, top, right, bottom) of the Studio lockup in the full 1254 x 1254 master (as in tabrion's build.py).
STUDIO_LOCKUP = (94, 798, 1140, 952)
#: Paper white: the ghost letters and the background noise lie at 247-255.
BACKGROUND = 247
#: Solid areas are darker than this; the lightest layer is about 204, flat paper noise lies above 235.
SOLID_MAX = 230
#: Width of the lockup in the 2048 x 640 transparent master.
MASTER_WIDTH = 1900
#: Minimum gap (px, in the 2048-px master) between symbol and wordmark for ``derive_branding.split_column``.
SPLIT_GAP = 40

#: Brand kit registration, made at run time so the vendored scripts stay unchanged. Same values as Tabrion: the
#: symbol's black top layer vanishes on dark surfaces, so the on-dark variant lifts the dark tones (0.6, about 3:1 on
#: near-black, the three layers keep their order); favicons sit on a rounded white tile.
PRODUCT_CONFIG: derive_branding.Product = {"tile": (255, 255, 255), "lift": 0.6, "name": "Tabrion Studio"}
THEME: social_preview.Theme = {
    "top": (30, 32, 37),
    "bottom": (11, 12, 14),
    "glow": (150, 158, 172),
    "strength": 0.12,
    "text": (205, 208, 214),
}

#: Derived files kept in this folder.
KEPT = (
    "tabrion-studio-logo.png",
    "tabrion-studio-logo-on-dark.png",
    "tabrion-studio-mark-128.png",
    "tabrion-studio-mark-on-dark-128.png",
    "favicon.ico",
    "favicon-32.png",
)
#: Copies served by MkDocs (only files below docs/) and by the frontend (frontend/public/branding/, built into the
#: wheel). ``tests/unit/test_branding.py`` checks that the copies match.
DOCS_COPIES = (
    "tabrion-studio-logo.png",
    "tabrion-studio-logo-on-dark.png",
    "tabrion-studio-mark-on-dark-128.png",
    "favicon-32.png",
)
FRONTEND_COPIES = (
    "tabrion-studio-logo.png",
    "tabrion-studio-logo-on-dark.png",
    "tabrion-studio-mark-128.png",
    "tabrion-studio-mark-on-dark-128.png",
    "favicon.ico",
    "favicon-32.png",
)

#: One-line caption on the GitHub social preview.
SOCIAL_CAPTION = "Local developer tool for SQLite and PostgreSQL databases"


def _grey(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB")).astype(np.float64)


def _clean(rgb: np.ndarray) -> np.ndarray:
    out = rgb.copy()
    out[rgb.min(axis=2) >= BACKGROUND] = 255.0
    return out


def studio_crop() -> np.ndarray:
    """The Studio lockup cut from the full master and cleaned, as ``tabrion/assets/branding/build.py --studio``."""
    return _clean(_grey(Image.open(FULL_MASTER).crop(STUDIO_LOCKUP)))


def _solid(rgb: np.ndarray) -> np.ndarray:
    """Pixels inside a flat area of the artwork: not paper, and a 3 x 3 neighbourhood of nearly one grey value."""
    value = rgb.mean(axis=2)
    lo, hi = value.copy(), value.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            shifted = np.roll(np.roll(value, dy, 0), dx, 1)
            lo, hi = np.minimum(lo, shifted), np.maximum(hi, shifted)
    return (value < SOLID_MAX) & (hi - lo < 6.0)


def _nearest_solid_colour(rgb: np.ndarray, solid: np.ndarray, steps: int = 40) -> np.ndarray:
    """For every pixel, the colour of the nearest solid pixel (grown ring by ring, averaged per ring)."""
    colour = np.where(solid[..., None], rgb, 0.0)
    known = solid.astype(np.float64)
    for _ in range(steps):
        todo = known == 0
        if not todo.any():
            break
        acc = np.zeros_like(colour)
        cnt = np.zeros_like(known)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy or dx:
                    acc += np.roll(np.roll(colour * known[..., None], dy, 0), dx, 1)
                    cnt += np.roll(np.roll(known, dy, 0), dx, 1)
        fill = todo & (cnt > 0)
        colour[fill] = acc[fill] / cnt[fill][:, None]
        known[fill] = 1.0
    return colour


#: Grey-value bands of the solid areas: black (top layer, "Tabrion"), dark grey (middle layer, "STUDIO"), light grey.
LAYER_BANDS = ((0.0, 40.0), (40.0, 150.0), (150.0, SOLID_MAX))


def _layer_colours(rgb: np.ndarray, solid: np.ndarray) -> np.ndarray:
    """Solid pixels set to their layer's median colour, so the paper and chroma noise of the raster drops out."""
    value = rgb.mean(axis=2)
    out = rgb.copy()
    for low, high in LAYER_BANDS:
        layer = solid & (value >= low) & (value < high)
        if layer.any():
            out[layer] = np.median(rgb[layer], axis=0)
    return out


#: Width (px, in the upscaled lockup) of the anti-aliased edge between artwork and paper.
EDGE = 4


def _near(mask: np.ndarray, radius: int) -> np.ndarray:
    """``mask`` grown by ``radius`` pixels (square neighbourhood)."""
    grown = mask.copy()
    for _ in range(radius):
        step = grown.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                step |= np.roll(np.roll(grown, dy, 0), dx, 1)
        grown = step
    return grown


def knock_out(rgb: np.ndarray, wordmark_x: int) -> np.ndarray:
    """RGBA from artwork on white: alpha reproduces each pixel's grey over white, colour from its own shape.

    Symbol (left of ``wordmark_x``): every pixel takes the colour of the nearest solid area (the median colour of
    that layer) and the alpha that turns that colour over white into the pixel's own grey value; away from the paper
    the alpha is 1, so grey noise inside a layer does not turn into transparency. Wordmark: pure black, with the alpha
    that gives the pixel's grey over white; solid pixels use their word's median grey, so "STUDIO" gets one even
    alpha. Shapes and anti-aliasing come from the supplied pixels.
    """
    value = rgb.mean(axis=2)
    solid = _solid(rgb)
    layered = rgb.copy()
    # The symbol and the wordmark share grey bands ("STUDIO" and the middle layer), so each gets its own medians.
    layered[:, :wordmark_x] = _layer_colours(rgb[:, :wordmark_x], solid[:, :wordmark_x])
    layered[:, wordmark_x:] = _layer_colours(rgb[:, wordmark_x:], solid[:, wordmark_x:])

    base = _nearest_solid_colour(layered, solid)
    base_value = base.mean(axis=2)
    alpha = np.clip((BACKGROUND - value) / np.maximum(BACKGROUND - base_value, 1.0), 0.0, 1.0)
    interior = ~_near(value >= BACKGROUND, EDGE)
    alpha[:, :wordmark_x][interior[:, :wordmark_x]] = 1.0

    word = layered[:, wordmark_x:].mean(axis=2)
    word_solid = solid[:, wordmark_x:]
    black = float(np.median(word[word_solid & (word < LAYER_BANDS[0][1])]))
    alpha[:, wordmark_x:] = np.clip((BACKGROUND - word) / (BACKGROUND - black), 0.0, 1.0)
    base[:, wordmark_x:] = 0.0

    out = np.dstack([base, alpha * 255.0])
    out[alpha == 0] = 0.0
    return np.rint(out).astype(np.uint8)


def _gap_column(rgb: np.ndarray) -> int:
    """Middle of the first paper-only column run wider than SPLIT_GAP: the gap between symbol and wordmark."""
    ink = (rgb.min(axis=2) < BACKGROUND).any(axis=0)
    cols = np.where(ink)[0]
    gaps = np.diff(cols)
    i = int(np.argmax(gaps > SPLIT_GAP))
    return int(cols[i] + cols[i + 1]) // 2


def prepare_masters(out: Path) -> None:
    """Write ``tabrion-studio-logo-transparent.png`` and ``tabrion-studio-logo-white.png`` (2048 x 640)."""
    supplied = _clean(_grey(Image.open(SUPPLIED)))
    height = round(supplied.shape[0] * MASTER_WIDTH / supplied.shape[1])
    big = Image.fromarray(supplied.astype(np.uint8), "RGB").resize((MASTER_WIDTH, height), Image.Resampling.LANCZOS)
    big_rgb = _clean(_grey(big))
    rgba = Image.fromarray(knock_out(big_rgb, _gap_column(big_rgb)), "RGBA")
    rgba = rgba.crop(rgba.getbbox())
    canvas = Image.new("RGBA", (2048, 640), (0, 0, 0, 0))
    canvas.alpha_composite(rgba, ((2048 - rgba.width) // 2, (640 - rgba.height) // 2))
    canvas.save(out / f"{PRODUCT}-logo-transparent.png", optimize=True)
    derive_branding.flatten(canvas).save(out / f"{PRODUCT}-logo-white.png", optimize=True)


def main() -> int:
    derive_branding.PRODUCTS[PRODUCT] = PRODUCT_CONFIG
    derive_branding.SPLIT_GAP = SPLIT_GAP
    social_preview.THEMES[PRODUCT] = THEME

    if not np.array_equal(studio_crop(), _grey(Image.open(SUPPLIED))):
        print("the Studio lockup master is not the cleaned crop of the full master", file=sys.stderr)
        return 1
    prepare_masters(HERE)
    if derive_branding.main(["--product", PRODUCT, "--masters", str(HERE), "--check"]):
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        derived = ["--product", PRODUCT, "--masters", str(HERE), "--out", str(out), "--sets", "web,favicons"]
        if derive_branding.main(derived):
            return 1
        for name in KEPT:
            shutil.copyfile(out / name, HERE / name)

    # Social preview: the shared 1280 x 640 template (social_preview.py, the same in every product repository). The
    # lockup already says "STUDIO", so there is no extra tag.
    social_preview.compose(PRODUCT, HERE, SOCIAL_CAPTION, None).save(HERE / "social-preview.png", optimize=True)

    for target, names in ((DOCS, DOCS_COPIES), (FRONTEND, FRONTEND_COPIES)):
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copyfile(HERE / name, target / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
