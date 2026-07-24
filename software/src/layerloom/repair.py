"""Safe 3MF mesh repair helpers for headless LayerLoom."""

from __future__ import annotations

import os
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
M = lambda tag: f"{{{CORE_NS}}}{tag}"


@dataclass(frozen=True)
class MeshGeometryCounts:
    objects: int = 0
    vertices: int = 0
    triangles: int = 0


@dataclass(frozen=True)
class RepairResult:
    path: str
    repaired: bool
    warning: Optional[str] = None
    before: MeshGeometryCounts = MeshGeometryCounts()
    after: MeshGeometryCounts = MeshGeometryCounts()


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_model_xml_name(zf: zipfile.ZipFile) -> Optional[str]:
    if "3D/3dmodel.model" in zf.namelist():
        return "3D/3dmodel.model"
    for name in zf.namelist():
        if name.startswith("3D/") and name.lower().endswith(".model"):
            return name
    return None


def count_3mf_mesh_geometry(path: str | os.PathLike[str]) -> MeshGeometryCounts:
    counts = {"objects": 0, "vertices": 0, "triangles": 0}
    with zipfile.ZipFile(path, "r") as zf:
        model_name = _find_model_xml_name(zf)
        if not model_name:
            return MeshGeometryCounts()
        current: Optional[dict[str, Any]] = None
        with zf.open(model_name, "r") as model_fp:
            for event, elem in ET.iterparse(model_fp, events=("start", "end")):
                tag = _strip_ns(elem.tag)
                if event == "start" and tag == "object":
                    current = {"type": (elem.get("type") or "").strip().lower(), "mesh": False, "v": 0, "t": 0}
                elif event == "start" and tag == "mesh" and current is not None:
                    current["mesh"] = True
                elif event == "end" and current is not None:
                    if tag == "vertex" and current["mesh"]:
                        current["v"] += 1
                    elif tag == "triangle" and current["mesh"]:
                        current["t"] += 1
                    elif tag == "object":
                        obj_type = current["type"]
                        if current["mesh"] and (not obj_type or obj_type == "model"):
                            counts["objects"] += 1
                            counts["vertices"] += int(current["v"])
                            counts["triangles"] += int(current["t"])
                        current = None
                if event == "end":
                    elem.clear()
    return MeshGeometryCounts(**counts)


def repair_rejection_reason(before: MeshGeometryCounts, after: MeshGeometryCounts) -> Optional[str]:
    if before.objects > 0 and after.objects != before.objects:
        return f"Repair discarded because object count changed ({before.objects}->{after.objects})."
    if before.triangles > 0 and after.triangles <= 0:
        return "Repair discarded because repaired output has no triangles."
    if before.vertices > 0 and after.vertices <= 0:
        return "Repair discarded because repaired output has no vertices."
    if before.triangles >= 1000 and after.triangles / float(before.triangles) < 0.25:
        return (
            "Repair discarded because repair removed too much geometry "
            f"(triangles {before.triangles}->{after.triangles})."
        )
    if before.vertices >= 1000 and after.vertices / float(before.vertices) < 0.20:
        return (
            "Repair discarded because repair removed too many vertices "
            f"(vertices {before.vertices}->{after.vertices})."
        )
    return None


def _require_repair_deps():
    try:
        import lib3mf  # type: ignore
        import pymeshlab as ml  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "3MF repair requires the headless extras: lib3mf and pymeshlab. "
            "Install with `pip install layerloom[headless]`."
        ) from exc
    return lib3mf, ml


def _percentage_value(ml: Any, value: float):
    factory = getattr(ml, "PercentageValue", None) or getattr(ml, "Percentage", None)
    return factory(value) if factory is not None else value


def _apply_safe(ms: Any, name: str, quiet: bool, **kw) -> None:
    try:
        ms.apply_filter(name, **kw)
    except Exception as exc:
        if not quiet:
            print(f"      repair skipped {name}: {exc}")


def _repair_geometry(vertices: np.ndarray, faces: np.ndarray, *, quiet: bool) -> tuple[np.ndarray, np.ndarray]:
    _lib3mf, ml = _require_repair_deps()
    ms = ml.MeshSet()
    ms.add_mesh(ml.Mesh(vertex_matrix=vertices.astype(np.float64), face_matrix=faces.astype(np.int32)), "part")
    ms.set_current_mesh(0)
    _apply_safe(ms, "meshing_remove_duplicate_vertices", quiet)
    _apply_safe(ms, "meshing_remove_duplicate_faces", quiet)
    _apply_safe(ms, "meshing_merge_close_vertices", quiet, threshold=_percentage_value(ml, 0.01))
    _apply_safe(ms, "meshing_repair_non_manifold_edges", quiet, method="Remove Faces")
    _apply_safe(ms, "meshing_repair_non_manifold_vertices", quiet, vertdispratio=0.02)
    _apply_safe(
        ms,
        "meshing_close_holes",
        quiet,
        maxholesize=60,
        selected=False,
        newfaceselected=False,
        selfintersection=True,
        refinehole=False,
        refineholeedgelen=_percentage_value(ml, 3.0),
    )
    _apply_safe(ms, "meshing_remove_unreferenced_vertices", quiet)
    mesh = ms.current_mesh()
    v2 = mesh.vertex_matrix().astype(np.float64)
    f2 = mesh.face_matrix().astype(np.int32)
    if v2.size == 0 or f2.size == 0:
        return vertices, faces
    return v2, f2


def _iter_mesh_objects(model: Any) -> list[Any]:
    it = model.GetMeshObjects()
    out = []
    while it.MoveNext():
        out.append(it.GetCurrentMeshObject())
    return out


def safe_repair_3mf(
    input_3mf: str,
    output_3mf: str,
    *,
    strict: bool = False,
    quiet: bool = True,
) -> RepairResult:
    """Repair direct mesh objects and reject suspicious repaired output."""
    try:
        lib3mf, _ml = _require_repair_deps()
    except Exception as exc:
        if strict:
            raise
        return RepairResult(path=os.path.abspath(input_3mf), repaired=False, warning=str(exc))

    before = count_3mf_mesh_geometry(input_3mf)
    try:
        wrapper = lib3mf.Wrapper()
        model = wrapper.CreateModel()
        reader = model.QueryReader("3mf")
        if hasattr(reader, "SetStrictModeActive"):
            reader.SetStrictModeActive(False)
        reader.ReadFromFile(str(input_3mf))

        for obj in _iter_mesh_objects(model):
            verts = obj.GetVertices()
            tris = obj.GetTriangleIndices()
            v = np.asarray([p.Coordinates for p in verts], dtype=np.float64)
            f = np.asarray([t.Indices for t in tris], dtype=np.int32)
            if v.size == 0 or f.size == 0:
                continue
            v2, f2 = _repair_geometry(v, f, quiet=quiet)
            positions = []
            for xyz in v2:
                p = lib3mf.Position()
                p.Coordinates = (float(xyz[0]), float(xyz[1]), float(xyz[2]))
                positions.append(p)
            triangles = []
            for face in f2:
                t = lib3mf.Triangle()
                t.Indices = (int(face[0]), int(face[1]), int(face[2]))
                triangles.append(t)
            obj.SetGeometry(positions, triangles)

        Path(output_3mf).parent.mkdir(parents=True, exist_ok=True)
        writer = model.QueryWriter("3mf")
        writer.WriteToFile(str(output_3mf))
    except Exception as exc:
        if strict:
            raise RuntimeError(f"3MF repair failed: {exc}") from exc
        return RepairResult(path=os.path.abspath(input_3mf), repaired=False, warning=f"Repair failed; using unrepaired input: {exc}")

    after = count_3mf_mesh_geometry(output_3mf)
    reject = repair_rejection_reason(before, after)
    if reject:
        if strict:
            raise RuntimeError(reject)
        return RepairResult(path=os.path.abspath(input_3mf), repaired=False, warning=reject, before=before, after=after)
    return RepairResult(path=os.path.abspath(output_3mf), repaired=True, before=before, after=after)
