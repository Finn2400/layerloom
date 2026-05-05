#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layer_perm_cmy_visualizer.py — CMY stack explorer (PDF + optional ASCII/ANSI)

Features
- Page 1 (PDF): hue-sorted color chips + 20-layer "pancake" mini-stacks
  • Each stack repeats its CMY token sequence up to 20 layers, truncating extra.
  • Each layer (pancake) has equal width and thickness; thin outlines separate layers.
  • Labels tuned: CMY token moved up more; hex moved up a little.
- Page 2 (PDF): solid Voronoi regions in CMY triangle (no outlines/background)
  • Regions numbered at polygon centroids (matches “old version” style)
- ASCII mode: chips + triangle only (no stacks). Triangle shows colored regions and numbers.
- Generation:
  • Default enumerates all heights up to and including --max-height (default 6)
  • Optional filters by # of distinct colors: --filter-exact / --filter-lte / --filter-gte
  • You can target a specific pair with --height H --max-run R
  • When NOT targeting a specific pair, prunes redundant max-run values
    yielding the same number of unique colors (keeps lower max-run).

Usage examples
  python layer_perm_cmy_visualizer.py
  python layer_perm_cmy_visualizer.py --ascii
  python layer_perm_cmy_visualizer.py --filter-lte 2
  python layer_perm_cmy_visualizer.py --filter-gte 3
  python layer_perm_cmy_visualizer.py --height 5 --max-run 4
"""

import itertools, math, colorsys, argparse, shutil, os, sys
from typing import Tuple, Union, List
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle

# ──────────────────────────────
# Constants & layout
# ──────────────────────────────
PALETTE = ["C", "M", "Y"]
PURE_RGB = {
    "C": (0.0, 1.0, 1.0),
    "M": (1.0, 0.0, 1.0),
    "Y": (1.0, 1.0, 0.0),
}
SQRT3_2 = math.sqrt(3) / 2.0

# Chips grid
MAX_COLUMNS = 10
CHIP_FIG_CELL_W = 1.25
CHIP_FIG_CELL_H = 1.05
TEXT_LIGHT_THRESHOLD = 1.3

# Pancake stack layout
STACK_LAYERS = 20        # total layers per stack (truncate repeats)
STACK_WIDTH_FRAC = 0.3   # relative width of mini-stack within tile
GRID_LINE_LW_PT = 0.25   # thin layer separators

# ASCII layout
ASCII_TRI_WIDTH = 58
ASCII_TRI_HEIGHT = 30
ASCII_CHIP_BLOCK_W = 6
ASCII_CHIP_BLOCK_H = 2
ASCII_COLS = 6

# ──────────────────────────────
# Helpers
# ──────────────────────────────
def to_tuple(s: Union[str, Tuple[str, ...]]) -> Tuple[str, ...]:
    return tuple(s) if isinstance(s, str) else tuple(s)

def is_valid_run(stack: Tuple[str, ...], max_run: int) -> bool:
    run = 1
    for i in range(1, len(stack)):
        if stack[i] == stack[i - 1]:
            run += 1
            if run > max_run:
                return False
        else:
            run = 1
    return True

def canonical_rotation(stack: Tuple[str, ...]) -> Tuple[str, ...]:
    n = len(stack)
    rots = [stack[i:] + stack[:i] for i in range(n)]
    return min(rots)

def generate_rotation_unique(height: int, max_run: int) -> List[Tuple[str, ...]]:
    seen = set()
    out: List[Tuple[str, ...]] = []
    for s in itertools.product(PALETTE, repeat=height):
        s = tuple(s)
        if not is_valid_run(s, max_run):
            continue
        key = canonical_rotation(s)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out

def adjacency_repeats(s: Union[str, Tuple[str, ...]]) -> int:
    s = to_tuple(s)
    return sum(1 for i in range(len(s) - 1) if s[i] == s[i + 1])

def dedupe_by_composition_choose_best(stacks: List[Tuple[str, ...]]) -> List[Tuple[str, ...]]:
    """One representative per (C,M,Y) composition; prefer fewer adjacencies."""
    buckets = {}
    for raw_s in stacks:
        s = to_tuple(raw_s)
        comp = (s.count("C"), s.count("M"), s.count("Y"))
        score = adjacency_repeats(s)
        candidate = (score, s)
        if comp not in buckets or candidate < buckets[comp]:
            buckets[comp] = candidate
    return [v[1] for v in buckets.values()]

def stack_fractions(s: Union[str, Tuple[str, ...]]) -> Tuple[float, float, float]:
    s = to_tuple(s)
    L = len(s) or 1
    return (s.count("C") / L, s.count("M") / L, s.count("Y") / L)

def blend_rgb(s: Union[str, Tuple[str, ...]]) -> Tuple[float, float, float]:
    fC, fM, fY = stack_fractions(s)
    return (1 - fC, 1 - fM, 1 - fY)

def rgb_to_hex(rgb: Tuple[float, float, float]) -> str:
    r = int(max(0, min(1, rgb[0])) * 255)
    g = int(max(0, min(1, rgb[1])) * 255)
    b = int(max(0, min(1, rgb[2])) * 255)
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

def sort_by_hue(stacks: List[Tuple[str, ...]]) -> List[Tuple[str, ...]]:
    def key(s):
        r, g, b = blend_rgb(s)
        h, _, _ = colorsys.rgb_to_hsv(r, g, b)
        return h
    return sorted(stacks, key=key)

def dedupe_by_color(stacks: List[Tuple[str, ...]]) -> List[Tuple[str, ...]]:
    seen = set()
    out = []
    for s in stacks:
        hexcol = rgb_to_hex(blend_rgb(s))
        if hexcol not in seen:
            seen.add(hexcol)
            out.append(s)
    return out


def build_palette_stacks(max_height: int, max_run: int, distinct_lte: int) -> List[Tuple[str, ...]]:
    pure_anchors: List[Tuple[str, ...]] = [("C",), ("M",), ("Y",)]
    stacks: List[Tuple[str, ...]] = []
    for height in range(1, max_height + 1):
        stacks.extend(generate_rotation_unique(height, max_run))
    stacks = [s for s in stacks if len(set(s)) <= distinct_lte]
    stacks = dedupe_by_composition_choose_best(stacks + pure_anchors)
    return sort_by_hue(dedupe_by_color(stacks))

# ──────────────────────────────
# Voronoi helpers
# ──────────────────────────────
def barycentric_xy(fC: float, fM: float, fY: float) -> Tuple[float, float]:
    return (fM + 0.5 * fY, SQRT3_2 * fY)

def polygon_area2(poly):
    s=0.0
    for i in range(len(poly)):
        x1,y1=poly[i];x2,y2=poly[(i+1)%len(poly)]
        s+=x1*y2-x2*y1
    return s

def polygon_centroid(poly):
    A2=polygon_area2(poly)
    if abs(A2)<1e-18:
        xs,ys=zip(*poly);return(sum(xs)/len(xs),sum(ys)/len(ys))
    cx=cy=0.0
    for i in range(len(poly)):
        x1,y1=poly[i];x2,y2=poly[(i+1)%len(poly)]
        cross=x1*y2-x2*y1
        cx+=(x1+x2)*cross;cy+=(y1+y2)*cross
    A=A2/2.0
    return(cx/(6*A),cy/(6*A))

def clip_halfplane(poly,nx,ny,c):
    out=[];inside=lambda p:nx*p[0]+ny*p[1]<=c+1e-12
    def intersect(p1,p2):
        x1,y1=p1;x2,y2=p2;dx,dy=x2-x1,y2-y1
        denom=nx*dx+ny*dy
        if abs(denom)<1e-18:return p2
        t=(c-(nx*x1+ny*y1))/denom
        return(x1+t*dx,y1+t*dy)
    prev=poly[-1];prev_in=inside(prev)
    for cur in poly:
        cur_in=inside(cur)
        if cur_in:
            if not prev_in:out.append(intersect(prev,cur))
            out.append(cur)
        elif prev_in:out.append(intersect(prev,cur))
        prev,prev_in=cur,cur_in
    return out

def bounded_voronoi_polygon(p,others,tri):
    poly=tri[:];px,py=p
    for qx,qy in others:
        nx,ny=2*(qx-px),2*(qy-py)
        c=(qx*qx+qy*qy)-(px*px+py*py)
        poly=clip_halfplane(poly,nx,ny,c)
        if len(poly)<3:break
    return poly

# ──────────────────────────────
# PDF plotting
# ──────────────────────────────
def plot_page1(stacks, height, max_run, pdf, specific=False):
    stacks = sort_by_hue(dedupe_by_color(stacks))
    n = len(stacks)
    cols = min(MAX_COLUMNS, max(1, n))
    rows = (n + cols - 1) // cols

    fig = plt.figure(figsize=(cols * CHIP_FIG_CELL_W, rows * CHIP_FIG_CELL_H + 1.25))
    gs = fig.add_gridspec(2, 1, height_ratios=[3.0, 1.6], hspace=0.35)

    # Chips
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.axis("off")
    title = (f"Hue-sorted chips (n={n}) | Height={height}, Max-Run={max_run}"
             if specific else
             f"Hue-sorted chips (n={n}) | Up to Height={height}, Max-Run={max_run}")
    ax1.set_title(title, fontsize=14, pad=10)
    cw, ch = 1 / cols, 1 / rows

    for idx, s in enumerate(stacks, 1):
        c, r = (idx - 1) % cols, (idx - 1) // cols
        x0, y0 = c * cw, 1 - (r + 1) * ch
        rgb = blend_rgb(s)
        hexcol = rgb_to_hex(rgb)
        ax1.add_patch(Rectangle((x0, y0), cw, ch, color=rgb, ec="k", lw=0.35))
        txt = "black" if sum(rgb) > TEXT_LIGHT_THRESHOLD else "white"
        ax1.text(x0 + cw / 2, y0 + ch / 2 + 0.015, f"#{idx}",
                 ha="center", va="center", fontsize=9, color=txt, fontweight="bold")
        ax1.text(x0 + cw / 2, y0 + 0.040, "".join(s),
                 ha="center", va="center", fontsize=8, color=txt)
        ax1.text(x0 + cw / 2, y0 + 0.020, hexcol,
                 ha="center", va="center", fontsize=6.5, color=txt)

    # 20-layer "pancake stacks"
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.axis("off")
    ax2.set_title("20-layer 'pancake' stacks (equal width/thickness)", fontsize=12, pad=8)
    for idx, s in enumerate(stacks, 1):
        token_seq = to_tuple(s)
        L = len(token_seq)
        if L == 0:
            continue
        c, r = (idx - 1) % cols, (idx - 1) // cols
        cw2, ch2 = 1 / cols, 1 / rows
        x0, y0 = c * cw2, 1 - (r + 1) * ch2

        stack_w = cw2 * STACK_WIDTH_FRAC
        stack_h = ch2 * 0.9
        x_left = x0 + (cw2 - stack_w) / 2
        y_bottom = y0 + (ch2 - stack_h) / 2
        layer_h = stack_h / STACK_LAYERS

        for i in range(STACK_LAYERS):
            token = token_seq[i % L]
            ax2.add_patch(Rectangle(
                (x_left, y_bottom + i * layer_h),
                stack_w, layer_h,
                facecolor=PURE_RGB[token],
                edgecolor='black',
                lw=GRID_LINE_LW_PT / 72.0
            ))
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)
    return stacks

def plot_page2_regions(stacks, height, max_run, pdf, specific=False):
    stacks = sort_by_hue(dedupe_by_color(stacks))
    fig, ax = plt.subplots(figsize=(6.8, 6.3))
    ax.axis("off")
    title = (f"Ternary CMY — Height={height}, Max-Run={max_run} | Solid Voronoi regions"
             if specific else
             f"Ternary CMY — Up to Height={height}, Max-Run={max_run} | Solid Voronoi regions")
    ax.set_title(title, fontsize=14, pad=10)

    vC, vM, vY = (0, 0), (1, 0), (0.5, SQRT3_2)
    tri = [vC, vM, vY]
    sites = [barycentric_xy(*stack_fractions(s)) for s in stacks]
    rgbs  = [blend_rgb(s) for s in stacks]
    regions = []

    # fill regions
    for i, p in enumerate(sites):
        others = [q for j, q in enumerate(sites) if j != i]
        poly = bounded_voronoi_polygon(p, others, tri)
        regions.append(poly)
        if len(poly) >= 3:
            xs = [x for x, _ in poly]
            ys = [y for _, y in poly]
            ax.fill(xs, ys, color=rgbs[i], lw=0)

    # number regions at centroid
    for idx, (poly, rgb) in enumerate(zip(regions, rgbs), 1):
        if len(poly) >= 3:
            cx, cy = polygon_centroid(poly)
            txt = "black" if sum(rgb) > TEXT_LIGHT_THRESHOLD else "white"
            ax.text(cx, cy, f"{idx}", ha="center", va="center",
                    fontsize=8, color=txt, fontweight="bold")

    ax.set_aspect('equal', 'box')
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, vY[1] + 0.05)
    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)

# ──────────────────────────────
# ASCII rendering (chips + triangle only)
# ──────────────────────────────
RESET = "\x1b[0m"
def supports_truecolor():
    cterm=os.environ.get("COLORTERM","").lower()
    term=os.environ.get("TERM","").lower()
    return "truecolor" in cterm or "24bit" in cterm or "direct" in term
def ansi_bg(r,g,b): return f"\x1b[48;2;{r};{g};{b}m"
def rgb_to_255(rgb): return tuple(int(max(0,min(1,c))*255) for c in rgb)

def draw_color_block(w,h,rgb,use_color=True):
    r,g,b=rgb_to_255(rgb)
    line=" "*w
    if use_color:
        return [(ansi_bg(r,g,b)+line+RESET) for _ in range(h)]
    else:
        return [("#"*w) for _ in range(h)]

def ascii_chip_grid(stacks, cols=ASCII_COLS, use_color=True):
    stacks = sort_by_hue(dedupe_by_color(stacks))
    n = len(stacks); rows = (n + cols - 1)//cols
    out = []; idx = 1
    for r in range(rows):
        block_rows = [""]*ASCII_CHIP_BLOCK_H
        label_row = ""
        for c in range(cols):
            if idx <= n:
                s = stacks[idx-1]; rgb = blend_rgb(s); hexcol = rgb_to_hex(rgb)
                blk = draw_color_block(ASCII_CHIP_BLOCK_W, ASCII_CHIP_BLOCK_H, rgb, use_color)
                for i in range(ASCII_CHIP_BLOCK_H): block_rows[i] += blk[i] + " "
                label_row += f"#{idx} {''.join(s)} {hexcol}  "
            idx += 1
        out.extend(block_rows); out.append(label_row.rstrip()); out.append("")
    return out

def point_in_triangle(px,py,v0=(0,0),v1=(1,0),v2=(0.5,SQRT3_2)):
    x0,y0=v0; x1,y1=v1; x2,y2=v2
    D=(y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
    s=((y1-y2)*(px-x2)+(x2-x1)*(py-y2))/D
    t=((y2-y0)*(px-x2)+(x0-x2)*(py-y2))/D
    return s>=0 and t>=0 and s+t<=1

def ascii_triangle_regions(stacks, width=ASCII_TRI_WIDTH, height=ASCII_TRI_HEIGHT, use_color=True):
    stacks = sort_by_hue(dedupe_by_color(stacks))
    sites  = [barycentric_xy(*stack_fractions(s)) for s in stacks]
    colors = [blend_rgb(s) for s in stacks]

    buf = [[" " for _ in range(width)] for _ in range(height)]
    bg  = [[""  for _ in range(width)] for _ in range(height)]
    vC, vM, vY = (0,0), (1,0), (0.5, SQRT3_2)

    # fill by nearest site
    for row in range(height):
        y = vY[1] * (1 - row / (height - 1))
        for col in range(width):
            x = col / (width - 1)
            if not point_in_triangle(x, y, vC, vM, vY):
                continue
            best_i, best_d2 = 0, 1e9
            for i, (sx, sy) in enumerate(sites):
                dx, dy = x - sx, y - sy
                d2 = dx*dx + dy*dy
                if d2 < best_d2:
                    best_d2, best_i = d2, i
            rgb = colors[best_i]
            if use_color and sys.stdout.isatty():
                r,g,b = rgb_to_255(rgb)
                bg[row][col] = ansi_bg(r,g,b)
                buf[row][col] = " "
            else:
                buf[row][col] = "#"

    # overlay numeric labels near site
    for i, (sx, sy) in enumerate(sites, start=1):
        col = int(round(sx * (width - 1)))
        row = int(round((1 - sy / vY[1]) * (height - 1))) if vY[1] > 0 else 0
        s = str(i)
        for k, ch in enumerate(s):
            cc = min(width - 1, col + k)
            if 0 <= row < height and 0 <= cc < width:
                bg[row][cc]  = ""  # let digit be visible
                buf[row][cc] = ch

    # assemble lines
    lines = []
    for r in range(height):
        parts = []
        for c in range(width):
            if bg[r][c]:
                parts.append(bg[r][c] + buf[r][c] + RESET)
            else:
                parts.append(buf[r][c])
        lines.append("".join(parts))
    return lines

def render_ascii_for_group(stacks, height, max_run, use_color=True, specific=False, width=None):
    width = width or shutil.get_terminal_size((100, 40)).columns
    title = f"== Height={height}, Max-Run={max_run} ==" if specific else f"== Height≤{height}, Max-Run={max_run} =="
    sep = "=" * min(len(title), width)
    lines = [sep, title, sep, ""]
    lines.append("[Hue-sorted chips]")
    lines += ascii_chip_grid(stacks, cols=ASCII_COLS, use_color=use_color)
    lines.append("(CMY triangle: regions colored & numbered)")
    lines += ascii_triangle_regions(stacks, width=ASCII_TRI_WIDTH, height=ASCII_TRI_HEIGHT, use_color=use_color)
    lines.append("")
    print("\n".join(lines))

# ──────────────────────────────
# Main
# ──────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ascii", action="store_true", help="Render simplified ASCII/ANSI output (chips + triangle)")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI truecolor in ASCII mode")
    ap.add_argument("--filter-equal", "--filter-exact", type=int, dest="filter_equal",
                    help="Keep stacks with exactly N distinct colors")
    ap.add_argument("--filter-lte", "--filter-less-than-equal", type=int, dest="filter_lte",
                    help="Keep stacks with ≤ N distinct colors")
    ap.add_argument("--filter-gte", "--filter-greater-than-equal", type=int, dest="filter_gte",
                    help="Keep stacks with ≥ N distinct colors")
    ap.add_argument("--max-height", type=int, default=6, help="Maximum height to include (inclusive)")
    ap.add_argument("--pdf-out", default="layer_perm_cmy_visualizer.pdf", help="Output PDF filename")
    ap.add_argument("--height", type=int, help="Generate only this specific height")
    ap.add_argument("--max-run", type=int, help="Generate only this specific max-run")
    args = ap.parse_args()

    pure_anchors = [("C",), ("M",), ("Y")]

    def passes_filters(s):
        c = len(set(s))
        if args.filter_equal is not None and c != args.filter_equal: return False
        if args.filter_lte   is not None and c >  args.filter_lte:   return False
        if args.filter_gte   is not None and c <  args.filter_gte:   return False
        return True

    heights  = [args.height] if args.height else list(range(1, args.max_height + 1))
    max_runs = [args.max_run] if args.max_run else list(range(1, 6))
    specific = (args.height is not None) and (args.max_run is not None)

    # ASCII mode
    if args.ascii:
        use_color = (not args.no_color) and supports_truecolor()
        for height in heights:
            seen_counts = {}
            for max_run in max_runs:
                if max_run > height: continue
                stacks = []
                for h in range(1, height + 1):
                    stacks.extend(generate_rotation_unique(h, max_run))
                stacks = [s for s in stacks if passes_filters(s)]
                if not stacks: continue
                stacks = dedupe_by_composition_choose_best(stacks + pure_anchors)
                uniq = len(dedupe_by_color(stacks))
                if uniq in seen_counts and not specific:
                    continue  # prune later run with same color-count
                seen_counts[uniq] = max_run
                render_ascii_for_group(stacks, height, max_run, use_color=use_color, specific=specific)
        return

    # PDF mode
    with PdfPages(args.pdf_out) as pdf:
        for height in heights:
            seen_counts = {}
            for max_run in max_runs:
                if max_run > height: continue
                stacks = []
                for h in range(1, height + 1):
                    stacks.extend(generate_rotation_unique(h, max_run))
                stacks = [s for s in stacks if passes_filters(s)]
                if not stacks: continue
                # representative per composition + include pure anchors
                stacks = dedupe_by_composition_choose_best(stacks + pure_anchors)
                uniq = len(dedupe_by_color(stacks))
                if uniq in seen_counts and not specific:
                    # prune later (higher) max-run if color-count matches
                    continue
                seen_counts[uniq] = max_run

                ordered = sort_by_hue(dedupe_by_color(stacks))
                plot_page1(ordered, height, max_run, pdf, specific=specific)
                plot_page2_regions(ordered, height, max_run, pdf, specific=specific)

    print(f"[✓] wrote {args.pdf_out}")

if __name__ == "__main__":
    main()
