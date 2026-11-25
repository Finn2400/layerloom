#!/usr/bin/env python3
"""Prepare island-safe split p53 surface parts for a Snapmaker U1."""

from __future__ import annotations

import argparse
import json
import math
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import lib3mf
import manifold3d as manifold
import numpy as np
import trimesh
from scipy.spatial import ConvexHull
from scipy.spatial import cKDTree


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
ET.register_namespace("", CORE_NS)
M = lambda name: f"{{{CORE_NS}}}{name}"

CHAIN_BY_SOURCE_COLOR = {
    "#0080a8": ("A", "#00bcd4", "cyan"),
    "#d00320": ("B", "#e91e63", "magenta"),
    "#ff9705": ("C", "#ffca28", "yellow"),
    "#0e5a10": ("D", "#43a047", "green"),
}
INTERCHAIN_INTERFACES = (("A", "B"), ("B", "C"), ("C", "D"), ("D", "A"))


@dataclass
class Surface:
    chain: str
    color: str
    color_name: str
    vertices: np.ndarray
    faces: np.ndarray
    removed_cavities: list[dict[str, float | int]]


@dataclass
class SplitCandidate:
    normal: np.ndarray
    offset: float
    quantile: float
    scale: float
    balance: float
    halves: list[tuple[np.ndarray, np.ndarray, np.ndarray, float]]


def _as_manifold(vertices: np.ndarray, faces: np.ndarray) -> manifold.Manifold:
    return manifold.Manifold(
        manifold.Mesh(
            np.ascontiguousarray(vertices, dtype=np.float32),
            np.ascontiguousarray(faces, dtype=np.uint32),
        )
    )


def _source_color_from_name(name: str) -> str:
    prefix = "color_material_"
    if not name.startswith(prefix):
        raise RuntimeError(f"Cannot infer chain color from 3MF object name: {name}")
    return "#" + name[len(prefix) :].lower()


def _read_mesh_objects(path: Path) -> list[tuple[str, np.ndarray, np.ndarray]]:
    wrapper = lib3mf.Wrapper()
    model = wrapper.CreateModel()
    reader = model.QueryReader("3mf")
    reader.ReadFromFile(str(path))
    iterator = model.GetMeshObjects()
    objects = []
    while iterator.MoveNext():
        obj = iterator.GetCurrentMeshObject()
        vertices = np.asarray([p.Coordinates for p in obj.GetVertices()], dtype=np.float64)
        faces = np.asarray([t.Indices for t in obj.GetTriangleIndices()], dtype=np.int64)
        objects.append((obj.GetName(), vertices, faces))
    return objects


def load_outer_surfaces(path: Path) -> list[Surface]:
    """Weld export duplicates and discard only cavity shells inside the envelope."""
    surfaces = []
    for name, vertices, faces in _read_mesh_objects(path):
        source_color = _source_color_from_name(name)
        if source_color not in CHAIN_BY_SOURCE_COLOR:
            raise RuntimeError(f"Unexpected p53 source color {source_color} on {name}")
        chain, color, color_name = CHAIN_BY_SOURCE_COLOR[source_color]
        mesh = trimesh.Trimesh(vertices, faces, process=True, validate=True)
        components = sorted(
            mesh.split(only_watertight=True),
            key=lambda item: abs(item.volume),
            reverse=True,
        )
        if not components:
            raise RuntimeError(f"Chain {chain} has no closed surface components")
        outer = components[0]
        if not outer.is_watertight:
            raise RuntimeError(f"Chain {chain} outer surface is not watertight")
        removed = []
        for component in components[1:]:
            sample = component.vertices.mean(axis=0)
            enclosed = bool(outer.contains([sample])[0])
            if not enclosed and len(component.faces) > 20:
                raise RuntimeError(
                    f"Chain {chain} contains a detached external component "
                    f"with {len(component.faces)} triangles"
                )
            removed.append(
                {
                    "triangles": int(len(component.faces)),
                    "signed_volume_mm3": float(component.volume),
                    "enclosed": enclosed,
                }
            )
        surfaces.append(
            Surface(
                chain=chain,
                color=color,
                color_name=color_name,
                vertices=np.asarray(outer.vertices, dtype=np.float64),
                faces=np.asarray(outer.faces, dtype=np.uint32),
                removed_cavities=removed,
            )
        )
    return sorted(surfaces, key=lambda item: item.chain)


def _interface_sites(
    first: Surface,
    second: Surface,
    *,
    count: int,
    separation: float,
) -> list[tuple[np.ndarray, np.ndarray]]:
    tree = cKDTree(second.vertices)
    distances, nearest = tree.query(first.vertices, k=1)
    chosen = []
    for first_index in np.argsort(distances):
        first_point = first.vertices[first_index]
        second_point = second.vertices[nearest[first_index]]
        midpoint = (first_point + second_point) / 2.0
        if all(np.linalg.norm(midpoint - previous) >= separation for previous in chosen):
            chosen.append(midpoint)
            yield first_point, second_point
            if len(chosen) == count:
                return
    raise RuntimeError(
        f"Could not find {count} separated connector sites for "
        f"chains {first.chain}-{second.chain}"
    )


def _cylinder_manifold(
    start: np.ndarray,
    end: np.ndarray,
    radius: float,
) -> manifold.Manifold:
    cylinder = trimesh.creation.cylinder(
        radius=radius,
        segment=np.vstack((start, end)),
        sections=32,
    )
    return _as_manifold(cylinder.vertices, cylinder.faces)


def add_interchain_half_struts(
    surfaces: list[Surface],
    *,
    count_per_interface: int,
    radius: float,
    penetration: float,
    site_separation: float,
) -> list[dict]:
    """Union matching, glueable connector halves into adjacent chain surfaces."""
    by_chain = {surface.chain: surface for surface in surfaces}
    solids = {
        chain: _as_manifold(surface.vertices, surface.faces)
        for chain, surface in by_chain.items()
    }
    connectors = []
    for first_chain, second_chain in INTERCHAIN_INTERFACES:
        first = by_chain[first_chain]
        second = by_chain[second_chain]
        for connector_index, (first_point, second_point) in enumerate(
            _interface_sites(
                first,
                second,
                count=count_per_interface,
                separation=site_separation,
            ),
            start=1,
        ):
            direction = second_point - first_point
            gap = float(np.linalg.norm(direction))
            if gap < 1e-6:
                direction = second.vertices.mean(axis=0) - first.vertices.mean(axis=0)
            direction /= np.linalg.norm(direction)
            midpoint = (first_point + second_point) / 2.0
            first_inner = first_point - direction * penetration
            second_inner = second_point + direction * penetration
            solids[first_chain] = solids[first_chain] + _cylinder_manifold(
                first_inner, midpoint, radius
            )
            solids[second_chain] = solids[second_chain] + _cylinder_manifold(
                midpoint, second_inner, radius
            )
            connectors.append(
                {
                    "interface": f"{first_chain}-{second_chain}",
                    "index": connector_index,
                    "midpoint_source_mm": midpoint.tolist(),
                    "surface_gap_source_mm": gap,
                    "radius_source_mm": radius,
                    "penetration_source_mm": penetration,
                    "joint": "matching planar half-struts; glue at midpoint",
                }
            )

    for surface in surfaces:
        solid = solids[surface.chain]
        if (
            solid.is_empty()
            or solid.status() != manifold.Error.NoError
            or len(solid.decompose()) != 1
        ):
            raise RuntimeError(
                f"Inter-chain struts did not form one manifold with chain {surface.chain}"
            )
        mesh = solid.to_mesh()
        surface.vertices = np.asarray(mesh.vert_properties[:, :3], dtype=np.float64)
        surface.faces = np.asarray(mesh.tri_verts, dtype=np.uint32)
    return connectors


def _fibonacci_normals(count: int) -> list[np.ndarray]:
    normals = []
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    for index in range(count):
        z = (index + 0.5) / count
        radius = math.sqrt(max(0.0, 1.0 - z * z))
        angle = golden_angle * index
        normals.append(np.array([radius * math.cos(angle), radius * math.sin(angle), z]))
    return normals


def _plane_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    normal = normal / np.linalg.norm(normal)
    helper = np.array([0.0, 0.0, 1.0])
    if abs(float(normal @ helper)) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    x_axis = np.cross(helper, normal)
    x_axis /= np.linalg.norm(x_axis)
    return x_axis, np.cross(normal, x_axis)


def _cap_component_count(
    vertices: np.ndarray,
    faces: np.ndarray,
    normal: np.ndarray,
    offset: float,
) -> int:
    distance = np.abs(vertices @ normal - offset)
    cap_faces = faces[np.all(distance[faces] < 2e-4, axis=1)]
    if len(cap_faces) == 0:
        return 0
    cap = trimesh.Trimesh(vertices, cap_faces, process=True)
    return len(cap.split(only_watertight=False))


def _cut_face_down_layout(
    vertices: np.ndarray,
    normal: np.ndarray,
    offset: float,
    side: int,
    limit: float,
) -> tuple[np.ndarray, float]:
    x_axis, y_axis = _plane_basis(normal)
    centered = vertices - normal * offset
    planar = np.column_stack((centered @ x_axis, centered @ y_axis))
    hull = planar[ConvexHull(planar).vertices]
    height = side * (vertices @ normal - offset)
    if height.min() < -2e-4:
        raise RuntimeError("Split half crosses its own cut plane")
    height = np.maximum(height, 0.0)

    best_scale = -1.0
    best_angle = 0.0
    for angle in np.linspace(0.0, math.pi / 2.0, 721, endpoint=True):
        c, s = math.cos(angle), math.sin(angle)
        xy = hull @ np.array([[c, -s], [s, c]])
        dimensions = np.array([np.ptp(xy[:, 0]), np.ptp(xy[:, 1]), np.ptp(height)])
        scale = float(np.min(limit / np.maximum(dimensions, 1e-9)))
        if scale > best_scale:
            best_scale = scale
            best_angle = angle
    c, s = math.cos(best_angle), math.sin(best_angle)
    best_xy = planar @ np.array([[c, -s], [s, c]])
    return np.column_stack((best_xy, height)), best_scale


def _candidate_normals(vertices: np.ndarray, sphere_samples: int) -> list[np.ndarray]:
    _u, _s, vh = np.linalg.svd(vertices - vertices.mean(axis=0), full_matrices=False)
    normals = [axis / np.linalg.norm(axis) for axis in vh]
    normals.extend(_fibonacci_normals(sphere_samples))
    return normals


def find_best_split(
    surface: Surface,
    *,
    limit: float,
    sphere_samples: int,
) -> SplitCandidate:
    solid = _as_manifold(surface.vertices, surface.faces)
    if solid.is_empty() or solid.status() != manifold.Error.NoError:
        raise RuntimeError(f"Chain {surface.chain} is not a valid manifold: {solid.status()}")

    candidates = []
    for normal in _candidate_normals(surface.vertices, sphere_samples):
        projection = surface.vertices @ normal
        for quantile in np.linspace(0.35, 0.65, 7):
            offset = float(np.quantile(projection, quantile))
            split = solid.split_by_plane(tuple(normal), offset)
            halves = []
            valid = True
            for half in split:
                if (
                    half.is_empty()
                    or half.status() != manifold.Error.NoError
                    or len(half.decompose()) != 1
                ):
                    valid = False
                    break
                mesh = half.to_mesh()
                vertices = np.asarray(mesh.vert_properties[:, :3], dtype=np.float64)
                faces = np.asarray(mesh.tri_verts, dtype=np.uint32)
                side = 1 if float(np.mean(vertices @ normal)) >= offset else -1
                if _cap_component_count(vertices, faces, normal, offset) != 1:
                    valid = False
                    break
                layout, max_scale = _cut_face_down_layout(
                    vertices, normal, offset, side, limit
                )
                halves.append((layout, faces, vertices, max_scale))
            if not valid:
                continue
            volumes = [abs(float(part.volume())) for part in split]
            balance = min(volumes) / max(volumes)
            candidates.append(
                SplitCandidate(
                    normal=normal,
                    offset=offset,
                    quantile=float(quantile),
                    scale=min(item[3] for item in halves),
                    balance=balance,
                    halves=halves,
                )
            )
    if not candidates:
        raise RuntimeError(f"No island-safe planar split found for chain {surface.chain}")
    return max(candidates, key=lambda item: (item.scale, item.balance))


def _model_xml(
    label: str,
    color: str,
    vertices: np.ndarray,
    faces: np.ndarray,
) -> bytes:
    root = ET.Element(
        M("model"),
        {
            "unit": "millimeter",
            "{http://www.w3.org/XML/1998/namespace}lang": "en-US",
        },
    )
    ET.SubElement(root, M("metadata"), {"name": "Application"}).text = (
        "LayerLoom p53 U1 surface preparation"
    )
    ET.SubElement(root, M("metadata"), {"name": "Title"}).text = label
    resources = ET.SubElement(root, M("resources"))
    materials = ET.SubElement(resources, M("basematerials"), {"id": "1"})
    ET.SubElement(
        materials,
        M("base"),
        {"name": label, "displaycolor": color.upper() + "FF"},
    )
    obj = ET.SubElement(
        resources,
        M("object"),
        {"id": "2", "type": "model", "name": label, "pid": "1", "pindex": "0"},
    )
    mesh = ET.SubElement(obj, M("mesh"))
    vertex_elements = ET.SubElement(mesh, M("vertices"))
    for x, y, z in vertices:
        ET.SubElement(
            vertex_elements,
            M("vertex"),
            {"x": f"{x:.6f}", "y": f"{y:.6f}", "z": f"{z:.6f}"},
        )
    triangle_elements = ET.SubElement(mesh, M("triangles"))
    for a, b, c in faces:
        ET.SubElement(
            triangle_elements,
            M("triangle"),
            {"v1": str(int(a)), "v2": str(int(b)), "v3": str(int(c))},
        )
    build = ET.SubElement(root, M("build"))
    ET.SubElement(build, M("item"), {"objectid": "2", "partnumber": label})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_3mf(
    path: Path,
    label: str,
    color: str,
    vertices: np.ndarray,
    faces: np.ndarray,
) -> None:
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>"""
    relationships = b"""<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("[Content_Types].xml", content_types)
        output.writestr("_rels/.rels", relationships)
        output.writestr("3D/3dmodel.model", _model_xml(label, color, vertices, faces))


def _validate_written_part(path: Path) -> dict[str, int | bool]:
    objects = _read_mesh_objects(path)
    if len(objects) != 1:
        raise RuntimeError(f"{path.name} contains {len(objects)} objects instead of one")
    _name, vertices, faces = objects[0]
    mesh = trimesh.Trimesh(vertices, faces, process=True, validate=True)
    components = mesh.split(only_watertight=False)
    if not mesh.is_watertight or len(components) != 1:
        raise RuntimeError(
            f"{path.name} failed solid validation: "
            f"watertight={mesh.is_watertight}, components={len(components)}"
        )
    return {
        "watertight": True,
        "connected_components": 1,
        "vertices": int(len(mesh.vertices)),
        "triangles": int(len(mesh.faces)),
    }


def prepare(
    source: Path,
    destination: Path,
    *,
    limit: float,
    sphere_samples: int,
    connector_count: int,
    connector_radius: float,
    connector_penetration: float,
    connector_separation: float,
) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    surfaces = load_outer_surfaces(source)
    connectors = add_interchain_half_struts(
        surfaces,
        count_per_interface=connector_count,
        radius=connector_radius,
        penetration=connector_penetration,
        site_separation=connector_separation,
    )
    selected = {}
    for surface in surfaces:
        print(f"Searching island-safe split for chain {surface.chain}...", flush=True)
        selected[surface.chain] = find_best_split(
            surface,
            limit=limit,
            sphere_samples=sphere_samples,
        )
        candidate = selected[surface.chain]
        print(
            f"  chain {surface.chain}: scale {candidate.scale:.6f}, "
            f"balance {candidate.balance:.3f}",
            flush=True,
        )

    shared_scale = min(candidate.scale for candidate in selected.values())
    limiting_chain = min(selected, key=lambda chain: selected[chain].scale)
    manifest = {
        "source": str(source.resolve()),
        "representation": "protein molecular surface",
        "printer": "Snapmaker U1",
        "official_build_volume_mm": [270.0, 270.0, 270.0],
        "working_limit_mm": limit,
        "shared_scale": shared_scale,
        "limiting_chain": limiting_chain,
        "hard_constraints": {
            "maximum_pieces_per_chain": 2,
            "connected_solids_per_file": 1,
            "connected_cut_faces_per_half": 1,
            "print_orientation": "planar cut face on bed",
        },
        "interchain_connectors": connectors,
        "chains": [],
        "parts": [],
    }
    for surface in surfaces:
        candidate = selected[surface.chain]
        manifest["chains"].append(
            {
                "chain": surface.chain,
                "color": surface.color,
                "removed_enclosed_cavity_shells": surface.removed_cavities,
                "split_plane_normal": candidate.normal.tolist(),
                "split_plane_offset_source_mm": candidate.offset,
                "split_quantile": candidate.quantile,
                "individual_max_scale": candidate.scale,
                "volume_balance": candidate.balance,
            }
        )
        for half_index, (layout, faces, _source_vertices, max_scale) in enumerate(
            candidate.halves, start=1
        ):
            vertices = layout * shared_scale
            vertices[:, 0] += 135.0 - (vertices[:, 0].min() + vertices[:, 0].max()) / 2.0
            vertices[:, 1] += 135.0 - (vertices[:, 1].min() + vertices[:, 1].max()) / 2.0
            vertices[:, 2] -= vertices[:, 2].min()
            label = (
                f"p53_3kmd_surface_chain_{surface.chain}_half_{half_index}_"
                f"{surface.color_name}"
            )
            filename = label + ".3mf"
            path = destination / filename
            write_3mf(path, label, surface.color, vertices, faces)
            validation = _validate_written_part(path)
            dimensions = np.ptp(vertices, axis=0)
            manifest["parts"].append(
                {
                    "file": filename,
                    "chain": surface.chain,
                    "half": half_index,
                    "color": surface.color,
                    "dimensions_mm": dimensions.tolist(),
                    "individual_max_scale": max_scale,
                    **validation,
                }
            )
            print(f"  wrote {filename}: {np.round(dimensions, 2)} mm", flush=True)

    manifest_path = destination / "p53_3kmd_surface_u1_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Four-color source 3MF from GLB import.")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--working-limit", type=float, default=262.0)
    parser.add_argument("--sphere-samples", type=int, default=24)
    parser.add_argument("--connector-count", type=int, default=2)
    parser.add_argument("--connector-radius", type=float, default=0.10)
    parser.add_argument("--connector-penetration", type=float, default=0.15)
    parser.add_argument("--connector-separation", type=float, default=0.80)
    args = parser.parse_args()
    manifest = prepare(
        args.source.expanduser(),
        args.destination.expanduser(),
        limit=args.working_limit,
        sphere_samples=args.sphere_samples,
        connector_count=args.connector_count,
        connector_radius=args.connector_radius,
        connector_penetration=args.connector_penetration,
        connector_separation=args.connector_separation,
    )
    print(
        f"Shared scale {manifest['shared_scale']:.6f}; "
        f"limiting chain {manifest['limiting_chain']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
