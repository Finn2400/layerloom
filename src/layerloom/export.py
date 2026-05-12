#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layerloom.export
----------------
3MF writers for LayerLoom.

Writers in this module:
  • write_basic_3mf(...)      - legacy one-object-per-mesh export
  • write_safe_assembly_3mf(...) - sharded production-extension export used by
                                   the v58 fine-layer weave path
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
import math
import os
import re
import io
import uuid
import zipfile
import numpy as np
import xml.etree.ElementTree as ET
import trimesh

try:
    from layerloom.tokens import COLOR_OBJECT_LABELS, TOKEN_HEX
except Exception:
    from tokens import COLOR_OBJECT_LABELS, TOKEN_HEX

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
M = lambda tag: f"{{{CORE_NS}}}{tag}"
P = lambda tag: f"{{{PROD_NS}}}{tag}"
R = lambda tag: f"{{{REL_NS}}}{tag}"

ET.register_namespace("", CORE_NS)
ET.register_namespace("p", PROD_NS)

GROUPED_COLOR_INFO = {
    label: {"token": token, "hex": TOKEN_HEX.get(token, "")}
    for token, label in COLOR_OBJECT_LABELS.items()
}


@dataclass(frozen=True)
class LeafMeshSpec:
    label: str
    mesh: trimesh.Trimesh
    metadata: Dict[str, str]


@dataclass(frozen=True)
class SafeAssemblyWriteStats:
    top_level_count: int
    child_model_count: int
    leaf_mesh_count: int
    shard_count_by_group: Dict[str, int]


def _sanitize_filename(s: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or "").strip())
    return safe or "model"


def _copy_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices, dtype=np.float64),
        faces=np.asarray(mesh.faces, dtype=np.int64),
        process=False,
    )


def _validated_volume_mesh(mesh: trimesh.Trimesh, *, label: str) -> trimesh.Trimesh:
    """
    Make a best effort to coerce a mesh into a clean solid volume and raise if
    it still isn't valid.
    """
    candidate = _copy_mesh(mesh)
    try:
        candidate.remove_infinite_values()
        candidate.remove_unreferenced_vertices()
    except Exception:
        pass

    if candidate.faces.size == 0:
        raise RuntimeError(f"Leaf mesh '{label}' is empty after cleanup.")
    if candidate.is_volume:
        return candidate

    try:
        nondegenerate = getattr(candidate, "nondegenerate_faces", None)
        if callable(nondegenerate):
            candidate.update_faces(nondegenerate())
            candidate.remove_unreferenced_vertices()
    except Exception:
        pass

    if candidate.faces.size == 0:
        raise RuntimeError(f"Leaf mesh '{label}' is empty after cleanup.")
    if candidate.is_volume:
        return candidate

    for repair_fn in (
        lambda m: trimesh.repair.fix_normals(m),
        lambda m: m.process(validate=True),
    ):
        probe = _copy_mesh(candidate)
        try:
            repair_fn(probe)
        except Exception:
            pass
        try:
            probe.remove_unreferenced_vertices()
        except Exception:
            pass
        if probe.is_volume:
            return probe
        if probe.is_watertight:
            flipped = _copy_mesh(probe)
            try:
                flipped.invert()
            except Exception:
                pass
            if flipped.is_volume:
                return flipped

    raise RuntimeError(
        f"Leaf mesh '{label}' is not a valid solid volume "
        f"(watertight={candidate.is_watertight}, is_volume={candidate.is_volume})."
    )


def _add_name_metadata(parent: ET.Element, name: str) -> None:
    ET.SubElement(parent, M("metadata"), {"name": "Name"}).text = name


def _add_metadatagroup(parent: ET.Element, metadata: Dict[str, str]) -> None:
    if not metadata:
        return
    mg = ET.SubElement(parent, M("metadatagroup"))
    for key, value in metadata.items():
            ET.SubElement(mg, M("metadata"), {"name": str(key)}).text = str(value)


def _xml_bytes(root: ET.Element) -> bytes:
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


def _append_mesh_object(resources: ET.Element,
                        object_id: int,
                        name: str,
                        mesh: trimesh.Trimesh,
                        metadata: Optional[Dict[str, str]] = None) -> None:
    obj_el = ET.SubElement(
        resources,
        M("object"),
        {"id": str(object_id), "type": "model", "name": name},
    )
    _add_name_metadata(obj_el, name)
    if metadata:
        _add_metadatagroup(obj_el, metadata)

    mesh_el = ET.SubElement(obj_el, M("mesh"))
    vs_el = ET.SubElement(mesh_el, M("vertices"))
    for x, y, z in np.asarray(mesh.vertices):
        v = ET.SubElement(vs_el, M("vertex"))
        v.set("x", f"{float(x):.9f}")
        v.set("y", f"{float(y):.9f}")
        v.set("z", f"{float(z):.9f}")

    fs_el = ET.SubElement(mesh_el, M("triangles"))
    for a, b, c in np.asarray(mesh.faces):
        f = ET.SubElement(fs_el, M("triangle"))
        f.set("v1", str(int(a)))
        f.set("v2", str(int(b)))
        f.set("v3", str(int(c)))


def _estimate_leaf_xml_bytes(spec: LeafMeshSpec) -> int:
    mesh = spec.mesh
    vertex_cost = int(len(mesh.vertices) * 72)
    face_cost = int(len(mesh.faces) * 56)
    meta_cost = sum(len(str(k)) + len(str(v)) + 32 for k, v in spec.metadata.items())
    return vertex_cost + face_cost + meta_cost + len(spec.label) + 512


def _shard_leaf_specs(leaf_specs: Sequence[LeafMeshSpec],
                      *,
                      max_child_objects: int,
                      max_child_xml_bytes: int) -> List[List[LeafMeshSpec]]:
    shards: List[List[LeafMeshSpec]] = []
    current: List[LeafMeshSpec] = []
    current_bytes = 0
    for spec in leaf_specs:
        est = _estimate_leaf_xml_bytes(spec)
        if current and (
            len(current) >= max_child_objects
            or current_bytes + est > max_child_xml_bytes
        ):
            shards.append(current)
            current = []
            current_bytes = 0
        current.append(spec)
        current_bytes += est
    if current:
        shards.append(current)
    return shards


def _content_types_xml() -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        b"</Types>"
    )


def _rels_root_xml() -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" '
        b'Target="/3D/3dmodel.model"/>'
        b"</Relationships>"
    )


def _root_model_rels_xml(child_targets: Sequence[str]) -> bytes:
    root = ET.Element(R("Relationships"))
    for idx, target in enumerate(child_targets, start=1):
        ET.SubElement(
            root,
            R("Relationship"),
            {
                "Id": f"rel-{idx}",
                "Type": "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel",
                "Target": target,
            },
        )
    return _xml_bytes(root)


def write_basic_3mf(
    items: List[Tuple[str, trimesh.Trimesh]],
    out_path: str,
    title: str | None = None,
) -> None:
    """
    Write a minimal 3MF file containing one <object> per mesh.
    """
    if not items:
        raise ValueError("No meshes provided to export.")

    title = title or out_path.split("/")[-1].replace(".3mf", "")
    root = ET.Element(M("model"), {"unit": "millimeter", f"{{{XML_NS}}}lang": "en-US"})
    ET.SubElement(root, M("metadata"), {"name": "Title"}).text = title
    resources = ET.SubElement(root, M("resources"))
    build = ET.SubElement(root, M("build"))

    obj_id = 1
    for label, mesh in items:
        if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.size == 0:
            continue
        _append_mesh_object(resources, obj_id, label, mesh, metadata={})
        ET.SubElement(
            build,
            M("item"),
            {"objectid": str(obj_id), "printable": "1", "partnumber": label},
        )
        obj_id += 1

    xml_bytes = _xml_bytes(root)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types_xml())
        z.writestr("_rels/.rels", _rels_root_xml())
        z.writestr("3D/3dmodel.model", xml_bytes)

    print(f"[export] wrote {obj_id - 1} object(s) → {out_path}")


def write_safe_assembly_3mf(
    grouped_leaf_parts: Dict[str, List[Tuple[str, trimesh.Trimesh]]],
    out_path: str,
    *,
    title: Optional[str] = None,
    max_child_objects: int = 2000,
    max_child_xml_bytes: int = 64 * 1024 * 1024,
) -> SafeAssemblyWriteStats:
    """
    Write a sharded multi-model 3MF where the root file exposes only grouped
    color assemblies, while child model files contain the leaf slice meshes.
    """
    if not grouped_leaf_parts:
        raise ValueError("No grouped leaf meshes provided to export.")

    base_title = title or os.path.splitext(os.path.basename(out_path))[0]
    base_name = _sanitize_filename(os.path.splitext(os.path.basename(out_path))[0])

    root_model = ET.Element(
        M("model"),
        {
            "unit": "millimeter",
            f"{{{XML_NS}}}lang": "en-US",
            "requiredextensions": "p",
        },
    )
    ET.SubElement(root_model, M("metadata"), {"name": "Title"}).text = base_title
    root_resources = ET.SubElement(root_model, M("resources"))
    root_build = ET.SubElement(root_model, M("build"))

    zip_entries: Dict[str, bytes] = {
        "[Content_Types].xml": _content_types_xml(),
        "_rels/.rels": _rels_root_xml(),
    }
    child_targets: List[str] = []
    shard_count_by_group: Dict[str, int] = {}
    total_leaf_count = 0
    root_object_id = 1
    grouped_root_ids: List[Tuple[int, str]] = []

    for grouped_name, raw_parts in grouped_leaf_parts.items():
        valid_specs: List[LeafMeshSpec] = []
        group_info = GROUPED_COLOR_INFO.get(grouped_name, {})
        for leaf_index, (leaf_label, mesh) in enumerate(raw_parts, start=1):
            if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.size == 0:
                continue
            solid = _validated_volume_mesh(mesh, label=leaf_label)
            metadata = {
                "source_hex": group_info.get("hex", ""),
                "stack_token": group_info.get("token", ""),
                "group_name": grouped_name,
                "leaf_index": str(leaf_index),
                "original_label": leaf_label,
            }
            canonical_leaf_name = f"{grouped_name}__leaf_{leaf_index:06d}"
            valid_specs.append(LeafMeshSpec(canonical_leaf_name, solid, metadata))

        if not valid_specs:
            continue

        total_leaf_count += len(valid_specs)
        shards = _shard_leaf_specs(
            valid_specs,
            max_child_objects=max_child_objects,
            max_child_xml_bytes=max_child_xml_bytes,
        )
        shard_count_by_group[grouped_name] = len(shards)

        grouped_obj = ET.SubElement(
            root_resources,
            M("object"),
            {"id": str(root_object_id), "type": "model", "name": grouped_name},
        )
        _add_name_metadata(grouped_obj, grouped_name)
        if group_info:
            _add_metadatagroup(
                grouped_obj,
                {
                    "source_hex": group_info.get("hex", ""),
                    "stack_token": group_info.get("token", ""),
                    "group_name": grouped_name,
                },
            )
        comps = ET.SubElement(grouped_obj, M("components"))

        for shard_idx, shard_specs in enumerate(shards, start=1):
            child_rel_target = f"/3D/Objects/{base_name}_{grouped_name}_{shard_idx}.model"
            child_zip_path = child_rel_target.lstrip("/")
            child_targets.append(child_rel_target)

            child_model = ET.Element(M("model"), {"unit": "millimeter", f"{{{XML_NS}}}lang": "en-US"})
            child_resources = ET.SubElement(child_model, M("resources"))

            leaf_object_ids: List[int] = []
            object_id = 1
            for spec in shard_specs:
                _append_mesh_object(child_resources, object_id, spec.label, spec.mesh, spec.metadata)
                leaf_object_ids.append(object_id)
                object_id += 1

            zip_entries[child_zip_path] = _xml_bytes(child_model)

            for local_leaf_index, leaf_object_id in enumerate(leaf_object_ids, start=1):
                ET.SubElement(
                    comps,
                    M("component"),
                    {
                        "objectid": str(leaf_object_id),
                        P("path"): child_rel_target,
                        P("UUID"): str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"{base_name}:{grouped_name}:{shard_idx}:{local_leaf_index}",
                            )
                        ),
                    },
                )

        grouped_root_ids.append((root_object_id, grouped_name))
        root_object_id += 1

    if root_object_id == 1:
        raise ValueError("No valid grouped leaf meshes remained after validation.")

    assembly_id = root_object_id
    root_assembly = ET.SubElement(
        root_resources,
        M("object"),
        {"id": str(assembly_id), "type": "model", "name": base_name},
    )
    _add_name_metadata(root_assembly, base_name)
    assembly_components = ET.SubElement(root_assembly, M("components"))
    for group_id, grouped_name in grouped_root_ids:
        ET.SubElement(
            assembly_components,
            M("component"),
            {
                "objectid": str(group_id),
                P("UUID"): str(uuid.uuid5(uuid.NAMESPACE_URL, f"{base_name}:{grouped_name}:root")),
            },
        )

    ET.SubElement(
        root_build,
        M("item"),
        {
            "objectid": str(assembly_id),
            "partnumber": base_name,
            "printable": "1",
            P("UUID"): str(uuid.uuid5(uuid.NAMESPACE_URL, f"{base_name}:build")),
        },
    )

    root_model.attrib.pop(P("requiredextensions"), None)
    zip_entries["3D/3dmodel.model"] = _xml_bytes(root_model)
    zip_entries["3D/_rels/3dmodel.model.rels"] = _root_model_rels_xml(child_targets)

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in zip_entries.items():
            zf.writestr(name, data)

    print(
        f"[export] wrote safe assembly 3MF → {out_path} "
        f"(top_level={root_object_id - 1}, child_models={len(child_targets)}, leaf_meshes={total_leaf_count})"
    )
    return SafeAssemblyWriteStats(
        top_level_count=len(grouped_root_ids),
        child_model_count=len(child_targets),
        leaf_mesh_count=total_leaf_count,
        shard_count_by_group=shard_count_by_group,
    )


def _find_model_xml_name(zf: zipfile.ZipFile, *, prefix: Optional[str] = None) -> Optional[str]:
    if prefix is None and "3D/3dmodel.model" in zf.namelist():
        return "3D/3dmodel.model"
    for name in zf.namelist():
        if prefix and not name.startswith(prefix):
            continue
        if name.startswith("3D/") and name.lower().endswith(".model"):
            return name
    return None


def _build_trimesh_from_object(obj: ET.Element) -> Optional[trimesh.Trimesh]:
    mesh_el = obj.find(M("mesh"))
    if mesh_el is None:
        return None
    verts_el = mesh_el.find(M("vertices"))
    tris_el = mesh_el.find(M("triangles"))
    if verts_el is None or tris_el is None:
        return None
    verts = []
    faces = []
    for v in verts_el.findall(M("vertex")):
        verts.append([float(v.get("x", "0")), float(v.get("y", "0")), float(v.get("z", "0"))])
    for tri in tris_el.findall(M("triangle")):
        faces.append([int(tri.get("v1", "0")), int(tri.get("v2", "0")), int(tri.get("v3", "0"))])
    if not verts or not faces:
        return None
    return trimesh.Trimesh(
        vertices=np.asarray(verts, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        process=False,
    )


def validate_safe_assembly_3mf(path: str) -> Dict[str, object]:
    """
    Lightweight validation used by the v58 weave diagnostics.
    """
    with zipfile.ZipFile(path, "r") as zf:
        if "3D/3dmodel.model" not in zf.namelist():
            raise RuntimeError("3MF archive is missing 3D/3dmodel.model")
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
        build = root.find(M("build"))
        resources = root.find(M("resources"))
        if build is None or resources is None:
            raise RuntimeError("3MF root model is missing resources/build")

        root_objects = {obj.get("id"): obj for obj in resources.findall(M("object")) if obj.get("id")}
        build_names: List[str] = []
        grouped_names: List[str] = []
        child_paths: List[str] = []
        for item in build.findall(M("item")):
            oid = item.get("objectid")
            obj = root_objects.get(oid)
            if obj is None:
                raise RuntimeError(f"Build item references missing root object id {oid}")
            build_names.append(obj.get("name") or f"object_{oid}")
            comps = obj.find(M("components"))
            if comps is None:
                raise RuntimeError(f"Root grouped object {oid} has no components")
            for comp in comps.findall(M("component")):
                child_oid = comp.get("objectid")
                child_path = comp.get(P("path"))
                if child_path:
                    child_paths.append(child_path)
                    continue
                child_obj = root_objects.get(child_oid)
                if child_obj is None:
                    raise RuntimeError(f"Root assembly references missing grouped object id {child_oid}")
                child_name = child_obj.get("name") or f"object_{child_oid}"
                grouped_names.append(child_name)
                child_comps = child_obj.find(M("components"))
                if child_comps is None:
                    raise RuntimeError(f"Grouped object {child_oid} has no child components")
                for child_comp in child_comps.findall(M("component")):
                    child_path = child_comp.get(P("path"))
                    if child_path:
                        child_paths.append(child_path)

        child_leaf_count = 0
        for child_path in child_paths:
            zip_path = child_path.lstrip("/")
            if zip_path not in zf.namelist():
                raise RuntimeError(f"Missing child model referenced by root: {child_path}")
            child_root = ET.fromstring(zf.read(zip_path))
            child_resources = child_root.find(M("resources"))
            if child_resources is None:
                raise RuntimeError(f"Child model {child_path} has no resources section")
            for obj in child_resources.findall(M("object")):
                if obj.find(M("mesh")) is None:
                    continue
                mesh = _build_trimesh_from_object(obj)
                if mesh is None:
                    raise RuntimeError(f"Child model {child_path} contains an empty mesh object")
                if not mesh.is_volume:
                    raise RuntimeError(
                        f"Child model {child_path} contains a non-volume mesh "
                        f"(object id {obj.get('id')}, name {obj.get('name')})"
                    )
                child_leaf_count += 1

    return {
        "top_level_names": build_names,
        "grouped_names": grouped_names,
        "child_paths": child_paths,
        "leaf_mesh_count": child_leaf_count,
    }


def write_color_stls(items: list[tuple[str, trimesh.Trimesh]], out_dir: str, verbose: bool = True) -> None:
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


if __name__ == "__main__":
    import sys
    from layerloom.ingest import read_parts

    if len(sys.argv) < 3:
        print("Usage: python -m layerloom.export input.3mf output.3mf")
        raise SystemExit(1)

    input_3mf = sys.argv[1]
    output_3mf = sys.argv[2]

    parts = read_parts([input_3mf])
    write_basic_3mf(items=parts, out_path=output_3mf)
    print(f"[done] wrote {output_3mf}")
