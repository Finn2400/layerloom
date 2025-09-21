# Developer Tools (Layer Loom)

These scripts are for debugging and inspection. They are **not** installed with the `layerloom` package or CLI — they live here for developers who want to peek inside intermediate steps.

## Tools

- `ll_parts_report.py` — print per-object bounds, triangle counts, and IDs from a baked 3MF.
- `ll_bands_dryrun.py` — preview which Z-bands (slices) are non-empty for each part, without exporting files.
- `ll_route_groups.py` — route bands into K groups (e.g. EVEN/ODD), output a manifest for inspection.
- `ll_export_groups_stl.py` — export grouped STLs to a folder for quick slicer inspection.

## Usage examples

```bash
# Print part statistics
python3 scripts/dev/ll_parts_report.py -i samples/benchy_cutup_baked.3mf

# Dry-run banding at 0.20 mm
python3 scripts/dev/ll_bands_dryrun.py -i samples/benchy_cutup_baked.3mf -H 0.20

# Route into 2 groups, dump manifest
python3 scripts/dev/ll_route_groups.py -i samples/benchy_cutup_baked.3mf \
  -H 0.20 --z0-mode auto-global --groups 2 --manifest route.json

# Export grouped STLs for slicer preview
python3 scripts/dev/ll_export_groups_stl.py -i samples/benchy_cutup_baked.3mf \
  -o out_stl -H 0.20 --z0-mode auto-global --groups 2 --scene
```

