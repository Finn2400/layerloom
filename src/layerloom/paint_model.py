"""Shared, editable surface-paint data. Geometry is in world-space millimeters.

Paint never changes geometry: only ``face_rgba`` is edited. ``source_rgba``
retains the imported color so that erase is independent of save/reopen cycles.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class PaintMesh:
    key: str
    name: str
    vertices: np.ndarray
    faces: np.ndarray
    face_rgba: np.ndarray
    source_rgba: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.key = str(self.key)
        self.name = str(self.name)
        self.vertices = np.asarray(self.vertices, dtype=np.float64)
        # Validate before narrowing: astype silently truncates fractional face
        # indices and wraps out-of-range colors in array-backed project files.
        self.faces = np.asarray(self.faces)
        self.face_rgba = np.asarray(self.face_rgba)
        self.source_rgba = (self.face_rgba.copy() if self.source_rgba is None
                            else np.asarray(self.source_rgba))
        self.validate()
        self.faces = self.faces.astype(np.int64, copy=False)
        self.face_rgba = self.face_rgba.astype(np.uint8, copy=False)
        self.source_rgba = self.source_rgba.astype(np.uint8, copy=False)

    def validate(self) -> None:
        if not self.key:
            raise ValueError("Paint mesh key cannot be empty")
        if self.vertices.ndim != 2 or self.vertices.shape[1:] != (3,):
            raise ValueError(f"{self.name}: vertices must have shape (N, 3)")
        if self.faces.ndim != 2 or self.faces.shape[1:] != (3,):
            raise ValueError(f"{self.name}: faces must have shape (F, 3)")
        if not len(self.vertices) or not len(self.faces):
            raise ValueError(f"{self.name}: a non-empty triangle mesh is required")
        if not np.isfinite(self.vertices).all():
            raise ValueError(f"{self.name}: vertices contain non-finite values")
        if (self.faces.dtype.kind not in "iuf" or
                not np.isfinite(self.faces).all() or
                (self.faces.dtype.kind == "f" and np.any(self.faces != np.floor(self.faces)))):
            raise ValueError(f"{self.name}: face indices must be finite integers")
        if self.faces.size and (self.faces.min() < 0 or self.faces.max() >= len(self.vertices)):
            raise ValueError(f"{self.name}: face references an invalid vertex")
        if self.face_rgba.shape != (len(self.faces), 4):
            raise ValueError(f"{self.name}: face_rgba must have shape (F, 4)")
        if self.source_rgba is None or self.source_rgba.shape != self.face_rgba.shape:
            raise ValueError(f"{self.name}: source_rgba must match face_rgba")
        for label, colors in (("face_rgba", self.face_rgba), ("source_rgba", self.source_rgba)):
            if (colors.dtype.kind not in "iuf" or not np.isfinite(colors).all() or
                    np.any(colors < 0) or np.any(colors > 255) or
                    (colors.dtype.kind == "f" and np.any(colors != np.floor(colors)))):
                raise ValueError(f"{self.name}: {label} must contain integer colors from 0 to 255")

    def copy(self) -> PaintMesh:
        return PaintMesh(self.key, self.name, self.vertices.copy(), self.faces.copy(),
                         self.face_rgba.copy(), self.source_rgba.copy())


@dataclass
class PaintDocument:
    meshes: list[PaintMesh]
    source_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.meshes:
            raise ValueError("Paint document contains no meshes")
        if not isinstance(self.metadata, dict):
            raise ValueError("Paint document metadata must be an object")
        if self.source_path is not None and not isinstance(self.source_path, str):
            raise ValueError("Paint document source_path must be text or null")
        keys: set[str] = set()
        for mesh in self.meshes:
            mesh.validate()
            if mesh.key in keys:
                raise ValueError(f"Duplicate paint mesh key: {mesh.key}")
            keys.add(mesh.key)

    def copy(self, *, include_metadata: bool = True) -> PaintDocument:
        """Copy editable arrays; geometry export can omit unused project data."""
        return PaintDocument([m.copy() for m in self.meshes], self.source_path,
                             deepcopy(self.metadata) if include_metadata else {})
