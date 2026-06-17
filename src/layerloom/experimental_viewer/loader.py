"""Mesh loading for the experimental viewer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np


@dataclass
class LoadedMesh:
    name: str
    vertices: np.ndarray
    faces: np.ndarray
    color: Optional[str] = None


@dataclass
class LoadedScene:
    path: Path
    meshes: List[LoadedMesh]
    points: np.ndarray

    @property
    def is_empty(self) -> bool:
        return not self.meshes or self.points.size == 0


def _safe_name(raw: object, index: int) -> str:
    text = str(raw or "").strip()
    return text if text else f"mesh_{index + 1}"


def _mesh_color_hex(mesh) -> Optional[str]:
    try:
        visual = getattr(mesh, "visual", None)
        material = getattr(visual, "material", None)
        diffuse = getattr(material, "diffuse", None)
        if diffuse is None:
            return None
        rgba = np.asarray(diffuse, dtype=np.float64).reshape(-1)
        if rgba.size < 3:
            return None
        rgb_values = rgba[:3]
        if np.nanmax(rgb_values) <= 1.0:
            rgb_values = rgb_values * 255.0
        rgb = np.clip(rgb_values, 0, 255).astype(int)
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    except Exception:
        return None


def _coerce_trimesh_geometry(geometry):
    try:
        import trimesh
    except Exception as exc:  # pragma: no cover - dependency failure path
        raise RuntimeError("trimesh is required to load models.") from exc

    if isinstance(geometry, trimesh.Trimesh):
        return [geometry]
    if isinstance(geometry, trimesh.Scene):
        out = []
        for node_name in geometry.graph.nodes_geometry:
            transform, geom_name = geometry.graph[node_name]
            mesh = geometry.geometry[geom_name].copy()
            mesh.apply_transform(transform)
            out.append(mesh)
        return out
    return []


def load_model(path: str | Path) -> LoadedScene:
    """Load a model file as individual display meshes and one point cloud."""

    try:
        import trimesh
    except Exception as exc:  # pragma: no cover - dependency failure path
        raise RuntimeError("trimesh is required to load models.") from exc

    model_path = Path(path).expanduser().resolve()
    if not model_path.exists():
        raise FileNotFoundError(str(model_path))

    try:
        loaded = trimesh.load(str(model_path), force="scene", process=False)
    except Exception as exc:
        raise RuntimeError(f"Could not load model with trimesh: {exc}") from exc

    raw_meshes = _coerce_trimesh_geometry(loaded)
    meshes: List[LoadedMesh] = []
    point_chunks = []
    for idx, mesh in enumerate(raw_meshes):
        if mesh is None or getattr(mesh, "vertices", None) is None or getattr(mesh, "faces", None) is None:
            continue
        vertices = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int64)
        if vertices.size == 0 or faces.size == 0:
            continue
        metadata = getattr(mesh, "metadata", {}) or {}
        meshes.append(
            LoadedMesh(
                name=_safe_name(metadata.get("name") or getattr(mesh, "name", None), idx),
                vertices=vertices,
                faces=faces,
                color=_mesh_color_hex(mesh),
            )
        )
        point_chunks.append(vertices)

    if not meshes:
        raise RuntimeError("No mesh geometry was found in the model.")

    return LoadedScene(
        path=model_path,
        meshes=meshes,
        points=np.vstack(point_chunks),
    )
