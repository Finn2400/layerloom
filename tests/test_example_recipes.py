"""Check the real packaged tutorials before a GUI or export can hide bad colors."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PyQt5")
from layerloom.desktop_ui import DesktopController, QtCore
from layerloom.example_assets import find_example_file
from layerloom.paint_io import load_paint_document, save_paint_document


@pytest.mark.parametrize("filename,expected", [
    ("tutorial_cmy_cubes.3mf", ["c", "y", "cy", "cm"]),
    ("tutorial_cmy_benchy_cutup.3mf", ["my", "cmy", "cmy", "cy", "cm", "cy", "my", "cm"]),
])
def test_real_tutorial_preserves_every_part_recipe_and_source(tmp_path, monkeypatch, filename, expected):
    source = find_example_file(filename)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    document = load_paint_document(source)
    names = [mesh.name for mesh in document.meshes]
    records = []
    editor = SimpleNamespace(document=document, set_document=records.append)
    monkeypatch.setattr(QtCore.QStandardPaths, "writableLocation", lambda *_: str(tmp_path))
    DesktopController._initialize_example_paint(SimpleNamespace(paint_editor=editor), filename)
    assert records == [document]
    assert [mesh.name for mesh in document.meshes] == names
    mapping = document.metadata["paint_mapping"]
    actual = []
    for mesh in document.meshes:
        colors = np.unique(mesh.face_rgba, axis=0)
        assert colors.shape == (1, 4)
        color = "#" + bytes(colors[0, :3]).hex().upper()
        actual.append(mapping[color]["token"])
        np.testing.assert_array_equal(mesh.face_rgba, mesh.source_rgba)
    assert actual == expected
    assert set("".join(actual)) == {"c", "m", "y"}
    assert document.metadata["paint_export_layer_height_mm"] == .16
    if "benchy" in filename:
        assert len(document.meshes) == 8
        assert sum(len(mesh.faces) for mesh in document.meshes) == 253176
        assert len(mapping) == 4
        assert all(name.startswith("3DBenchy_small_tilt.stl_") for name in names)
        assert document.metadata["paint_export_memory_budget_mib"] == 4096
    project = tmp_path / "editable tutorial.llpaint"
    save_paint_document(document, project)
    reopened = load_paint_document(project)
    assert reopened.metadata["paint_recipe_preferences"] == document.metadata["paint_recipe_preferences"]
    assert reopened.metadata["3mf_source_object_names"] == document.metadata["3mf_source_object_names"]
    for original, saved in zip(document.meshes, reopened.meshes):
        np.testing.assert_array_equal(original.face_rgba, saved.face_rgba)
        np.testing.assert_array_equal(original.source_rgba, saved.source_rgba)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_real_benchy_layer_202_measures_exported_partition_without_resnapping():
    import shapely
    import layerloom.paint_export as export
    from layerloom.paint_nearest import NearestTriangles

    source = find_example_file("tutorial_cmy_benchy_cutup.3mf")
    document = load_paint_document(source)
    editor = SimpleNamespace(document=document, set_document=lambda _document: None)
    DesktopController._initialize_example_paint(SimpleNamespace(paint_editor=editor), source.name)
    assignments = export.assign_palette(document, "Normal", recipe_overrides=
                                         document.metadata["paint_recipe_preferences"]["Normal"])
    surface, solid, recipes, _ = export._source_geometry(document, assignments)
    nearest = NearestTriangles(surface)
    bounds = surface.bounds
    z = bounds[0, 2] + (202 + .5) * .16
    domain = export._layer_domain(solid.slice(z), precision=.001, layer_index=202)
    regions, _, _, overlap, gap, area = export._partition_layer(
        surface, recipes, domain, z=z, layer_index=202, pitch=.5,
        origin=bounds[0, :2], precision=.001, interface_offset=.001,
        nearest_query=nearest.query)
    # A second snapped union loses a tiny sloping triangle on this real layer.
    # It used to be reported as material overlap despite disjoint material interiors.
    exact = [shapely.set_precision(region, 0) for region in regions.values()]
    assert shapely.intersection(*exact).area == 0
    assert overlap < 1e-10 and gap == 0
    assert area == pytest.approx(sum(region.area for region in exact), abs=1e-10)
    resnapped = shapely.union_all(exact, grid_size=.001)
    assert area - resnapped.area > domain.area * 1e-8
