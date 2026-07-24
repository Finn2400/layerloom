# LayerLoom Reviewer-Ready Revision Package

This directory contains the local revision package assembled from the submitted anonymous manuscript, verified submitted figure assets, the complete current supplement, and traceable tensile calculations.

## Primary files

- `layerloom__computers_graphics_anon.tex`: anonymous main manuscript.
- `layerloom_references.bib`: bibliography, including the three citations requested by Reviewer 2.
- `figures/`: the eight submitted main-figure assets, retained without visual replacement.
- `supplement/supp.tex`: complete S1-S11 supplement.
- `REVIEWER_COMMENT_CHECKLIST.md`: exact-comment checklist with evidence and claim boundaries.
- `response_to_reviewers_draft.md`: concise response-letter draft.

## Reproducibility material

- `analysis/build_revision_figures.py`: archived helper functions for figure development; its default command intentionally does not overwrite submitted main-figure artwork.
- `analysis/generate_tensile_screen.py`: reads the 20 raw Mark-10 workbooks and rebuilds Supplementary Figs. S9-S11 plus the specimen- and group-level CSVs supporting Tables S6-S7.
- `data/tensile_raw/`: the raw Mark-10 workbooks used by the tensile script.
- `supplement/tables/`: retained color, benchmark, and tensile CSV summaries.

The public release must include these inputs, scripts, the exact TeX source, relevant palette/configuration files, and calibrated image-analysis inputs before resubmission. The local package records that requirement but does not claim it has already been published.

## Local build

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error layerloom__computers_graphics_anon.tex
(cd supplement && latexmk -pdf -interaction=nonstopmode -halt-on-error supp.tex)
```

## Rebuild generated supplementary figures

```bash
/Users/finn/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  analysis/generate_tensile_screen.py \
  --data-dir data/tensile_raw \
  --revision-dir .
```

All submitted main-text artwork in `figures/` is intentionally retained and is not regenerated. The tracked/generated outputs are `supplement/figures/supp_fig09_tensile_strength_screen.png`, `supplement/figures/supp_fig10_tensile_crosshead_curves.png`, and `supplement/tables/tensile_screen_specimen_summary.csv`.

## Overleaf upload tree

The final clean upload-only tree is `overleaf_upload/` and its corresponding zip is created after the final fresh compile. It intentionally excludes raw workbooks, scripts, review documents, and TeX build artifacts.
