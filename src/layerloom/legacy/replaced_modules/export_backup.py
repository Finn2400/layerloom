#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layerloom.export
----------------
Minimal 3MF writer for LayerLoom.

Writes one <object> per mesh (no Prusa metadata, no extruder tags).

Intended for use after grouping/merging, e.g.:

    from layerloom.ingest import read_parts
    from layerloom.group_by_color import group_by_color
    from layerloom.export import write_basic_3mf

    parts = read_parts(["baked_input.3mf"])
    grouped = group_by_color(parts, merge=True)   # CMYKW-aware grouping
    write_basic_3mf(items=grouped, out_path="grouped_output.3mf")

Behavior
--------
  • Each (label, mesh) pair becomes one <object> in the 3MF XML.
  • Each object includes <metadata name="Name">LABEL</metadata>.
  • All objects are listed as printable items in the build section.
  • The file is self-contained and minimal (no Prusa metadata).
  • Token content (e.g., '__PAT_c__', '__PAT_k__', '__PAT_w__') is written verbatim; no assumptions about CMY vs CMYKW.

Dependencies
------------
  - trimesh
  - numpy
  - xml.etree.ElementTree
  - zipfile
"""

from __future__ import annotations
from typing import List, Tuple
import zipfile
import numpy as np
import xml.etree.ElementTree as ET
import trimesh

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
XML_NS  = "http://www.w3.org/XML/1998/namespace"
M = lambda tag: f"{{{CORE_NS}}}{tag}"


# ---------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------

def write_basic_3mf(items: List[Tuple[str, trimesh.Trimesh]],
                    out_path: str,
                    title: str | None = None) -> None:
    """
    Write a minimal 3MF file containing one <object> per mesh.

    Parameters
    ----------
    items : list of (label, mesh)
        Meshes to include as separate 3MF objects.
    out_path : str
        Path to write the .3mf file.
    title : str, optional
        Title metadata stored in the 3MF (defaults to output filename).

    Raises
    ------
    ValueError
        If no valid meshes are provided.
    RuntimeError
        If writing to the 3MF container fails.
    """
    if not items:
        raise ValueError("No meshes provided to export.")

    title = title or out_path.split("/")[-1].replace(".3mf", "")

    # ------------------------------------------------------------------
    # Build XML model tree
    # ------------------------------------------------------------------
    root = ET.Element(M("model"), {"unit": "millimeter", f"{{{XML_NS}}}lang": "en-US"})
    ET.SubElement(root, M("metadata"), {"name": "Title"}).text = title

    resources = ET.SubElement(root, M("resources"))
    build = ET.SubElement(root, M("build"))

    obj_id = 1
    for label, mesh in items:
        if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.size == 0:
            continue

        obj_el = ET.SubElement(resources, M("object"),
                               {"id": str(obj_id), "type": "model", "name": label})
        ET.SubElement(obj_el, M("metadata"), {"name": "Name"}).text = label

        # Vertices and faces
        mesh_el = ET.SubElement(obj_el, M("mesh"))
        vs_el = ET.SubElement(mesh_el, M("vertices"))
        for x, y, z in mesh.vertices.view(np.ndarray):
            v = ET.SubElement(vs_el, M("vertex"))
            v.set("x", f"{x:.9f}")
            v.set("y", f"{y:.9f}")
            v.set("z", f"{z:.9f}")

        fs_el = ET.SubElement(mesh_el, M("triangles"))
        for a, b, c in mesh.faces.view(np.ndarray):
            f = ET.SubElement(fs_el, M("triangle"))
            f.set("v1", str(int(a)))
            f.set("v2", str(int(b)))
            f.set("v3", str(int(c)))

        # Build entry
        ET.SubElement(build, M("item"),
                      {"objectid": str(obj_id), "printable": "1", "partnumber": label})
        obj_id += 1

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    # ------------------------------------------------------------------
    # Write ZIP package (.3mf)
    # ------------------------------------------------------------------
    try:
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", _content_types_xml())
            z.writestr("_rels/.rels", _rels_root_xml())
            z.writestr("3D/3dmodel.model", xml_bytes)
    except Exception as e:
        raise RuntimeError(f"Failed to write 3MF file: {e}")

    print(f"[export] wrote {obj_id - 1} object(s) → {out_path}")


# ---------------------------------------------------------------------
# XML helper fragments
# ---------------------------------------------------------------------

def _content_types_xml() -> bytes:
    """Standard [Content_Types].xml for 3MF container."""
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        b'</Types>'
    )


def _rels_root_xml() -> bytes:
    """Root .rels file linking to 3D/3dmodel.model."""
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" '
        b'Target="/3D/3dmodel.model"/>'
        b'</Relationships>'
    )

# ---------------------------------------------------------------------
# Optional: export each mesh as an individual STL file
# ---------------------------------------------------------------------
def write_color_stls(items: list[tuple[str, trimesh.Trimesh]], out_dir: str, verbose: bool = True) -> None:
    """
    Write one STL per (label, mesh) pair.

    Parameters
    ----------
    items : list of (label, mesh)
        Meshes to export separately (e.g., grouped by color).
    out_dir : str
        Destination folder; created if missing.
    verbose : bool, optional
        Print output paths and summary (default: True)
    """
    import os

    os.makedirs(out_dir, exist_ok=True)
    written = 0

    for label, mesh in items:
        if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.size == 0:
            if verbose:
                print(f"[stl] skipped empty mesh: {label}")
            continue

        safe_label = label.replace("/", "_").replace("\\", "_")
        out_path = os.path.join(out_dir, f"{safe_label}.stl")

        try:
            mesh.export(out_path)
            written += 1
            if verbose:
                print(f"[stl] wrote {out_path}")
        except Exception as e:
            print(f"[stl:error] failed to write {label}: {e}")

    if verbose:
        print(f"[stl] wrote {written} STL file(s) to {out_dir}")


# ---------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from layerloom.ingest import read_parts

    if len(sys.argv) < 3:
        print("Usage: python -m layerloom.export input.3mf output.3mf")
        sys.exit(1)

    input_3mf = sys.argv[1]
    output_3mf = sys.argv[2]

    parts = read_parts([input_3mf])
    write_basic_3mf(items=parts, out_path=output_3mf)
    print(f"[done] wrote {output_3mf}")

