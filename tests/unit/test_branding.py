"""STU-005: the brand assets come from the supplied logo masters, and the docs and the frontend serve the same bytes.

``assets/branding/build.py`` derives every file; see ``assets/branding/README.md``. The sdist leaves out ``assets/``,
so these checks need a repository checkout.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRANDING = ROOT / "assets" / "branding"
DOCS = ROOT / "docs" / "assets" / "branding"
FRONTEND = ROOT / "frontend" / "public" / "branding"

pytestmark = pytest.mark.skipif(not BRANDING.is_dir(), reason="needs a repository checkout")

STUDIO_MASTER = "tabrion-studio-logo-master-2026-11-08.png"
FULL_MASTER = "tabrion-logo-master-2026-11-08.png"

# sha256 of the committed masters: Peter Karlhuber's files of 2026-11-08 with their metadata chunks removed. The
# copies must never change. The full master is byte-identical to tabrion/assets/branding/.
MASTER_SHA256 = {
    STUDIO_MASTER: "1ae73bfe3fc7b311357c2a4dfab0ff2d62cd2568c7b206cf3cd0a31c5bcd31ca",
    FULL_MASTER: "e253dbf74d714222e4c00c43d5475757c771af124844c66fc3ed6d5d8d796997",
}
# The supplied files (planning folder, logo/) keep their C2PA (caBX) chunk, the full master also an eXIf chunk; their
# sha256 are 3b66457ddc9b96a9cfdd6193e4e169004ee32acec07c54fb786e650dd41ecac9 (Studio) and
# b3e5f441c9e7200153f1d4b437100c72a3da4445f23ef7e0f52c9728850ff7e1 (full). These are the sha256 of their decompressed
# image data (all IDAT chunks): the stripped copies have the same pixels.
SUPPLIED_PIXELS_SHA256 = {
    STUDIO_MASTER: "f366762c955397e4681e0eb45d3d54330e7791f21607d221b246f6eb9c8f6b95",
    FULL_MASTER: "4342c95fbbe04af6a53a5ded28153969ebf6f092f245dcb6171c0adfc8a31e12",
}
ALLOWED_CHUNKS = {
    STUDIO_MASTER: {b"IHDR", b"IDAT", b"IEND"},
    FULL_MASTER: {b"IHDR", b"sRGB", b"IDAT", b"IEND"},
}

# sha256 of the brand kit scripts, vendored unchanged from nodestro-studio/scripts/ (the same as in tabrion).
VENDORED_SHA256 = {
    "derive_branding.py": "278b67212c204e445f351042025468e74846d36c26eac0c081f5238289484135",
    "social_preview.py": "93f2573af31be3f567ec5b000a6bf15fc6164e974e1df404db4398319f21e189",
}

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

#: Crop box of the Studio lockup in the full master and the paper-white threshold (assets/branding/build.py).
STUDIO_LOCKUP = (94, 798, 1140, 952)
BACKGROUND = 247


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chunks(path: Path) -> list[tuple[bytes, bytes]]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    chunks, i = [], 8
    while i < len(data):
        (length,) = struct.unpack(">I", data[i : i + 4])
        chunks.append((data[i + 4 : i + 8], data[i + 8 : i + 8 + length]))
        i += 12 + length
    return chunks


def _png_size(path: Path) -> tuple[int, int]:
    # PNG IHDR: width and height are the big-endian 32-bit integers at bytes 16..24.
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", path
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def _rgb_rows(path: Path) -> list[bytes]:
    """The unfiltered RGB rows of an 8-bit, non-interlaced RGB PNG (standard library only)."""
    chunks = _chunks(path)
    ihdr = next(body for kind, body in chunks if kind == b"IHDR")
    width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", ihdr)
    assert (depth, colour, interlace) == (8, 2, 0), path
    raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    stride = width * 3
    rows: list[bytes] = []
    previous = bytearray(stride)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for x in range(stride):
            left = line[x - 3] if x >= 3 else 0
            up = previous[x]
            corner = previous[x - 3] if x >= 3 else 0
            if kind == 1:
                line[x] = (line[x] + left) & 0xFF
            elif kind == 2:
                line[x] = (line[x] + up) & 0xFF
            elif kind == 3:
                line[x] = (line[x] + (left + up) // 2) & 0xFF
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else corner
                line[x] = (line[x] + predictor) & 0xFF
        rows.append(bytes(line))
        previous = line
    return rows


@pytest.mark.parametrize("name", sorted(MASTER_SHA256))
def test_the_logo_masters_are_unchanged(name: str) -> None:
    assert _sha256(BRANDING / name) == MASTER_SHA256[name]


@pytest.mark.parametrize("name", sorted(MASTER_SHA256))
def test_the_masters_carry_no_metadata_and_the_supplied_pixels(name: str) -> None:
    """Only the chunks needed to show the image; the image data is the supplied file's, byte for byte."""
    chunks = _chunks(BRANDING / name)
    assert {kind for kind, _ in chunks} == ALLOWED_CHUNKS[name]
    pixels = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    assert hashlib.sha256(pixels).hexdigest() == SUPPLIED_PIXELS_SHA256[name]


def test_the_studio_lockup_is_the_cleaned_crop_of_the_full_master() -> None:
    """The Studio master is the bottom lockup of the full master (the ghost letters lie outside the crop box)."""
    left, top, right, bottom = STUDIO_LOCKUP
    full = _rgb_rows(BRANDING / FULL_MASTER)
    studio = _rgb_rows(BRANDING / STUDIO_MASTER)
    assert len(studio) == bottom - top
    for y, row in enumerate(studio):
        source = full[top + y][left * 3 : right * 3]
        expected = bytearray(source)
        for x in range(0, len(expected), 3):
            if min(expected[x : x + 3]) >= BACKGROUND:
                expected[x : x + 3] = b"\xff\xff\xff"
        assert row == bytes(expected), f"row {y}"


@pytest.mark.parametrize("name", sorted(VENDORED_SHA256))
def test_the_brand_kit_scripts_are_vendored_unchanged(name: str) -> None:
    assert _sha256(BRANDING / name) == VENDORED_SHA256[name]


@pytest.mark.parametrize("name", DOCS_COPIES)
def test_docs_serve_the_same_brand_assets(name: str) -> None:
    assert (DOCS / name).read_bytes() == (BRANDING / name).read_bytes()


@pytest.mark.parametrize("name", FRONTEND_COPIES)
def test_the_frontend_serves_the_same_brand_assets(name: str) -> None:
    assert (FRONTEND / name).read_bytes() == (BRANDING / name).read_bytes()


def test_derived_masters_and_social_preview_sizes() -> None:
    assert _png_size(BRANDING / "tabrion-studio-logo-transparent.png") == (2048, 640)
    assert _png_size(BRANDING / "tabrion-studio-logo-white.png") == (2048, 640)
    assert _png_size(BRANDING / "social-preview.png") == (1280, 640)
    assert _png_size(BRANDING / "tabrion-studio-mark-128.png") == (128, 128)
    assert _png_size(BRANDING / "favicon-32.png") == (32, 32)


def test_readme_docs_and_frontend_use_the_assets() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    raw = "https://raw.githubusercontent.com/peterkarlhuber/tabrion-studio/master/assets/branding/"
    # Absolute URLs: the README is also the PyPI description.
    assert f'srcset="{raw}tabrion-studio-logo-on-dark.png"' in readme
    assert f'src="{raw}tabrion-studio-logo.png"' in readme
    assert "[TRADEMARKS.md](TRADEMARKS.md)" in readme
    assert "not licensed under the license in LICENSE" in (ROOT / "TRADEMARKS.md").read_text(encoding="utf-8")
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "logo: assets/branding/tabrion-studio-mark-on-dark-128.png" in mkdocs
    assert "favicon: assets/branding/favicon-32.png" in mkdocs
    index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    assert "tabrion-studio-logo.png#only-light" in index
    assert "tabrion-studio-logo-on-dark.png#only-dark" in index
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'href="/branding/favicon.ico"' in html


def test_no_css_filter_is_applied_to_the_logo() -> None:
    """The on-dark variants come from build.py; the frontend never inverts or filters the logo in CSS."""
    sources = [*(ROOT / "frontend" / "src").rglob("*.css"), *(ROOT / "frontend" / "src").rglob("*.tsx")]
    assert sources
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "filter:" not in text and "invert(" not in text, path
