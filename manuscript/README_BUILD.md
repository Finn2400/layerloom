# LayerLoom Reviewer-Ready Revision Package

This directory contains the local revision package assembled from the submitted anonymous manuscript, verified submitted figure assets, the complete current supplement, and traceable tensile calculations.

## Primary files

- `layerloom__computers_graphics_anon.tex`: anonymous main manuscript.
- `layerloom_references.bib`: bibliography, including the three citations requested by Reviewer 2.
- `figures/`: the submitted main-figure assets, with Figures 1--4 and 6--8
  retained and Figure 5 replaced by the author-provided revised benchmark figure.
- `supplement/supp.tex`: complete S1-S11 supplement.
- `REVIEWER_COMMENT_CHECKLIST.md`: exact-comment checklist with evidence and claim boundaries.
- `response_to_reviewers_draft.md`: concise response-letter draft.

## Reproducibility material

- `analysis/build_revision_figures.py`: archived helper functions for figure development; its default command intentionally does not overwrite submitted main-figure artwork.
- `analysis/generate_tensile_screen.py`: reads the 20 raw Mark-10 workbooks and rebuilds Supplementary Figs. S9-S11 plus the specimen- and group-level CSVs supporting Tables S6-S7.
- `data/tensile_raw/`: the raw Mark-10 workbooks used by the tensile script.
- `supplement/tables/`: retained color, benchmark, and tensile CSV summaries.

These inputs, scripts, the exact TeX source, relevant palette/configuration
files, and calibrated image-analysis inputs are collected in the versioned
archive under `public_archive/layerloom_cag_revision_2026_07_23/`. The matching
public repository branch is recorded on the separate identifying title page;
the anonymous reviewer-facing manuscript refers only to the anonymized
supplementary repository.

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

Figures 1--4 and 6--8 retain the submitted artwork. Figure 5 is the
author-provided revised benchmark figure. The tracked/generated supplementary
outputs are `supplement/figures/supp_fig09_tensile_strength_screen.png`,
`supplement/figures/supp_fig10_tensile_crosshead_curves.png`, and
`supplement/tables/tensile_screen_specimen_summary.csv`.

## Overleaf upload tree

The final clean upload-only tree is `overleaf_upload/` and its corresponding zip is created after the final fresh compile. It intentionally excludes raw workbooks, scripts, review documents, and TeX build artifacts.
