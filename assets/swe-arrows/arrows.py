"""Export red / yellow / green ^ tips from quiver final 6b-9 as standalone assets.

Normal: tip_round geometry + GLY hex from concepts_r6b / final.py.
Pixel: hand grids matching final favicon-16 (T4S) and favicon-32 (T32).
"""
import io, os, tarfile
from functools import reduce
import cairosvg
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Polygon, box
from shapely import affinity
from shapely.ops import unary_union

BASE = "/workspace/quiver-logo"
OUT = os.path.join(BASE, "arrows")
NORM = os.path.join(OUT, "normal")
PIX = os.path.join(OUT, "pixel")
for d in (OUT, NORM, PIX):
    os.makedirs(d, exist_ok=True)

# Exact colours from concepts_r4 / final palette
RED, YEL, GRN = "#fa3228", "#ffd200", "#14e63c"
COLS = [("red", RED), ("yellow", YEL), ("green", GRN)]
TIP_H = 8.5  # final.py TIP_H
TIP_SPACING = 12.0  # final TIP_X = [12, 24, 36]


def path(geom):
    polys = [geom] if geom.geom_type == "Polygon" else [g for g in geom.geoms if g.geom_type == "Polygon"]
    d = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            c = list(ring.coords)
            d.append("M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in c[:-1]) + " Z")
    return " ".join(d)


def P(g, fill, extra=""):
    return f'<path d="{path(g)}" fill="{fill}" fill-rule="evenodd" {extra}/>'


def tip_round(x, y, H):
    """Same as concepts_r6b.tip_round used by final 6b-9."""
    r = .14 * H
    w = 0.66 * H - r
    h = H - 2 * r
    p = Polygon([(0, 0), (w, .92 * h), (0, .62 * h), (-w, .92 * h)]).buffer(r, quad_segs=12)
    p = affinity.translate(p, 0, -p.bounds[1])
    return affinity.translate(p, x, y)


def tip_geom():
    """Unit tip with tip at origin-ish; returns shapely polygon and its bounds."""
    return tip_round(0, 0, TIP_H)


def svg_wrap(body, vb, w=None, h=None, crisp=False):
    vw, vh = vb
    ww = w if w is not None else vw
    hh = h if h is not None else vh
    crisp_attr = ' shape-rendering="crispEdges"' if crisp else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vw} {vh}" '
            f'width="{ww}" height="{hh}"{crisp_attr}>{body}</svg>\n')


def render_png(svg, n=None, nearest=False):
    kwargs = {}
    if n is not None:
        kwargs["output_width"] = n
        kwargs["output_height"] = n
    im = Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode(), **kwargs))).convert("RGBA")
    return im


def write(path, data):
    with open(path, "w") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# NORMAL (vector) tips
# ---------------------------------------------------------------------------
# Fit one tip into a square viewBox with a little padding; keep proportions of TIP_H geometry.
g0 = tip_geom()
bx0, by0, bx1, by1 = g0.bounds
tw_, th_ = bx1 - bx0, by1 - by0
PAD = 1.0
# Square canvas that fits tip + pad
side = max(tw_, th_) + 2 * PAD
# Center tip in that square
tx = side / 2 - (bx0 + bx1) / 2
ty = side / 2 - (by0 + by1) / 2
g_centered = affinity.translate(g0, tx, ty)

# Also keep an "artboard" version in the original 64-space units for the combined row
# Tip at local (0,0) top of tip, used for spacing like the icon (centers 12 apart).
# On the icon, tip_round(x, TIP_Y, TIP_H) places tip apex near y=TIP_Y=6.5 with center x.
# For standalone, use centered canvas above. For combined row, place three tips with
# centers 12 units apart on a transparent strip.

NORMAL_SIZES = [16, 32, 64, 128, 256, 512]

for name, col in COLS:
    body = P(g_centered, col)
    svg = svg_wrap(body, (side, side))
    write(os.path.join(NORM, f"{name}.svg"), svg)
    for n in NORMAL_SIZES:
        render_png(svg, n).save(os.path.join(NORM, f"{name}-{n}.png"))

# Combined row: three tips spaced as on the icon (centers 12 units apart on 64 artboard).
# Use original tip_round(x, 0, TIP_H) so y starts at tip apex; pack tightly vertically.
tips_geom = [tip_round(x, 0, TIP_H) for x in (0, TIP_SPACING, 2 * TIP_SPACING)]
union = unary_union(tips_geom)
ub = union.bounds
row_pad = 1.0
row_w = (ub[2] - ub[0]) + 2 * row_pad
row_h = (ub[3] - ub[1]) + 2 * row_pad
row_body = "".join(
    P(affinity.translate(g, row_pad - ub[0], row_pad - ub[1]), c)
    for g, (_, c) in zip(tips_geom, COLS)
)
row_svg = svg_wrap(row_body, (row_w, row_h))
write(os.path.join(NORM, "row.svg"), row_svg)
# @1x and @2x: choose a base height so tips look similar to icon bar; use 32 as 1x height
row_h_px_1x = 32
row_w_px_1x = max(1, round(row_w / row_h * row_h_px_1x))
for scale, tag in ((1, "1x"), (2, "2x")):
    render_png(row_svg, None).resize(
        (row_w_px_1x * scale, row_h_px_1x * scale), Image.LANCZOS
    ) if False else None
# Prefer cairosvg direct sizing for crisp vectors
for scale, tag in ((1, "1x"), (2, "2x")):
    wpx = row_w_px_1x * scale
    hpx = row_h_px_1x * scale
    im = Image.open(io.BytesIO(cairosvg.svg2png(
        bytestring=row_svg.encode(), output_width=wpx, output_height=hpx
    ))).convert("RGBA")
    im.save(os.path.join(NORM, f"row-{tag}.png"))

print(f"normal tip canvas: {side:.2f} units, bounds {g_centered.bounds}")
print(f"normal row: {row_w:.2f}x{row_h:.2f}, 1x={row_w_px_1x}x{row_h_px_1x}")


# ---------------------------------------------------------------------------
# PIXEL tips: match final favicon grids
# ---------------------------------------------------------------------------
# 16-grid tip sprite used in final.py (T4S): 4x3 solid
T4S = [".XX.", "XXXX", "XXXX"]
# 32-grid tip sprite used in final.py (T32): 5x4 notched
T32 = ["..X..", ".XXX.", "XXXXX", "XX.XX"]
# Compact 8-grid tip (derived from T3T / solid triangle language of the set)
T8 = [".X.", "XXX", "XXX"]


def sprite_cells(rows):
    cells = []
    for dy, row in enumerate(rows):
        for dx, v in enumerate(row):
            if v == "X":
                cells.append((dx, dy))
    return cells, len(rows[0]), len(rows)


def pixel_svg(cells, cw, ch, col, canvas, cell_px=1):
    """cells in sprite coords; place sprite centered on canvas x canvas grid.
    Each cell is a 1x1 rect in viewBox units = canvas; export viewBox canvas x canvas.
    """
    sw = max(c[0] for c in cells) + 1 if cells else 0
    sh = max(c[1] for c in cells) + 1 if cells else 0
    ox = (canvas - sw) // 2
    oy = (canvas - sh) // 2
    # Merge horizontal runs per row for smaller SVG
    grid = set((ox + x, oy + y) for x, y in cells)
    rects = []
    for y in range(canvas):
        x = 0
        while x < canvas:
            if (x, y) not in grid:
                x += 1
                continue
            x1 = x
            while (x1 + 1, y) in grid:
                x1 += 1
            rects.append(
                f'<rect x="{x}" y="{y}" width="{x1 - x + 1}" height="1" fill="{col}"/>'
            )
            x = x1 + 1
    return svg_wrap("".join(rects), (canvas, canvas), crisp=True), grid


def pixel_png_from_grid(grid, canvas, col, scale):
    im = Image.new("RGBA", (canvas * scale, canvas * scale), (0, 0, 0, 0))
    rgb = tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
    px = im.load()
    for (x, y) in grid:
        for dy in range(scale):
            for dx in range(scale):
                px[x * scale + dx, y * scale + dy] = rgb + (255,)
    return im


PIXEL_SPECS = [
    ("8", T8, 8),
    ("16", T4S, 16),
    ("32", T32, 32),
]
UPSCALES = [1, 4, 8]

for name, col in COLS:
    for tag, rows, canvas in PIXEL_SPECS:
        cells, sw, sh = sprite_cells(rows)
        svg, grid = pixel_svg(cells, sw, sh, col, canvas)
        write(os.path.join(PIX, f"{name}-{tag}.svg"), svg)
        for s in UPSCALES:
            pixel_png_from_grid(grid, canvas, col, s).save(
                os.path.join(PIX, f"{name}-{tag}-{s}x.png")
            )

# Combined pixel rows: three tips side by side with the same spacing ratio as the icon.
# On 16-grid favicon tips at x=1,6,11 (5 cells center-to-center). On 32-grid at 3,9,15 (6).
# For standalone row assets, place three sprites with a 1-cell gap between bounding boxes.

def combined_pixel_row(rows, canvas_h, gap=1):
    cells, sw, sh = sprite_cells(rows)
    # three sprites: total width = 3*sw + 2*gap
    total_w = 3 * sw + 2 * gap
    total_h = max(canvas_h, sh)
    out = {}
    for i, (_, col) in enumerate(COLS):
        ox = i * (sw + gap)
        oy = (total_h - sh) // 2
        for (x, y) in cells:
            out[(ox + x, oy + y)] = col
    return out, total_w, total_h


def row_svg_from_map(cmap, w, h):
    rects = []
    for y in range(h):
        x = 0
        while x < w:
            c = cmap.get((x, y))
            if c is None:
                x += 1
                continue
            x1 = x
            while cmap.get((x1 + 1, y)) == c:
                x1 += 1
            rects.append(f'<rect x="{x}" y="{y}" width="{x1 - x + 1}" height="1" fill="{c}"/>')
            x = x1 + 1
    return svg_wrap("".join(rects), (w, h), crisp=True)


def row_png_from_map(cmap, w, h, scale):
    im = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    px = im.load()
    for (x, y), col in cmap.items():
        rgb = tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
        for dy in range(scale):
            for dx in range(scale):
                px[x * scale + dx, y * scale + dy] = rgb + (255,)
    return im


for tag, rows, canvas in PIXEL_SPECS:
    cells, sw, sh = sprite_cells(rows)
    gap = 1
    rw, rh = 3 * sw + 2 * gap, sh
    cmap = {}
    for i, (_, col) in enumerate(COLS):
        ox = i * (sw + gap)
        for (x, y) in cells:
            cmap[(ox + x, y)] = col
    write(os.path.join(PIX, f"row-{tag}.svg"), row_svg_from_map(cmap, rw, rh))
    for s in UPSCALES:
        row_png_from_map(cmap, rw, rh, s).save(os.path.join(PIX, f"row-{tag}-{s}x.png"))

print("pixel assets written")


# ---------------------------------------------------------------------------
# Preview sheet
# ---------------------------------------------------------------------------
F = lambda s, b=True: ImageFont.truetype(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if b else ""), s
)

W, H = 1500, 1680
sheet = Image.new("RGB", (W, H), (24, 24, 28))
d = ImageDraw.Draw(sheet)
d.text((24, 18), "quiver / swe: window-control arrow tips (from final 6b-9)", font=F(24), fill=(16, 185, 129))
d.text((24, 52), f"colours: red {RED}  ·  yellow {YEL}  ·  green {GRN}   |   geometry: tip_round H={TIP_H}, spacing={TIP_SPACING}",
       font=F(13, False), fill=(160, 160, 160))


def paste_on_bg(im, xy, bg, pad=8):
    x, y = xy
    bw, bh = im.size[0] + 2 * pad, im.size[1] + 2 * pad
    tile = Image.new("RGBA", (bw, bh), bg + (255,))
    tile.alpha_composite(im.convert("RGBA"), (pad, pad))
    sheet.paste(tile, (x, y))
    return bw, bh


d.text((24, 90), "normal (vector tip_round)", font=F(18), fill=(230, 230, 230))
x = 24
for name, col in COLS:
    im = Image.open(os.path.join(NORM, f"{name}-128.png"))
    for j, bg in enumerate(((24, 24, 28), (240, 240, 240))):
        paste_on_bg(im, (x, 120 + j * 160), bg, pad=12)
    d.text((x, 440), name, font=F(13, False), fill=(180, 180, 180))
    x += 160

d.text((520, 90), "red size strip (16→512)", font=F(14, False), fill=(170, 170, 170))
sx = 520
for n in NORMAL_SIZES:
    im = Image.open(os.path.join(NORM, f"red-{n}.png"))
    paste_on_bg(im, (sx, 120), (40, 40, 44), pad=4)
    d.text((sx, 120 + n + 12), str(n), font=F(11, False), fill=(140, 140, 140))
    sx += n + 20

row1 = Image.open(os.path.join(NORM, "row-1x.png"))
row2 = Image.open(os.path.join(NORM, "row-2x.png"))
d.text((520, 320), "combined row @1x / @2x (dark + light)", font=F(14, False), fill=(170, 170, 170))
paste_on_bg(row1, (520, 350), (24, 24, 28), pad=10)
paste_on_bg(row1, (520 + row1.size[0] + 40, 350), (240, 240, 240), pad=10)
paste_on_bg(row2, (520, 420), (24, 24, 28), pad=10)
paste_on_bg(row2, (520 + row2.size[0] + 40, 420), (240, 240, 240), pad=10)

d.text((24, 500), "pixel (hand grids: 8 compact · 16 = favicon T4S · 32 = favicon T32)", font=F(18), fill=(230, 230, 230))
d.text((24, 528), "shown at 8× for 8-grid, 4× for 16/32 so the sheet stays readable; files also ship 1x/4x/8x", font=F(12, False), fill=(140, 140, 140))

yy = 560
for tag, rows, canvas in PIXEL_SPECS:
    scale = 8 if canvas == 8 else 4
    d.text((24, yy), f"{tag}×{tag} grid  sprite={rows}  (preview ×{scale})", font=F(13, False), fill=(160, 160, 160))
    xx = 24
    shown = canvas * scale
    for name, col in COLS:
        im = Image.open(os.path.join(PIX, f"{name}-{tag}-{scale}x.png"))
        paste_on_bg(im, (xx, yy + 22), (24, 24, 28), pad=6)
        paste_on_bg(im, (xx, yy + 22 + shown + 18), (240, 240, 240), pad=6)
        d.text((xx, yy + 22 + 2 * (shown + 18) + 2), name, font=F(11, False), fill=(150, 150, 150))
        xx += shown + 28
    rim = Image.open(os.path.join(PIX, f"row-{tag}-{scale}x.png"))
    paste_on_bg(rim, (xx + 16, yy + 22), (24, 24, 28), pad=6)
    paste_on_bg(rim, (xx + 16, yy + 22 + shown + 18), (240, 240, 240), pad=6)
    d.text((xx + 16, yy + 22 + 2 * (shown + 18) + 2), f"row-{tag}", font=F(11, False), fill=(150, 150, 150))
    yy += 22 + 2 * (shown + 18) + 40

d.text((24, H - 40), "Source: /workspace/quiver-logo/final (6b-9). Transparent backgrounds. Exact tip_round + GLY hex. No redesign.",
       font=F(12, False), fill=(120, 120, 120))

preview_path = os.path.join(OUT, "arrows-preview.png")
sheet.save(preview_path)
sheet.save(os.path.join(BASE, "arrows-preview.png"))
print("preview:", preview_path)

# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------
archive = "/workspace/quiver-arrows.tar.gz"
with tarfile.open(archive, "w:gz") as tar:
    tar.add(OUT, arcname="arrows")
    tar.add(os.path.join(BASE, "arrows.py"), arcname="arrows/arrows.py")
print("archive:", archive)

# File inventory
print("\n=== FILE LIST ===")
for root, dirs, files in os.walk(OUT):
    dirs.sort()
    for f in sorted(files):
        p = os.path.join(root, f)
        print(f"{os.path.getsize(p):7d}  {p}")
print("ok")
