"""Geometry-based paint selections without modifying source triangles.

A surface grows through shared edges only while both the adjacent-face angle
and the angle from the picked face remain within limits. The second bound
prevents a succession of tiny steps from wrapping around a rounded object.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree
import trimesh


@dataclass
class SelectionTopology:
    adjacency: csr_matrix
    centers: np.ndarray
    normals: np.ndarray
    tree: cKDTree


def build_topology(vertices, faces) -> SelectionTopology:
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError("Selection requires triangular faces")
    if not len(faces):
        raise ValueError("Selection requires at least one face")
    # STL may repeat a shared vertex for each triangle. Only the temporary
    # connectivity mesh is welded; canonical face order and coordinates stay.
    mesh = trimesh.Trimesh(vertices.copy(), faces.copy(), process=False)
    mesh.merge_vertices()
    pairs = mesh.face_adjacency
    rows = np.concatenate((pairs[:, 0], pairs[:, 1]))
    cols = np.concatenate((pairs[:, 1], pairs[:, 0]))
    adjacency = csr_matrix((np.ones(len(rows), dtype=np.uint8), (rows, cols)),
                           shape=(len(faces), len(faces)))
    triangles = vertices[faces]
    centers = triangles.mean(axis=1)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    np.divide(normals, lengths[:, None], out=normals, where=lengths[:, None] > 0)
    return SelectionTopology(adjacency, centers, normals, cKDTree(centers))


def _seed(topology, seed):
    seed = int(seed)
    if not 0 <= seed < len(topology.normals):
        raise ValueError("Picked face is outside this mesh")
    return seed


def connected_faces(topology: SelectionTopology, seed: int, allowed=None,
                    *, edge_cosine: float | None = None) -> np.ndarray:
    seed = _seed(topology, seed)
    n = len(topology.normals)
    allowed = np.ones(n, dtype=bool) if allowed is None else np.asarray(allowed, dtype=bool).copy()
    if allowed.shape != (n,):
        raise ValueError("Selection mask length does not match faces")
    allowed[seed] = True
    seen = np.zeros(n, dtype=bool)
    seen[seed] = True
    queue = deque([seed])
    adjacency = topology.adjacency
    while queue:
        current = queue.popleft()
        neighbors = adjacency.indices[adjacency.indptr[current]:adjacency.indptr[current + 1]]
        candidates = neighbors[allowed[neighbors] & ~seen[neighbors]]
        if edge_cosine is not None and len(candidates):
            dots = topology.normals[candidates] @ topology.normals[current]
            candidates = candidates[dots >= edge_cosine - 1e-10]
        seen[candidates] = True
        queue.extend(int(face) for face in candidates)
    return np.flatnonzero(seen)


def select_surface(topology: SelectionTopology, seed: int, *, edge_angle: float = 35.0,
                   seed_angle: float = 60.0) -> np.ndarray:
    """Select a connected patch bounded by crease and cumulative curvature.

    ``seed_angle`` must remain below 90 degrees, so the patch cannot cross to
    the side whose normals face away from the originally picked surface.
    """
    seed = _seed(topology, seed)
    if not np.isfinite(edge_angle) or not 0 <= edge_angle <= 180:
        raise ValueError("Edge angle must be between 0 and 180 degrees")
    if not np.isfinite(seed_angle) or not 0 <= seed_angle < 90:
        raise ValueError("Surface span must be at least zero and below 90 degrees")
    dots = topology.normals @ topology.normals[seed]
    allowed = dots >= np.cos(np.deg2rad(seed_angle)) - 1e-10
    return connected_faces(topology, seed, allowed, edge_cosine=float(np.cos(np.deg2rad(edge_angle))))


def select_area(topology: SelectionTopology, seed: int, point, radius: float) -> np.ndarray:
    seed = _seed(topology, seed)
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("Selection radius must be positive")
    nearby = np.asarray(topology.tree.query_ball_point(np.asarray(point), radius), dtype=np.int64)
    allowed = np.zeros(len(topology.normals), dtype=bool)
    allowed[nearby] = (topology.normals[nearby] @ topology.normals[seed]) > 0.05
    return connected_faces(topology, seed, allowed)


def select_color_region(topology: SelectionTopology, seed: int, rgba) -> np.ndarray:
    seed = _seed(topology, seed)
    rgba = np.asarray(rgba)
    if len(rgba) != len(topology.normals):
        raise ValueError("Color array length does not match faces")
    return connected_faces(topology, seed, np.all(rgba == rgba[seed], axis=1))
