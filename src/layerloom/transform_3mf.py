from __future__ import annotations

import math
import io
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda tag: f"{{{CORE_NS}}}{tag}"


@dataclass(frozen=True)
class Bounds3D:
    min_corner: np.ndarray
    max_corner: np.ndarray

    @property
    def center(self) -> np.ndarray:
        return 0.5 * (self.min_corner + self.max_corner)

    @property
    def size(self) -> np.ndarray:
        return self.max_corner - self.min_corner


@dataclass(frozen=True)
class TransformPlan:
    global_matrix: np.ndarray
    original_bounds: Bounds3D
    transformed_bounds: Bounds3D


@dataclass
class _ObjectInfo:
    vertices: Optional[np.ndarray]
    components: List[Tuple[str, np.ndarray]]


IDENTITY_12 = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]


def _find_model_xml_name(zf: zipfile.ZipFile) -> Optional[str]:
    for name in zf.namelist():
        if name.startswith("3D/") and name.lower().endswith(".model"):
            return name
    return None


def _xml_bytes(root: ET.Element) -> bytes:
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


def _parse_tf_3mf(value: Optional[str]) -> np.ndarray:
    if not value:
        vals = IDENTITY_12
    else:
        vals = [float(v) for v in value.replace(",", " ").split()]
        if len(vals) != 12:
            vals = IDENTITY_12
    return np.array(
        [
            [vals[0], vals[3], vals[6], vals[9]],
            [vals[1], vals[4], vals[7], vals[10]],
            [vals[2], vals[5], vals[8], vals[11]],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _format_tf_3mf(matrix: np.ndarray) -> str:
    vals = [
        matrix[0, 0], matrix[1, 0], matrix[2, 0],
        matrix[0, 1], matrix[1, 1], matrix[2, 1],
        matrix[0, 2], matrix[1, 2], matrix[2, 2],
        matrix[0, 3], matrix[1, 3], matrix[2, 3],
    ]
    return " ".join(f"{float(v):.9g}" for v in vals)


def _translation(tx: float, ty: float, tz: float) -> np.ndarray:
    out = np.eye(4, dtype=np.float64)
    out[:3, 3] = [tx, ty, tz]
    return out


def _uniform_scale(scale: float) -> np.ndarray:
    out = np.eye(4, dtype=np.float64)
    out[0, 0] = scale
    out[1, 1] = scale
    out[2, 2] = scale
    return out


def _rotation_x(deg: float) -> np.ndarray:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return np.array(
        [[1.0, 0.0, 0.0, 0.0], [0.0, c, -s, 0.0], [0.0, s, c, 0.0], [0.0, 0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def _rotation_y(deg: float) -> np.ndarray:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return np.array(
        [[c, 0.0, s, 0.0], [0.0, 1.0, 0.0, 0.0], [-s, 0.0, c, 0.0], [0.0, 0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def _rotation_z(deg: float) -> np.ndarray:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return np.array(
        [[c, -s, 0.0, 0.0], [s, c, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def orthonormalize_rotation(matrix: np.ndarray) -> np.ndarray:
    rot = np.asarray(matrix, dtype=np.float64)
    if rot.shape == (4, 4):
        rot = rot[:3, :3]
    if rot.shape != (3, 3):
        raise ValueError("Rotation matrix must be 3x3 or 4x4.")
    u, _, vh = np.linalg.svd(rot)
    out = u @ vh
    if np.linalg.det(out) < 0:
        u[:, -1] *= -1.0
        out = u @ vh
    return out


def rotation_matrix_xyz(rot_x_deg: float, rot_y_deg: float, rot_z_deg: float) -> np.ndarray:
    rot4 = _rotation_z(rot_z_deg) @ _rotation_y(rot_y_deg) @ _rotation_x(rot_x_deg)
    return orthonormalize_rotation(rot4[:3, :3])


def rotation_matrix_to_euler_xyz(matrix: np.ndarray) -> Tuple[float, float, float]:
    rot = orthonormalize_rotation(matrix)
    if abs(rot[2, 0]) < 1.0 - 1e-9:
        y = math.asin(-rot[2, 0])
        cy = math.cos(y)
        x = math.atan2(rot[2, 1] / cy, rot[2, 2] / cy)
        z = math.atan2(rot[1, 0] / cy, rot[0, 0] / cy)
    else:
        y = math.pi / 2.0 if rot[2, 0] <= -1.0 else -math.pi / 2.0
        x = math.atan2(-rot[0, 1], rot[1, 1])
        z = 0.0
    return tuple(math.degrees(v) for v in (x, y, z))


def axis_angle_rotation(axis: np.ndarray, angle_deg: float) -> np.ndarray:
    vec = np.asarray(axis, dtype=np.float64).reshape(3)
    nrm = np.linalg.norm(vec)
    if nrm <= 1e-12 or abs(angle_deg) <= 1e-12:
        return np.eye(3, dtype=np.float64)
    x, y, z = vec / nrm
    rad = math.radians(angle_deg)
    c, s = math.cos(rad), math.sin(rad)
    t = 1.0 - c
    return np.array(
        [
            [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
        ],
        dtype=np.float64,
    )


def align_vectors_rotation(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    src = np.asarray(source, dtype=np.float64).reshape(3)
    dst = np.asarray(target, dtype=np.float64).reshape(3)
    src_n = np.linalg.norm(src)
    dst_n = np.linalg.norm(dst)
    if src_n <= 1e-12 or dst_n <= 1e-12:
        return np.eye(3, dtype=np.float64)
    src = src / src_n
    dst = dst / dst_n
    cross = np.cross(src, dst)
    dot = float(np.clip(np.dot(src, dst), -1.0, 1.0))
    cross_n = np.linalg.norm(cross)
    if cross_n <= 1e-12:
        if dot >= 0.0:
            return np.eye(3, dtype=np.float64)
        basis = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        if abs(src[0]) > 0.9:
            basis = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        axis = np.cross(src, basis)
        return axis_angle_rotation(axis, 180.0)
    angle = math.degrees(math.atan2(cross_n, dot))
    return axis_angle_rotation(cross / cross_n, angle)


def embed_rotation_matrix(rotation: np.ndarray) -> np.ndarray:
    out = np.eye(4, dtype=np.float64)
    out[:3, :3] = orthonormalize_rotation(rotation)
    return out


def _all_model_objects(root: ET.Element) -> List[ET.Element]:
    resources = root.find("m:resources", NS)
    if resources is None:
        return []
    out = []
    for obj in resources.findall("m:object", NS):
        obj_type = (obj.get("type") or "").strip().lower()
        if not obj_type or obj_type == "model":
            out.append(obj)
    return out


def _parse_mesh_vertices(obj_el: ET.Element) -> Optional[np.ndarray]:
    mesh = obj_el.find("m:mesh", NS)
    if mesh is None:
        return None
    vertices_el = mesh.find("m:vertices", NS)
    if vertices_el is None:
        return None
    verts = []
    for vertex in vertices_el.findall("m:vertex", NS):
        verts.append(
            (
                float(vertex.get("x", 0.0)),
                float(vertex.get("y", 0.0)),
                float(vertex.get("z", 0.0)),
            )
        )
    if not verts:
        return None
    return np.asarray(verts, dtype=np.float64)


def _parse_object_infos(root: ET.Element) -> Dict[str, _ObjectInfo]:
    out: Dict[str, _ObjectInfo] = {}
    for obj in _all_model_objects(root):
        oid = obj.get("id")
        if not oid:
            continue
        comps = []
        comps_el = obj.find("m:components", NS)
        if comps_el is not None:
            for comp in comps_el.findall("m:component", NS):
                child = comp.get("objectid")
                if child:
                    comps.append((child, _parse_tf_3mf(comp.get("transform"))))
        out[oid] = _ObjectInfo(vertices=_parse_mesh_vertices(obj), components=comps)
    return out


def _parse_build_items(root: ET.Element) -> List[Tuple[str, np.ndarray]]:
    build = root.find("m:build", NS)
    if build is None:
        return []
    out = []
    for item in build.findall("m:item", NS):
        oid = item.get("objectid")
        if oid:
            out.append((oid, _parse_tf_3mf(item.get("transform"))))
    return out


def _transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    ones = np.ones((points.shape[0], 1), dtype=np.float64)
    pts_h = np.hstack([points, ones])
    out = (matrix @ pts_h.T).T
    return out[:, :3]


def _compute_bounds_from_infos(
    object_infos: Dict[str, _ObjectInfo],
    build_items: List[Tuple[str, np.ndarray]],
    extra_matrix: Optional[np.ndarray] = None,
) -> Bounds3D:
    if not build_items:
        raise RuntimeError("3MF has no build items.")

    mins = np.array([np.inf, np.inf, np.inf], dtype=np.float64)
    maxs = np.array([-np.inf, -np.inf, -np.inf], dtype=np.float64)
    found = False

    def walk(oid: str, matrix: np.ndarray, stack: Tuple[str, ...]) -> None:
        nonlocal mins, maxs, found
        if oid in stack:
            raise RuntimeError(f"Component cycle detected at object {oid}.")

        info = object_infos.get(oid)
        if info is None:
            return

        if info.vertices is not None and info.vertices.size > 0:
            pts = _transform_points(info.vertices, matrix)
            mins = np.minimum(mins, pts.min(axis=0))
            maxs = np.maximum(maxs, pts.max(axis=0))
            found = True

        for child_oid, child_tf in info.components:
            walk(child_oid, matrix @ child_tf, stack + (oid,))

    base = np.eye(4, dtype=np.float64) if extra_matrix is None else np.asarray(extra_matrix, dtype=np.float64)
    for oid, build_tf in build_items:
        walk(oid, base @ build_tf, tuple())

    if not found:
        raise RuntimeError("3MF has no build-reachable mesh geometry.")
    return Bounds3D(min_corner=mins, max_corner=maxs)


def compute_transform_plan(
    input_3mf: str,
    *,
    rot_x_deg: float = 0.0,
    rot_y_deg: float = 0.0,
    rot_z_deg: float = 0.0,
    scale: float,
    orientation_matrix: Optional[np.ndarray] = None,
    plate_width: float = 256.0,
    plate_depth: float = 256.0,
    center_xy: bool = True,
) -> TransformPlan:
    if scale <= 0:
        raise ValueError("Scale must be > 0.")

    with zipfile.ZipFile(input_3mf, "r") as zf:
        model_name = _find_model_xml_name(zf)
        if not model_name:
            raise RuntimeError("No 3D/*.model found in 3MF.")
        root = ET.fromstring(zf.read(model_name))

    object_infos = _parse_object_infos(root)
    build_items = _parse_build_items(root)
    original_bounds = _compute_bounds_from_infos(object_infos, build_items)

    center = original_bounds.center
    orientation = (
        rotation_matrix_xyz(rot_x_deg, rot_y_deg, rot_z_deg)
        if orientation_matrix is None
        else orthonormalize_rotation(orientation_matrix)
    )
    pre_matrix = (
        embed_rotation_matrix(orientation)
        @ _uniform_scale(scale)
        @ _translation(-center[0], -center[1], -center[2])
    )
    pre_bounds = _compute_bounds_from_infos(object_infos, build_items, extra_matrix=pre_matrix)

    if center_xy:
        target_x = plate_width * 0.5
        target_y = plate_depth * 0.5
    else:
        target_x = original_bounds.center[0]
        target_y = original_bounds.center[1]
    tx = target_x - pre_bounds.center[0]
    ty = target_y - pre_bounds.center[1]
    tz = -pre_bounds.min_corner[2]
    placement = _translation(tx, ty, tz)
    global_matrix = placement @ pre_matrix
    transformed_bounds = Bounds3D(
        min_corner=pre_bounds.min_corner + np.array([tx, ty, tz], dtype=np.float64),
        max_corner=pre_bounds.max_corner + np.array([tx, ty, tz], dtype=np.float64),
    )
    return TransformPlan(
        global_matrix=global_matrix,
        original_bounds=original_bounds,
        transformed_bounds=transformed_bounds,
    )


def _stamp_object_metadata(obj_el: ET.Element, new_label: str) -> None:
    obj_el.set("name", new_label)
    for key in ("Title", "Name"):
        md = obj_el.find(f"m:metadata[@name='{key}']", NS)
        if md is None:
            md = ET.SubElement(obj_el, M("metadata"), {"name": key})
        md.text = new_label


def _stamp_object_metadata_values(obj_el: ET.Element, metadata: Dict[str, str]) -> None:
    for key, value in (metadata or {}).items():
        text = str(value or "").strip()
        if not key or not text:
            continue
        md = obj_el.find(f"m:metadata[@name='{key}']", NS)
        if md is None:
            md = ET.SubElement(obj_el, M("metadata"), {"name": key})
        md.text = text


def _sync_build_partnumbers(root: ET.Element) -> None:
    build = root.find("m:build", NS)
    resources = root.find("m:resources", NS)
    if build is None or resources is None:
        return
    idmap = {obj.get("id"): obj for obj in resources.findall("m:object", NS)}
    for item in build.findall("m:item", NS):
        oid = item.get("objectid")
        if oid and oid in idmap:
            name = idmap[oid].get("name")
            if name:
                item.set("partnumber", name)


def write_transformed_3mf(
    input_3mf: str,
    output_3mf: str,
    global_matrix: np.ndarray,
    *,
    name_updates: Optional[Dict[str, str]] = None,
    metadata_updates: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    with zipfile.ZipFile(input_3mf, "r") as zin:
        model_name = _find_model_xml_name(zin)
        if not model_name:
            raise RuntimeError("No 3D/*.model found in 3MF.")
        root = ET.fromstring(zin.read(model_name))
        others = {name: zin.read(name) for name in zin.namelist() if name != model_name}

    if name_updates or metadata_updates:
        for obj in _all_model_objects(root):
            oid = obj.get("id")
            if oid and name_updates and oid in name_updates:
                _stamp_object_metadata(obj, name_updates[oid])
            if oid and metadata_updates and oid in metadata_updates:
                _stamp_object_metadata_values(obj, metadata_updates[oid])
        _sync_build_partnumbers(root)

    build = root.find("m:build", NS)
    if build is None:
        raise RuntimeError("3MF has no <build> section.")

    for item in build.findall("m:item", NS):
        item_tf = _parse_tf_3mf(item.get("transform"))
        item.set("transform", _format_tf_3mf(np.asarray(global_matrix, dtype=np.float64) @ item_tf))

    with zipfile.ZipFile(output_3mf, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for name, data in others.items():
            zout.writestr(name, data)
        zout.writestr(model_name, _xml_bytes(root))
