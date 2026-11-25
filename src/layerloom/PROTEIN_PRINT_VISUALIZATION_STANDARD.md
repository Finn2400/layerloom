# LayerLoom Protein Print Visualization Standard

This is the canonical reference for preparing proteins in ChimeraX for 3D
printing with LayerLoom. Use these defaults unless a project explicitly needs a
surface representation, unusual scaling, or different structural cleanup.

## Motivation

Ordinary molecular graphics optimize for visual detail, not fabrication. Thin
ribbons, dashed pseudobonds, isolated waters, small atoms, and unsupported
chains can disappear or break during printing. This standard keeps the familiar
ChimeraX appearance while thickening important geometry, adding printable
connections, preserving meaningful colors, and exporting GLB vertex colors in
a form LayerLoom can assign and weave reliably.

The Jaila gift based on PDB `1E27` is the visual reference. Its editable session
is:

`~/Downloads/Jaila_gift_1E27_green_pHLA_reference.cxs`

The companion written reference is:

`~/Downloads/Jaila_gift_1E27_green_pHLA_reference.md`

The executable preset and its regression test are:

- `scripts/chimerax_make_1e27_pmhc_presentation.py`
- `tests/test_chimerax_make_1e27_pmhc_presentation.py`

Use the session for visual comparison and the preset as the implementation
example. The preset colors the retained atoms before it creates one solid,
half-colored `#1@ca` strut group, then hides the protein atoms and native bonds
before GLB export.

## Standard Geometry Defaults

| Feature | Default |
| --- | --- |
| Protein representation | Ribbon/cartoon |
| Ribbon width | 3.6 Angstrom |
| Ribbon thickness | 2.0 Angstrom |
| Ribbon cross-section | Oval, 24 sides, 20 divisions, arrows off |
| Strut endpoints | Alpha carbons (`@ca`) |
| Maximum strut length | 7.0 Angstrom |
| Minimum loop separation | 30.0 Angstrom |
| Strut radius | 0.8 Angstrom |
| Strut appearance | Solid, not dashed; half-colored by endpoints |
| Small-molecule/peptide atom radius | 2.0 Angstrom |
| Small-molecule/peptide stick radius | 1.1 Angstrom |

Hide the protein's atomic representation after making the ribbon and struts.
Keep atoms and bonds visible for a peptide or ligand when their chemistry is
part of the intended model. Remove solvent and other disconnected non-printable
objects unless they are deliberately retained.

## Color Defaults

Coloring is project-specific, but colors must be applied to the underlying
atoms as well as the visible representation so half-colored struts inherit the
correct endpoint colors.

For the green `1E27` pHLA reference:

| Region | Color |
| --- | --- |
| HLA ribbon | `#9CD36E` |
| Peptide carbon/backbone | `#3AC8DC` |
| Peptide nitrogen | `#5C9CCA` |
| Peptide oxygen | `#E83020` |

For general proteins, use residue rainbow, chain rainbow, or explicit chain
colors. Prefer a small deliberate palette that LayerLoom can reproduce.

## Residue Colors Without Breaking Chains

When colors mark intended LayerLoom regions within a chain, keep one intact
ChimeraX atomic model. Color residue selections on that model; do **not** make
one `combine` copy per color and delete each copy's complement. Model copies
break the continuous cartoon at the boundary and prevent full-chain struts
from reinforcing across it.

Apply colors to cartoons **and** their underlying atoms before making struts.
For example, this keeps an HLA chain continuous while placing a color boundary
between its alpha1 and alpha2 peptide-binding regions:

```chimerax
cartoon #1/N
color #1/N:1-90 #004080 target ac
color #1/N:91-182 #40BFFF target ac
show (#1 & protein) bonds
struts #1@ca length 7 loop 30 radius 0.8 fattenRibbon true replace true name layerloom_struts
hide #1 atoms
hide #1 bonds
```

The general `struts` command normally omits adjacent backbone residues.  If
the colored regions will become separate print objects, add one solid,
half-colored CA--CA pseudobond across every residue-color boundary (for this
example, N90--N91) while retaining the one atomic model.  This seam brace is
print geometry; it does not split, duplicate, or alter the molecular chain.

After the `struts` command, use the `_solidify_struts` pattern in
`scripts/chimerax_prepare_layerloom.py` or
`scripts/chimerax_make_1e27_pmhc_presentation.py` to set `dashes = 0`, the
0.8-Angstrom radius, and `halfbond = True` on the group and each pseudobond.
Reapply the ribbon style after `fattenRibbon true` if needed. A single
full-model `@ca` selection can add both intrachain braces and native-proximity
interchain braces; half-colored endpoints preserve the residue-region colors.

LayerLoom separates the exported vertex colors later. Color separation does
not require, and should not be implemented as, atomic chain separation in
ChimeraX. Reopen the GLB and confirm that the ribbon stays continuous, the
solid struts are present, and struts crossing color boundaries inherit the two
endpoint colors.

## General Reproduction

The reusable preparation script is:

`scripts/chimerax_prepare_layerloom.py`

Run it in ChimeraX with a PDB ID or local structure file:

```bash
/Applications/ChimeraX-1.10.1.app/Contents/MacOS/ChimeraX \
  --nogui --exit \
  --script "/Users/finn/Documents/layerloom/scripts/chimerax_prepare_layerloom.py 1ubq --overwrite"
```

Useful options include `--color-mode by-chain`, repeated
`--chain-color A:#RRGGBB`, `--protein-only`, and `--inspect`. The script applies
the standard ribbon and strut geometry automatically.

Before final export, save an editable `.cxs` session with an informative name.
Export GLB with scene centering, shared node centering, retained vertex colors,
and back-face culling disabled. Then reopen the GLB in ChimeraX for a visual
check before sending it to LayerLoom.

## Reproducing Jaila's 1E27 Reference

Use the dedicated preset:

```bash
/Applications/ChimeraX-1.10.1.app/Contents/MacOS/ChimeraX \
  --nogui --exit \
  --script /Users/finn/Documents/layerloom/scripts/chimerax_make_1e27_pmhc_presentation.py
```

This preset retains HLA chain A residues 1-180 and peptide chain C residues
1-9. It removes water, beta-2-microglobulin chain B, and the HLA alpha3 domain.
Those deletions reproduce the presentation composition and are **not** general
defaults for unrelated proteins.

## Pre-Weave Checklist

1. Confirm every intended chain or ligand is visible and every unwanted solvent
   or isolated object is gone.
2. Confirm ribbons, peptide sticks, and struts are thick enough at the planned
   physical scale.
3. Confirm struts are solid and their two halves match their endpoint colors.
4. Save the editable `.cxs` session.
5. Export and reopen the GLB in ChimeraX to verify geometry and colors.
6. Run LayerLoom, inspect the labeled/scaled 3MF, and only then weave.

Treat this file as the default decision record for future protein-print work.

## Woven Output Validation

Closed/manifold topology, consistent winding, bounds, and total volume are
necessary checks, but do not establish that a woven mesh is artifact-free.
Check the actual exported coordinates, including float32 precision used by
the slicer, for collapsed/zero-area triangles. Check representative layer
boundaries for duplicate internal faces and coincident edges where regions
assigned the same filament meet. A concatenation of individually closed
regions is not necessarily a clean geometric union.

Do not assume that increasing weave height or applying a generic repair to
the finished woven assembly fixes these defects. Keep source repair separate
from weaving, preserve the approved source geometry, and validate any proposed
repair against both geometry and color-region preservation. Match the slicer's
first and subsequent layer heights to the weave height and disable adaptive
layer heights when evaluating its preview.

The September 27, 2026 MAGE-A3/Titin charge-model investigation found collapsed
cut-plane triangles and coincident internal faces at both 0.08 and 0.12 mm.
Those controlled comparisons had known unresolved defects and were never
approved examples of artifact-free exports. Their geometry and working caches
were permanently removed at the user's request on September 30, 2026.
The cleanup inventory is
`~/Downloads/Jared_Titin_MAGEA3_pHLA/cleanup_2026-09-30.json`; small historical
code and diagnostic records remain under that project's `cleanup_records/`.

### Corrected Charge-Surface Export

The corrected September 27 pair is in
`~/Downloads/Jared_Titin_MAGEA3_pHLA/charge/woven_max5_012_fixed/`.
Its whole-complex `*_WOVEN.3mf` files were superseded and removed September 30,
2026; use the binding-cleft-only pair described below. The scripts and
validation records remain as references.

For these complex continuous-charge surfaces, `surface_field_weave.py` partitions
each printer-layer footprint into physical filament colors before triangulation.
It samples the unchanged molecular solid at the layer midpoint, evaluates the
same smoothed charge field, unions same-filament regions in 2D, and extrudes
closed contours with constrained triangulation. This replaces the problematic
independent 3D binary masks and thin solid boolean cuts. It intentionally makes
0.12 mm layer-stepped geometry; it does not retain a mathematically smooth
exterior between layer midpoints. Overall source bounds changed by less than
0.006 mm for this pair.

The recipes retain at most five consecutive white layers in mixed colors.
Only a few point-contact regions needed 0.001 mm clearance, recorded in
the per-layer audit. No generic post-weave repair, whole-model remeshing,
orientation change, or scaling was applied. All exported parts passed
zero-area triangle checks, closed/oriented checks, and PrusaSlicer CLI import;
every generated layer passed duplicate-face and geometric-edge checks.
This is geometry/import validation, not a physical print or G-code test.

Set both first-layer and subsequent layer heights to **0.12 mm** and disable
adaptive layers for these files. Keep the aligned parts together and assign
`all_red`, `all_white`, and `all_blue` to their matching physical filaments.
The fixed folder includes the job scripts, scalar grids, recipes, and validation
reports for reproduction. This surface-specific workaround does not change the
preferred ChimeraX ribbon, stick, or strut defaults elsewhere in this document.

### Jared Pair: Side-Facing Binding Site

The user's subsequent orientation preference for the MAGE-A3/Titin pair is to
put the binding cleft and peptide on the side of the print. The corresponding
files are in
`~/Downloads/Jared_Titin_MAGEA3_pHLA/charge/woven_max5_012_side/`.
Both original solids and their scalar grids were rotated +90 degrees about X
and grounded before generating new 0.12 mm layers. Do not obtain this pose by
rotating an already woven file, which would rotate the filament layers too.

Scale, charge values, red/white/blue recipes, and attachment geometry are
unchanged. The whole-complex upright and side-facing woven files were removed
at the user's request after validating the cropped replacements. This is a
pose choice for this pair, not a universal orientation rule for all proteins.

### Jared Pair: Binding-Cleft-Only Replacement

The latest September 30, 2026 pair is in
`~/Downloads/Jared_Titin_MAGEA3_pHLA/charge/woven_binding_cleft_012_side/`.
Retain HLA chain A residues 1-182 (alpha1/alpha2) and peptide chain C residues
1-9; remove beta-2-microglobulin chain B and HLA residues 183 onward. Regenerate
the molecular surface after residue selection, rather than cutting woven
geometry. Fill only tiny closed inward-facing cavity shells; never silently
discard disconnected positive-volume protein components.

Keep the existing 1.980915971119904 mm/Angstrom scale, reinforced peptide and
both bridges, +90-degree-X side-facing pose, original charge field, and 0.12 mm
recipes with at most five consecutive white layers in mixed colors. Recenter
and ground by translation only; do not enlarge the cropped model to fill the
plate. The resulting dimensions are approximately 118.35 x 66.49 x 96.49 mm
(MAGE-A3) and 116.52 x 67.66 x 94.80 mm (Titin). Their solid volumes are about
52% lower than the whole-complex models, not an estimate of slicer filament
consumption. The unchanged full-complex field preserves visual comparison;
it is not an electrostatic calculation of the isolated truncated domain.

The final parts remain `all_red`, `all_white`, and `all_blue`. Set first and
subsequent layers to 0.12 mm, disable adaptive layers, and retain their common
alignment. Geometry and slicer import were checked, not G-code or a physical
print. Editable ChimeraX sessions preserve the selected molecular source in
Angstrom coordinates; the reproduction GLBs contain the scaled, side-facing,
reinforced printable solid.
