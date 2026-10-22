"""One editable paint document with placement relative to immutable geometry.

The importer's world-space millimeters are the baseline. Merely opening a
document or switching tools does not center or ground it. Explicit placement
uses the same bounds, rotation and grounding math as Arrange's 3MF path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .paint_model import PaintDocument
from .transform_3mf import (
    Bounds3D,
    TransformPlan,
    _ObjectInfo,
    _compute_bounds_from_infos,
    _compute_transform_plan_from_infos,
    _transform_points,
)


def _readonly(values: np.ndarray) -> np.ndarray:
    result = np.array(values, copy=True)
    result.setflags(write=False)
    return result


def _readonly_bounds(bounds: Bounds3D) -> Bounds3D:
    return Bounds3D(_readonly(bounds.min_corner), _readonly(bounds.max_corner))


@dataclass(frozen=True)
class _WorkspacePlan(TransformPlan):
    _source_token: object = field(repr=False, compare=False)


def _readonly_plan(plan: TransformPlan, source_token: object) -> TransformPlan:
    return _WorkspacePlan(_readonly(plan.global_matrix),
                          _readonly_bounds(plan.original_bounds),
                          _readonly_bounds(plan.transformed_bounds), source_token)


class PaintWorkspaceModel:
    """Keep painting and placement on the same document and face identities.

    ``plan`` is a read-only preview and always starts from imported geometry.
    ``apply`` replaces only vertex arrays; it never replaces meshes, triangles,
    edited paint, imported paint, or metadata. Paint undo records therefore keep
    referring to the same faces after a placement change.
    """

    def __init__(self, document: PaintDocument):
        document.validate()
        self.document = document
        self._meshes = tuple(document.meshes)
        self._keys = tuple(mesh.key for mesh in self._meshes)
        self._vertices = tuple(_readonly(mesh.vertices) for mesh in self._meshes)
        self._faces = tuple(_readonly(mesh.faces) for mesh in self._meshes)
        self._object_infos = {
            key: _ObjectInfo(vertices=vertices, components=[])
            for key, vertices in zip(self._keys, self._vertices)
        }
        self._build_items = [(key, np.eye(4)) for key in self._keys]
        bounds = _compute_bounds_from_infos(self._object_infos, self._build_items)
        self._source_token = object()
        self._identity_plan = _readonly_plan(TransformPlan(np.eye(4), bounds, bounds), self._source_token)
        self._current_plan = self._identity_plan

    @property
    def identity_plan(self) -> TransformPlan:
        """The exact loaded placement, including its original height above Z=0."""
        return self._identity_plan

    @property
    def current_plan(self) -> TransformPlan:
        return self._current_plan

    @property
    def current_matrix(self) -> np.ndarray:
        return self._current_plan.global_matrix

    @property
    def original_bounds(self) -> Bounds3D:
        return self._identity_plan.original_bounds

    def plan(self, *, scale: float = 1.0,
             orientation_matrix: np.ndarray | None = None,
             target_center_xy: np.ndarray | None = None,
             center_xy: bool = False,
             plate_width: float = 256.0,
             plate_depth: float = 256.0) -> TransformPlan:
        """Plan an absolute scale/rotation and XY position, grounded at Z=0.

        ``scale`` is a factor, not a percentage. An explicit XY target takes
        precedence over plate centering, exactly as in ``compute_transform_plan``.
        Calling this method does not change the document or current placement.
        """
        scale = float(scale)
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("Scale must be finite and > 0.")
        if orientation_matrix is not None:
            orientation_matrix = np.asarray(orientation_matrix, dtype=np.float64)
            if orientation_matrix.shape not in {(3, 3), (4, 4)} or not np.isfinite(orientation_matrix).all():
                raise ValueError("Rotation matrix must be a finite 3x3 or 4x4 array.")
        if target_center_xy is not None:
            target_center_xy = np.asarray(target_center_xy, dtype=np.float64)
            if target_center_xy.shape != (2,) or not np.isfinite(target_center_xy).all():
                raise ValueError("Target XY center must contain two finite coordinates.")
        if not np.isfinite([plate_width, plate_depth]).all() or min(plate_width, plate_depth) <= 0:
            raise ValueError("Plate dimensions must be finite and positive.")
        return _readonly_plan(_compute_transform_plan_from_infos(
            self._object_infos, self._build_items, scale=scale,
            orientation_matrix=orientation_matrix, target_center_xy=target_center_xy,
            center_xy=center_xy, plate_width=plate_width, plate_depth=plate_depth,
        ), self._source_token)

    def apply(self, plan: TransformPlan) -> bool:
        """Apply a plan from the original geometry; return whether it changed.

        Validate the complete plan and geometry before replacing any vertices.
        A repeated matrix is a no-op, and restoring ``identity_plan`` copies the
        original coordinates directly, without accumulating inverse transforms.
        """
        if not isinstance(plan, _WorkspacePlan) or plan._source_token is not self._source_token:
            raise ValueError("Placement plan belongs to a different workspace.")
        matrix = np.asarray(plan.global_matrix, dtype=np.float64)
        if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                or not np.array_equal(matrix[3], [0.0, 0.0, 0.0, 1.0])):
            raise ValueError("Placement must be a finite affine 4x4 matrix.")
        if (not np.array_equal(plan.original_bounds.min_corner, self.original_bounds.min_corner)
                or not np.array_equal(plan.original_bounds.max_corner, self.original_bounds.max_corner)):
            raise ValueError("Placement plan belongs to different source geometry.")
        if len(self.document.meshes) != len(self._meshes):
            raise ValueError("Document topology changed; create a new workspace model.")
        for mesh, original, key, vertices, faces in zip(
                self.document.meshes, self._meshes, self._keys, self._vertices, self._faces):
            if (mesh is not original or mesh.key != key or mesh.vertices.shape != vertices.shape
                    or not np.array_equal(mesh.faces, faces)):
                raise ValueError("Document topology changed; create a new workspace model.")
        expected = np.asarray([plan.transformed_bounds.min_corner, plan.transformed_bounds.max_corner])
        if expected.shape != (2, 3) or not np.isfinite(expected).all():
            raise ValueError("Placement bounds must contain finite XYZ coordinates.")
        if np.array_equal(matrix, self.current_matrix):
            current_bounds = self.current_plan.transformed_bounds
            if (not np.allclose(expected[0], current_bounds.min_corner, rtol=1e-12, atol=1e-9)
                    or not np.allclose(expected[1], current_bounds.max_corner, rtol=1e-12, atol=1e-9)):
                raise ValueError("Placement bounds do not match its matrix.")
            return False
        identity = np.array_equal(matrix, self.identity_plan.global_matrix)
        updated = [vertices.copy() if identity else _transform_points(vertices, matrix)
                   for vertices in self._vertices]
        if any(not np.isfinite(vertices).all() for vertices in updated):
            raise ValueError("Placement produced non-finite coordinates.")
        nonempty = [vertices for vertices in updated if len(vertices)]
        lower = np.min([vertices.min(axis=0) for vertices in nonempty], axis=0)
        upper = np.max([vertices.max(axis=0) for vertices in nonempty], axis=0)
        if not np.allclose([lower, upper], expected, rtol=1e-12, atol=1e-9):
            raise ValueError("Placement bounds do not match its matrix.")
        committed_plan = _readonly_plan(plan, self._source_token)
        for mesh, vertices in zip(self._meshes, updated):
            mesh.vertices = vertices
        self._current_plan = committed_plan
        return True
