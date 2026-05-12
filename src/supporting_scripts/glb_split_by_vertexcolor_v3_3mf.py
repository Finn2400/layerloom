#!/usr/bin/env python3
"""
glb__merge_sceneplaced_binnedcolor__3mf.py

Read a GLB/GLTF, preserve full scene placement, group faces by binned average
face color across the ENTIRE scene, and write one 3MF object per color.

This means:
- node/world transforms are applied first, so layout/placement is preserved
- all geometry that falls into the same color bin is merged into one global part
- each color part may contain many disconnected islands across the scene

Good for:
- flipbook/frame layouts
- "one object per color across all models"
- preserving arrangement while reducing part count

Examples
--------
python glb__merge_sceneplaced_binnedcolor__3mf.py input.glb --out output.3mf --color-levels 4 --verbose
python glb__merge_sceneplaced_binnedcolor__3mf.py input.glb --out output.3mf --color-levels 3 --no-color-mode skip
"""

from __future__ import annotations

import json
import os
import argparse
import sys
import time
import numpy as np
import lib3mf
from pygltflib import GLTF2

try:
    import resource
except Exception:
    resource = None


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


def perf(label: str, started_at: float, *, extra: str = "") -> None:
    elapsed = time.perf_counter() - started_at
    msg = f"[perf] {label}: {elapsed:.3f}s"
    rss = rss_mb()
    if rss is not None:
        msg += f" | rss≈{rss:.1f} MB"
    if extra:
        msg += f" | {extra}"
    print(msg)


def load_gltf(path: str) -> GLTF2:
    gltf = GLTF2().load(path)
    gltf._base_dir = os.path.dirname(os.path.abspath(path))
    return gltf


def get_buffer_bytes(gltf: GLTF2, buffer_index: int) -> bytes:
    buf = gltf.buffers[buffer_index]
    if buf.uri is None:
        return gltf.binary_blob()
    if buf.uri.startswith("data:"):
        return gltf.get_data_from_buffer_uri(buf.uri)
    with open(os.path.join(gltf._base_dir, buf.uri), "rb") as f:
        return f.read()


def read_accessor(gltf: GLTF2, accessor_idx: int) -> np.ndarray:
    acc = gltf.accessors[accessor_idx]
    bv = gltf.bufferViews[acc.bufferView]
    blob = get_buffer_bytes(gltf, bv.buffer)

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

    if getattr(acc, "normalized", False):
        if np.issubdtype(dtype, np.integer):
            if np.issubdtype(dtype, np.signedinteger):
                info = np.iinfo(dtype)
                arr = np.maximum(arr.astype(np.float32) / float(info.max), -1.0)
            else:
                arr = arr.astype(np.float32) / float(np.iinfo(dtype).max)

    return arr


def compact(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    used = np.unique(faces.reshape(-1))
    remap = -np.ones(vertices.shape[0], dtype=np.int64)
    remap[used] = np.arange(len(used), dtype=np.int64)
    return vertices[used], remap[faces]


def build_indices_from_implicit_tris(n_verts: int) -> np.ndarray | None:
    if n_verts % 3 != 0:
        return None
    return np.arange(n_verts, dtype=np.int64).reshape(-1, 3)


def quat_to_rot3(q) -> np.ndarray:
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z

    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz),       2.0 * (xz + wy)],
            [2.0 * (xy + wz),       1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy),       2.0 * (yz + wx),       1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float64,
    )


def node_local_matrix(node) -> np.ndarray:
    if getattr(node, "matrix", None):
        return np.array(node.matrix, dtype=np.float64).reshape((4, 4)).T

    t = np.eye(4, dtype=np.float64)
    r = np.eye(4, dtype=np.float64)
    s = np.eye(4, dtype=np.float64)

    if getattr(node, "translation", None):
        t[:3, 3] = np.array(node.translation, dtype=np.float64)

    if getattr(node, "rotation", None):
        r[:3, :3] = quat_to_rot3(node.rotation)

    if getattr(node, "scale", None):
        sx, sy, sz = node.scale
        s[0, 0] = sx
        s[1, 1] = sy
        s[2, 2] = sz

    return t @ r @ s


def compute_world_matrices(gltf: GLTF2) -> list[np.ndarray]:
    nodes = gltf.nodes or []
    world = [None] * len(nodes)

    if not gltf.scenes:
        for i, node in enumerate(nodes):
            world[i] = node_local_matrix(node)
        return world

    scene_index = gltf.scene if gltf.scene is not None else 0
    scene = gltf.scenes[scene_index]
    roots = scene.nodes or []

    def visit(node_idx: int, parent_world: np.ndarray) -> None:
        local = node_local_matrix(nodes[node_idx])
        here = parent_world @ local
        world[node_idx] = here
        for child_idx in (nodes[node_idx].children or []):
            visit(child_idx, here)

    identity = np.eye(4, dtype=np.float64)
    for root_idx in roots:
        visit(root_idx, identity)

    for i, mat in enumerate(world):
        if mat is None:
            world[i] = node_local_matrix(nodes[i])

    return world


def apply_transform(pos: np.ndarray, mat4: np.ndarray) -> np.ndarray:
    pos3 = pos[:, :3].astype(np.float64)
    pos_h = np.hstack([pos3, np.ones((pos3.shape[0], 1), dtype=np.float64)])
    out = (mat4 @ pos_h.T).T
    return out[:, :3].astype(np.float32)


def rgb01_to_hex(rgb: np.ndarray) -> str:
    rgb = np.clip(np.asarray(rgb, dtype=np.float64)[:3], 0.0, 1.0)
    return "#{:02x}{:02x}{:02x}".format(*[int(round(c * 255.0)) for c in rgb])


def face_color_key(face_vertex_colors: np.ndarray, color_levels: int) -> tuple[int, int, int]:
    avg = np.mean(face_vertex_colors[:, :3], axis=0)
    avg = np.clip(avg, 0.0, 1.0)
    return tuple(np.round(avg * (color_levels - 1)).astype(int).tolist())


def summarize_group_color(
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

    rgb_sum = np.sum(face_rgbs, axis=0)
    face_count = int(face_rgbs.shape[0])
    return weighted_rgb_sum, weight_sum, rgb_sum, face_count


def resolve_average_rgb(stats: dict | None) -> np.ndarray | None:
    if not stats:
        return None
    if stats["weight_sum"] > 0:
        return np.asarray(stats["weighted_rgb_sum"], dtype=np.float64) / float(stats["weight_sum"])
    if stats["face_count"] > 0:
        return np.asarray(stats["rgb_sum"], dtype=np.float64) / float(stats["face_count"])
    return None


def default_manifest_path(out_path: str) -> str:
    stem, _ = os.path.splitext(out_path)
    return f"{stem}.colors.json"


def add_layerloom_metadata(
    obj,
    *,
    group_key: tuple,
    color_levels: int,
    avg_rgb: np.ndarray | None,
    avg_hex: str | None,
) -> None:
    mg = obj.GetMetaDataGroup()
    mg.AddMetaData(LAYERLOOM_META_NS, "group_key", json.dumps(list(group_key)), "string", True)
    mg.AddMetaData(LAYERLOOM_META_NS, "color_levels", str(color_levels), "string", True)
    mg.AddMetaData(
        LAYERLOOM_META_NS,
        "source_kind",
        "vertex_color" if avg_hex is not None else "no_color",
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


def merge_chunks(chunks: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
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

    verts_merged = np.vstack(all_verts).astype(np.float32, copy=False)
    faces_merged = np.vstack(all_faces).astype(np.int64, copy=False)
    return verts_merged, faces_merged


def main() -> None:
    t_total = time.perf_counter()
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="Input .glb or .gltf")
    ap.add_argument("--scale", type=float, default=0.1, help="Uniform scale applied after world transforms")
    ap.add_argument("--out", default="output.3mf", help="Output .3mf path")
    ap.add_argument(
        "--manifest-out",
        default=None,
        help="Optional JSON manifest path (default: <out_basename>.colors.json)",
    )
    ap.add_argument(
        "--color-levels",
        type=int,
        default=4,
        help="Number of bins per RGB channel for grouping. Lower = fewer groups. Recommended: 3 to 6.",
    )
    ap.add_argument(
        "--no-color-mode",
        choices=["skip", "group"],
        default="group",
        help="What to do when a primitive has no COLOR_0: skip it or merge it into one global 'no_color' part.",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-node/per-primitive group counts and final global color counts",
    )
    args = ap.parse_args()

    if args.color_levels < 2:
        raise SystemExit("--color-levels must be at least 2")

    t_load = time.perf_counter()
    gltf = load_gltf(args.input)
    perf("glb load", t_load, extra=os.path.basename(args.input))
    t_world = time.perf_counter()
    world_mats = compute_world_matrices(gltf)
    perf("glb world matrices", t_world, extra=f"nodes={len(gltf.nodes or [])}")

    nodes = gltf.nodes or []
    meshes = gltf.meshes or []

    # Global color buckets: key -> list of (verts, faces) chunks
    global_groups: dict[tuple, list[tuple[np.ndarray, np.ndarray]]] = {}
    global_group_stats: dict[tuple, dict[str, object]] = {}

    t_group = time.perf_counter()
    for ni, node in enumerate(nodes):
        if node.mesh is None:
            continue

        if node.mesh < 0 or node.mesh >= len(meshes):
            if args.verbose:
                print(f"skip node{ni}: mesh index out of range ({node.mesh})")
            continue

        mesh = meshes[node.mesh]
        transform = world_mats[ni]

        for pi, prim in enumerate(mesh.primitives or []):
            mode = prim.mode if prim.mode is not None else TRIANGLES
            if mode != TRIANGLES:
                if args.verbose:
                    print(f"skip node{ni} mesh{node.mesh} prim{pi}: mode={mode} (not TRIANGLES)")
                continue

            attrs = prim.attributes
            pos_idx = getattr(attrs, "POSITION", None)
            if pos_idx is None:
                if args.verbose:
                    print(f"skip node{ni} mesh{node.mesh} prim{pi}: missing POSITION")
                continue

            pos_local = read_accessor(gltf, pos_idx).astype(np.float32)
            pos_world = apply_transform(pos_local, transform) * float(args.scale)

            if prim.indices is None:
                idx = build_indices_from_implicit_tris(pos_world.shape[0])
                if idx is None:
                    if args.verbose:
                        print(
                            f"skip node{ni} mesh{node.mesh} prim{pi}: "
                            f"no indices and vertex count not multiple of 3 ({pos_world.shape[0]})"
                        )
                    continue
            else:
                idx_raw = read_accessor(gltf, prim.indices).astype(np.int64).reshape(-1)
                if idx_raw.size % 3 != 0:
                    if args.verbose:
                        print(
                            f"skip node{ni} mesh{node.mesh} prim{pi}: "
                            f"indices length not divisible by 3 ({idx_raw.size})"
                        )
                    continue
                idx = idx_raw.reshape(-1, 3)

            col_idx = getattr(attrs, "COLOR_0", None)

            local_groups: dict[tuple, list[np.ndarray]] = {}

            if col_idx is None:
                if args.no_color_mode == "skip":
                    if args.verbose:
                        print(f"skip node{ni} mesh{node.mesh} prim{pi}: missing COLOR_0")
                    continue
                local_groups[("no_color",)] = [f for f in idx]
            else:
                col_raw = read_accessor(gltf, col_idx).astype(np.float32)
                col = np.clip(col_raw[:, :3], 0.0, 1.0)

                for f in idx:
                    key = face_color_key(col[f], args.color_levels)
                    local_groups.setdefault(key, []).append(f)

            if args.verbose:
                print(
                    f"node{ni} mesh{node.mesh} prim{pi}: "
                    f"{len(local_groups)} local groups (color_levels={args.color_levels})"
                )

            for key, faces_list in local_groups.items():
                faces = np.array(faces_list, dtype=np.int64)
                v2, f2 = compact(pos_world, faces)
                global_groups.setdefault(key, []).append((v2, f2))
                if col_idx is not None:
                    weighted_rgb_sum, weight_sum, rgb_sum, face_count = summarize_group_color(
                        pos_world,
                        col,
                        faces,
                    )
                    stats = global_group_stats.setdefault(
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

    perf("glb face grouping", t_group, extra=f"groups={len(global_groups)}")

    if not global_groups:
        raise SystemExit("No parts produced; nothing to write.")

    if args.verbose:
        print("\nFinal global groups:")
        for key in sorted(global_groups, key=str):
            print(f"  {key}: {len(global_groups[key])} chunks")

    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    manifest_out = args.manifest_out or default_manifest_path(args.out)
    manifest = {
        "input": os.path.abspath(args.input),
        "output_3mf": os.path.abspath(args.out),
        "color_levels": int(args.color_levels),
        "scale": float(args.scale),
        "objects": [],
    }

    t_write = time.perf_counter()
    for key in sorted(global_groups, key=str):
        verts, faces = merge_chunks(global_groups[key])
        if verts.shape[0] == 0 or faces.shape[0] == 0:
            continue

        safe_key = "_".join(map(str, key))
        name = f"color_{safe_key}"
        avg_rgb = resolve_average_rgb(global_group_stats.get(key))
        avg_hex = rgb01_to_hex(avg_rgb) if avg_rgb is not None else None

        obj = model.AddMeshObject()
        obj.SetName(name)
        add_layerloom_metadata(
            obj,
            group_key=key,
            color_levels=args.color_levels,
            avg_rgb=avg_rgb,
            avg_hex=avg_hex,
        )

        for v in verts:
            p = lib3mf.Position()
            p.Coordinates[0] = float(v[0])
            p.Coordinates[1] = float(v[1])
            p.Coordinates[2] = float(v[2])
            obj.AddVertex(p)

        for f in faces:
            t = lib3mf.Triangle()
            t.Indices[0] = int(f[0])
            t.Indices[1] = int(f[1])
            t.Indices[2] = int(f[2])
            obj.AddTriangle(t)

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
                "source_kind": "vertex_color" if avg_hex is not None else "no_color",
            }
        )

    writer = model.QueryWriter("3mf")
    writer.WriteToFile(args.out)
    perf("glb write 3mf", t_write, extra=os.path.basename(args.out))
    with open(manifest_out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    perf("glb total", t_total, extra=f"objects={len(manifest['objects'])}")
    print(f"Wrote: {args.out}")
    print(f"Wrote manifest: {manifest_out}")


if __name__ == "__main__":
    main()
