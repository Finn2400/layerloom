"""Transform consistency without constructing a native VTK renderer."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.gui
def test_numeric_preview_matches_export_after_baked_and_streaming_transforms(tmp_path):
    pytest.importorskip("qtpy")
    pytest.importorskip("pyvistaqt")
    script = r'''
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

import numpy as np
import trimesh
from qtpy import QtWidgets
from layerloom.gui_app import _load_current_gui_module
from layerloom.export import write_basic_3mf
from layerloom.transform_3mf import compute_transform_plan, rotation_matrix_xyz

gui = _load_current_gui_module()
app = QtWidgets.QApplication([])
root = Path(sys.argv[1])
source = root / "asymmetric.3mf"
points = np.array([[0., 0., 0.], [14., 0., 0.], [0., 9., 0.], [2., 3., 11.]]) + [40., 70., 8.]
mesh = trimesh.Trimesh(vertices=points, faces=[[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], process=False)
write_basic_3mf([("wedge", mesh)], str(source))

class Actor:
    def __init__(self, points):
        self.dataset = SimpleNamespace(points=points.copy())
        self.matrix = np.eye(4)
    def SetUserMatrix(self, matrix):
        self.matrix = matrix.copy()

class Viewer:
    def __init__(self):
        self.actors_by_name = {"wedge": Actor(points)}
        self.plotter = SimpleNamespace(render=lambda: None)
        self._scene_revision = 0
    def _actor_dataset(self, actor):
        return actor.dataset
    def _vtk_matrix(self, matrix):
        return matrix.copy()
    def _rebuild_selection_outline(self):
        pass
    def _rebuild_gizmo(self):
        pass
    def set_plate_warning(self, *args, **kwargs):
        pass
    def set_tool_mode(self, mode, **kwargs):
        self.tool_mode = mode

class Window(gui.QtAssignColorsApp):
    def __init__(self):
        QtWidgets.QMainWindow.__init__(self)
        self.base_file = self.current_file = str(source)
        self._temp_dir = str(root)
        self.transformed_preview_file = None
        self.viewer = Viewer()
        self.viewer_tool_mode = "gizmo"
        self.pending_target_center_xy = np.array([91., 112.])
        self.applied_target_center_xy = None
        self.pending_rotation_matrix = rotation_matrix_xyz(23., 17., 35.)
        self.applied_rotation_matrix = np.eye(3)
        self.applied_scale = 1.
        self._preview_dataset_matrix = np.eye(4)
        self._preview_dataset_base_file = self.base_file
        self._preview_file_matrix = self._preview_file_path = None
        self._preview_original_bounds = None
        self._last_transform_plan = None
        self._setting_transform_widgets = False
        self.streaming_preview_mode = False
        self.large_model_mode = False
        self.rot_x_spin = QtWidgets.QDoubleSpinBox()
        self.rot_y_spin = QtWidgets.QDoubleSpinBox()
        self.rot_z_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setValue(1.25)
    def _refresh_transform_readout(self, *args):
        pass
    def _update_transform_ui_state(self):
        pass
    def _refresh_status_summary(self):
        pass
    def _sync_viewer_tool_mode(self):
        pass
    def _refresh_tool_button_states(self):
        pass

def expected(window):
    return compute_transform_plan(str(source), scale=window.scale_spin.value(),
                                  orientation_matrix=window.pending_rotation_matrix,
                                  center_xy=False, target_center_xy=window.pending_target_center_xy)

def assert_preview(window):
    window._apply_streaming_actor_transform()
    actor = window.viewer.actors_by_name["wedge"]
    actual = actor.dataset.points @ actor.matrix[:3, :3].T + actor.matrix[:3, 3]
    plan = expected(window)
    desired = points @ plan.global_matrix[:3, :3].T + plan.global_matrix[:3, 3]
    np.testing.assert_allclose(actual, desired, atol=1e-9)
    np.testing.assert_allclose(actual.min(axis=0), plan.transformed_bounds.min_corner, atol=1e-9)
    assert abs(actual[:, 2].min()) < 1e-9

window = Window()
assert_preview(window)

# Bake one preview to disk, then rebuild with those transformed coordinates.
# A later save/readout can replace _last_transform_plan; it is not the baked matrix.
preview = window._preview_temp_path("transform_preview")
baked = window._materialize_current_model(preview, include_assignments=False)
window.current_file = preview
window.viewer.actors_by_name["wedge"] = Actor(points @ baked.global_matrix[:3, :3].T + baked.global_matrix[:3, 3])
def completed_build(window):
    window.viewer._scene_revision += 1
with patch.object(gui._V62.QtAssignColorsApp, "_build_or_rebuild_viewer", completed_build):
    window._build_or_rebuild_viewer()
np.testing.assert_allclose(window._preview_dataset_matrix, baked.global_matrix)
window.pending_target_center_xy = np.array([72.5, 131.25])
window.pending_rotation_matrix = rotation_matrix_xyz(41., -32., 127.)
window.scale_spin.setValue(1.75)
window._last_transform_plan = expected(window)
assert_preview(window)
assert_preview(window)  # No cumulative transform on repeated updates.

# Streaming updates must not parse the entire 3MF XML, lose XY, or leave Z floating.
window.streaming_preview_mode = True
with patch.object(gui._V58, "compute_transform_plan", side_effect=AssertionError("streaming parsed XML")):
    window._apply_streaming_actor_transform()
    plan = window._pending_transform_plan()
    np.testing.assert_allclose(plan.transformed_bounds.center[:2], window.pending_target_center_xy)
    assert window._apply_transform_preview()
    assert window.viewer_tool_mode == "gizmo"
assert_preview(window)

# The inherited builder catches renderer failures; a failed rebuild must not
# relabel old actor data as a new dataset and apply a different baked matrix.
before_failed_build = window.viewer.actors_by_name["wedge"].matrix.copy()
with patch.object(gui._V62.QtAssignColorsApp, "_build_or_rebuild_viewer", lambda self: None):
    window._build_or_rebuild_viewer()
assert window._preview_dataset_base_file is None
np.testing.assert_array_equal(window.viewer.actors_by_name["wedge"].matrix, before_failed_build)

# Reopening resets all coordinates and the baked-matrix identity before touching
# the previous scene, even if the new file happens to have the same pathname.
with patch.object(gui._V62.QtAssignColorsApp, "_load_canonical_model", lambda self, path: None):
    window._load_canonical_model(str(source))
assert window.pending_target_center_xy is None
assert window.applied_target_center_xy is None
assert window._preview_dataset_base_file is None
assert window._preview_file_matrix is None
np.testing.assert_array_equal(window._preview_dataset_matrix, np.eye(4))
window.deleteLater()
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
