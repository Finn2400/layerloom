import numpy as np
import pytest


def test_read_parts_applies_normalized_build_item_transform(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    from layerloom.export import write_basic_3mf
    from layerloom.ingest import read_parts
    from layerloom.normalize_3mf_import import normalize_3mf_import
    from layerloom.transform_3mf import compute_transform_plan, rotation_matrix_xyz, write_transformed_3mf

    source = tmp_path / "cube_source.3mf"
    transformed = tmp_path / "cube_transformed.3mf"

    mesh = trimesh.creation.box(extents=(10.0, 6.0, 4.0))
    mesh.apply_translation((5.0, 3.0, 2.0))
    write_basic_3mf([("cube__PAT_cy__", mesh)], str(source), title="transform ingest test")

    normalized = normalize_3mf_import(str(source)).normalized_path
    rotation = rotation_matrix_xyz(0.0, 0.0, 90.0)
    plan = compute_transform_plan(
        normalized,
        scale=1.0,
        orientation_matrix=rotation,
        plate_width=256.0,
        plate_depth=256.0,
    )
    write_transformed_3mf(normalized, str(transformed), plan.global_matrix)

    parts = read_parts([str(transformed)])

    assert len(parts) == 1
    label, part_mesh = parts[0]
    assert "__PAT_cy__" in label
    assert np.allclose(part_mesh.bounds[0], plan.transformed_bounds.min_corner, atol=1e-5)
    assert np.allclose(part_mesh.bounds[1], plan.transformed_bounds.max_corner, atol=1e-5)
