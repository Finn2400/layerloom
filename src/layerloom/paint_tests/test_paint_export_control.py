"""Cancellation and resource bounds protect previous printable exports."""
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest
import trimesh

from layerloom import paint_export
from layerloom.paint_export_control import (
    DEFAULT_MEMORY_BUDGET_BYTES, ExportCancelled, ExportControl,
    ExportMemoryBudgetExceeded, estimate_export_resources,
)
from layerloom.paint_model import PaintDocument, PaintMesh


def document():
    mesh = trimesh.creation.box((2, 2, 2))
    colors = np.tile([58, 200, 220, 255], (len(mesh.faces), 1))
    return PaintDocument([PaintMesh("cube", "Cube", mesh.vertices, mesh.faces, colors)])


def existing_output(tmp_path):
    output = tmp_path / "painted dragon – 彩色.3mf"
    output.write_bytes(b"previous printable geometry")
    report = output.with_suffix(".paint-report.json")
    report.write_text("previous audit", encoding="utf-8")
    return output, report


def unchanged(output, report):
    assert output.read_bytes() == b"previous printable geometry"
    assert report.read_text(encoding="utf-8") == "previous audit"
    assert not list(output.parent.glob("layerloom_paint_*"))


def export(doc, output, **kwargs):
    return paint_export.export_painted_3mf(doc, output, pitch=1, layer_height=1, **kwargs)


def test_estimate_is_monotonic_and_does_not_copy_geometry(monkeypatch):
    doc = document()
    monkeypatch.setattr(doc, "copy", lambda: pytest.fail("Preflight must not copy the model"))
    coarse = estimate_export_resources(doc, pitch=1, layer_height=1)
    fine = estimate_export_resources(doc, pitch=.5, layer_height=.5)
    assert fine.estimated_cells > coarse.estimated_cells
    assert fine.estimated_peak_bytes > coarse.estimated_peak_bytes
    assert fine.snapshot_bytes == sum(array.nbytes for mesh in doc.meshes
        for array in (mesh.vertices, mesh.faces, mesh.face_rgba, mesh.source_rgba))
    assert fine.estimated_peak_bytes < DEFAULT_MEMORY_BUDGET_BYTES
    assert fine.to_dict()["source_triangles"] == 12


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, float("inf"), None])
def test_budget_requires_positive_integer(budget):
    with pytest.raises(ValueError, match="positive integer"):
        estimate_export_resources(document()).check_budget(budget)


def test_tiny_sampling_fails_before_mesh_processing(tmp_path, monkeypatch):
    monkeypatch.setattr(paint_export, "_source_geometry", lambda *a, **k: pytest.fail("No geometry work"))
    with pytest.raises(ValueError, match="grid cells"):
        paint_export.export_painted_3mf(document(), tmp_path / "bad.3mf", layer_height=1e-300,
                                        pitch=1e-300, precision=1e-305, interface_offset=0)


def test_budget_rejection_precedes_palette_and_mesh_work(tmp_path, monkeypatch):
    output, report = existing_output(tmp_path)
    monkeypatch.setattr(paint_export, "assign_palette", lambda *a, **k: pytest.fail("No palette allocation"))
    with pytest.raises(ExportMemoryBudgetExceeded, match="working memory"):
        export(document(), output, memory_budget_bytes=1)
    unchanged(output, report)


def test_pre_requested_cancellation_does_no_geometry_work(tmp_path, monkeypatch):
    output, report = existing_output(tmp_path)
    monkeypatch.setattr(paint_export, "estimate_export_resources", lambda *a, **k: pytest.fail("No scan"))
    with pytest.raises(ExportCancelled):
        export(document(), output, cancel_requested=lambda: True)
    unchanged(output, report)


@pytest.mark.parametrize("cancel_at", ["Preparing model", "Sampling layers", "Finalizing material geometry", "Writing 3MF"])
def test_stage_cancellation_preserves_previous_files(tmp_path, cancel_at):
    output, report = existing_output(tmp_path)
    stop = Event()
    with pytest.raises(ExportCancelled):
        export(document(), output, cancel_requested=stop.is_set,
               stage=lambda name: stop.set() if name == cancel_at else None)
    unchanged(output, report)


def test_mid_layer_cancellation_stops_before_next_layer(tmp_path):
    output, report = existing_output(tmp_path)
    stop = Event()
    progress = []
    def on_progress(done, total):
        progress.append((done, total))
        stop.set()
    with pytest.raises(ExportCancelled):
        export(document(), output, cancel_requested=stop.is_set, progress=on_progress)
    assert progress == [(1, 2)]
    unchanged(output, report)


def test_cancellation_during_finalization_is_observed(tmp_path, monkeypatch):
    output, report = existing_output(tmp_path)
    stop = Event()
    finalize = paint_export._finalize_filament
    def cancel_after_finalize(*args, **kwargs):
        result = finalize(*args, **kwargs)
        stop.set()
        return result
    monkeypatch.setattr(paint_export, "_finalize_filament", cancel_after_finalize)
    with pytest.raises(ExportCancelled):
        export(document(), output, cancel_requested=stop.is_set)
    unchanged(output, report)


@pytest.mark.parametrize("point", ["geometry_writer", "staged_report"])
def test_cancellation_during_staging_cleans_temporary_files(tmp_path, monkeypatch, point):
    output, report = existing_output(tmp_path)
    stop = Event()
    if point == "geometry_writer":
        writer = paint_export.write_orca_multipart_3mf
        def cancel_after_writer(*args, **kwargs):
            result = writer(*args, **kwargs)
            stop.set()
            return result
        monkeypatch.setattr(paint_export, "write_orca_multipart_3mf", cancel_after_writer)
    else:
        writer = Path.write_text
        def cancel_after_report(path, *args, **kwargs):
            result = writer(path, *args, **kwargs)
            if path.name == report.name:
                stop.set()
            return result
        monkeypatch.setattr(Path, "write_text", cancel_after_report)
    with pytest.raises(ExportCancelled):
        export(document(), output, cancel_requested=stop.is_set)
    unchanged(output, report)


def test_cancellation_after_geometry_publication_is_success(tmp_path, monkeypatch):
    output, report_path = existing_output(tmp_path)
    stop = Event()
    atomic_replace = paint_export.os.replace
    def publish_then_cancel(source, destination):
        result = atomic_replace(source, destination)
        if Path(destination) == output:
            stop.set()
        return result
    monkeypatch.setattr(paint_export.os, "replace", publish_then_cancel)
    result = export(document(), output, cancel_requested=stop.is_set)
    assert stop.is_set() and zipfile.is_zipfile(output)
    assert result["report_written"] is True
    assert result["resources"]["peak_estimated_bytes"] <= DEFAULT_MEMORY_BUDGET_BYTES
    assert report_path.read_text() != "previous audit"


def test_runtime_retained_triangle_budget_preserves_files(tmp_path, monkeypatch):
    output, report = existing_output(tmp_path)
    enormous = SimpleNamespace(status=lambda: paint_export.mf.Error.NoError, num_tri=lambda: 10_000_000)
    monkeypatch.setattr(paint_export, "_region_solid", lambda *a, **k: enormous)
    with pytest.raises(ExportMemoryBudgetExceeded, match="retained layer geometry"):
        export(document(), output)
    unchanged(output, report)


def test_nearest_search_budget_bounds_candidates_before_vector_allocations(monkeypatch):
    doc = document()
    estimate = estimate_export_resources(doc)
    control = ExportControl(estimate, estimate.estimated_peak_bytes + 1_000_000)
    batch_size = control.nearest_batch_size(1000)
    assert 1 <= batch_size < 4096
    mesh = trimesh.creation.box()
    monkeypatch.setattr(trimesh.proximity, "nearby_faces", lambda *args: [np.arange(100_000)])
    monkeypatch.setattr(trimesh.triangles, "closest_point", lambda *a, **k: pytest.fail("Candidates must be bounded first"))
    with pytest.raises(ExportMemoryBudgetExceeded, match="nearest-surface candidates"):
        paint_export._nearest_faces(mesh, np.zeros((1, 3)), control=control)


def test_unused_vertex_bounds_do_not_add_empty_or_inverted_export_layers(tmp_path):
    doc = document()
    doc.meshes[0].vertices = np.vstack((doc.meshes[0].vertices, [[0, 0, 20]]))
    result = export(doc, tmp_path / "unused.3mf")
    assert result["resources"]["layers"] == 21
    assert result["layers"] == 2
    assert result["output_volume_mm3"] == pytest.approx(8)
