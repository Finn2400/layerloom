"""Exercise document handlers and real 3MF I/O without a native renderer."""

from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest


pytestmark = pytest.mark.gui


@pytest.fixture
def document_window(tmp_path):
    pytest.importorskip("PyQt5.QtWidgets")
    pytest.importorskip("pyvistaqt")
    from layerloom.gui_app import _load_current_gui_module

    gui = _load_current_gui_module()
    base = gui._V58
    v60 = gui._V62._V61._V60

    class DocumentWindow:
        _on_open = v60.QtAssignColorsApp._on_open
        _on_load_example = gui._V62.QtAssignColorsApp._on_load_example
        _normalize_incoming_3mf = v60.QtAssignColorsApp._normalize_incoming_3mf
        _stamp_ids_only = base.QtAssignColorsApp._stamp_ids_only
        _on_save = base.QtAssignColorsApp._on_save
        _on_weave = base.QtAssignColorsApp._on_weave
        _materialize_current_model = base.QtAssignColorsApp._materialize_current_model
        _build_name_updates = base.QtAssignColorsApp._build_name_updates
        _build_assignment_metadata_updates = base.QtAssignColorsApp._build_assignment_metadata_updates
        _apply_source_metadata_assignments = base.QtAssignColorsApp._apply_source_metadata_assignments
        _set_assignment_for_oid = base.QtAssignColorsApp._set_assignment_for_oid
        _exact_palette_entry_for_token = gui._V62.QtAssignColorsApp._exact_palette_entry_for_token
        _nearest_palette_entry = base.QtAssignColorsApp._nearest_palette_entry
        _refresh_status_summary = base.QtAssignColorsApp._refresh_status_summary
        _preweave_pat_audit = base.QtAssignColorsApp._preweave_pat_audit
        _postweave_pat_audit = base.QtAssignColorsApp._postweave_pat_audit

        def __init__(self):
            self._temp_dir = str(tmp_path)
            self.base_file = self.current_file = self.document_path = None
            self.model_objects = []
            self.assignments = {}
            self.pending_rotation_matrix = np.eye(3)
            self.large_model_mode = False
            self.current_palette_name = "Normal"
            self.current_palette = [gui._V62._entry_for_token(token) for token in ("c", "m", "y")]
            self.errors = []
            self.toolbar_status_label = SimpleNamespace(setText=lambda text: setattr(self, "summary", text))
            self.status_bar = SimpleNamespace(showMessage=lambda *args: None)
            self.layer_height_spin = SimpleNamespace(value=lambda: 0.2)

        def _load_canonical_model(self, path):
            # The model loader's data-only portion: no Qt/VTK allocation.
            self.base_file = self.current_file = path
            with zipfile.ZipFile(path) as archive:
                root = base._read_model_root(archive, base._find_model_xml_name(archive))
            self.oid_to_name = base._oid_to_name_map(root)
            real_oids = base._mesh_oid_set(root)
            self.model_objects = [(oid, name) for oid, name in self.oid_to_name.items() if oid in real_oids]
            self.display_names = base._build_preferred_display_name_map(
                self.model_objects, self.oid_to_name, base._oid_to_import_build_label_map(root)
            )
            self.assignments = {}
            self._apply_source_metadata_assignments(
                base._oid_to_source_hex_map(root), base._oid_to_stack_token_map(root)
            )
            self._refresh_status_summary()

        def _item_for_oid(self, oid):
            return None

        def _current_selected_oid(self):
            return None

        def _error(self, title, message):
            self.errors.append((title, message))

        def _tool_mode_label(self):
            return "View"

        def setWindowTitle(self, title):
            self.title = title

        def _current_scale_value(self, **kwargs):
            return 1.0

        def _ensure_transform_preview_current(self, **kwargs):
            return True

        def _refresh_transform_readout(self, *args):
            self._refresh_status_summary()

    return DocumentWindow(), base


def test_example_save_reopen_retains_part_names_and_user_filename(document_window, tmp_path, monkeypatch):
    from layerloom.ingest import read_parts

    window, base = document_window
    window._on_load_example()
    assert not window.errors
    names = dict(window.display_names)
    assert len(window.model_objects) == 8
    assert Path(window.document_path).name == "tutorial_cmy_benchy_cutup.3mf"
    assert "layerloom_normalized_" not in window.title
    source_parts = read_parts([window.base_file])
    window.assignments["1"] = {"token": "y", "hex": "#ffdf00"}
    # A real user filename that resembles an internal prefix must stay intact.
    saved = tmp_path / "preview_id_Benchý chosen name.3mf"
    monkeypatch.setattr(base.QtWidgets.QFileDialog, "getSaveFileName", lambda *a: (str(saved), "3MF"))
    window._on_save()
    assert not window.errors
    assert saved.is_file()
    assert window.document_path == str(saved)
    assert window.title.endswith(saved.name)
    saved_parts = read_parts([str(saved)])
    assert len(saved_parts) == len(source_parts)
    for (_, original), (_, result) in zip(source_parts, saved_parts):
        np.testing.assert_array_equal(result.faces, original.faces)
        np.testing.assert_allclose(result.vertices, original.vertices, atol=1e-6, rtol=0)

    monkeypatch.setattr(base.QtWidgets.QFileDialog, "getOpenFileName", lambda *a: (str(saved), "3MF"))
    window._on_open()
    assert not window.errors
    assert window.display_names == names
    assert window.document_path == str(saved)
    assert window.title.endswith(saved.name)
    assert "__PAT_y__" in window.oid_to_name["1"]
    assert window.assignments["1"]["token"] == "y"
    assert window.assignments["1"]["hex"].lower() == "#ffdf00"


def test_failed_or_canceled_io_keeps_document_identity(document_window, tmp_path, monkeypatch):
    window, base = document_window
    window._on_load_example()
    original = (window.document_path, window.base_file, window.title, dict(window.display_names))

    for handler, dialog in ((window._on_open, "getOpenFileName"), (window._on_save, "getSaveFileName")):
        monkeypatch.setattr(base.QtWidgets.QFileDialog, dialog, lambda *a: ("", ""))
        handler()
        assert (window.document_path, window.base_file, window.title, window.display_names) == original

    invalid = tmp_path / "invalid.3mf"
    invalid.write_bytes(b"not a 3MF")
    monkeypatch.setattr(base.QtWidgets.QFileDialog, "getOpenFileName", lambda *a: (str(invalid), "3MF"))
    window._on_open()
    assert len(window.errors) == 1
    assert (window.document_path, window.base_file, window.title, window.display_names) == original

    def fail_save(*args, **kwargs):
        raise OSError("test write failure")

    monkeypatch.setattr(window, "_materialize_current_model", fail_save)
    monkeypatch.setattr(base.QtWidgets.QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "failed.3mf"), "3MF"))
    window._on_save()
    assert len(window.errors) == 2
    assert (window.document_path, window.base_file, window.title, window.display_names) == original


def test_legacy_mixed_metadata_recovers_saved_recipe_and_next_save_updates_both(document_window, tmp_path):
    import xml.etree.ElementTree as ET

    from layerloom.normalize_3mf_import import normalize_3mf_import
    from layerloom.transform_3mf import write_transformed_3mf

    _, base = document_window
    source = Path(__file__).resolve().parents[1] / "examples" / "tutorial_cmy_cubes.3mf"
    normalized = normalize_3mf_import(str(source), cache_dir=str(tmp_path)).normalized_path
    with zipfile.ZipFile(normalized) as archive:
        model_name = base._find_model_xml_name(archive)
        root = base._read_model_root(archive, model_name)
        entries = {name: archive.read(name) for name in archive.namelist() if name != model_name}
    obj = base._all_model_objects(root)[0]
    oid = obj.get("id")
    # Reproduce older saves: the current choice is direct metadata while the
    # normalized import's original choice remains in metadatagroup.
    for key, value in {"stack_token": "mymym", "source_hex": "#db685a"}.items():
        ET.SubElement(obj, f"{{{base.NS['m']}}}metadata", {"name": key}).text = value
    assert base._oid_to_stack_token_map(root)[oid] == "mymym"
    assert base._oid_to_source_hex_map(root)[oid] == "#db685a"

    mixed = tmp_path / "mixed-metadata.3mf"
    with zipfile.ZipFile(mixed, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        archive.writestr(model_name, ET.tostring(root))
    saved = tmp_path / "updated-metadata.3mf"
    write_transformed_3mf(str(mixed), str(saved), np.eye(4),
                          metadata_updates={oid: {"stack_token": "y", "source_hex": "#ffdf00"}})
    with zipfile.ZipFile(saved) as archive:
        result = base._read_model_root(archive, model_name)
    obj = next(obj for obj in base._all_model_objects(result) if obj.get("id") == oid)
    for key, expected in {"stack_token": "y", "source_hex": "#ffdf00"}.items():
        values = [md.text for md in obj.findall(".//m:metadata", base.NS) if md.get("name") == key]
        assert values and set(values) == {expected}


def test_weave_uses_chosen_filename_and_changes_identity_only_on_success(document_window, tmp_path, monkeypatch):
    import shutil

    window, base = document_window
    window._on_load_example()
    window.document_path = str(tmp_path / "My Benchý.3mf")
    window._refresh_status_summary()
    original = (window.document_path, window.base_file, window.title)
    expected = tmp_path / "My Benchý_woven.3mf"
    monkeypatch.setattr(base.QtWidgets.QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path))
    destinations = []

    def cancel_weave(input_path, *, step, output_path):
        destinations.append(output_path)
        return None

    window._run_weave_subprocess = cancel_weave
    window._on_weave()
    assert destinations == [str(expected)]
    assert (window.document_path, window.base_file, window.title) == original

    def produce_output(input_path, *, step, output_path):
        # Only the subprocess is replaced; output naming, success handling,
        # stamping, and the resulting document path are production methods.
        shutil.copyfile(input_path, output_path)
        return output_path

    window._run_weave_subprocess = produce_output
    window._on_weave()
    assert not window.errors
    assert window.document_path == str(expected)
    assert window.title.endswith(expected.name)
