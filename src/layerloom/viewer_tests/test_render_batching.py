"""Render-cost regressions using real VTK data without a native GL window."""

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

pv = pytest.importorskip("pyvista")
pytest.importorskip("pyvistaqt")

from layerloom.gui_app import _load_current_gui_module
from layerloom.paint_model import PaintDocument, PaintMesh
from layerloom.paint_workspace import SharedPaintWorkspace


@pytest.fixture(scope="module")
def base():
    return _load_current_gui_module()._V58


class Actor:
    def __init__(self):
        self.mapper = SimpleNamespace(scalar_visibility=True)
        self.prop = SimpleNamespace()
        self.SetUserMatrix = Mock()

    def __repr__(self):
        raise AssertionError("Disabled debug logging must not format full VTK actors")


class Plotter:
    def __init__(self, visible=True):
        self.actors = {}
        self.visible = visible
        self.render_count = 0
        self.add_calls = []
        self.removed = []

    def render(self):
        self.render_count += 1

    def isVisible(self):
        return self.visible

    def add_mesh(self, poly, *, name, render=True, **kwargs):
        self.add_calls.append((poly, render))
        self.actors[name] = actor = Actor()
        if render:
            self.render()
        return actor

    def remove_actor(self, actor, *, render=True, **kwargs):
        self.removed.append(actor)
        if render:
            self.render()

    def reset_camera(self, *, render=True):
        if render:
            self.render()

    def reset_camera_clipping_range(self):
        pass


@pytest.mark.parametrize("part_count", [1, 100])
def test_preview_build_and_bulk_tint_render_once_each(base, part_count):
    viewer = base.PVWindow()
    viewer.show_part_labels = False
    viewer.plotter = plotter = Plotter()
    viewer._ensure_plotter = lambda: plotter
    viewer._teardown_scene = lambda: None
    records = [base.PreviewPartRecord(str(i), str(i), str(i), f"part_{i}",
                                     f"Part {i}", i, str(i)) for i in range(part_count)]
    manifest = base.PreviewManifest.from_records(records)
    geometry = pv.Sphere(theta_resolution=8, phi_resolution=8)
    cache = {record.instance_key: geometry for record in records}
    viewer.build_scene("unused.3mf", [r.actor_name for r in records], manifest=manifest, mesh_cache=cache)
    assert len(viewer.actors_by_name) == part_count
    assert plotter.render_count == 1
    assert all(not render for _, render in plotter.add_calls)
    viewer.bulk_tint({r.actor_name: "#123456" for r in records})
    assert plotter.render_count == 2
    for actor in viewer.actors_by_name.values():
        np.testing.assert_allclose(actor.prop.color, [0x12 / 255, 0x34 / 255, 0x56 / 255])


@pytest.mark.parametrize("visible, expected", [(True, 1), (False, 0)])
def test_teardown_removes_all_parts_without_per_part_redraw(base, visible, expected):
    viewer = base.PVWindow()
    viewer.plotter = plotter = Plotter(visible)
    viewer.actors_by_name = {str(i): Actor() for i in range(100)}
    viewer._remove_raw_interaction_handlers = lambda: None
    viewer._clear_builtin_picking = lambda: None
    viewer._teardown_scene()
    assert len(plotter.removed) == 100
    assert plotter.render_count == expected
    assert not viewer.actors_by_name


def workspace(visible, part_count=20):
    geometry = pv.Sphere(theta_resolution=8, phi_resolution=8)
    vertices = geometry.points.astype(np.float64)
    faces = geometry.faces.reshape(-1, 4)[:, 1:]
    colors = np.tile([12, 34, 56, 255], (len(faces), 1)).astype(np.uint8)
    document = PaintDocument([
        PaintMesh(str(i), f"Part {i}", vertices.copy(), faces.copy(), colors.copy())
        for i in range(part_count)
    ])
    source_polys = {}
    for index, mesh in enumerate(document.meshes):
        poly = pv.PolyData(mesh.vertices.copy(), geometry.faces.copy())
        poly.cell_data["paint_rgba"] = np.tile([255, 255, 0, 255], (len(faces), 1)).astype(np.uint8)
        source_polys[index] = poly
    plotter = Plotter(visible)
    viewer = SimpleNamespace(
        plotter=plotter, actors_by_name={}, _ensure_plotter=lambda: plotter,
        _teardown_scene=lambda: None, _clear_layer_preview=lambda: None,
        _clear_hover_model_pick_cache=lambda: None,
    )
    host = SimpleNamespace(viewer=viewer, _handle_viewer_pick_event=lambda: None,
                           oid_to_name={str(i): f"part_{i}" for i in range(part_count)})
    editor = SimpleNamespace(document=document, _polys=source_polys,
                             display_mesh_colors=lambda index, **kwargs: document.meshes[index].face_rgba.copy())
    bridge = SharedPaintWorkspace.__new__(SharedPaintWorkspace)
    bridge.host, bridge.editor = host, editor
    bridge.model = SimpleNamespace(document=document)
    bridge.mesh_index_by_oid = {str(i): i for i in range(part_count)}
    return bridge, plotter


@pytest.mark.parametrize("visible, expected", [(True, 1), (False, 0)])
def test_shared_workspace_batches_render_and_shares_only_geometry(visible, expected):
    bridge, plotter = workspace(visible)
    bridge.rebuild_scene()
    assert len(plotter.actors) == 20
    assert plotter.render_count == expected
    for index, poly in bridge.polys.items():
        source = bridge.editor._polys[index]
        assert np.shares_memory(poly.points, source.points)
        assert poly.GetPolys() == source.GetPolys()
        assert not np.shares_memory(poly.cell_data["paint_rgba"], source.cell_data["paint_rgba"])
        np.testing.assert_array_equal(poly.cell_data["paint_rgba"], bridge.editor.document.meshes[index].face_rgba)
        source.cell_data["paint_rgba"][:] = [255, 0, 0, 255]
        assert not np.any(poly.cell_data["paint_rgba"][:, 0] == 255)
