import numpy as np

from layerloom.experimental_viewer.transform import (
    Bounds3D,
    BuildPlateSpec,
    TransformController,
)


def _box_points(size=(10.0, 6.0, 4.0), center=(0.0, 0.0, 0.0)):
    sx, sy, sz = np.asarray(size, dtype=np.float64) * 0.5
    cx, cy, cz = np.asarray(center, dtype=np.float64)
    return Bounds3D(
        np.array([cx - sx, cy - sy, cz - sz], dtype=np.float64),
        np.array([cx + sx, cy + sy, cz + sz], dtype=np.float64),
    ).corners


def test_repeated_axis_rotations_remain_orthonormal():
    controller = TransformController(_box_points())

    for _ in range(60):
        controller.rotate_axis("x", 7.0)
        controller.rotate_axis("y", -11.0)
        controller.rotate_axis("z", 5.0)

    rot = controller.rotation
    assert np.allclose(rot.T @ rot, np.eye(3), atol=1e-9)
    assert np.isclose(np.linalg.det(rot), 1.0, atol=1e-9)


def test_drop_to_plate_sets_min_z_to_zero():
    controller = TransformController(_box_points(center=(0.0, 0.0, 20.0)))
    controller.rotate_axis("x", 35.0)
    controller.drop_to_plate()

    assert np.isclose(controller.transformed_bounds().min_corner[2], 0.0, atol=1e-9)


def test_center_and_fit_keep_model_inside_plate_margin():
    plate = BuildPlateSpec(width_mm=100.0, depth_mm=80.0, margin_mm=5.0)
    controller = TransformController(_box_points(size=(180.0, 40.0, 10.0)), plate=plate)

    factor = controller.fit_to_plate(enlarge=False)
    bounds = controller.transformed_bounds()

    assert factor < 1.0
    assert controller.footprint_inside_plate(bounds)
    assert bounds.min_corner[0] >= plate.margin_mm - 1e-9
    assert bounds.max_corner[0] <= plate.width_mm - plate.margin_mm + 1e-9


def test_fit_to_plate_does_not_enlarge_small_models():
    controller = TransformController(_box_points(size=(10.0, 10.0, 10.0)))

    factor = controller.fit_to_plate(enlarge=False)

    assert np.isclose(factor, 1.0)
    assert np.isclose(controller.scale, 1.0)


def test_orient_model_normal_to_plate_down_direction():
    controller = TransformController(_box_points())

    controller.orient_normal_to_plate([0.0, 0.0, 1.0], normal_space="model")
    world_normal = controller.rotation @ np.array([0.0, 0.0, 1.0], dtype=np.float64)

    assert np.allclose(world_normal, np.array([0.0, 0.0, -1.0]), atol=1e-9)
    assert np.isclose(controller.transformed_bounds().min_corner[2], 0.0, atol=1e-9)
