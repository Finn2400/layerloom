#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compatibility wrapper for the archived chip-only CMY visualizer.

The canonical gamut/palette workflow now lives in:
  - layer_perm_cmy_visualizer.py
  - exact_voronoi_regions_to_3mf.py
"""

from legacy.gamut.color_stack_visualizer_legacy import main


if __name__ == "__main__":
    main()
