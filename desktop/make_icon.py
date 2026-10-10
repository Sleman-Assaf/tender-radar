"""
Generate the TenderRadar Windows icon from the brand mark.

    .venv\\Scripts\\python.exe desktop\\make_icon.py

Why this exists
---------------
desktop/TenderRadar.ico previously held a single 16x16 image. Windows scales that
up for every other context — the desktop shortcut (48px), the taskbar (24-32px),
Alt-Tab (32-48px), the Start Menu tile and the large-icons view (96-256px) — so
the application looked blurry everywhere except the smallest one. A Windows icon
has to carry a purpose-drawn bitmap for each size.

The artwork is the TenderRadar radar mark from static/img/logo-icon.svg: a white
disc, three concentric black rings, a yellow sweep beam and a black centre dot.
It is redrawn here rather than rasterised from the SVG so that each size can be
tuned — three hairline rings turn to mush at 16px, so the small sizes drop the
innermost ring and use proportionally heavier strokes. That is ordinary icon
practice, not a different logo.

Pillow is needed only to BUILD the icon. It is not a runtime dependency and is
not bundled into the shipped Python runtime.
"""
from __future__ import annotations

import struct
import sys
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:                                            # pragma: no cover
    raise SystemExit('Pillow is required to build the icon:\n'
                     r'    .venv\Scripts\python.exe -m pip install pillow')

ROOT = Path(__file__).resolve().parent.parent
ICO_PATH = ROOT / 'desktop' / 'TenderRadar.ico'
FAVICON_PATH = ROOT / 'static' / 'favicon.ico'
PNG_DIR = ROOT / 'static' / 'img'

# Brand palette, straight from the stylesheet and logo-icon.svg.
INK = (15, 15, 13, 255)          # #0F0F0D
DISC = (255, 255, 255, 255)      # #FFFFFF
BEAM = (252, 215, 0, 255)        # #FCD700
BEAM_DARK = (228, 194, 0, 255)   # #E4C200

# Every size Windows actually asks for: shell small/medium/large/extra-large,
# taskbar at 100/125/150/200% scaling, Alt-Tab, and the 256px Explorer preview.
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

SS = 8                           # supersampling factor; downsampled with LANCZOS


# Per-size geometry, in the SVG's 100-unit coordinate space.
#
# The full mark has three rings. Below 48px they stop resolving: at 16px an
# 8-unit stroke is 1.3 physical pixels and the white gaps between rings are about
# the same, so three rings average out into a grey smear. Each tier therefore
# drops rings and thickens what is left, and scales the beam up so the yellow —
# the one element that identifies TenderRadar at a glance — still reads.
#
#   rings      : (radius, stroke) pairs, outermost first
#   dot        : centre-dot radius
#   beam_scale : multiplier on the sweep beam's length and spread
TIERS = [
    # up to    rings                                     dot   beam
    (16,  [(40, 15)],                                    9.5,  1.16),
    (24,  [(41, 12), (22, 11)],                          7.5,  1.10),
    (32,  [(41.5, 11), (21, 9.5)],                       6.5,  1.06),
    (40,  [(42, 9), (28, 8), (15, 7)],                   5.2,  1.02),
    (999, [(42, 8), (29, 7), (16, 6)],                   4.5,  1.00),   # exact SVG
]
# The third ring only appears from 40px. At 32px its stroke and the centre dot
# end up about one pixel apart and merge into a blob, which looks like a defect
# rather than a smaller logo.


def draw_mark(size: int) -> Image.Image:
    """The radar mark at one pixel size, drawn size-appropriately."""
    S = size * SS
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    rings, dot, beam_scale = next(t[1:] for t in TIERS if size <= t[0])

    def u(v: float) -> float:
        """SVG user units (100-unit viewBox) -> supersampled pixels."""
        return v * S / 100.0

    def pt(x: float, y: float, k: float) -> tuple[float, float]:
        """A beam vertex, scaled about the centre by k."""
        return u(50 + (x - 50) * k), u(50 + (y - 50) * k)

    # The white disc. Inset by a hair so anti-aliasing has room and the edge does
    # not get clipped flat against the bitmap border.
    pad = u(0.6)
    d.ellipse([pad, pad, S - pad, S - pad], fill=DISC)

    for r, stroke in rings:
        d.ellipse([u(50 - r), u(50 - r), u(50 + r), u(50 + r)],
                  outline=INK, width=max(1, round(u(stroke))))

    # The sweep beam: apex at the centre, pointing to the lower left, as in
    # logo-icon.svg. Two tones give it a sense of rotation. Drawn after the rings
    # so it lies over them, which is what makes it visible when they are heavy.
    k = beam_scale
    d.polygon([pt(50, 50, k), pt(32.75, 83.86, k), pt(16.14, 67.25, k)],
              fill=BEAM)
    d.polygon([pt(50, 50, k), pt(23.13, 76.87, k), pt(16.14, 67.25, k)],
              fill=BEAM_DARK)

    d.ellipse([u(50 - dot), u(50 - dot), u(50 + dot), u(50 + dot)], fill=INK)

    return img.resize((size, size), Image.LANCZOS)


# ── ICO container ────────────────────────────────────────────────────────────
# Written by hand rather than via Pillow's ICO writer, which would resample one
# source image for every entry and throw away the per-size tuning above.

def _dib(img: Image.Image) -> bytes:
    """A 32-bit BOTTOMUP DIB with an all-zero AND mask (alpha does the work)."""
    w, h = img.size
    px = img.load()
    xor = bytearray()
    for y in range(h - 1, -1, -1):                 # DIB rows run bottom-up
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))             # BGRA
    mask_stride = ((w + 31) // 32) * 4             # 1bpp, 4-byte aligned rows
    and_mask = bytes(mask_stride * h)
    header = struct.pack('<IiiHHIIiiII',
                         40,            # biSize
                         w, h * 2,      # biWidth, biHeight (XOR + AND stacked)
                         1, 32,         # biPlanes, biBitCount
                         0,             # biCompression = BI_RGB
                         len(xor) + len(and_mask),
                         0, 0, 0, 0)
    return header + bytes(xor) + and_mask


def write_ico(images: list[Image.Image], path: Path) -> None:
    """ICONDIR + ICONDIRENTRY[] + payloads.

    Sizes up to 48 are stored as DIBs, which every Windows shell code path reads
    directly; 64 and above are stored as PNG, which is how Windows itself keeps
    large icons from bloating the file.
    """
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for img in images:
        w, h = img.size
        if w >= 64:
            buf = BytesIO()
            img.save(buf, format='PNG', optimize=True)
            data = buf.getvalue()
        else:
            data = _dib(img)
        entries.append(struct.pack('<BBBBHHII',
                                   0 if w == 256 else w,     # 0 means 256
                                   0 if h == 256 else h,
                                   0, 0,                     # palette, reserved
                                   1, 32,                    # planes, bpp
                                   len(data), offset))
        blobs.append(data)
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(struct.pack('<HHH', 0, 1, len(images)))      # type 1 = icon
        for e in entries:
            fh.write(e)
        for b in blobs:
            fh.write(b)


def main() -> int:
    print('Drawing the TenderRadar mark at each size...')
    images = []
    for s in SIZES:
        images.append(draw_mark(s))
        print(f'   {s:>3}x{s:<3}')

    write_ico(images, ICO_PATH)
    print(f'\n  {ICO_PATH.relative_to(ROOT)}  '
          f'{ICO_PATH.stat().st_size:,} bytes, {len(images)} sizes')

    # The web favicon was also 16x16-only; refresh it from the same artwork so
    # the browser tab, the installed app and the shortcut all match.
    web = [im for im in images if im.size[0] in (16, 32, 48, 64, 128, 256)]
    write_ico(web, FAVICON_PATH)
    print(f'  {FAVICON_PATH.relative_to(ROOT)}  '
          f'{FAVICON_PATH.stat().st_size:,} bytes, {len(web)} sizes')

    # A PNG for anything that cannot read .ico (docs, the About box, README).
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    png = PNG_DIR / 'app-icon-256.png'
    draw_mark(256).save(png, format='PNG', optimize=True)
    print(f'  {png.relative_to(ROOT)}  {png.stat().st_size:,} bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
