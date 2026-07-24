"""Automatic print orientation scoring for headless LayerLoom."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np
import trimesh

from layerloom.ingest import Part, read_parts
from layerloom.transform_3mf import axis_angle_rotation, orthonormalize_rotation


@dataclass(frozen=True)
class OrientationResult:
    matrix: np.ndarray
    score: float
    evaluated: int


def analysis_mesh_from_parts(parts: Sequence[Part], *, max_faces: int = 200_000) -> trimesh.Trimesh:
    meshes = [mesh for _label, mesh in parts if isinstance(mesh, trimesh.Trimesh) and mesh.faces.size > 0]
    if not meshes:
        raise RuntimeError("No mesh geometry available for orientation analysis.")
    mesh = meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
    mesh = trimesh.Trimesh(vertices=np.asarray(mesh.vertices), faces=np.asarray(mesh.faces), process=False)
    if len(mesh.faces) > max_faces:
        idx = np.linspace(0, len(mesh.faces) - 1, max_faces, dtype=np.int64)
        mesh = trimesh.Trimesh(vertices=np.asarray(mesh.vertices), faces=np.asarray(mesh.faces)[idx], process=False)
        mesh.remove_unreferenced_vertices()
    return mesh


def _rotation_from_up_vector(up: np.ndarray) -> np.ndarray:
    up = np.asarray(up, dtype=np.float64).reshape(3)
    nrm = np.linalg.norm(up)
    if nrm <= 1e-12:
        return np.eye(3, dtype=np.float64)
    up = up / nrm
    target = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    dot = float(np.clip(np.dot(up, target), -1.0, 1.0))
    cross = np.cross(up, target)
    cross_n = np.linalg.norm(cross)
    if cross_n <= 1e-12:
        if dot >= 0.0:
            return np.eye(3, dtype=np.float64)
        return axis_angle_rotation(np.array([1.0, 0.0, 0.0]), 180.0)
    angle = math.degrees(math.atan2(cross_n, dot))
    return axis_angle_rotation(cross / cross_n, angle)


def _z_roll(deg: float) -> np.ndarray:
    rad = math.radians(deg)
    c = math.cos(rad)
    s = math.sin(rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def _fibonacci_sphere(count: int) -> Iterable[np.ndarray]:
    if count <= 1:
        yield np.array([0.0, 0.0, 1.0], dtype=np.float64)
        return
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(count):
        y = 1.0 - (i / float(count - 1)) * 2.0
        radius = math.sqrt(max(0.0, 1.0 - y * y))
        theta = golden * i
        yield np.array([math.cos(theta) * radius, math.sin(theta) * radius, y], dtype=np.float64)


def candidate_rotations(quality: str = "balanced") -> list[np.ndarray]:
    quality = (quality or "balanced").lower()
    if quality == "none":
        return [np.eye(3, dtype=np.float64)]
    settings = {
        "fast": (18, 6),
        "balanced": (42, 8),
        "thorough": (72, 12),
    }
    sphere_count, roll_count = settings.get(quality, settings["balanced"])
    rotations = []
    for up in _fibonacci_sphere(sphere_count):
        base = _rotation_from_up_vector(up)
        for roll in np.linspace(0.0, 360.0, roll_count, endpoint=False):
            rotations.append(orthonormalize_rotation(_z_roll(float(roll)) @ base))
    rotations.append(np.eye(3, dtype=np.float64))
    return rotations


def _sample_arrays(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    tris = vertices[faces]
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    double_area = np.linalg.norm(cross, axis=1)
    valid = double_area > 1e-12
    normals = np.zeros_like(cross)
    normals[valid] = cross[valid] / double_area[valid, None]
    areas = 0.5 * double_area
    centroids = np.mean(tris, axis=1)
    return vertices, centroids, normals, areas


def score_orientation(
    mesh: trimesh.Trimesh,
    rotation: np.ndarray,
    *,
    overhang_degrees: float = 45.0,
    bed_epsilon: float = 0.05,
) -> float:
    """Lower is better; estimates support need from downward overhang area and height."""
    rotation = orthonormalize_rotation(rotation)
    vertices, centroids, normals, areas = _sample_arrays(mesh)
    if len(areas) == 0:
        return float("inf")
    z_vertices = vertices @ rotation.T
    min_z = float(np.min(z_vertices[:, 2]))
    z_centroids = (centroids @ rotation.T)[:, 2] - min_z
    nz = (normals @ rotation.T)[:, 2]
    threshold = math.cos(math.radians(overhang_degrees))
    downward = np.maximum(0.0, -nz - threshold)
    unsupported = z_centroids > bed_epsilon
    support_score = np.sum(areas * downward * np.maximum(z_centroids, 0.0) * unsupported)
    height = float(np.max(z_vertices[:, 2]) - min_z)
    footprint = np.ptp(z_vertices[:, 0]) * np.ptp(z_vertices[:, 1])
    return float(support_score + 1e-6 * footprint + 1e-7 * height)


def _refine_rotations(base: np.ndarray, quality: str) -> list[np.ndarray]:
    if quality == "fast":
        angles = [10.0, -10.0]
    elif quality == "thorough":
        angles = [15.0, -15.0, 7.5, -7.5, 3.0, -3.0]
    else:
        angles = [12.0, -12.0, 6.0, -6.0]
    axes = np.eye(3, dtype=np.float64)
    out = []
    for angle in angles:
        for axis in axes:
            out.append(orthonormalize_rotation(axis_angle_rotation(axis, angle) @ base))
    return out


def choose_orientation_for_mesh(mesh: trimesh.Trimesh, *, quality: str = "balanced") -> OrientationResult:
    quality = (quality or "balanced").lower()
    if quality == "none":
        matrix = np.eye(3, dtype=np.float64)
        return OrientationResult(matrix, score_orientation(mesh, matrix), 1)

    scored: list[tuple[float, np.ndarray]] = []
    for rot in candidate_rotations(quality):
        scored.append((score_orientation(mesh, rot), rot))
    scored.sort(key=lambda item: item[0])

    top_n = {"fast": 3, "balanced": 6, "thorough": 10}.get(quality, 6)
    evaluated = len(scored)
    best_score, best_rot = scored[0]
    for _score, rot in scored[:top_n]:
        for refined in _refine_rotations(rot, quality):
            evaluated += 1
            refined_score = score_orientation(mesh, refined)
            if refined_score < best_score:
                best_score, best_rot = refined_score, refined
    return OrientationResult(best_rot, float(best_score), evaluated)


def choose_orientation_for_3mf(path: str, *, quality: str = "balanced", max_faces: int = 200_000) -> OrientationResult:
    parts = read_parts([path])
    mesh = analysis_mesh_from_parts(parts, max_faces=max_faces)
    return choose_orientation_for_mesh(mesh, quality=quality)
