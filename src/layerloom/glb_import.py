"""GLB/GLTF color import for headless LayerLoom."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np


COMP_TYPE_TO_DTYPE = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}

TYPE_TO_COMPS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
}

TRIANGLES = 4
LAYERLOOM_META_NS = "https://layerloom.dev/source-color/1"


@dataclass(frozen=True)
class GlbImportResult:
    output_3mf: str
    manifest_path: str
    object_count: int
    color_levels: int


def target_colors_to_levels(target_colors: int) -> int:
    target_colors = max(2, int(target_colors))
    return max(2, int(math.ceil(target_colors ** (1.0 / 3.0))))


def _require_glb_deps():
    try:
        import lib3mf  # type: ignore
        from pygltflib import GLTF2  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "GLB import requires the headless extras: lib3mf and pygltflib. "
            "Install with `pip install layerloom[headless]`."
        ) from exc
    return lib3mf, GLTF2


def _perf(label: str, started_at: float, *, extra: str = "", verbose: bool = False) -> None:
    if not verbose:
        return
    msg = f"[perf] {label}: {time.perf_counter() - started_at:.3f}s"
    if extra:
        msg += f" | {extra}"
    print(msg)


def _load_gltf(path: str):
    _lib3mf, GLTF2 = _require_glb_deps()
    gltf = GLTF2().load(path)
    gltf._base_dir = os.path.dirname(os.path.abspath(path))
    return gltf


def _buffer_bytes(gltf: Any, buffer_index: int) -> bytes:
    buf = gltf.buffers[buffer_index]
    uri = getattr(buf, "uri", None)
    if uri is None:
        blob = gltf.binary_blob()
        if blob is None:
            raise RuntimeError("GLB has no binary blob for buffer 0.")
        return blob
    if str(uri).startswith("data:"):
        return gltf.get_data_from_buffer_uri(uri)
    with open(os.path.join(gltf._base_dir, uri), "rb") as f:
        return f.read()


def _read_accessor(gltf: Any, accessor_idx: int) -> np.ndarray:
    acc = gltf.accessors[accessor_idx]
    bv = gltf.bufferViews[acc.bufferView]
    blob = _buffer_bytes(gltf, bv.buffer)

    dtype = COMP_TYPE_TO_DTYPE[acc.componentType]
    comps = TYPE_TO_COMPS[acc.type]
    count = acc.count

    base = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    elem = np.dtype(dtype).itemsize * comps
    stride = bv.byteStride or elem

    arr = np.ndarray(
        shape=(count, comps),
        dtype=dtype,
        buffer=blob,
        offset=base,
        strides=(stride, np.dtype(dtype).itemsize),
    ).copy()

    if getattr(acc, "normalized", False) and np.issubdtype(dtype, np.integer):
        if np.issubdtype(dtype, np.signedinteger):
            info = np.iinfo(dtype)
            arr = np.maximum(arr.astype(np.float32) / float(info.max), -1.0)
        else:
            arr = arr.astype(np.float32) / float(np.iinfo(dtype).max)
    return arr


def _compact(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    used = np.unique(faces.reshape(-1))
    remap = -np.ones(vertices.shape[0], dtype=np.int64)
    remap[used] = np.arange(len(used), dtype=np.int64)
    return vertices[used], remap[faces]


def _implicit_triangle_indices(n_verts: int) -> Optional[np.ndarray]:
    if n_verts % 3 != 0:
        return None
    return np.arange(n_verts, dtype=np.int64).reshape(-1, 3)


def _quat_to_rot3(q: Sequence[float]) -> np.ndarray:
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float64,
    )


def _node_local_matrix(node: Any) -> np.ndarray:
    if getattr(node, "matrix", None):
        return np.array(node.matrix, dtype=np.float64).reshape((4, 4)).T

    t = np.eye(4, dtype=np.float64)
    r = np.eye(4, dtype=np.float64)
    s = np.eye(4, dtype=np.float64)
    if getattr(node, "translation", None):
        t[:3, 3] = np.asarray(node.translation, dtype=np.float64)
    if getattr(node, "rotation", None):
        r[:3, :3] = _quat_to_rot3(node.rotation)
    if getattr(node, "scale", None):
        sx, sy, sz = node.scale
        s[0, 0] = sx
        s[1, 1] = sy
        s[2, 2] = sz
    return t @ r @ s


def _world_matrices(gltf: Any) -> list[np.ndarray]:
    nodes = gltf.nodes or []
    world: list[Optional[np.ndarray]] = [None] * len(nodes)
    if not gltf.scenes:
        return [_node_local_matrix(node) for node in nodes]

    scene_index = gltf.scene if gltf.scene is not None else 0
    roots = gltf.scenes[scene_index].nodes or []

    def visit(node_idx: int, parent_world: np.ndarray) -> None:
        here = parent_world @ _node_local_matrix(nodes[node_idx])
        world[node_idx] = here
        for child_idx in nodes[node_idx].children or []:
            visit(child_idx, here)

    for root_idx in roots:
        visit(root_idx, np.eye(4, dtype=np.float64))
    for idx, mat in enumerate(world):
        if mat is None:
            world[idx] = _node_local_matrix(nodes[idx])
    return [np.asarray(mat, dtype=np.float64) for mat in world]


def _apply_transform(pos: np.ndarray, mat4: np.ndarray) -> np.ndarray:
    pos3 = pos[:, :3].astype(np.float64)
    pos_h = np.hstack([pos3, np.ones((pos3.shape[0], 1), dtype=np.float64)])
    return (mat4 @ pos_h.T).T[:, :3].astype(np.float32)


def rgb01_to_hex(rgb: Sequence[float]) -> str:
    rgb_arr = np.clip(np.asarray(rgb, dtype=np.float64)[:3], 0.0, 1.0)
    return "#{:02x}{:02x}{:02x}".format(*[int(round(float(c) * 255.0)) for c in rgb_arr])


def _face_color_key(face_vertex_colors: np.ndarray, color_levels: int) -> tuple[int, int, int]:
    avg = np.clip(np.mean(face_vertex_colors[:, :3], axis=0), 0.0, 1.0)
    return tuple(np.round(avg * (color_levels - 1)).astype(int).tolist())


def _material_rgb(gltf: Any, material_index: Optional[int]) -> Optional[np.ndarray]:
    if material_index is None:
        return None
    materials = gltf.materials or []
    if material_index < 0 or material_index >= len(materials):
        return None
    pbr = getattr(materials[material_index], "pbrMetallicRoughness", None)
    factor = getattr(pbr, "baseColorFactor", None) if pbr is not None else None
    if factor is None:
        return None
    rgb = np.asarray(factor[:3], dtype=np.float64)
    if rgb.size != 3:
        return None
    return np.clip(rgb, 0.0, 1.0)


def _summarize_group_color(
    positions: np.ndarray,
    colors: np.ndarray,
    faces: np.ndarray,
) -> tuple[np.ndarray, float, np.ndarray, int]:
    face_rgbs = np.mean(colors[faces][:, :, :3], axis=1, dtype=np.float64)
    tris = positions[faces]
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    areas = 0.5 * np.linalg.norm(cross, axis=1)
    positive = areas > 0
    if np.any(positive):
        weighted_rgb_sum = np.sum(face_rgbs[positive] * areas[positive, None], axis=0)
        weight_sum = float(np.sum(areas[positive]))
    else:
        weighted_rgb_sum = np.zeros(3, dtype=np.float64)
        weight_sum = 0.0
    return weighted_rgb_sum, weight_sum, np.sum(face_rgbs, axis=0), int(face_rgbs.shape[0])


def _stats_rgb(stats: Optional[dict[str, Any]]) -> Optional[np.ndarray]:
    if not stats:
        return None
    if "fixed_rgb" in stats:
        return np.asarray(stats["fixed_rgb"], dtype=np.float64)
    if float(stats.get("weight_sum", 0.0)) > 0:
        return np.asarray(stats["weighted_rgb_sum"], dtype=np.float64) / float(stats["weight_sum"])
    if int(stats.get("face_count", 0)) > 0:
        return np.asarray(stats["rgb_sum"], dtype=np.float64) / float(stats["face_count"])
    return None


def _merge_chunks(chunks: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    all_verts = []
    all_faces = []
    v_offset = 0
    for verts, faces in chunks:
        if verts.size == 0 or faces.size == 0:
            continue
        all_verts.append(verts)
        all_faces.append(faces + v_offset)
        v_offset += verts.shape[0]
    if not all_verts:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.int64)
    return np.vstack(all_verts).astype(np.float32, copy=False), np.vstack(all_faces).astype(np.int64, copy=False)


def _safe_key_string(key: tuple[Any, ...]) -> str:
    return "_".join(str(v).replace("#", "") for v in key)


def _add_layerloom_metadata(obj: Any, *, group_key: tuple[Any, ...], color_levels: int, avg_rgb: Optional[np.ndarray]) -> None:
    mg = obj.GetMetaDataGroup()
    avg_hex = rgb01_to_hex(avg_rgb) if avg_rgb is not None else None
    mg.AddMetaData(LAYERLOOM_META_NS, "group_key", json.dumps(list(group_key)), "string", True)
    mg.AddMetaData(LAYERLOOM_META_NS, "color_levels", str(color_levels), "string", True)
    mg.AddMetaData(
        LAYERLOOM_META_NS,
        "source_kind",
        "source_color" if avg_hex is not None else "no_color",
        "string",
        True,
    )
    if avg_hex is not None and avg_rgb is not None:
        mg.AddMetaData(LAYERLOOM_META_NS, "source_hex", avg_hex, "string", True)
        mg.AddMetaData(
            LAYERLOOM_META_NS,
            "source_rgb01",
            ",".join(f"{float(v):.6f}" for v in avg_rgb),
            "string",
            True,
        )


def import_glb_to_3mf(
    input_path: str,
    output_3mf: str,
    *,
    manifest_out: Optional[str] = None,
    target_colors: int = 20,
    color_levels: Optional[int] = None,
    scale: float = 0.1,
    missing_color_mode: str = "error",
    verbose: bool = False,
) -> GlbImportResult:
    """Convert GLB/GLTF scene geometry into one source-color 3MF object per color group."""
    lib3mf, _GLTF2 = _require_glb_deps()
    if missing_color_mode not in {"error", "skip", "group"}:
        raise ValueError("missing_color_mode must be one of: error, skip, group")
    if color_levels is None:
        color_levels = target_colors_to_levels(target_colors)
    if color_levels < 2:
        raise ValueError("color_levels must be at least 2")

    t_total = time.perf_counter()
    t_load = time.perf_counter()
    gltf = _load_gltf(input_path)
    _perf("glb load", t_load, extra=os.path.basename(input_path), verbose=verbose)
    t_world = time.perf_counter()
    world_mats = _world_matrices(gltf)
    _perf("glb world matrices", t_world, extra=f"nodes={len(gltf.nodes or [])}", verbose=verbose)

    global_groups: dict[tuple[Any, ...], list[tuple[np.ndarray, np.ndarray]]] = {}
    global_stats: dict[tuple[Any, ...], dict[str, Any]] = {}
    nodes = gltf.nodes or []
    meshes = gltf.meshes or []

    t_group = time.perf_counter()
    for node_index, node in enumerate(nodes):
        if node.mesh is None or node.mesh < 0 or node.mesh >= len(meshes):
            continue
        mesh = meshes[node.mesh]
        transform = world_mats[node_index]
        for prim_index, prim in enumerate(mesh.primitives or []):
            mode = prim.mode if prim.mode is not None else TRIANGLES
            if mode != TRIANGLES:
                if verbose:
                    print(f"skip node{node_index} mesh{node.mesh} prim{prim_index}: mode={mode}")
                continue

            attrs = prim.attributes
            pos_idx = getattr(attrs, "POSITION", None)
            if pos_idx is None:
                if verbose:
                    print(f"skip node{node_index} mesh{node.mesh} prim{prim_index}: missing POSITION")
                continue
            pos_world = _apply_transform(_read_accessor(gltf, pos_idx).astype(np.float32), transform) * float(scale)

            if prim.indices is None:
                idx = _implicit_triangle_indices(pos_world.shape[0])
                if idx is None:
                    raise RuntimeError("Primitive has no indices and vertex count is not divisible by 3.")
            else:
                raw = _read_accessor(gltf, prim.indices).astype(np.int64).reshape(-1)
                if raw.size % 3 != 0:
                    raise RuntimeError("Primitive index buffer length is not divisible by 3.")
                idx = raw.reshape(-1, 3)

            color_idx = getattr(attrs, "COLOR_0", None)
            local_groups: dict[tuple[Any, ...], list[np.ndarray]] = {}
            color_array: Optional[np.ndarray] = None
            material_rgb = None

            if color_idx is not None:
                color_array = np.clip(_read_accessor(gltf, color_idx).astype(np.float32)[:, :3], 0.0, 1.0)
                for face in idx:
                    local_groups.setdefault(_face_color_key(color_array[face], color_levels), []).append(face)
            else:
                material_rgb = _material_rgb(gltf, prim.material)
                if material_rgb is not None:
                    mat_hex = rgb01_to_hex(material_rgb)
                    local_groups[("material", mat_hex)] = [face for face in idx]
                elif missing_color_mode == "error":
                    raise RuntimeError(
                        "GLB primitive has neither COLOR_0 vertex colors nor material baseColorFactor. "
                        "Pass --missing-color-mode skip or group to override."
                    )
                elif missing_color_mode == "skip":
                    continue
                else:
                    local_groups[("no_color",)] = [face for face in idx]

            if verbose:
                print(
                    f"node{node_index} mesh{node.mesh} prim{prim_index}: "
                    f"{len(local_groups)} local groups"
                )

            for key, faces_list in local_groups.items():
                faces = np.asarray(faces_list, dtype=np.int64)
                verts2, faces2 = _compact(pos_world, faces)
                global_groups.setdefault(key, []).append((verts2, faces2))
                if color_array is not None:
                    weighted_rgb_sum, weight_sum, rgb_sum, face_count = _summarize_group_color(pos_world, color_array, faces)
                    stats = global_stats.setdefault(
                        key,
                        {
                            "weighted_rgb_sum": np.zeros(3, dtype=np.float64),
                            "weight_sum": 0.0,
                            "rgb_sum": np.zeros(3, dtype=np.float64),
                            "face_count": 0,
                        },
                    )
                    stats["weighted_rgb_sum"] += weighted_rgb_sum
                    stats["weight_sum"] += weight_sum
                    stats["rgb_sum"] += rgb_sum
                    stats["face_count"] += face_count
                elif material_rgb is not None:
                    global_stats.setdefault(key, {"fixed_rgb": material_rgb})

    _perf("glb face grouping", t_group, extra=f"groups={len(global_groups)}", verbose=verbose)
    if not global_groups:
        raise RuntimeError("No parts produced from GLB/GLTF input.")

    os.makedirs(os.path.dirname(os.path.abspath(output_3mf)) or ".", exist_ok=True)
    manifest_path = manifest_out or f"{os.path.splitext(output_3mf)[0]}.colors.json"
    manifest = {
        "input": os.path.abspath(input_path),
        "output_3mf": os.path.abspath(output_3mf),
        "color_levels": int(color_levels),
        "scale": float(scale),
        "objects": [],
    }

    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    t_write = time.perf_counter()
    for key in sorted(global_groups, key=str):
        verts, faces = _merge_chunks(global_groups[key])
        if verts.shape[0] == 0 or faces.shape[0] == 0:
            continue
        avg_rgb = _stats_rgb(global_stats.get(key))
        avg_hex = rgb01_to_hex(avg_rgb) if avg_rgb is not None else None
        name = f"color_{_safe_key_string(key)}"
        obj = model.AddMeshObject()
        obj.SetName(name)
        _add_layerloom_metadata(obj, group_key=key, color_levels=color_levels, avg_rgb=avg_rgb)

        for v in verts:
            p = lib3mf.Position()
            p.Coordinates[0] = float(v[0])
            p.Coordinates[1] = float(v[1])
            p.Coordinates[2] = float(v[2])
            obj.AddVertex(p)
        for face in faces:
            tri = lib3mf.Triangle()
            tri.Indices[0] = int(face[0])
            tri.Indices[1] = int(face[1])
            tri.Indices[2] = int(face[2])
            obj.AddTriangle(tri)
        model.AddBuildItem(obj, wrapper.GetIdentityTransform())
        manifest["objects"].append(
            {
                "name": name,
                "group_key": list(key),
                "source_hex": avg_hex,
                "source_rgb01": [round(float(v), 6) for v in avg_rgb] if avg_rgb is not None else None,
                "vertex_count": int(verts.shape[0]),
                "triangle_count": int(faces.shape[0]),
                "chunk_count": len(global_groups[key]),
                "source_kind": "source_color" if avg_hex is not None else "no_color",
            }
        )

    writer = model.QueryWriter("3mf")
    writer.WriteToFile(output_3mf)
    _perf("glb write 3mf", t_write, extra=os.path.basename(output_3mf), verbose=verbose)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    _perf("glb total", t_total, extra=f"objects={len(manifest['objects'])}", verbose=verbose)
    return GlbImportResult(
        output_3mf=os.path.abspath(output_3mf),
        manifest_path=os.path.abspath(manifest_path),
        object_count=len(manifest["objects"]),
        color_levels=int(color_levels),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert GLB/GLTF colors into a source-color 3MF.")
    parser.add_argument("input", help="Input .glb or .gltf")
    parser.add_argument("--out", required=True, help="Output .3mf")
    parser.add_argument("--manifest-out")
    parser.add_argument("--target-colors", type=int, default=20)
    parser.add_argument("--color-levels", type=int)
    parser.add_argument("--scale", type=float, default=0.1)
    parser.add_argument("--missing-color-mode", choices=["error", "skip", "group"], default="error")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = import_glb_to_3mf(
            args.input,
            args.out,
            manifest_out=args.manifest_out,
            target_colors=args.target_colors,
            color_levels=args.color_levels,
            scale=args.scale,
            missing_color_mode=args.missing_color_mode,
            verbose=args.verbose,
        )
        print(result.output_3mf)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
