# Tabrion Studio brand assets

Two logo masters as supplied by Peter Karlhuber on 2026-11-08, each kept byte for byte after the metadata was removed
(see below):

* `tabrion-studio-logo-master-2026-11-08.png` (1046 × 154 px, RGB on white): the "Tabrion STUDIO" lockup. Every
  other image here is derived from it by `build.py` and must not be edited by hand.
* `tabrion-logo-master-2026-11-08.png` (1254 × 1254 px, RGB on white): the full Tabrion master with the "Tabrion"
  lockup at the top and the "Tabrion STUDIO" lockup at the bottom. It is the source of the Studio lockup and is kept
  for provenance; it is the same file as `tabrion/assets/branding/tabrion-logo-master-2026-11-08.png`.

**License:** the logos and every image in this folder, in `docs/assets/branding/` and in `frontend/public/branding/`
(and so in the built frontend inside the wheel) are © Peter Karlhuber and are **not** covered by the repository's MIT
license; see [TRADEMARKS.md](../../TRADEMARKS.md). The scripts are MIT.

## Metadata removed, pixels unchanged

The supplied files carry metadata chunks: a C2PA content-credentials manifest (`caBX`) in both, and an `eXIf` chunk
with the dimensions in the full master. The committed copies keep only the chunks needed to show the image (`IHDR`,
`sRGB` where present, `IDAT`, `IEND`). The image data is untouched: `tests/unit/test_branding.py` pins the sha256 of
the decompressed pixel data of the supplied files and checks it against the committed copies.

| File | sha256 of the supplied file (planning folder, `logo/`) | sha256 of the committed copy |
|------|------|------|
| `tabrion-studio-logo-master-2026-11-08.png` | `3b66457ddc9b96a9cfdd6193e4e169004ee32acec07c54fb786e650dd41ecac9` | `1ae73bfe3fc7b311357c2a4dfab0ff2d62cd2568c7b206cf3cd0a31c5bcd31ca` |
| `tabrion-logo-master-2026-11-08.png` | `b3e5f441c9e7200153f1d4b437100c72a3da4445f23ef7e0f52c9728850ff7e1` | `e253dbf74d714222e4c00c43d5475757c771af124844c66fc3ed6d5d8d796997` |

The Studio lockup is pixel for pixel the bottom lockup of the full master, cropped at `(94, 798, 1140, 952)` with
paper white (grey 247-255) set to white, the crop of `tabrion/assets/branding/build.py --studio`. That box leaves out
the faint ghost of old letters right of the top wordmark. The test checks this as well.

## Files

| File | Size | Use |
|------|------|-----|
| `tabrion-studio-logo-transparent.png` | 2048 × 640 | brand kit master prepared from the lockup: transparent background, black wordmark ("STUDIO" as black with alpha) |
| `tabrion-studio-logo-white.png` | 2048 × 640 | the same flattened on white (the brand kit checks that both agree) |
| `tabrion-studio-logo.png` | 960 wide | lockup for light surfaces: README hero, docs landing page and Studio start page (light theme) |
| `tabrion-studio-logo-on-dark.png` | 960 wide | lockup with white wordmark and lifted symbol for dark surfaces (dark mode and dark theme) |
| `tabrion-studio-mark-128.png` | 128 × 128 | symbol for light surfaces: the Studio header (light theme) |
| `tabrion-studio-mark-on-dark-128.png` | 128 × 128 | symbol with the lifted black layer: the Studio header (dark theme), docs header (dark slate in both schemes) |
| `favicon.ico` | 16, 32, 48 | browser favicon (symbol on a rounded white tile, readable on light and dark tabs) |
| `favicon-32.png` | 32 × 32 | PNG favicon (Studio and docs site) |
| `social-preview.png` | 1280 × 640 | GitHub social preview: the on-dark lockup on the shared dark brand template (optional; uploaded manually in the repository settings if wanted) |

`build.py` copies the web assets into `docs/assets/branding/` (MkDocs serves only files below `docs/`) and into
`frontend/public/branding/` (Vite copies `public/` into the build, which goes into the wheel). The test checks that
the copies match.

## Rules

These are the rules of `tabrion/assets/branding/`, applied unchanged:

* Nothing is redrawn. `build.py` prepares the brand kit's transparent master from the supplied pixels: it cleans the
  paper (grey 247-255 to white), upscales with Lanczos and knocks out the white. Each symbol pixel keeps the shape and
  anti-aliasing of the supplied artwork as alpha and takes the median colour of its layer (black, slate grey, light
  grey), which drops the colour noise of the raster. The wordmark is pure black with alpha: "Tabrion" is opaque, the
  grey "STUDIO" is black at the alpha that gives the supplied grey over white (about 0.73), so the reversed wordmark
  on dark surfaces keeps the difference between the two words.
* `derive_branding.py` and `social_preview.py` are the brand kit scripts, vendored unchanged from
  `nodestro-studio/scripts/` (the test checks their sha256). `build.py` registers Tabrion Studio in their product and
  theme tables at run time instead of editing them, and lowers `SPLIT_GAP` to 40 px there: the gap between symbol
  and wordmark is about 51 px in the 2048-px master, narrower than in the other brands.
* The symbol's top layer is black and disappears on dark surfaces, so the on-dark variants lift the dark tones
  (`lift` 0.6, as for Tabrion): the three layers stay in order and the top layer reaches about 3:1 contrast on
  near-black. The light variants are unchanged.
* Light surfaces use the light lockup or mark, dark surfaces the on-dark one; the switch follows the surface's own
  theme mechanism: `<picture>` with `prefers-color-scheme` in the README, `#only-light` / `#only-dark` in MkDocs
  Material, and the resolved theme (`data-theme`) in the Studio frontend. No CSS filters, no `invert()` and no boxes
  behind the logo.
* Below about 40 px height, use the symbol instead of the full lockup (the Studio header shows the symbol next to the
  product name as text).
* Favicons show the symbol only, never the lockup.
* There is no vector (SVG) version; an official vector master would have to come from the logo's designer.

## Regenerating

```bash
uv run --with pillow==12.3.0 --with numpy==2.5.3 python assets/branding/build.py
```

`build.py` checks that the Studio master is the crop of the full master, prepares the two brand kit masters, checks
them with `derive_branding.py --check`, derives the web and favicon sets, keeps the files listed above, renders
`social-preview.png` with `social_preview.py` and copies the web assets. The committed files were produced with
Pillow 12.3.0 and NumPy 2.5.3 (the pinned versions above); other versions may write different bytes, so commit only
the files you meant to change.
