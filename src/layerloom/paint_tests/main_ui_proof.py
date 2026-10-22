"""Native development proof of main-window surface selection and repaired paint.

Use --baby-project with a repaired .llpaint project and --output-dir for local
evidence. --stay-open leaves the verified baby in the main LayerLoom window.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from qtpy import QtCore, QtWidgets, QtTest

from layerloom.gui_app import _load_current_gui_module
from layerloom.paint_examples import example_document, assert_roundtrip
from layerloom.paint_io import load_paint_document
from layerloom.paint_tests.gui_proof import point, click_action, save_via_ui, screenshot, digest, model_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baby-project", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stay-open", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    main_window = _load_current_gui_module().QtAssignColorsApp()
    main_window.resize(1150, 800)
    main_window.show()
    main_window.raise_()
    main_window.activateWindow()
    editor = main_window.paint_panel.editor
    assert main_window._paint_active() and not editor.isWindow()
    editor.set_document(example_document("cube", 20))
    editor.view("top")
    app.processEvents()
    QtTest.QTest.qWait(150)
    mesh = editor.document.meshes[0]
    original = mesh.face_rgba.copy()
    top = np.flatnonzero(np.all(mesh.vertices[mesh.faces, 2] == 20.0, axis=1))
    editor.tool_combo.setCurrentText("Surface select")
    pos = point(editor, (2.0, 1.0, 20.0))
    assert editor.pick_at(pos) is not None
    QtTest.QTest.mouseClick(editor.plotter, QtCore.Qt.LeftButton, pos=pos)
    app.processEvents()
    np.testing.assert_array_equal(np.flatnonzero(editor._selection[0]), top)
    np.testing.assert_array_equal(mesh.face_rgba, original)
    selection_image = screenshot(editor, output / "cube_surface_selected.png", host=main_window)
    editor.set_paint_color("#F4792A")
    QtTest.QTest.mouseClick(editor.apply_selection_button, QtCore.Qt.LeftButton)
    app.processEvents()
    painted = mesh.face_rgba.copy()
    assert np.all(painted[top] == [244, 121, 42, 255])
    other = np.setdiff1d(np.arange(len(mesh.faces)), top)
    np.testing.assert_array_equal(painted[other], original[other])
    click_action(editor, editor.undo_action)
    np.testing.assert_array_equal(mesh.face_rgba, original)
    click_action(editor, editor.redo_action)
    np.testing.assert_array_equal(mesh.face_rgba, painted)
    editor.mapping_mode_combo.setCurrentIndex(editor.mapping_mode_combo.findData("source"))
    QtTest.QTest.mouseClick(editor.mapping_toggle, QtCore.Qt.LeftButton)
    QtTest.QTest.mouseClick(editor.map_button, QtCore.Qt.LeftButton)
    cube_project = output / "cube_surface_painted.llpaint"
    save_via_ui(editor, cube_project)
    saved = editor.document.copy()
    click_action(editor, main_window._paint_action)
    click_action(editor, main_window._paint_action)
    assert_roundtrip(saved, editor.document)
    assert main_window.open_paint_path(str(cube_project))
    assert_roundtrip(saved, editor.document)
    editor.view("iso")
    painted_image = screenshot(editor, output / "cube_surface_painted_reopened.png", host=main_window)

    assert main_window.open_paint_path(str(args.baby_project.resolve()))
    before = editor.document.copy()
    # Keep the project's saved woven recipes; only assign if none were saved.
    if not editor._mapping:
        QtTest.QTest.mouseClick(editor.map_button, QtCore.Qt.LeftButton)
    assert editor.mapping_table.rowCount() == 4
    baby_project = output / "baby_repaired_main_ui.llpaint"
    save_via_ui(editor, baby_project)
    saved_baby = editor.document.copy()
    assert main_window.open_paint_path(str(baby_project))
    assert_roundtrip(saved_baby, editor.document)
    for left, right in zip(before.meshes, editor.document.meshes):
        for field in ("vertices", "faces", "face_rgba", "source_rgba"):
            np.testing.assert_array_equal(getattr(left, field), getattr(right, field))
    if before.metadata.get("paint_mapping"):
        assert editor._mapping == before.metadata["paint_mapping"]
        assert editor._mapping_mode == before.metadata["paint_mapping_mode"]
    QtTest.QTest.mouseClick(editor.mapping_toggle, QtCore.Qt.LeftButton)
    assert not editor.mapping_section.isVisible()
    # Frame the painted face from the eye-region side; only the camera changes.
    vertices = np.concatenate([m.vertices for m in editor.document.meshes])
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    center = (lo + hi) / 2
    face_centers = []
    for m in editor.document.meshes:
        centers = m.vertices[m.faces].mean(axis=1)
        eyes = np.all(m.face_rgba[:, :3] == 0, axis=1) & (centers[:, 2] > lo[2] + (hi[2] - lo[2]) * .5)
        if eyes.any():
            face_centers.append(centers[eyes])
    if face_centers:
        direction = np.concatenate(face_centers).mean(axis=0) - center
        direction[2] = 0
        direction /= max(np.linalg.norm(direction), 1e-9)
        direction += np.array([-.25 * direction[1], .25 * direction[0], .45])
        distance = float(np.linalg.norm(hi - lo)) * 1.35
        editor.plotter.camera_position = [center + direction * distance, center, (0, 0, 1)]
        editor.plotter.reset_camera_clipping_range()
        editor.plotter.render()
    baby_image = screenshot(editor, output / "baby_repaired_main_ui.png", host=main_window)
    QtTest.QTest.mouseClick(editor.tool_settings_button, QtCore.Qt.LeftButton)
    app.processEvents()
    assert editor.tool_settings_menu.isVisible()
    settings_image = screenshot(editor, output / "baby_surface_settings.png", host=main_window)
    QtTest.QTest.keyClick(editor.tool_settings_menu, QtCore.Qt.Key_Escape)
    app.processEvents()
    assert not editor.tool_settings_menu.isVisible()
    report = {
        "default_paint_workspace": True, "embedded_editor": not editor.isWindow(),
        "cube": {"selected_top_face_triangles": int(len(top)), "selection_did_not_change_paint": True,
                 "only_selected_surface_painted": True, "undo_redo_exact": True,
                 "mode_switch_preserved_edits": True, "save_reopen_exact": True,
                 "project": str(cube_project), "project_sha256": digest(cube_project),
                 "selection_image": selection_image, "painted_image": painted_image},
        "baby": {"input_project": str(args.baby_project.resolve()), "input_sha256": digest(args.baby_project),
                 "project": str(baby_project), "project_sha256": digest(baby_project),
                 "save_reopen_exact": True, "geometry_and_paint_unchanged": True,
                 "mapping_mode": editor._mapping_mode, "mapping": editor._mapping,
                 "physical_color_slots": (len(editor._mapping) if editor._mapping_mode == "source"
                     else len({token for entry in editor._mapping.values() for token in entry["token"]})),
                 "meshes": model_rows(editor.document), "screenshot": baby_image,
                 "settings_screenshot": settings_image},
    }
    target = output / "main-ui-proof.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(target, flush=True)
    if args.stay_open:
        return app.exec_()
    main_window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
