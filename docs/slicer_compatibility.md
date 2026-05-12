# Slicer Compatibility Notes

LayerLoom outputs standard 3MF files that encode woven color as geometry. The
slicer does not need to understand LayerLoom; it only needs to preserve the
objects and assign them to the intended filaments.

## Tested Slicers

LayerLoom outputs have been opened and sliced in:

| Slicer | Status | Notes |
| --- | --- | --- |
| OrcaSlicer | Tested opening and slicing | Keep slicer layer height matched to the LayerLoom weave height. |
| Bambu Studio | Tested opening and slicing | Keep first layer height as a multiple of the weave height. |
| PrusaSlicer | Tested opening and slicing | Keep first layer height as a multiple of the weave height. |
| Ultimaker Cura 5.12.0 | Tested opening and slicing | Disable automatic object drop-to-plate behavior. |

These notes describe practical compatibility, not full preservation of vendor
project semantics. LayerLoom outputs are normalized generic 3MFs, not
slicer-project-preserving files.

## Layer Height Alignment

LayerLoom creates woven geometry in fixed Z-bands. The slicer should use the
same layer height as the LayerLoom weave height, and the first layer height
should be an integer multiple of that value.

| Weave height | Good first layer heights | Bad first layer heights |
| --- | --- | --- |
| `0.08 mm` | `0.08 mm`, `0.16 mm`, `0.24 mm` | `0.20 mm` |
| `0.10 mm` | `0.10 mm`, `0.20 mm`, `0.30 mm` | `0.24 mm` |
| `0.12 mm` | `0.12 mm`, `0.24 mm`, `0.36 mm` | `0.20 mm` |
| `0.20 mm` | `0.20 mm`, `0.40 mm` | `0.24 mm` |

The rule is:

```text
first_layer_height / weave_height = integer
```

For example, a `0.08 mm` weave can use a `0.16 mm` first layer, but not a
`0.20 mm` first layer.

## Cura-Specific Note

In Ultimaker Cura 5.12.0, turn off automatic object drop-to-plate behavior
before loading woven LayerLoom outputs. Woven models intentionally contain
geometry arranged in Z-bands; automatically moving individual objects to the
plate can destroy that vertical alignment.

## What To Expect In The Slicer

LayerLoom output usually appears as a conventional multi-object or
multi-assembly 3MF. Assign the generated color objects to the matching physical
filaments, then slice normally.

The output is intended for slicing and printing, not for recovering the original
vendor project. Modifiers, textures, vendor metadata, and full slicer project
settings are not guaranteed to round-trip.
