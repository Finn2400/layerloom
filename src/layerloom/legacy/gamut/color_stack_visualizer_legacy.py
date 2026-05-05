#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
color_stack_visualizer.py
-------------------------
Generate all non-redundant CMY color stacks up to a given height and max-run constraint,
compute their subtractive mixed colors, sort by hue, and visualize them as a multipage PDF.
"""

import itertools
import colorsys
from matplotlib import pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

PALETTE = ["C", "M", "Y"]
CMY_TO_RGB = {
    "C": (0.0, 1.0, 1.0),
    "M": (1.0, 0.0, 1.0),
    "Y": (1.0, 1.0, 0.0)
}


# ─────────────────────────────────────────────
# Stack enumeration and constraints
# ─────────────────────────────────────────────
def is_valid(stack, max_run):
    """Return True if no run of identical colors exceeds max_run."""
    run = 1
    for i in range(1, len(stack)):
        run = run + 1 if stack[i] == stack[i - 1] else 1
        if run > max_run:
            return False
    return True


def canonical_rotation(stack):
    """Return the lexicographically smallest rotation of the sequence."""
    n = len(stack)
    rotations = [tuple(stack[i:] + stack[:i]) for i in range(n)]
    return min(rotations)


def generate_stacks(height, max_run):
    """Generate all rotation-unique stacks up to 'height' layers."""
    all_stacks = []
    seen = set()
    for stack in itertools.product(PALETTE, repeat=height):
        if not is_valid(stack, max_run):
            continue
        key = canonical_rotation(list(stack))
        if key not in seen:
            seen.add(key)
            all_stacks.append(stack)
    return all_stacks


# ─────────────────────────────────────────────
# Deduplication and color math
# ─────────────────────────────────────────────
def dedupe_by_composition(stacks):
    """Keep one representative per unique CMY count composition."""
    seen = set()
    unique = []
    for s in stacks:
        comp = (s.count("C"), s.count("M"), s.count("Y"))
        if comp not in seen:
            seen.add(comp)
            unique.append(s)
    return unique


def mix_cmy_to_rgb(stack):
    """Compute average subtractive mix of CMY stack."""
    n = len(stack)
    fC = sum(1 for s in stack if s == "C") / n
    fM = sum(1 for s in stack if s == "M") / n
    fY = sum(1 for s in stack if s == "Y") / n
    r = 1 - fC
    g = 1 - fM
    b = 1 - fY
    return (r, g, b)


def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(
        int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255)
    )


def sort_by_hue(stacks):
    """Sort stacks by hue of their blended RGB color."""
    def hue_key(stack):
        r, g, b = mix_cmy_to_rgb(stack)
        h, _, _ = colorsys.rgb_to_hsv(r, g, b)
        return h
    return sorted(stacks, key=hue_key)


# ─────────────────────────────────────────────
# Plotting utilities
# ─────────────────────────────────────────────
def plot_page(stacks, height, max_run, pdf):
    """Plot one page of stacks and save to PDF."""
    stacks = sort_by_hue(stacks)
    n = len(stacks)
    cols = min(10, n)
    rows = (n + cols - 1) // cols

    fig, ax = plt.subplots(figsize=(cols * 1.1, rows * 0.8))
    ax.set_title(f"Height = {height}, Max Run = {max_run} (n={n})", fontsize=14, pad=12)
    ax.axis("off")

    cell_w, cell_h = 1.0 / cols, 1.0 / rows
    for idx, stack in enumerate(stacks):
        c = idx % cols
        r = idx // cols
        rgb = mix_cmy_to_rgb(stack)
        hexcol = rgb_to_hex(rgb)
        x0, y0 = c * cell_w, 1 - (r + 1) * cell_h

        # Draw color chip
        ax.add_patch(plt.Rectangle((x0, y0), cell_w, cell_h, color=rgb, ec="k", lw=0.4))

        label = "".join(stack)
        text_color = "black" if sum(rgb) > 1.3 else "white"

        # Stack label
        ax.text(
            x0 + cell_w / 2,
            y0 + cell_h / 2 + 0.015,
            label,
            ha="center",
            va="center",
            fontsize=8,
            color=text_color,
        )
        # Hex label
        ax.text(
            x0 + cell_w / 2,
            y0 + 0.02,
            hexcol,
            ha="center",
            va="center",
            fontsize=6,
            color=text_color,
        )

    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


# ─────────────────────────────────────────────
# Main driver
# ─────────────────────────────────────────────
def main():
    output = "color_permutations.pdf"
    with PdfPages(output) as pdf:
        for height in range(1, 7):
            for max_run in range(1, 6):
                stacks = generate_stacks(height, max_run)
                stacks = dedupe_by_composition(stacks)
                if stacks:
                    plot_page(stacks, height, max_run, pdf)

        # Summary page
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.axis("off")
        ax.text(
            0.5, 0.5,
            "CMY Color Stack Visualizer\n"
            "— rotation & composition unique —\n"
            "Subtractive CMY → RGB blend, hue sorted.\n\n"
            "Generated with Python + Matplotlib.",
            ha="center", va="center", fontsize=12,
        )
        pdf.savefig(fig)
        plt.close(fig)

    print(f"[✓] Wrote {output}")


if __name__ == "__main__":
    main()

