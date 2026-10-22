"""Reproducible painted solids for the LayerLoom paint prototype.

Run ``python -m layerloom.paint_examples --output-dir PATH`` to create source
3MFs and editable projects, verify both round trips, and export printable 3MFs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import trimesh

from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.paint_io import load_paint_document, save_paint_document


def example_document(shape: str, size: float = 20.0) -> PaintDocument:
    if not np.isfinite(size) or size <= 0:
        raise ValueError("Example size must be positive")
    if shape == "cube":
        base = trimesh.creation.box(extents=(size, size, size))
        vertices, faces = trimesh.remesh.subdivide_to_size(
            base.vertices, base.faces, max_edge=size / 12, max_iter=6)
        mesh = trimesh.Trimesh(vertices, faces, process=True)
    elif shape == "sphere":
        mesh = trimesh.creation.icosphere(subdivisions=4, radius=size / 2)
    else:
        raise ValueError("Example shape must be cube or sphere")
    mesh.apply_translation((0, 0, size / 2))
    # Deliberate lateral boundaries, visible on curved and flat surfaces.
    centers = mesh.triangles_center
    colors = np.tile(np.array((0, 255, 255, 255), np.uint8), (len(mesh.faces), 1))
    colors[centers[:, 0] > 0] = (255, 0, 255, 255)
    return PaintDocument([
        PaintMesh(shape, f"Painted {shape}", mesh.vertices, mesh.faces, colors)
    ], metadata={"example": shape, "size_mm": size})


def assert_roundtrip(before: PaintDocument, after: PaintDocument, *, originals: bool = True) -> None:
    if len(before.meshes) != len(after.meshes):
        raise AssertionError("Round trip changed mesh count")
    for left, right in zip(before.meshes, after.meshes):
        for field in ("vertices", "faces", "face_rgba") + (("source_rgba",) if originals else ()):
            if not np.array_equal(getattr(left, field), getattr(right, field)):
                raise AssertionError(f"Round trip changed {left.name} {field}")
    if originals and before.metadata != after.metadata:
        raise AssertionError("Round trip changed document metadata")


def build_examples(output: Path, *, size: float = 20, export: bool = True,
                   layer_height: float = .2, pitch: float = .75) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    reports = {}
    for shape in ("cube", "sphere"):
        doc = example_document(shape, size)
        source = output / f"{shape}_source.3mf"
        save_paint_document(doc, source)
        imported = load_paint_document(source)
        assert_roundtrip(doc, imported, originals=False)
        project = output / f"{shape}.llpaint"
        save_paint_document(imported, project)
        reopened = load_paint_document(project)
        assert_roundtrip(imported, reopened)
        info = {"source_3mf": str(source.resolve()), "project": str(project.resolve()),
                "source_roundtrip_exact": True, "project_roundtrip_exact": True,
                "triangles": sum(len(m.faces) for m in reopened.meshes)}
        if export:
            from layerloom.paint_export import export_painted_3mf
            # Two mixed recipes exercise the global layer phase and all CMY
            # filaments, independently of nearest-palette numerical changes.
            for mode in ("direct", "woven"):
                overrides = ({"#00FFFF": "c", "#FF00FF": "m"} if mode == "direct"
                             else {"#00FFFF": "cy", "#FF00FF": "cm"})
                info[mode] = export_painted_3mf(
                    reopened, output / f"{shape}_{mode}.3mf", layer_height=layer_height,
                    pitch=pitch, palette_name="CMY", mode=mode, recipe_overrides=overrides)
        reports[shape] = info
    (output / "examples-validation.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--size", type=float, default=20)
    parser.add_argument("--layer-height", type=float, default=.2)
    parser.add_argument("--pitch", type=float, default=.75)
    parser.add_argument("--source-only", action="store_true")
    args = parser.parse_args()
    build_examples(args.output_dir, size=args.size, export=not args.source_only,
                   layer_height=args.layer_height, pitch=args.pitch)
    print(f"Verified painted examples: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
