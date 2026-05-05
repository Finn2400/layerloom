#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
normalize_3mf_import.py
-----------------------

Canonical 3MF import normalizer for LayerLoom.

This module accepts generic and vendor-saved 3MF packages, resolves reachable
geometry across assemblies and production-extension child-model references, and
writes a temporary LayerLoom-friendly generic 3MF where every editable part is
its own concrete mesh object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from collections import Counter, defaultdict, deque
import hashlib
import io
import mmap
import os
import posixpath
import re
import struct
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

try:
    from layerloom.bake_in_memory_stlroundtrip import (
        CORE_NS,
        PROD_NS,
        XML_NS,
        MeshSpool,
        _strip_ns,
        _unit_to_mm,
        apply_tf_to_point,
        mul_tf,
        parse_tf_3mf,
        scale_tf_translation,
    )
except Exception:
    from bake_in_memory_stlroundtrip import (
        CORE_NS,
        PROD_NS,
        XML_NS,
        MeshSpool,
        _strip_ns,
        _unit_to_mm,
        apply_tf_to_point,
        mul_tf,
        parse_tf_3mf,
        scale_tf_translation,
    )


REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CORE_REL_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
NS = {"m": CORE_NS}
M = lambda tag: f"{{{CORE_NS}}}{tag}"
R = lambda tag: f"{{{REL_NS}}}{tag}"

ET.register_namespace("", CORE_NS)

IDENTITY_12 = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
HEX_RE = re.compile(r"#?[0-9a-fA-F]{6}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
PAT_RE = re.compile(r"__PAT_([cmykwCMYKW]+)__")
NORMALIZED_FORMAT_VERSION = "2"


@dataclass(frozen=True)
class NormalizedPartRecord:
    label: str
    source_member: str
    source_object_id: str
    source_hex: Optional[str]
    stack_token: Optional[str]


@dataclass(frozen=True)
class NormalizedImportResult:
    normalized_path: str
    parts: List[NormalizedPartRecord]
    warnings: List[str]


def _normalize_hex(value: Optional[str]) -> Optional[str]:
    s = str(value or "").strip()
    if not s or not HEX_RE.fullmatch(s):
        return None
    if not s.startswith("#"):
        s = "#" + s
    return s.lower()


def _metadata_key(name: Optional[str]) -> str:
    s = str(name or "").strip()
    return s.split(":")[-1] if s else ""


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _extract_pat_token(value: Optional[str]) -> Optional[str]:
    m = PAT_RE.search(str(value or ""))
    if not m:
        return None
    token = m.group(1).lower()
    return token if token and set(token).issubset(set("cmykw")) else None


def _sanitize_label(label: str) -> str:
    s = CONTROL_RE.sub("", str(label or "")).strip()
    s = s.replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or "part"


def _resolve_package_target(base_member: str, target: str) -> Optional[str]:
    raw = str(target or "").strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("/"):
        resolved = raw[1:]
    elif base_member == "_rels/.rels":
        resolved = raw
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base_member), raw))
    if resolved.startswith("../"):
        resolved = posixpath.normpath(resolved)
    return resolved.lstrip("./")


def _find_root_model_member(zf: zipfile.ZipFile) -> Optional[str]:
    if "_rels/.rels" in zf.namelist():
        try:
            root = ET.fromstring(zf.read("_rels/.rels"))
            for rel in root.findall(R("Relationship")):
                rel_type = str(rel.get("Type") or "")
                if rel_type == CORE_REL_TYPE or rel_type.endswith("/3dmodel"):
                    target = _resolve_package_target("_rels/.rels", rel.get("Target") or "")
                    if target and target in zf.namelist():
                        return target
        except Exception:
            pass
    if "3D/3dmodel.model" in zf.namelist():
        return "3D/3dmodel.model"
    for name in zf.namelist():
        if name.startswith("3D/") and name.lower().endswith(".model"):
            return name
    return None


def _is_layerloom_normalized(path: str) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            member = _find_root_model_member(zf)
            if not member:
                return False
            normalized = False
            version = None
            with zf.open(member, "r") as fp:
                for event, elem in ET.iterparse(fp, events=("start", "end")):
                    tag = _strip_ns(elem.tag)
                    if event == "start" and tag == "resources":
                        break
                    if event != "end" or tag != "metadata":
                        continue
                    key = _metadata_key(elem.get("name"))
                    if key == "LayerLoomNormalized" and _truthy(elem.text):
                        normalized = True
                    elif key == "LayerLoomNormalizedVersion":
                        version = (elem.text or "").strip()
                    elem.clear()
    except Exception:
        return False
    return normalized and version == NORMALIZED_FORMAT_VERSION


def _read_normalized_records(path: str) -> List[NormalizedPartRecord]:
    out: List[NormalizedPartRecord] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            member = _find_root_model_member(zf)
            if not member:
                return []
            with zf.open(member, "r") as fp:
                current = None
                for event, elem in ET.iterparse(fp, events=("start", "end")):
                    tag = _strip_ns(elem.tag)
                    if event == "start":
                        if tag == "object":
                            oid = elem.get("id") or ""
                            current = {
                                "oid": oid,
                                "label": elem.get("name") or (f"object_{oid}" if oid else "object"),
                                "has_mesh": False,
                                "source_member": "",
                                "source_object_id": "",
                                "source_hex": None,
                                "stack_token": None,
                            }
                        elif tag == "mesh" and current is not None:
                            current["has_mesh"] = True
                        continue

                    if tag == "metadata" and current is not None:
                        key = _metadata_key(elem.get("name"))
                        text = (elem.text or "").strip()
                        if key in ("Title", "Name") and text:
                            current["label"] = text
                        elif key == "import_source_member":
                            current["source_member"] = text
                        elif key == "import_source_oid":
                            current["source_object_id"] = text
                        elif key == "source_hex":
                            current["source_hex"] = _normalize_hex(text)
                        elif key == "stack_token":
                            current["stack_token"] = text.lower() or None
                    elif tag == "object" and current is not None:
                        oid = str(current.get("oid") or "")
                        if oid and bool(current.get("has_mesh")):
                            stack_token = current.get("stack_token") or _extract_pat_token(current.get("label"))
                            out.append(
                                NormalizedPartRecord(
                                    label=str(current.get("label") or f"object_{oid}"),
                                    source_member=str(current.get("source_member") or ""),
                                    source_object_id=str(current.get("source_object_id") or ""),
                                    source_hex=current.get("source_hex"),
                                    stack_token=stack_token,
                                )
                            )
                        current = None
                    elem.clear()
    except Exception:
        return []
    return out


def _parse_component_path(elem: ET.Element, current_member: str) -> Optional[str]:
    path = elem.get(f"{{{PROD_NS}}}path")
    if not path:
        path = elem.get("path")
    if not path:
        for key, value in elem.attrib.items():
            if key.endswith("}path") and value:
                path = value
                break
    if not path:
        return None
    return _resolve_package_target(current_member, path)


def _read_vendor_model_settings(zf: zipfile.ZipFile) -> Dict[str, Dict[str, object]]:
    name = "Metadata/model_settings.config"
    if name not in zf.namelist():
        return {}
    try:
        root = ET.fromstring(zf.read(name))
    except Exception:
        return {}
    if _strip_ns(root.tag) != "config":
        return {}

    out: Dict[str, Dict[str, object]] = {}
    for obj in root.findall("object"):
        oid = str(obj.get("id") or "").strip()
        if not oid:
            continue
        obj_name = ""
        for md in obj.findall("metadata"):
            if (md.get("key") or "").strip().lower() == "name":
                obj_name = _sanitize_label(md.get("value") or "")
                break
        part_names: Dict[str, str] = {}
        for part in obj.findall("part"):
            pid = str(part.get("id") or "").strip()
            if not pid:
                continue
            part_name = ""
            for md in part.findall("metadata"):
                if (md.get("key") or "").strip().lower() == "name":
                    part_name = _sanitize_label(md.get("value") or "")
                    break
            if part_name:
                part_names[pid] = part_name
        out[oid] = {"name": obj_name, "parts": part_names}
    return out


def _parse_model_streaming(
    zf: zipfile.ZipFile,
    member: str,
    tmp_dir: str,
    *,
    needed_obj_ids: Optional[set[str]] = None,
    pass_mode: str = "meta",
) -> Dict[str, object]:
    objects: Dict[str, Dict[str, object]] = {}
    build_items: List[Dict[str, object]] = []
    meshes: Dict[str, Dict[str, object]] = {}
    child_models: set[str] = set()
    unit_scale_mm = 1.0

    current_object: Optional[Dict[str, object]] = None
    current_object_id: Optional[str] = None
    in_components = False
    in_mesh = False
    in_vertices = False
    in_triangles = False
    current_spool: Optional[MeshSpool] = None

    with zf.open(member) as fp:
        for event, elem in ET.iterparse(fp, events=("start", "end")):
            tag = _strip_ns(elem.tag)
            if event == "start":
                if tag == "model":
                    unit_scale_mm = _unit_to_mm(elem.get("unit"))
                elif tag == "object":
                    current_object_id = elem.get("id")
                    if current_object_id:
                        current_object = {
                            "id": current_object_id,
                            "name": elem.get("name") or "",
                            "label": elem.get("name") or "",
                            "components": [],
                            "has_mesh": False,
                            "type": (elem.get("type") or "").strip().lower(),
                            "metadata": {},
                        }
                        objects.setdefault(current_object_id, current_object)
                        if current_object["name"] and not objects[current_object_id].get("name"):
                            objects[current_object_id]["name"] = current_object["name"]
                elif tag == "components":
                    in_components = True
                elif tag == "component" and in_components and current_object_id and current_object is not None:
                    child_id = elem.get("objectid")
                    if child_id:
                        child_path = _parse_component_path(elem, member)
                        if child_path:
                            child_models.add(child_path)
                        current_object["components"].append(
                            {
                                "objectid": child_id,
                                "transform": parse_tf_3mf(elem.get("transform")),
                                "path": child_path,
                            }
                        )
                elif tag == "mesh" and current_object_id and current_object is not None:
                    in_mesh = True
                    current_object["has_mesh"] = True
                    if pass_mode == "mesh" and (
                        needed_obj_ids is None or current_object_id in needed_obj_ids
                    ):
                        current_spool = MeshSpool(tmp_dir, f"{hashlib.md5(member.encode('utf-8')).hexdigest()[:8]}_{current_object_id}")
                elif tag == "vertices" and in_mesh:
                    in_vertices = True
                elif tag == "triangles" and in_mesh:
                    in_triangles = True
                elif tag == "item":
                    oid = elem.get("objectid")
                    if oid:
                        build_items.append(
                            {
                                "objectid": oid,
                                "transform": parse_tf_3mf(elem.get("transform")),
                                "partnumber": (elem.get("partnumber") or "").strip(),
                                "printable": str(elem.get("printable", "1")).strip().lower() not in {"0", "false", "no"},
                            }
                        )
                continue

            if tag == "metadata" and current_object is not None:
                key = _metadata_key(elem.get("name"))
                text = (elem.text or "").strip()
                if key in {"Title", "Name"} and text and not current_object.get("label"):
                    current_object["label"] = text
                elif key == "source_hex":
                    hx = _normalize_hex(text)
                    if hx:
                        current_object["metadata"]["source_hex"] = hx
                elif key == "stack_token":
                    tok = text.lower()
                    if tok:
                        current_object["metadata"]["stack_token"] = tok
            elif tag == "vertex" and in_vertices and current_spool is not None:
                x = float(elem.get("x", "0")) * unit_scale_mm
                y = float(elem.get("y", "0")) * unit_scale_mm
                z = float(elem.get("z", "0")) * unit_scale_mm
                current_spool.add_vertex_mm(x, y, z)
            elif tag == "triangle" and in_triangles and current_spool is not None:
                a = int(elem.get("v1", "0"))
                b = int(elem.get("v2", "0"))
                c = int(elem.get("v3", "0"))
                current_spool.add_tri(a, b, c)
            elif tag == "vertices":
                in_vertices = False
            elif tag == "triangles":
                in_triangles = False
            elif tag == "mesh":
                in_mesh = False
                if current_spool is not None:
                    current_spool.close()
                    meshes[current_object_id or ""] = {
                        "v_path": current_spool.v_path,
                        "t_path": current_spool.t_path,
                        "v_count": current_spool.v_count,
                        "t_count": current_spool.t_count,
                    }
                    current_spool = None
            elif tag == "components":
                in_components = False
            elif tag == "object":
                if current_object_id and current_object is not None:
                    metadata = current_object.setdefault("metadata", {})
                    if not metadata.get("stack_token"):
                        tok = _extract_pat_token(current_object.get("label") or current_object.get("name"))
                        if tok:
                            metadata["stack_token"] = tok
                    objects[current_object_id] = current_object
                current_object = None
                current_object_id = None
            elem.clear()

    return {
        "member": member,
        "unit_scale_mm": unit_scale_mm,
        "objects": objects,
        "build_items": build_items,
        "meshes": meshes,
        "child_models": child_models,
    }


def _ensure_root_build_items(root_meta: Dict[str, object]) -> None:
    build_items = root_meta["build_items"]
    if build_items:
        return
    objects = root_meta["objects"]
    for oid, info in objects.items():
        if info.get("has_mesh") and (info.get("type") in {"", "model"}):
            build_items.append(
                {
                    "objectid": oid,
                    "transform": IDENTITY_12[:],
                    "partnumber": str(info.get("label") or info.get("name") or f"part_{oid}"),
                    "printable": True,
                }
            )


def _pick_label(leaf_label: str, build_label: str, source_member: str, oid: str) -> str:
    for candidate in (leaf_label, build_label):
        clean = _sanitize_label(candidate)
        if clean and not clean.lower().startswith("id_"):
            return clean
    return _sanitize_label(leaf_label or build_label or f"{posixpath.basename(source_member)}_{oid}")


def _iter_reachable_leaves(
    models: Dict[str, Dict[str, object]],
    current_member: str,
    obj_id: str,
    tf_mm: List[float],
    build_label: str,
    warnings: List[str],
):
    model_meta = models.get(current_member)
    if model_meta is None:
        warnings.append(f"Referenced model member not found during flatten: {current_member}")
        return
    obj = model_meta["objects"].get(obj_id)
    if obj is None:
        warnings.append(f"Referenced object {obj_id} not found in {current_member}")
        return

    if obj.get("has_mesh"):
        yield {
            "member": current_member,
            "object_id": obj_id,
            "tf_mm_total": tf_mm,
            "leaf_label": str(obj.get("label") or obj.get("name") or f"id_{obj_id}"),
            "build_label": build_label,
            "metadata": dict(obj.get("metadata") or {}),
        }
        return

    for comp in obj.get("components", []):
        child_member = comp.get("path") or current_member
        local_tf_mm = scale_tf_translation(comp.get("transform") or IDENTITY_12, float(model_meta["unit_scale_mm"]))
        next_tf = mul_tf(tf_mm, local_tf_mm)
        yield from _iter_reachable_leaves(
            models,
            child_member,
            str(comp.get("objectid") or ""),
            next_tf,
            build_label or str(obj.get("label") or obj.get("name") or f"id_{obj_id}"),
            warnings,
        )


def _collect_needed_objects(
    models: Dict[str, Dict[str, object]],
    root_member: str,
    warnings: List[str],
) -> Dict[str, set[str]]:
    needed_by_member: Dict[str, set[str]] = defaultdict(set)
    seen: set[Tuple[str, str]] = set()
    queue: deque[Tuple[str, str]] = deque()

    root_meta = models[root_member]
    _ensure_root_build_items(root_meta)
    for item in root_meta["build_items"]:
        if not item.get("printable", True):
            continue
        queue.append((root_member, str(item.get("objectid") or "")))

    while queue:
        member, obj_id = queue.popleft()
        if not member or not obj_id:
            continue
        key = (member, obj_id)
        if key in seen:
            continue
        seen.add(key)
        needed_by_member[member].add(obj_id)
        model_meta = models.get(member)
        if model_meta is None:
            warnings.append(f"Missing referenced model member: {member}")
            continue
        obj = model_meta["objects"].get(obj_id)
        if obj is None:
            warnings.append(f"Missing referenced object {obj_id} in {member}")
            continue
        for comp in obj.get("components", []):
            child_member = comp.get("path") or member
            queue.append((child_member, str(comp.get("objectid") or "")))

    return needed_by_member


def _flatten_parts(
    models: Dict[str, Dict[str, object]],
    root_member: str,
    warnings: List[str],
    vendor_settings: Optional[Dict[str, Dict[str, object]]] = None,
) -> List[Dict[str, object]]:
    root_meta = models[root_member]
    _ensure_root_build_items(root_meta)
    parts_raw: List[Dict[str, object]] = []
    vendor_settings = vendor_settings or {}

    for idx, item in enumerate(root_meta["build_items"], start=1):
        if not item.get("printable", True):
            continue
        base_oid = str(item.get("objectid") or "")
        root_obj = root_meta["objects"].get(base_oid, {})
        sidecar = vendor_settings.get(base_oid, {})
        build_label = _sanitize_label(
            str(item.get("partnumber") or "")
            or str(sidecar.get("name") or "")
            or str(root_obj.get("label") or "")
            or f"part_{idx:03d}"
        )
        base_tf_mm = scale_tf_translation(
            item.get("transform") or IDENTITY_12,
            float(root_meta["unit_scale_mm"]),
        )

        def _append_leaf(leaf: Dict[str, object], applied_build_label: str) -> None:
            label = _pick_label(
                str(leaf.get("leaf_label") or ""),
                str(applied_build_label or leaf.get("build_label") or ""),
                str(leaf["member"]),
                str(leaf["object_id"]),
            )
            metadata = dict(leaf.get("metadata") or {})
            metadata["import_source_member"] = str(leaf["member"])
            metadata["import_source_oid"] = str(leaf["object_id"])
            if applied_build_label:
                metadata["import_source_build_label"] = applied_build_label
            parts_raw.append(
                {
                    "label": label,
                    "leaf_obj_id": str(leaf["object_id"]),
                    "leaf_member": str(leaf["member"]),
                    "tf_mm_total": leaf["tf_mm_total"],
                    "metadata": metadata,
                }
            )

        direct_components = list(root_obj.get("components") or [])
        direct_part_names = dict(sidecar.get("parts") or {})
        if direct_components and direct_part_names:
            for comp in direct_components:
                comp_oid = str(comp.get("objectid") or "")
                comp_label = _sanitize_label(direct_part_names.get(comp_oid) or build_label)
                child_member = comp.get("path") or root_member
                local_tf_mm = scale_tf_translation(
                    comp.get("transform") or IDENTITY_12,
                    float(root_meta["unit_scale_mm"]),
                )
                comp_tf_mm = mul_tf(base_tf_mm, local_tf_mm)
                for leaf in _iter_reachable_leaves(models, child_member, comp_oid, comp_tf_mm, comp_label, warnings):
                    _append_leaf(leaf, comp_label)
        else:
            for leaf in _iter_reachable_leaves(models, root_member, base_oid, base_tf_mm, build_label, warnings):
                _append_leaf(leaf, build_label)

    if not parts_raw:
        raise RuntimeError("No printable mesh leaves were found after flattening the 3MF package.")

    counts = Counter(part["label"] for part in parts_raw)
    seen = Counter()
    for part in parts_raw:
        label = part["label"]
        if counts[label] > 1:
            seen[label] += 1
            member_stem = os.path.splitext(posixpath.basename(str(part["leaf_member"])))[0]
            part["label"] = f"{label}__{member_stem}_{part['leaf_obj_id']}__inst_{seen[label]:03d}"
    return parts_raw


def _fmt_f(value: float) -> str:
    s = f"{float(value):.9f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _xml_attr(value: object) -> str:
    return _xml_escape(str(value), {'"': "&quot;"})


def _xml_text(value: object) -> str:
    return _xml_escape(str(value))


def _xml_bytes(root: ET.Element) -> bytes:
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


def _write_normalized_3mf(
    out_path: str,
    *,
    title: str,
    source_name: str,
    parts: List[Dict[str, object]],
    meshes_by_member: Dict[str, Dict[str, Dict[str, object]]],
) -> None:
    tmp_xml = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", newline="\n")
    tmp_xml_path = tmp_xml.name

    def w(line: str) -> None:
        tmp_xml.write(line)

    w('<?xml version="1.0" encoding="utf-8"?>\n')
    w(f'<model xmlns="{CORE_NS}" unit="millimeter" xml:lang="en-US">\n')
    w(f'  <metadata name="Title">{_xml_text(title)}</metadata>\n')
    w('  <metadata name="LayerLoomNormalized">1</metadata>\n')
    w(f'  <metadata name="LayerLoomNormalizedVersion">{NORMALIZED_FORMAT_VERSION}</metadata>\n')
    w(f'  <metadata name="LayerLoomNormalizedSource">{_xml_text(source_name)}</metadata>\n')
    w("  <resources>\n")

    for out_id, part in enumerate(parts, start=1):
        label = str(part["label"])
        member = str(part["leaf_member"])
        leaf_id = str(part["leaf_obj_id"])
        tf12 = part["tf_mm_total"]
        metadata = dict(part.get("metadata") or {})
        mesh_meta = meshes_by_member.get(member, {}).get(leaf_id)
        if not mesh_meta:
            continue

        w(f'    <object id="{out_id}" type="model" name="{_xml_attr(label)}">\n')
        w(f'      <metadata name="Title">{_xml_text(label)}</metadata>\n')
        w(f'      <metadata name="Name">{_xml_text(label)}</metadata>\n')
        if metadata:
            w("      <metadatagroup>\n")
            for key, value in metadata.items():
                if value is None or str(value).strip() == "":
                    continue
                safe_key = _xml_attr(key)
                safe_value = _xml_text(value)
                w(f'        <metadata name="{safe_key}">{safe_value}</metadata>\n')
            w("      </metadatagroup>\n")
        w("      <mesh>\n")
        w("        <vertices>\n")

        with open(str(mesh_meta["v_path"]), "rb") as vf:
            vmm = mmap.mmap(vf.fileno(), 0, access=mmap.ACCESS_READ)
            for i in range(int(mesh_meta["v_count"])):
                off = i * 12
                x, y, z = struct.unpack_from("<3f", vmm, off)
                X, Y, Z = apply_tf_to_point(tf12, x, y, z)
                w(f'          <vertex x="{_fmt_f(X)}" y="{_fmt_f(Y)}" z="{_fmt_f(Z)}"/>\n')
            vmm.close()

        w("        </vertices>\n")
        w("        <triangles>\n")

        with open(str(mesh_meta["t_path"]), "rb") as tf:
            tmm = mmap.mmap(tf.fileno(), 0, access=mmap.ACCESS_READ)
            for i in range(int(mesh_meta["t_count"])):
                off = i * 12
                a, b, c = struct.unpack_from("<3I", tmm, off)
                w(f'          <triangle v1="{a}" v2="{b}" v3="{c}"/>\n')
            tmm.close()

        w("        </triangles>\n")
        w("      </mesh>\n")
        w("    </object>\n")

    w("  </resources>\n")
    w("  <build>\n")
    for out_id, part in enumerate(parts, start=1):
        label = str(part["label"])
        w(f'    <item objectid="{out_id}" printable="1" partnumber="{_xml_attr(label)}"/>\n')
    w("  </build>\n")
    w("</model>\n")
    tmp_xml.close()

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            b'<?xml version="1.0"?>'
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            b'<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            b'</Types>',
        )
        zf.writestr(
            "_rels/.rels",
            b'<?xml version="1.0"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="3D/3dmodel.model"/>'
            b"</Relationships>",
        )
        zf.write(tmp_xml_path, "3D/3dmodel.model", compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr(
            "3D/_rels/3dmodel.model.rels",
            b'<?xml version="1.0"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )

    os.unlink(tmp_xml_path)


def normalize_3mf_import(path: str) -> NormalizedImportResult:
    source_path = os.path.abspath(path)
    if not os.path.exists(source_path):
        raise FileNotFoundError(source_path)
    if _is_layerloom_normalized(source_path):
        return NormalizedImportResult(source_path, _read_normalized_records(source_path), [])

    st = os.stat(source_path)
    token = hashlib.md5(
        f"{source_path}|{int(st.st_size)}|{int(getattr(st, 'st_mtime_ns', int(st.st_mtime * 1_000_000_000)))}".encode("utf-8")
    ).hexdigest()[:12]
    base = os.path.splitext(os.path.basename(source_path))[0]
    out_path = os.path.join(tempfile.gettempdir(), f"layerloom_normalized_{base}_{token}.3mf")
    if os.path.exists(out_path) and _is_layerloom_normalized(out_path):
        return NormalizedImportResult(out_path, _read_normalized_records(out_path), [])

    warnings: List[str] = []
    tmp_root = tempfile.mkdtemp(prefix="layerloom_import_norm_")
    tmp_mesh_dir = os.path.join(tmp_root, "mesh_spools")
    os.makedirs(tmp_mesh_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(source_path, "r") as zf:
            root_member = _find_root_model_member(zf)
            if not root_member:
                raise RuntimeError("No 3D model member could be found in the 3MF package.")
            vendor_settings = _read_vendor_model_settings(zf)

            models: Dict[str, Dict[str, object]] = {}
            queue: deque[str] = deque([root_member])
            while queue:
                member = queue.popleft()
                if member in models:
                    continue
                if member not in zf.namelist():
                    warnings.append(f"Referenced child model is missing from package: {member}")
                    continue
                meta = _parse_model_streaming(zf, member, tmp_mesh_dir, pass_mode="meta")
                models[member] = meta
                for child_member in meta["child_models"]:
                    if child_member not in models:
                        queue.append(child_member)

            if root_member not in models:
                raise RuntimeError("The root 3MF model could not be parsed.")

            needed_by_member = _collect_needed_objects(models, root_member, warnings)
            meshes_by_member: Dict[str, Dict[str, Dict[str, object]]] = {}
            for member, needed_ids in needed_by_member.items():
                if member not in models:
                    continue
                mesh_pass = _parse_model_streaming(
                    zf,
                    member,
                    tmp_mesh_dir,
                    needed_obj_ids=needed_ids,
                    pass_mode="mesh",
                )
                meshes_by_member[member] = mesh_pass["meshes"]

        parts = _flatten_parts(models, root_member, warnings, vendor_settings=vendor_settings)
        _write_normalized_3mf(
            out_path,
            title=f"LayerLoom normalized import: {os.path.basename(source_path)}",
            source_name=os.path.basename(source_path),
            parts=parts,
            meshes_by_member=meshes_by_member,
        )

        records = [
            NormalizedPartRecord(
                label=str(part["label"]),
                source_member=str(part["leaf_member"]),
                source_object_id=str(part["leaf_obj_id"]),
                source_hex=_normalize_hex((part.get("metadata") or {}).get("source_hex")),
                stack_token=str((part.get("metadata") or {}).get("stack_token") or "").lower() or None,
            )
            for part in parts
        ]
        return NormalizedImportResult(out_path, records, warnings)
    finally:
        for root, _dirs, files in os.walk(tmp_root, topdown=False):
            for file_name in files:
                try:
                    os.remove(os.path.join(root, file_name))
                except Exception:
                    pass
            try:
                os.rmdir(root)
            except Exception:
                pass


def normalize_3mf_path(path: str) -> str:
    return normalize_3mf_import(path).normalized_path
