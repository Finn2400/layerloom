#!/usr/bin/env python3
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

import layer_perm_cmy_visualizer as vis


SQRT3_2 = vis.SQRT3_2


@dataclass(frozen=True)
class CaseSpec:
    slug: str
    title: str
    max_height: int
    max_run: int
    distinct_lte: int


CASES: Sequence[CaseSpec] = (
    CaseSpec("simple", "H≤6  |  Run≤2  |  Distinct≤2", 6, 2, 2),
    CaseSpec("normal", "H≤6  |  Run≤3  |  Distinct≤3", 6, 3, 3),
    CaseSpec("extended", "H≤7  |  Run≤6  |  Distinct≤3", 7, 6, 3),
)

GRID_DIV = 12
GRID_COLOR_A = "#d7e5f6"
GRID_COLOR_B = "#f3d5c4"
EDGE_COLOR = "#1f1f1f"
PANEL_FACE = "none"


def _ordered_stacks(case: CaseSpec) -> List[Tuple[str, ...]]:
    return vis.sort_by_hue(
        vis.dedupe_by_color(
            vis.build_palette_stacks(case.max_height, case.max_run, case.distinct_lte)
        )
    )


def _triangle_vertices() -> Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]:
    return (0.0, 0.0), (1.0, 0.0), (0.5, SQRT3_2)


def _setup_triangle_axes(ax, *, show_grid: bool = True) -> None:
    v_c, v_m, v_y = _triangle_vertices()
    ax.set_aspect("equal", "box")
    ax.set_facecolor(PANEL_FACE)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    if show_grid:
        for i in range(1, GRID_DIV):
            t = i / GRID_DIV
            p1 = (0.5 * t, SQRT3_2 * t)
            p2 = (1.0 - 0.5 * t, SQRT3_2 * t)
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=GRID_COLOR_B, lw=0.55, alpha=0.45, zorder=0)

            p1 = (1.0 - t, 0.0)
            p2 = (0.5 * (1.0 - t), SQRT3_2 * (1.0 - t))
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=GRID_COLOR_A, lw=0.55, alpha=0.45, zorder=0)

            p1 = (t, 0.0)
            p2 = (0.5 + 0.5 * t, SQRT3_2 * (1.0 - t))
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=GRID_COLOR_B, lw=0.55, alpha=0.26, zorder=0)

    ax.plot([v_c[0], v_y[0]], [v_c[1], v_y[1]], color=EDGE_COLOR, lw=1.5, zorder=1)
    ax.plot([v_y[0], v_m[0]], [v_y[1], v_m[1]], color=EDGE_COLOR, lw=1.5, zorder=1)
    ax.plot([v_m[0], v_c[0]], [v_m[1], v_c[1]], color=EDGE_COLOR, lw=1.5, zorder=1)
    ax.set_xlim(-0.06, 1.06)
    ax.set_ylim(-0.045, SQRT3_2 + 0.095)


def _tile_size(count: int) -> Tuple[float, float, float]:
    if count <= 25:
        return 0.09, 0.112, 10.8
    if count <= 60:
        return 0.065, 0.088, 8.2
    return 0.05, 0.072, 6.2


def _region_number_size(count: int) -> float:
    if count <= 30:
        return 9.8
    if count <= 60:
        return 6.8
    return 4.8


def _disc_radius(count: int) -> float:
    if count <= 25:
        return 0.028
    if count <= 60:
        return 0.022
    return 0.018


def _draw_tiles(ax, stacks: Sequence[Tuple[str, ...]]) -> None:
    _setup_triangle_axes(ax)
    tile_w, tile_h, _font_size = _tile_size(len(stacks))
    for stack in stacks:
        x, y = vis.barycentric_xy(*vis.stack_fractions(stack))
        x0 = x - tile_w / 2.0
        y0 = y - tile_h / 2.0
        layer_h = tile_h / len(stack)
        for i, token in enumerate(stack):
            ax.add_patch(
                Rectangle(
                    (x0, y0 + i * layer_h),
                    tile_w,
                    layer_h,
                    facecolor=vis.PURE_RGB[token],
                    edgecolor="white",
                    lw=0.9,
                    zorder=3,
                )
            )
        ax.add_patch(
            Rectangle(
                (x0, y0),
                tile_w,
                tile_h,
                fill=False,
                edgecolor="#4b4b4b",
                lw=0.9,
                zorder=4,
            )
        )


def _draw_discs(ax, stacks: Sequence[Tuple[str, ...]]) -> None:
    _setup_triangle_axes(ax)
    radius = _disc_radius(len(stacks))
    for stack in stacks:
        x, y = vis.barycentric_xy(*vis.stack_fractions(stack))
        ax.add_patch(
            Circle(
                (x, y),
                radius=radius,
                facecolor=vis.blend_rgb(stack),
                edgecolor="white",
                lw=0.9,
                zorder=3,
            )
        )


def _draw_regions(ax, stacks: Sequence[Tuple[str, ...]]) -> None:
    _setup_triangle_axes(ax, show_grid=False)
    tri = list(_triangle_vertices())
    sites = [vis.barycentric_xy(*vis.stack_fractions(s)) for s in stacks]
    rgbs = [vis.blend_rgb(s) for s in stacks]
    regions = []
    for i, point in enumerate(sites):
        others = [q for j, q in enumerate(sites) if j != i]
        poly = vis.bounded_voronoi_polygon(point, others, tri)
        regions.append(poly)
        if len(poly) >= 3:
            xs = [x for x, _ in poly]
            ys = [y for _, y in poly]
            ax.fill(xs, ys, color=rgbs[i], lw=0, zorder=2)

    number_size = _region_number_size(len(stacks))
    for idx, (poly, rgb) in enumerate(zip(regions, rgbs), 1):
        if len(poly) < 3:
            continue
        cx, cy = vis.polygon_centroid(poly)
        txt = "black" if sum(rgb) > vis.TEXT_LIGHT_THRESHOLD else "white"
        ax.text(
            cx,
            cy,
            str(idx),
            ha="center",
            va="center",
            fontsize=number_size,
            fontweight="bold",
            color=txt,
            zorder=4,
        )


def _render_single_case(case: CaseSpec, out_dir: Path) -> None:
    stacks = _ordered_stacks(case)
    fig = plt.figure(figsize=(12.6, 4.7), facecolor="none")
    gs = fig.add_gridspec(1, 3, left=0.05, right=0.985, top=0.95, bottom=0.08, wspace=0.12)

    axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
    _draw_tiles(axes[0], stacks)
    _draw_discs(axes[1], stacks)
    _draw_regions(axes[2], stacks)

    png = out_dir / f"cmy_publication_{case.slug}.png"
    pdf = out_dir / f"cmy_publication_{case.slug}.pdf"
    fig.savefig(png, dpi=280, bbox_inches="tight", pad_inches=0.12, transparent=True)
    fig.savefig(pdf, bbox_inches="tight", pad_inches=0.12, transparent=True)
    plt.close(fig)


def _render_composite(cases: Sequence[CaseSpec], out_dir: Path) -> None:
    fig = plt.figure(figsize=(13.8, 15.0), facecolor="none")
    gs = fig.add_gridspec(
        len(cases),
        3,
        left=0.06,
        right=0.985,
        top=0.985,
        bottom=0.04,
        wspace=0.08,
        hspace=0.34,
    )

    for row, case in enumerate(cases):
        stacks = _ordered_stacks(case)
        axes = [fig.add_subplot(gs[row, col]) for col in range(3)]
        _draw_tiles(axes[0], stacks)
        _draw_discs(axes[1], stacks)
        _draw_regions(axes[2], stacks)
    png = out_dir / "cmy_publication_comparison.png"
    pdf = out_dir / "cmy_publication_comparison.pdf"
    fig.savefig(png, dpi=280, bbox_inches="tight", pad_inches=0.12, transparent=True)
    fig.savefig(pdf, bbox_inches="tight", pad_inches=0.12, transparent=True)
    plt.close(fig)


def main() -> None:
    out_dir = Path("/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        _render_single_case(case, out_dir)
    _render_composite(CASES, out_dir)


if __name__ == "__main__":
    main()
