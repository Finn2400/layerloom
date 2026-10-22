"""Native proof of one shared Paint/Arrange document and geometry export.

Run only with an available native display and an unused output directory.
The supplied baby project and its original source are read and hashed only.
File-dialog choices are supplied by the proof; import, save, paint, placement,
worker conversion, and framebuffer captures use the actual application paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from unittest.mock import patch
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
from qtpy import QtCore, QtWidgets, QtTest

from layerloom.gui_app import _load_current_gui_module
from layerloom.paint_examples import assert_roundtrip, example_document
from layerloom.paint_io import load_paint_document
from layerloom.paint_tests.gui_proof import click_action, digest, model_rows, point, save_via_ui, screenshot


class CaptureView:
    """Give the existing screenshot helper the visible host and chosen VTK view."""

    def __init__(self, host, editor, plotter):
        self.host, self.editor, self.plotter = host, editor, plotter

    def __getattr__(self, name):
        return getattr(self.editor, name)

    def show(self):
        self.host.show()

    def raise_(self):
        self.host.raise_()

    def activateWindow(self):
        self.host.activateWindow()


def set_paint_mode(host, enabled):
    editor = host.paint_panel.editor
    if host._paint_active() != enabled:
        click_action(editor, host._paint_action)
    assert host._paint_active() == enabled
    assert host._paint_action.isChecked() == enabled


def assert_shared(host):
    editor, shared = host.paint_panel.editor, host._shared_paint_workspace
    assert shared.active and shared.model.document is editor.document
    for oid, index in shared.mesh_index_by_oid.items():
        mesh = editor.document.meshes[index]
        paint_poly, arrange_poly = editor._polys[index], shared.polys[index]
        for poly in (paint_poly, arrange_poly):
            np.testing.assert_array_equal(poly.points, mesh.vertices)
            np.testing.assert_array_equal(poly.faces.reshape(-1, 4)[:, 1:], mesh.faces)
        np.testing.assert_array_equal(paint_poly.cell_data["paint_rgba"], arrange_poly.cell_data["paint_rgba"])
        if not editor.preview_mapping.isChecked():
            np.testing.assert_array_equal(arrange_poly.cell_data["paint_rgba"], mesh.face_rgba)
        assert host.viewer.actors_by_name[host.oid_to_name[oid]].GetUserMatrix() is None


def camera_record(plotter):
    camera = plotter.camera
    return {"position": list(camera.GetPosition()), "focal_point": list(camera.GetFocalPoint()),
            "view_up": list(camera.GetViewUp()), "parallel_projection": bool(camera.GetParallelProjection()),
            "parallel_scale": camera.GetParallelScale(), "view_angle": camera.GetViewAngle()}


def capture_pair(host, output, prefix):
    editor = host.paint_panel.editor
    set_paint_mode(host, True)
    editor.plotter.render()
    camera = editor.plotter.camera.copy()
    paint = screenshot(CaptureView(host, editor, editor.plotter), output / f"{prefix}_paint.png", host=host)
    paint["camera"] = camera_record(editor.plotter)
    set_paint_mode(host, False)
    host.viewer.plotter.camera = camera.copy()
    QtWidgets.QApplication.processEvents()
    host.viewer.plotter.render_window.Render()
    arrange = screenshot(CaptureView(host, editor, host.viewer.plotter), output / f"{prefix}_arrange.png", host=host,
                         overlays=(host.viewer_top_overlay, host.camera_overlay))
    arrange["camera"] = camera_record(host.viewer.plotter)
    assert paint["camera"] == arrange["camera"]
    assert_shared(host)
    return {"paint": paint, "arrange": arrange, "same_camera": True,
            "note": "Identical camera parameters; the two layouts have different viewport aspect ratios."}


def enter_number(spin, value):
    assert spin.isVisible() and spin.isEnabled()
    line = spin.lineEdit()
    QtTest.QTest.mouseClick(line, QtCore.Qt.LeftButton)
    line.selectAll()
    QtTest.QTest.keyClicks(line, str(value))
    QtTest.QTest.keyClick(line, QtCore.Qt.Key_Return)
    QtWidgets.QApplication.processEvents()
    assert np.isclose(spin.value(), value)


def open_via_main(host, path):
    editor = host.paint_panel.editor
    with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        click_action(editor, editor.open_action)
    assert editor.project_path == str(path)
    assert_shared(host)


def export_cube(editor, output):
    notices = []
    # Capture notification text so macOS native sheets cannot block this proof.
    # The actual export worker, status and output checks remain unmodified.
    def capture_notice(_parent, title, text):
        notices.append({"title": title, "text": text})

    started = time.monotonic()
    with patch.object(QtWidgets.QMessageBox, "information", side_effect=capture_notice), \
            patch.object(QtWidgets.QMessageBox, "critical", side_effect=capture_notice):
        assert editor.start_export(output, mode="source", layer_height=0.5, pitch=0.5)
        while editor._export_thread is not None:
            assert time.monotonic() - started < 120, "Small geometry export exceeded two minutes"
            QtTest.QTest.qWait(20)
    assert editor.last_export_report is not None, notices
    assert not any(notice["title"] == "Printable export failed" for notice in notices)
    assert "Geometry export complete" in editor.statusBar().currentMessage()
    report = editor.last_export_report
    with zipfile.ZipFile(output) as archive:
        for name in archive.namelist():
            if name.endswith(".model"):
                for node in ET.fromstring(archive.read(name)).iter():
                    local = node.tag.rsplit("}", 1)[-1]
                    assert local not in {"colorgroup", "texture2d", "texture2dgroup", "multiproperties"}
                    if local == "triangle":
                        assert set(node.attrib) == {"v1", "v2", "v3"}
    restored = load_paint_document(output)
    vertices = np.concatenate([mesh.vertices for mesh in restored.meshes])
    np.testing.assert_allclose([vertices.min(axis=0), vertices.max(axis=0)],
                               np.asarray(report["output_bounds_mm"]) + [128, 128, 0], atol=2e-6)
    return {"path": str(output), "sha256": digest(output), "geometry_only": True,
            "real_worker_export": True, "elapsed_seconds": time.monotonic() - started,
            "report": report, "report_sha256": digest(report["report_path"]),
            "reimported_parts": model_rows(restored), "completion_messages": notices,
            "notification_dialogs_captured_by_test_harness": True}


def face_camera(editor):
    vertices = np.concatenate([mesh.vertices for mesh in editor.document.meshes])
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    center = (lo + hi) / 2
    eyes = []
    for mesh in editor.document.meshes:
        centers = mesh.vertices[mesh.faces].mean(axis=1)
        selected = np.all(mesh.face_rgba[:, :3] == 0, axis=1) & (centers[:, 2] > lo[2] + (hi[2] - lo[2]) * 0.5)
        if selected.any():
            eyes.append(centers[selected])
    direction = np.concatenate(eyes).mean(axis=0) - center if eyes else np.array([1.0, -1.0, 0.0])
    direction[2] = 0
    direction /= max(float(np.linalg.norm(direction)), 1e-9)
    direction += [-0.25 * direction[1], 0.25 * direction[0], 0.45]
    editor.plotter.camera_position = [center + direction * np.linalg.norm(hi - lo) * 1.35, center, (0, 0, 1)]
    editor.plotter.reset_camera_clipping_range()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baby-project", type=Path, required=True)
    parser.add_argument("--stay-open", action="store_true")
    args = parser.parse_args()
    output, baby_path = args.output_dir.resolve(), args.baby_project.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("--output-dir must be new or empty so earlier evidence is never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    baby_hash = digest(baby_path)
    baby_original = load_paint_document(baby_path)
    baby_rows = model_rows(baby_original)
    assert len(baby_rows) == 2, "This proof expects both downloaded baby-dragon instances"
    original_source = Path(baby_original.source_path) if baby_original.source_path else None
    original_hash = digest(original_source) if original_source and original_source.is_file() else None
    del baby_original

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    host = _load_current_gui_module().QtAssignColorsApp()
    host.resize(1250, 850)
    host.show()
    host.raise_()
    host.activateWindow()
    editor = host.paint_panel.editor
    editor.set_document(example_document("cube", 8))
    editor.view("top")
    app.processEvents()
    QtTest.QTest.qWait(150)
    mesh = editor.document.meshes[0]
    before_paint, before_source = mesh.face_rgba.copy(), mesh.source_rgba.copy()
    top = np.flatnonzero(np.all(mesh.vertices[mesh.faces, 2] == 8.0, axis=1))
    editor.tool_combo.setCurrentText("Surface select")
    pos = point(editor, (1.0, 1.0, 8.0))
    assert editor.pick_at(pos) is not None
    QtTest.QTest.mouseClick(editor.plotter, QtCore.Qt.LeftButton, pos=pos)
    np.testing.assert_array_equal(np.flatnonzero(editor._selection[0]), top)
    np.testing.assert_array_equal(mesh.face_rgba, before_paint)
    editor.set_paint_color("#F4792A")
    QtTest.QTest.mouseClick(editor.apply_selection_button, QtCore.Qt.LeftButton)
    assert np.all(mesh.face_rgba[top] == [244, 121, 42, 255])
    untouched = np.setdiff1d(np.arange(len(mesh.faces)), top)
    np.testing.assert_array_equal(mesh.face_rgba[untouched], before_paint[untouched])
    editor.mapping_mode_combo.setCurrentIndex(editor.mapping_mode_combo.findData("source"))
    assert editor.assign_colors()
    editor.preview_mapping.setChecked(False)
    set_paint_mode(host, False)
    enter_number(host.rot_y_spin, 90.0)
    enter_number(host.scale_spin, 0.75)
    QtTest.QTest.mouseClick(host.center_plate_btn, QtCore.Qt.LeftButton)
    host._apply_move_delta([12.0, -9.0])
    assert_shared(host)
    pose = host._shared_paint_workspace.model.current_plan
    np.testing.assert_allclose(pose.transformed_bounds.center[:2], [140, 119], atol=1e-9)
    np.testing.assert_allclose(pose.transformed_bounds.size, [6, 6, 6], atol=1e-9)
    np.testing.assert_array_equal(mesh.source_rgba, before_source)
    cube_path = output / "cube_shared_placed.llpaint"
    save_via_ui(editor, cube_path)
    saved = editor.document.copy()
    open_via_main(host, cube_path)
    assert not host._paint_active(), "Common Open should retain Arrange mode"
    assert_roundtrip(saved, editor.document)
    exported = export_cube(editor, output / "cube_shared_geometry.3mf")
    bounds = np.asarray([pose.transformed_bounds.min_corner, pose.transformed_bounds.max_corner])
    np.testing.assert_allclose(exported["report"]["source_bounds_mm"], bounds, atol=1e-9)
    assert np.isclose(exported["report"]["source_volume_mm3"], 8 ** 3 * 0.75 ** 3)
    assert_roundtrip(saved, editor.document)
    set_paint_mode(host, True)
    editor.view("iso")
    cube_images = capture_pair(host, output, "cube_shared")

    open_via_main(host, baby_path)
    assert model_rows(editor.document) == baby_rows
    assert_shared(host)
    baby_copy = output / "baby_shared_copy.llpaint"
    save_via_ui(editor, baby_copy)
    open_via_main(host, baby_copy)
    assert model_rows(editor.document) == baby_rows
    for enabled in [True, False, True]:
        set_paint_mode(host, enabled)
        assert_shared(host)
        assert model_rows(editor.document) == baby_rows
    editor.preview_mapping.setChecked(False)
    face_camera(editor)
    baby_images = capture_pair(host, output, "baby_shared")
    set_paint_mode(host, True)
    assert model_rows(editor.document) == baby_rows
    assert digest(baby_path) == baby_hash
    if original_hash:
        assert digest(original_source) == original_hash
    report = {
        "shared_document": True, "same_geometry_and_paint_in_both_views": True,
        "file_dialog_choices_supplied_by_proof": True,
        "cube": {"selected_surface_triangles": len(top), "only_selected_surface_painted": True,
                 "numeric_rotation_y_degrees": 90.0, "absolute_scale": 0.75,
                 "center_button_clicked": True, "move_callback_delta_xy_mm": [12.0, -9.0],
                 "posed_bounds_mm": bounds.tolist(), "source_paint_unchanged": True,
                 "saved_and_reopened_from_arrange": True, "project": str(cube_path),
                 "project_sha256": digest(cube_path), "export": exported, "screenshots": cube_images},
        "baby": {"input_project": str(baby_path), "input_project_sha256": baby_hash,
                 "original_source": str(original_source) if original_source else None,
                 "original_source_sha256": original_hash, "inputs_unchanged": True,
                 "proof_copy": str(baby_copy), "proof_copy_sha256": digest(baby_copy),
                 "both_instances_geometry_paint_source_colors_unchanged": True,
                 "meshes": baby_rows, "screenshots": baby_images},
        "left_open_in_paint": args.stay_open,
    }
    report_path = output / "shared-workspace-proof.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(report_path, flush=True)
    if args.stay_open:
        return app.exec_()
    host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
