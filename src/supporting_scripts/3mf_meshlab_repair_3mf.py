#!/usr/bin/env python3
"""
3mf_meshlab_repair_3mf.py

Repairs each MeshObject inside an input .3mf (PyMeshLab) and writes a repaired .3mf (lib3mf).

Why this version exists:
Some 3MFs (especially from slicers / “preview” / “weave” / vendor pipelines) contain XML that lib3mf refuses to read,
even in non-strict mode. Common failures:
  - "Invalid Element in namespace."
  - "Could not find Object associated to the Build item"
  - "Unknown Model Metadata."
  - "Duplicate Model Metadata"

This script now has a robust fallback path:
  1) Try strict read
  2) Try non-strict read
  3) If either fails, create a CLEANED copy of the 3MF by editing the .model XML:
       - remove ALL <metadata> entries (fixes Unknown/Duplicate Model Metadata)
       - remove non-core namespace elements/attrs (fixes Invalid Element in namespace)
       - drop build items referencing missing objects
       - drop components referencing missing objects
       - ensure build has at least one valid item if objects exist
     Then read the cleaned copy.

Goal: recover geometry + placements so we can repair meshes and re-write a sane 3MF.
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import numpy as np
import pymeshlab as ml
import lib3mf

try:
    import resource
except Exception:
    resource = None


def percentage_value(value: float):
    factory = getattr(ml, "PercentageValue", None) or getattr(ml, "Percentage", None)
    return factory(value) if factory is not None else value


def rss_mb() -> float | None:
    if resource is None:
        return None
    try:
        raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    if sys.platform == "darwin":
        return raw / (1024.0 * 1024.0)
    return raw / 1024.0


def perf(label: str, started_at: float, *, extra: str = "", quiet: bool = False) -> None:
    if quiet:
        return
    elapsed = time.perf_counter() - started_at
    msg = f"[perf] {label}: {elapsed:.3f}s"
    rss = rss_mb()
    if rss is not None:
        msg += f" | rss≈{rss:.1f} MB"
    if extra:
        msg += f" | {extra}"
    print(msg)


@dataclass
class RepairConfig:
    merge_threshold_pct: float = 0.01
    max_hole_size: int = 60
    vertdisp_ratio: float = 0.02

    targeted: bool = False
    dilate: int = 2
    delete_mode: str = "faces"  # "faces" or "faces_and_vertices"
    select_nonmanifold: bool = True
    select_self_intersections: bool = False

    # Safety for targeted deletion
    max_target_delete_fraction: float = 0.75
    force_targeted_wipe: bool = False

    report: bool = False
    quiet: bool = False


# ------------------------- naming helpers -------------------------


def collapse_double_underscores(s: str) -> str:
    while "__" in s:
        s = s.replace("__", "_")
    return s


def normalize_out_path(p: Path) -> Path:
    name = collapse_double_underscores(p.name)

    stem = Path(name).stem
    suffix = Path(name).suffix
    if suffix.lower() == ".3mf" and stem.lower().endswith("_3mf"):
        stem = stem[:-4]
        name = f"{stem}{suffix}"

    return p.with_name(name)


def default_out_path(infile: Path, targeted: bool) -> Path:
    tag = "meshlab_repair_targeted" if targeted else "meshlab_repair"
    return infile.with_name(f"{infile.stem}_{tag}{infile.suffix}")


# ------------------------- pymeshlab repair -------------------------


def apply_safe(ms: ml.MeshSet, name: str, quiet: bool, **kw) -> bool:
    try:
        ms.apply_filter(name, **kw)
        return True
    except Exception as e:
        if not quiet:
            print(f"      skip {name}: {e}")
        return False


def topo(ms: ml.MeshSet) -> Dict[str, Any]:
    return ms.apply_filter("get_topological_measures")


def selected_faces(ms: ml.MeshSet) -> int:
    return ms.current_mesh().selected_face_number()


def targeted_seam_cleanup(ms: ml.MeshSet, cfg: RepairConfig) -> int:
    if ms.current_mesh().face_number() <= 0:
        return 0

    apply_safe(ms, "set_selection_none", cfg.quiet)

    if cfg.select_nonmanifold:
        apply_safe(ms, "compute_selection_by_non_manifold_edges_per_face", cfg.quiet)

    if cfg.select_self_intersections:
        apply_safe(ms, "compute_selection_by_self_intersections_per_face", cfg.quiet)

    if selected_faces(ms) == 0:
        return 0

    for _ in range(max(0, cfg.dilate)):
        apply_safe(ms, "apply_selection_dilatation", cfg.quiet)

    n_sel = selected_faces(ms)
    face_count = int(ms.current_mesh().face_number())
    if face_count > 0:
        frac = n_sel / face_count
        if frac > cfg.max_target_delete_fraction and not cfg.force_targeted_wipe:
            if not cfg.quiet:
                print(
                    f"      targeted skip: selection would delete {n_sel}/{face_count} faces ({frac:.1%}) "
                    f"(raise --max_target_delete_fraction or use --force_targeted_wipe)"
                )
            apply_safe(ms, "set_selection_none", cfg.quiet)
            return 0

    if cfg.delete_mode == "faces":
        apply_safe(ms, "meshing_remove_selected_faces", cfg.quiet)
    else:
        apply_safe(ms, "meshing_remove_selected_vertices_and_faces", cfg.quiet)

    apply_safe(ms, "meshing_remove_unreferenced_vertices", cfg.quiet)
    return n_sel


def repair_geometry(v: np.ndarray, f: np.ndarray, cfg: RepairConfig) -> Tuple[np.ndarray, np.ndarray, dict, dict]:
    ms = ml.MeshSet()
    mesh = ml.Mesh(vertex_matrix=v.astype(np.float64), face_matrix=f.astype(np.int32))
    ms.add_mesh(mesh, "part")
    ms.set_current_mesh(0)

    t_before = topo(ms)

    # Remove exact duplicate topology before any close-vertex merge. GLB imports
    # from viewers can contain coincident duplicate surfaces; merging vertices
    # first turns those duplicates into non-manifold edges that later filters
    # remove aggressively.
    apply_safe(ms, "meshing_remove_duplicate_vertices", cfg.quiet)
    apply_safe(ms, "meshing_remove_duplicate_faces", cfg.quiet)
    apply_safe(ms, "meshing_merge_close_vertices", cfg.quiet, threshold=percentage_value(cfg.merge_threshold_pct))
    apply_safe(ms, "meshing_repair_non_manifold_edges", cfg.quiet, method="Remove Faces")
    apply_safe(ms, "meshing_repair_non_manifold_vertices", cfg.quiet, vertdispratio=cfg.vertdisp_ratio)

    if cfg.targeted:
        t_mid = topo(ms)
        has_nm = (
            t_mid.get("non_two_manifold_edges", 0) > 0
            or t_mid.get("non_two_manifold_vertices", 0) > 0
            or t_mid.get("incident_faces_on_non_two_manifold_edges", 0) > 0
            or t_mid.get("incident_faces_on_non_two_manifold_vertices", 0) > 0
        )
        if has_nm:
            removed = targeted_seam_cleanup(ms, cfg)
            if removed and (not cfg.quiet):
                print(f"      targeted delete: selected faces(after dilation)={removed}")
            apply_safe(ms, "meshing_repair_non_manifold_edges", cfg.quiet, method="Remove Faces")
            apply_safe(ms, "meshing_repair_non_manifold_vertices", cfg.quiet, vertdispratio=cfg.vertdisp_ratio)

    apply_safe(
        ms,
        "meshing_close_holes",
        cfg.quiet,
        maxholesize=int(cfg.max_hole_size),
        selected=False,
        newfaceselected=False,
        selfintersection=True,
        refinehole=False,
        refineholeedgelen=percentage_value(3.0),
    )

    apply_safe(ms, "meshing_remove_unreferenced_vertices", cfg.quiet)

    t_after = topo(ms)
    mesh2 = ms.current_mesh()
    v2 = mesh2.vertex_matrix().astype(np.float64)
    f2 = mesh2.face_matrix().astype(np.int32)

    if v2.size == 0 or f2.size == 0:
        return v, f, t_before, t_after
    return v2, f2, t_before, t_after


# ------------------------- lib3mf helpers -------------------------


def iter_mesh_objects(model) -> list:
    it = model.GetMeshObjects()
    objs = []
    while it.MoveNext():
        objs.append(it.GetCurrentMeshObject())
    return objs


def get_object_level_property(obj) -> Optional[Tuple[int, int]]:
    try:
        got = obj.GetObjectLevelProperty()
        if isinstance(got, tuple) and len(got) == 3 and bool(got[0]):
            return int(got[1]), int(got[2])
    except Exception:
        pass
    return None


def set_uniform_triangle_properties_if_possible(obj, prop: Tuple[int, int], tri_count: int) -> None:
    if tri_count <= 0:
        return
    if not hasattr(obj, "SetAllTriangleProperties"):
        return

    cls = None
    for name in ("TriangleProperties", "sTriangleProperties", "TriangleProperty"):
        if hasattr(lib3mf, name):
            cls = getattr(lib3mf, name)
            break
    if cls is None:
        return

    props = []
    for _ in range(tri_count):
        tp = cls()
        if hasattr(tp, "ResourceID"):
            tp.ResourceID = int(prop[0])
            tp.PropertyID = int(prop[1])
        elif hasattr(tp, "m_nResourceID"):
            tp.m_nResourceID = int(prop[0])
            tp.m_nPropertyID = int(prop[1])
        else:
            return
        props.append(tp)

    try:
        obj.SetAllTriangleProperties(props)
    except Exception:
        return


def validate_written_file(path: Path, quiet: bool) -> None:
    try:
        wrapper = lib3mf.Wrapper()
        m = wrapper.CreateModel()
        r = m.QueryReader("3mf")
        if hasattr(r, "SetStrictModeActive"):
            r.SetStrictModeActive(True)
        r.ReadFromFile(str(path))

        if hasattr(r, "GetWarningCount"):
            wc = r.GetWarningCount()
            if wc and not quiet:
                print(f"Writer validation: reader warnings={wc} (file is still readable in strict mode).")
    except Exception as e:
        if not quiet:
            print(
                "Writer validation: strict re-read failed. This often means extension namespaces/elements exist.\n"
                f"  Details: {e}"
            )


# ------------------------- 3MF CLEANUP FALLBACK -------------------------


def _ns_uri(tag: str) -> Optional[str]:
    if tag.startswith("{") and "}" in tag:
        return tag[1 : tag.index("}")]
    return None


def _strip_noncore_inplace(elem: ET.Element, core_uri: str) -> None:
    """
    Remove any child elements not in the core namespace, recursively.
    Also strip namespaced attributes not in the core namespace.
    """
    for k in list(elem.attrib.keys()):
        if k.startswith("{") and "}" in k:
            uri = _ns_uri(k)
            if uri != core_uri:
                del elem.attrib[k]

    for child in list(elem):
        if _ns_uri(child.tag) != core_uri:
            elem.remove(child)
            continue
        _strip_noncore_inplace(child, core_uri)


def _find_first_model_path(zf: zipfile.ZipFile) -> str:
    names = zf.namelist()
    for cand in ("3D/3dmodel.model", "3d/3dmodel.model"):
        if cand in names:
            return cand
    model_paths = [n for n in names if n.lower().startswith("3d/") and n.lower().endswith(".model")]
    if model_paths:
        return sorted(model_paths)[0]
    raise RuntimeError("Could not locate a 3D/*.model file inside the 3MF zip.")


def _clean_model_xml(model_xml_bytes: bytes) -> Tuple[bytes, Dict[str, int]]:
    """
    Aggressively sanitize the .model XML for lib3mf:

    - Remove ALL <metadata> elements (fixes Unknown/Duplicate Model Metadata)
    - Remove all non-core namespace elements/attrs
    - Drop build items referencing missing objects
    - Drop components referencing missing objects
    - Ensure build has at least one valid item if objects exist
    """
    stats: Dict[str, int] = {
        "removed_metadata": 0,
        "removed_noncore_children": 0,
        "removed_noncore_attrs": 0,
        "removed_build_items": 0,
        "removed_components": 0,
        "objects_before": 0,
        "objects_after": 0,
        "build_items_before": 0,
        "build_items_after": 0,
    }

    parser = ET.XMLParser()
    root = ET.fromstring(model_xml_bytes, parser=parser)

    core_uri = _ns_uri(root.tag)
    if not core_uri:
        raise RuntimeError("Model XML root has no namespace; unexpected .model format.")

    def q(local: str) -> str:
        return f"{{{core_uri}}}{local}"

    # 1) Remove ALL metadata
    # metadata are direct children of <model> in the core namespace.
    for md in list(root.findall(q("metadata"))):
        root.remove(md)
        stats["removed_metadata"] += 1

    # 2) Strip non-core content
    # We also count removals approximately.
    # Count attrs before/after
    def count_noncore_attrs(e: ET.Element) -> int:
        c = 0
        for k in e.attrib.keys():
            if k.startswith("{") and "}" in k:
                if _ns_uri(k) != core_uri:
                    c += 1
        for ch in list(e):
            c += count_noncore_attrs(ch)
        return c

    stats["removed_noncore_attrs"] = count_noncore_attrs(root)

    # Count non-core children (approx) then strip
    def count_noncore_children(e: ET.Element) -> int:
        c = 0
        for ch in list(e):
            if _ns_uri(ch.tag) != core_uri:
                c += 1
            c += count_noncore_children(ch)
        return c

    stats["removed_noncore_children"] = count_noncore_children(root)
    _strip_noncore_inplace(root, core_uri)

    # 3) Fix broken references
    resources = root.find(q("resources"))
    build = root.find(q("build"))

    if resources is None:
        raise RuntimeError("Cleaned model XML has no <resources> element (core).")
    if build is None:
        build = ET.SubElement(root, q("build"))

    objs = resources.findall(q("object"))
    stats["objects_before"] = len(objs)

    object_ids: set[str] = set()
    for obj in objs:
        oid = obj.get("id")
        if oid is not None:
            object_ids.add(oid)
    stats["objects_after"] = len(object_ids)

    # Drop broken components
    for obj in objs:
        comps = obj.find(q("components"))
        if comps is None:
            continue
        for comp in list(comps.findall(q("component"))):
            ref = comp.get("objectid")
            if ref is not None and ref not in object_ids:
                comps.remove(comp)
                stats["removed_components"] += 1

    # Drop broken build items
    build_items = build.findall(q("item"))
    stats["build_items_before"] = len(build_items)

    for item in list(build_items):
        ref = item.get("objectid")
        if ref is None or ref not in object_ids:
            build.remove(item)
            stats["removed_build_items"] += 1

    # Ensure build not empty if objects exist
    if len(build.findall(q("item"))) == 0 and len(object_ids) > 0:
        # pick a deterministic first object id
        def _key(s: str):
            return (0, int(s)) if s.isdigit() else (1, s)

        first_obj_id = sorted(object_ids, key=_key)[0]
        ET.SubElement(build, q("item"), {"objectid": first_obj_id})

    stats["build_items_after"] = len(build.findall(q("item")))

    # Serialize
    out = io.BytesIO()
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
    return out.getvalue(), stats


def make_clean_3mf_copy(infile: Path, quiet: bool) -> Tuple[Path, Dict[str, int]]:
    infile = infile.resolve()
    with zipfile.ZipFile(infile, "r") as zin:
        model_path = _find_first_model_path(zin)
        model_bytes = zin.read(model_path)
        cleaned_model_bytes, stats = _clean_model_xml(model_bytes)

        out_dir = infile.parent
        clean_path = out_dir / f"{infile.stem}_lib3mf_clean{infile.suffix}"

        fd, tmp_name = tempfile.mkstemp(prefix=f"{infile.stem}_", suffix=".3mf", dir=str(out_dir))
        Path(tmp_name).unlink(missing_ok=True)

        with zipfile.ZipFile(tmp_name, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == model_path:
                    data = cleaned_model_bytes
                zout.writestr(info.filename, data)

        Path(tmp_name).replace(clean_path)

    if not quiet:
        print(f"Created cleaned 3MF: {clean_path}")
        print(
            "Cleanup stats: "
            f"removed_metadata={stats['removed_metadata']}, "
            f"removed_noncore_children≈{stats['removed_noncore_children']}, "
            f"removed_noncore_attrs≈{stats['removed_noncore_attrs']}, "
            f"build_items {stats['build_items_before']}→{stats['build_items_after']} "
            f"(removed {stats['removed_build_items']}), "
            f"removed_components={stats['removed_components']}"
        )

    return clean_path, stats


def _attempt_read(model, path: Path, strict: bool) -> None:
    reader = model.QueryReader("3mf")
    if hasattr(reader, "SetStrictModeActive"):
        reader.SetStrictModeActive(bool(strict))
    reader.ReadFromFile(str(path))


def read_3mf_with_fallback(wrapper: lib3mf.Wrapper, infile: Path, quiet: bool):
    """
    Returns a loaded model + the path that was actually read.

    We create a fresh model for each attempt because lib3mf can leave partially
    initialized state after a failed parse.
    """
    # 1) strict
    model = wrapper.CreateModel()
    try:
        _attempt_read(model, infile, strict=True)
        return model, infile
    except lib3mf.Lib3MF.ELib3MFException as e:
        if not quiet:
            print(f"lib3mf strict read failed: {e}")

    # 2) non-strict
    model = wrapper.CreateModel()
    try:
        if not quiet:
            print("Retrying in NON-STRICT mode (tolerate nonstandard/extension namespaces)...")
        _attempt_read(model, infile, strict=False)
        return model, infile
    except lib3mf.Lib3MF.ELib3MFException as e:
        if not quiet:
            print(f"lib3mf non-strict read failed: {e}")

    # 3) clean and retry (this fixes both metadata errors and namespace errors)
    if not quiet:
        print("Attempting to CLEAN the 3MF (.model XML) and retry with lib3mf...")

    clean_path, _ = make_clean_3mf_copy(infile, quiet=quiet)

    model = wrapper.CreateModel()
    try:
        _attempt_read(model, clean_path, strict=True)
        return model, clean_path
    except lib3mf.Lib3MF.ELib3MFException as e:
        if not quiet:
            print(f"lib3mf strict read (cleaned) failed: {e}")

    model = wrapper.CreateModel()
    try:
        if not quiet:
            print("Retrying cleaned file in NON-STRICT mode...")
        _attempt_read(model, clean_path, strict=False)
        return model, clean_path
    except lib3mf.Lib3MF.ELib3MFException as e:
        raise RuntimeError(
            "lib3mf cannot read the original file (strict or non-strict) or the cleaned copy.\n"
            f"Last error: {e}"
        ) from e


# ------------------------- main -------------------------


def main(argv: Optional[list[str]] = None) -> int:
    t_total = time.perf_counter()
    ap = argparse.ArgumentParser(
        description="Repair meshes inside a 3MF (PyMeshLab) and write a standards-compliant 3MF (lib3mf)."
    )
    ap.add_argument("input_3mf", type=Path, help="Input .3mf file")
    ap.add_argument("--out", type=Path, default=None, help="Output .3mf (default: <input>_meshlab_repair.3mf)")

    ap.add_argument("--merge_threshold_pct", type=float, default=0.01)
    ap.add_argument("--max_hole_size", type=int, default=60)
    ap.add_argument("--vertdisp_ratio", type=float, default=0.02)

    ap.add_argument("--targeted", action="store_true")
    ap.add_argument("--dilate", type=int, default=2)
    ap.add_argument("--delete_mode", choices=["faces", "faces_and_vertices"], default="faces")
    ap.add_argument("--self_intersections", action="store_true")

    ap.add_argument("--max_target_delete_fraction", type=float, default=0.75)
    ap.add_argument("--force_targeted_wipe", action="store_true")

    ap.add_argument("--report", action="store_true")
    ap.add_argument("--quiet", action="store_true")

    args = ap.parse_args(argv)

    infile = args.input_3mf
    if infile.suffix.lower() != ".3mf":
        raise SystemExit("Input must be a .3mf file")

    outp = args.out if args.out is not None else default_out_path(infile, args.targeted)
    outp = normalize_out_path(outp)
    outp.parent.mkdir(parents=True, exist_ok=True)

    cfg = RepairConfig(
        merge_threshold_pct=args.merge_threshold_pct,
        max_hole_size=args.max_hole_size,
        vertdisp_ratio=args.vertdisp_ratio,
        targeted=args.targeted,
        dilate=args.dilate,
        delete_mode=args.delete_mode,
        select_nonmanifold=True,
        select_self_intersections=args.self_intersections,
        max_target_delete_fraction=args.max_target_delete_fraction,
        force_targeted_wipe=args.force_targeted_wipe,
        report=args.report,
        quiet=args.quiet,
    )

    wrapper = lib3mf.Wrapper()

    # UPDATED: returns a loaded model and the path it actually read
    t_read = time.perf_counter()
    model, actual_in = read_3mf_with_fallback(wrapper, infile, quiet=cfg.quiet)
    perf("repair read 3mf", t_read, extra=actual_in.name, quiet=cfg.quiet)

    if not cfg.quiet:
        print(f"Loaded: {actual_in}")

    mesh_objects = iter_mesh_objects(model)
    if not cfg.quiet:
        print(f"Parts (mesh resources): {len(mesh_objects)}")

    t_meshes = time.perf_counter()
    for i, obj in enumerate(mesh_objects):
        name = obj.GetName() or f"mesh_{obj.GetUniqueResourceID()}"
        if not cfg.quiet:
            print(f"\n== [{i+1}/{len(mesh_objects)}] {name} ==")

        verts = obj.GetVertices()
        tris = obj.GetTriangleIndices()
        v = np.array([p.Coordinates for p in verts], dtype=np.float64)
        f = np.array([t.Indices for t in tris], dtype=np.int32)

        if v.size == 0 or f.size == 0:
            if not cfg.quiet:
                print("      (empty mesh) skipping")
            continue

        obj_prop = get_object_level_property(obj)

        t_mesh = time.perf_counter()
        v2, f2, t_before, t_after = repair_geometry(v, f, cfg)
        perf("repair mesh", t_mesh, extra=f"{name} v={len(v)}->{len(v2)} f={len(f)}->{len(f2)}", quiet=cfg.quiet)

        if cfg.report and (not cfg.quiet):
            print(f"      TOPO before: {t_before}")
            print(f"      TOPO after : {t_after}")
        elif not cfg.quiet:
            print(f"      V/F: {len(v)} {len(f)} -> {len(v2)} {len(f2)}")

        new_positions = []
        for xyz in v2:
            p = lib3mf.Position()
            p.Coordinates = (float(xyz[0]), float(xyz[1]), float(xyz[2]))
            new_positions.append(p)

        new_triangles = []
        for tri in f2:
            t = lib3mf.Triangle()
            t.Indices = (int(tri[0]), int(tri[1]), int(tri[2]))
            new_triangles.append(t)

        obj.SetGeometry(new_positions, new_triangles)  # clears properties

        if obj_prop is not None:
            try:
                obj.SetObjectLevelProperty(obj_prop[0], obj_prop[1])
            except Exception:
                pass
            set_uniform_triangle_properties_if_possible(obj, obj_prop, tri_count=len(f2))

    perf("repair all meshes", t_meshes, extra=f"mesh_objects={len(mesh_objects)}", quiet=cfg.quiet)

    writer = model.QueryWriter("3mf")
    if hasattr(writer, "SetStrictModeActive"):
        writer.SetStrictModeActive(True)
    t_write = time.perf_counter()
    writer.WriteToFile(str(outp))
    perf("repair write 3mf", t_write, extra=outp.name, quiet=cfg.quiet)

    if not cfg.quiet:
        print(f"\nWROTE: {outp}")

    t_validate = time.perf_counter()
    validate_written_file(outp, quiet=cfg.quiet)
    perf("repair validate output", t_validate, extra=outp.name, quiet=cfg.quiet)
    perf("repair total", t_total, extra=outp.name, quiet=cfg.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
