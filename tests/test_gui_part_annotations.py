"""Viewer identification overlays without constructing a native renderer."""

from itertools import product
from types import SimpleNamespace

import numpy as np
import pytest


pytestmark = pytest.mark.gui


class FakeActor:
    def __init__(self, bounds=(0., 10., 0., 20., 0., 30.)):
        self.bounds = np.asarray(bounds)
        self.matrix = np.eye(4)
        self.visible = True
        self.color = "#ff00ff"

    def GetBounds(self):
        corners = np.array(list(product(*zip(self.bounds[::2], self.bounds[1::2]))))
        transformed = corners @ self.matrix[:3, :3].T + self.matrix[:3, 3]
        bounds = np.empty(6)
        bounds[::2], bounds[1::2] = transformed.min(axis=0), transformed.max(axis=0)
        return bounds

    def SetVisibility(self, visible):
        self.visible = bool(visible)

    def GetVisibility(self):
        return self.visible


class FakePlotter:
    def __init__(self):
        self.actors = {}
        self.render_count = 0

    def add_point_labels(self, points, labels, *, name, **kwargs):
        assert kwargs["show_points"] is False
        assert kwargs["pickable"] is False
        assert kwargs["reset_camera"] is False
        assert kwargs["always_visible"] is True
        actor = FakeActor()
        actor.points = np.array(points)
        actor.labels = list(labels)
        actor.options = kwargs
        self.actors[name + "-labels"] = actor
        return actor

    def add_mesh(self, mesh, *, name, **kwargs):
        assert kwargs["pickable"] is False
        assert kwargs["reset_camera"] is False
        actor = FakeActor()
        actor.mesh = mesh
        self.actors[name] = actor
        return actor

    def remove_actor(self, actor, **kwargs):
        for key, candidate in list(self.actors.items()):
            if candidate is actor:
                del self.actors[key]

    def render(self):
        self.render_count += 1


@pytest.fixture
def viewer(monkeypatch):
    pytest.importorskip("PyQt5.QtWidgets")
    pytest.importorskip("pyvistaqt")
    from layerloom.gui_app import _load_current_gui_module

    gui = _load_current_gui_module()
    # The overlay needs just twelve bounding-box edges, not a render window.
    monkeypatch.setattr(gui._V58.pv, "Cube", lambda *, bounds: SimpleNamespace(
        extract_all_edges=lambda: tuple(bounds)
    ))
    view = object.__new__(gui.EmbeddedPVViewerV65)
    view.plotter = FakePlotter()
    view.actors_by_name = {"hull": FakeActor(), "mast": FakeActor((1., 3., 4., 6., 7., 9.))}
    view.tool_mode = "normal"
    view.point_label_actor = None
    view.selection_outline_actor = None
    view._part_actor_numbers = {}
    view._selected_part_names = set()
    view._show_part_numbers = True
    view._part_annotations_visible = True
    view._part_annotation_actors = {}
    view.gui_module = gui
    return view


def test_numbers_follow_transformed_actor_bounds_without_changing_materials(viewer):
    viewer.set_part_annotations({"hull": "1", "mast": "2"}, {"mast"})
    regular = viewer._part_annotation_actors["__v65_part_numbers__"]
    selected = viewer._part_annotation_actors["__v65_selected_part_number__"]
    np.testing.assert_allclose(regular.points, [[5., 10., 15.]])
    np.testing.assert_allclose(selected.points, [[2., 5., 8.]])
    assert selected.labels == ["#2"]
    assert selected.options["font_size"] > regular.options["font_size"]

    # Translation plus rotation follows the actor's current rendered state,
    # including a dataset that has already had previous transforms baked in.
    viewer.actors_by_name["mast"].matrix = np.array([
        [0., -1., 0., 100.], [1., 0., 0., 120.],
        [0., 0., 1., 5.], [0., 0., 0., 1.],
    ])
    viewer.refresh_part_annotations()
    np.testing.assert_allclose(viewer._part_annotation_actors["__v65_selected_part_number__"].points,
                               [[95., 122., 13.]])
    np.testing.assert_allclose(viewer._part_annotation_actors["__v65_selected_part_outline__"].mesh,
                               [94., 96., 121., 123., 12., 14.])
    assert set(viewer.actors_by_name) == {"hull", "mast"}
    assert all(actor.color == "#ff00ff" for actor in viewer.actors_by_name.values())


def test_repeated_refresh_and_selection_do_not_accumulate_actors(viewer):
    viewer.set_part_annotations({"hull": "1", "mast": "2"}, {"mast"})
    for _ in range(15):
        previous = list(viewer._part_annotation_actors.values())
        viewer.refresh_part_annotations()
        assert len(viewer.plotter.actors) == 3
        assert not any(actor in viewer.plotter.actors.values() for actor in previous)

    viewer.set_part_annotations({"hull": "1", "mast": "2"}, {"hull"}, show_numbers=False)
    assert set(viewer._part_annotation_actors) == {
        "__v65_selected_part_number__", "__v65_selected_part_outline__",
    }
    assert viewer._part_annotation_actors["__v65_selected_part_number__"].labels == ["#1"]
    viewer.set_part_annotations({"hull": "1", "mast": "2"}, set(), show_numbers=False)
    assert viewer.plotter.actors == {}


def test_drag_hides_annotations_then_reanchors_without_changing_toggle(viewer):
    viewer.set_part_annotations({"hull": "1", "mast": "2"}, {"mast"}, show_numbers=False)
    viewer._set_point_labels_visible(False)
    assert all(not actor.visible for actor in viewer._part_annotation_actors.values())
    viewer.actors_by_name["mast"].matrix[:3, 3] = [10., 20., 30.]
    viewer._set_point_labels_visible(True)
    assert "__v65_part_numbers__" not in viewer._part_annotation_actors
    assert all(actor.visible for actor in viewer._part_annotation_actors.values())
    np.testing.assert_allclose(viewer._part_annotation_actors["__v65_selected_part_number__"].points,
                               [[12., 25., 38.]])

    viewer.tool_mode = "gizmo"
    viewer.refresh_part_annotations()
    assert "__v65_selected_part_outline__" not in viewer._part_annotation_actors
    assert "__v65_selected_part_number__" in viewer._part_annotation_actors


def test_instances_share_number_and_teardown_clears_references(viewer, monkeypatch):
    viewer.set_part_annotations({"hull": "3", "mast": "3", "missing": "4"}, {"hull", "mast"})
    assert viewer._part_annotation_actors["__v65_selected_part_number__"].labels == ["#3", "#3"]
    np.testing.assert_allclose(viewer._part_annotation_actors["__v65_selected_part_outline__"].mesh,
                               [0., 10., 0., 20., 0., 30.])
    # Exercise the real override without invoking the inherited VTK teardown.
    monkeypatch.setattr(viewer.gui_module._BASE_VIEWER, "_teardown_scene", lambda self: None)
    viewer._teardown_scene()
    assert viewer.plotter.actors == {}
    assert viewer._part_actor_numbers == {}
    assert viewer._selected_part_names == set()
    assert viewer._part_annotation_actors == {}
    assert viewer._part_annotations_visible is True


def test_invalid_or_hidden_bounds_do_not_create_annotations(viewer):
    viewer.actors_by_name["hull"].visible = False
    viewer.actors_by_name["mast"].GetBounds = lambda: (np.nan,) * 6
    viewer.set_part_annotations({"hull": "1", "mast": "2"}, {"mast"})
    assert viewer.plotter.actors == {}
