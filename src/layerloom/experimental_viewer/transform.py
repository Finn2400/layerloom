"""Pure transform helpers for the experimental build-plate viewer."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Optional

import numpy as np

from layerloom.transform_3mf import (
    align_vectors_rotation,
    axis_angle_rotation,
    orthonormalize_rotation,
    rotation_matrix_to_euler_xyz,
    rotation_matrix_xyz,
)


def _array3(values: Iterable[float] | np.ndarray) -> np.ndarray:
    out = np.asarray(values, dtype=np.float64).reshape(3)
    if not np.all(np.isfinite(out)):
        raise ValueError("Vector values must be finite.")
    return out


def _rotation(values: np.ndarray) -> np.ndarray:
    return orthonormalize_rotation(np.asarray(values, dtype=np.float64))


def translation_matrix(offset: Iterable[float] | np.ndarray) -> np.ndarray:
    out = np.eye(4, dtype=np.float64)
    out[:3, 3] = _array3(offset)
    return out


def uniform_scale_matrix(scale: float) -> np.ndarray:
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Scale must be a finite number greater than zero.")
    out = np.eye(4, dtype=np.float64)
    out[0, 0] = float(scale)
    out[1, 1] = float(scale)
    out[2, 2] = float(scale)
    return out


def transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("Points must be an Nx3 array.")
    mat = np.asarray(matrix, dtype=np.float64)
    ones = np.ones((pts.shape[0], 1), dtype=np.float64)
    out = (mat @ np.hstack([pts, ones]).T).T
    return out[:, :3]


@dataclass(frozen=True)
class BuildPlateSpec:
    width_mm: float = 256.0
    depth_mm: float = 256.0
    margin_mm: float = 8.0

    def __post_init__(self) -> None:
        if self.width_mm <= 0 or self.depth_mm <= 0:
            raise ValueError("Build plate dimensions must be greater than zero.")
        if self.margin_mm < 0:
            raise ValueError("Build plate margin must be non-negative.")
        if self.margin_mm * 2 >= min(self.width_mm, self.depth_mm):
            raise ValueError("Build plate margin leaves no usable area.")

    @property
    def center_xy(self) -> np.ndarray:
        return np.array([self.width_mm * 0.5, self.depth_mm * 0.5], dtype=np.float64)

    @property
    def min_xy(self) -> np.ndarray:
        return np.array([self.margin_mm, self.margin_mm], dtype=np.float64)

    @property
    def max_xy(self) -> np.ndarray:
        return np.array(
            [self.width_mm - self.margin_mm, self.depth_mm - self.margin_mm],
            dtype=np.float64,
        )

    @property
    def usable_size_xy(self) -> np.ndarray:
        return self.max_xy - self.min_xy


@dataclass(frozen=True)
class Bounds3D:
    min_corner: np.ndarray
    max_corner: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_corner", _array3(self.min_corner))
        object.__setattr__(self, "max_corner", _array3(self.max_corner))
        if np.any(self.max_corner < self.min_corner):
            raise ValueError("Bounds max_corner must be greater than or equal to min_corner.")

    @property
    def center(self) -> np.ndarray:
        return 0.5 * (self.min_corner + self.max_corner)

    @property
    def size(self) -> np.ndarray:
        return self.max_corner - self.min_corner

    @property
    def corners(self) -> np.ndarray:
        mn = self.min_corner
        mx = self.max_corner
        return np.array(
            [
                [mn[0], mn[1], mn[2]],
                [mx[0], mn[1], mn[2]],
                [mn[0], mx[1], mn[2]],
                [mx[0], mx[1], mn[2]],
                [mn[0], mn[1], mx[2]],
                [mx[0], mn[1], mx[2]],
                [mn[0], mx[1], mx[2]],
                [mx[0], mx[1], mx[2]],
            ],
            dtype=np.float64,
        )

    @classmethod
    def from_points(cls, points: np.ndarray) -> "Bounds3D":
        pts = np.asarray(points, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] == 0:
            raise ValueError("Bounds require a non-empty Nx3 point array.")
        return cls(pts.min(axis=0), pts.max(axis=0))


@dataclass(frozen=True)
class ViewerTransform:
    rotation: np.ndarray = field(default_factory=lambda: np.eye(3, dtype=np.float64))
    scale: float = 1.0
    translation_xyz: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    pivot: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))

    def __post_init__(self) -> None:
        object.__setattr__(self, "rotation", _rotation(self.rotation))
        if not np.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("Scale must be a finite number greater than zero.")
        object.__setattr__(self, "scale", float(self.scale))
        object.__setattr__(self, "translation_xyz", _array3(self.translation_xyz))
        object.__setattr__(self, "pivot", _array3(self.pivot))

    @property
    def matrix(self) -> np.ndarray:
        return (
            translation_matrix(self.translation_xyz)
            @ translation_matrix(self.pivot)
            @ self.rotation4
            @ uniform_scale_matrix(self.scale)
            @ translation_matrix(-self.pivot)
        )

    @property
    def rotation4(self) -> np.ndarray:
        out = np.eye(4, dtype=np.float64)
        out[:3, :3] = self.rotation
        return out

    def with_changes(self, **changes) -> "ViewerTransform":
        return replace(self, **changes)

    def euler_xyz_degrees(self) -> tuple[float, float, float]:
        return rotation_matrix_to_euler_xyz(self.rotation)


class TransformController:
    """Single source of truth for whole-model build-plate transforms."""

    def __init__(
        self,
        base_points: np.ndarray,
        *,
        plate: Optional[BuildPlateSpec] = None,
        transform: Optional[ViewerTransform] = None,
    ) -> None:
        pts = np.asarray(base_points, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] == 0:
            raise ValueError("TransformController requires a non-empty Nx3 point array.")
        self.base_points = pts
        self.base_bounds = Bounds3D.from_points(pts)
        self.plate = plate or BuildPlateSpec()
        self.transform = transform or ViewerTransform(pivot=self.base_bounds.center)

    @classmethod
    def from_bounds(
        cls,
        bounds: Bounds3D,
        *,
        plate: Optional[BuildPlateSpec] = None,
    ) -> "TransformController":
        return cls(bounds.corners, plate=plate)

    @property
    def matrix(self) -> np.ndarray:
        return self.transform.matrix

    @property
    def rotation(self) -> np.ndarray:
        return self.transform.rotation

    @property
    def scale(self) -> float:
        return self.transform.scale

    @property
    def translation_xyz(self) -> np.ndarray:
        return self.transform.translation_xyz.copy()

    def reset(self) -> None:
        self.transform = ViewerTransform(pivot=self.base_bounds.center)

    def transformed_points(self) -> np.ndarray:
        return transform_points(self.base_points, self.matrix)

    def transformed_bounds(self) -> Bounds3D:
        return Bounds3D.from_points(self.transformed_points())

    def footprint_fits_plate(self, bounds: Optional[Bounds3D] = None) -> bool:
        b = bounds or self.transformed_bounds()
        size = b.size[:2]
        usable = self.plate.usable_size_xy
        return bool(np.all(size <= usable + 1e-9))

    def footprint_inside_plate(self, bounds: Optional[Bounds3D] = None) -> bool:
        b = bounds or self.transformed_bounds()
        return bool(
            b.min_corner[0] >= self.plate.min_xy[0] - 1e-9
            and b.min_corner[1] >= self.plate.min_xy[1] - 1e-9
            and b.max_corner[0] <= self.plate.max_xy[0] + 1e-9
            and b.max_corner[1] <= self.plate.max_xy[1] + 1e-9
        )

    def _set_translation(self, translation: np.ndarray) -> None:
        self.transform = self.transform.with_changes(translation_xyz=translation)

    def center_xy(self) -> None:
        bounds = self.transformed_bounds()
        delta_xy = self.plate.center_xy - bounds.center[:2]
        translation = self.transform.translation_xyz.copy()
        translation[:2] += delta_xy
        self._set_translation(translation)

    def drop_to_plate(self) -> None:
        bounds = self.transformed_bounds()
        translation = self.transform.translation_xyz.copy()
        translation[2] -= bounds.min_corner[2]
        self._set_translation(translation)

    def clamp_xy_to_plate(self) -> None:
        bounds = self.transformed_bounds()
        if not self.footprint_fits_plate(bounds):
            self.center_xy()
            return

        translation = self.transform.translation_xyz.copy()
        if bounds.min_corner[0] < self.plate.min_xy[0]:
            translation[0] += self.plate.min_xy[0] - bounds.min_corner[0]
        if bounds.max_corner[0] > self.plate.max_xy[0]:
            translation[0] -= bounds.max_corner[0] - self.plate.max_xy[0]
        if bounds.min_corner[1] < self.plate.min_xy[1]:
            translation[1] += self.plate.min_xy[1] - bounds.min_corner[1]
        if bounds.max_corner[1] > self.plate.max_xy[1]:
            translation[1] -= bounds.max_corner[1] - self.plate.max_xy[1]
        self._set_translation(translation)

    def fit_to_plate(self, *, enlarge: bool = False) -> float:
        bounds = self.transformed_bounds()
        size = np.maximum(bounds.size[:2], 1e-12)
        usable = self.plate.usable_size_xy
        factor = float(min(usable[0] / size[0], usable[1] / size[1]))
        if not enlarge:
            factor = min(1.0, factor)
        factor = max(factor, 1e-9)
        if abs(factor - 1.0) > 1e-12:
            self.transform = self.transform.with_changes(scale=self.transform.scale * factor)
        self.center_xy()
        self.drop_to_plate()
        self.clamp_xy_to_plate()
        return factor

    def auto_place(self) -> float:
        factor = self.fit_to_plate(enlarge=False)
        self.center_xy()
        self.drop_to_plate()
        self.clamp_xy_to_plate()
        return factor

    def rotate_axis(self, axis: Iterable[float] | str, angle_deg: float) -> None:
        axis_vec = self._axis_vector(axis)
        delta = axis_angle_rotation(axis_vec, float(angle_deg))
        self.transform = self.transform.with_changes(rotation=delta @ self.transform.rotation)
        self.drop_to_plate()
        self.clamp_xy_to_plate()

    def set_rotation_matrix(self, rotation: np.ndarray) -> None:
        self.transform = self.transform.with_changes(rotation=rotation)
        self.drop_to_plate()
        self.clamp_xy_to_plate()

    def set_euler(self, rot_x_deg: float, rot_y_deg: float, rot_z_deg: float) -> None:
        self.set_rotation_matrix(rotation_matrix_xyz(rot_x_deg, rot_y_deg, rot_z_deg))

    def set_scale(self, scale: float) -> None:
        self.transform = self.transform.with_changes(scale=float(scale))
        self.drop_to_plate()
        self.clamp_xy_to_plate()

    def set_xy_position(self, x_mm: float, y_mm: float, *, clamp: bool = True) -> None:
        bounds = self.transformed_bounds()
        delta_xy = np.array([float(x_mm), float(y_mm)], dtype=np.float64) - bounds.center[:2]
        self.translate_xy(delta_xy[0], delta_xy[1], clamp=clamp)

    def translate_xy(self, dx_mm: float, dy_mm: float, *, clamp: bool = True) -> None:
        translation = self.transform.translation_xyz.copy()
        translation[0] += float(dx_mm)
        translation[1] += float(dy_mm)
        self._set_translation(translation)
        if clamp:
            self.clamp_xy_to_plate()

    def orient_normal_to_plate(
        self,
        normal: Iterable[float] | np.ndarray,
        *,
        normal_space: str = "model",
    ) -> None:
        normal_vec = _array3(normal)
        if normal_space == "model":
            source = self.transform.rotation @ normal_vec
        elif normal_space == "world":
            source = normal_vec
        else:
            raise ValueError("normal_space must be 'model' or 'world'.")
        delta = align_vectors_rotation(source, np.array([0.0, 0.0, -1.0], dtype=np.float64))
        self.transform = self.transform.with_changes(rotation=delta @ self.transform.rotation)
        self.drop_to_plate()
        self.clamp_xy_to_plate()

    def euler_xyz_degrees(self) -> tuple[float, float, float]:
        return self.transform.euler_xyz_degrees()

    @staticmethod
    def _axis_vector(axis: Iterable[float] | str) -> np.ndarray:
        if isinstance(axis, str):
            value = axis.lower()
            if value == "x":
                return np.array([1.0, 0.0, 0.0], dtype=np.float64)
            if value == "y":
                return np.array([0.0, 1.0, 0.0], dtype=np.float64)
            if value == "z":
                return np.array([0.0, 0.0, 1.0], dtype=np.float64)
            raise ValueError("Axis string must be 'x', 'y', or 'z'.")
        return _array3(axis)
