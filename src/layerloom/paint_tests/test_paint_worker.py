"""Geometry-export dispatch checks without opening a native GUI window."""
import pytest

pytest.importorskip("pyvistaqt")
from layerloom.paint_gui import ExportWorker
from layerloom.paint_examples import example_document


@pytest.mark.parametrize("mode", ["woven", "direct", "source"])
def test_every_color_mode_uses_material_geometry_export(monkeypatch, tmp_path, mode):
    from layerloom import paint_export
    seen = {}
    def geometry(document, output, *, progress=None, stage=None, cancel_requested=None, **settings):
        seen.update(document=document, output=output, settings=settings)
        assert not cancel_requested()
        stage("Sampling layers…")
        progress(1, 2)
        return {"output_3mf": str(output)}
    monkeypatch.setattr(paint_export, "export_painted_3mf", geometry)
    document = example_document("cube")
    settings = {"mode": mode, "palette_name": "Normal", "layer_height": .2, "pitch": .5}
    worker = ExportWorker(document, tmp_path / "geometry.3mf", settings)
    progress, results, failures, finished = [], [], [], []
    worker.progress.connect(lambda done, total: progress.append((done, total)))
    worker.succeeded.connect(results.append)
    worker.failed.connect(failures.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()
    assert seen["document"] is document
    assert seen["settings"] == settings
    assert progress == [(1, 2)] and not failures and finished == [True]
    assert results[0]["export_format"] == "volumes"


def test_geometry_worker_reports_conversion_errors_and_finishes(monkeypatch, tmp_path):
    from layerloom import paint_export
    def geometry(*args, **kwargs):
        raise ValueError("Closed material geometry is required")
    monkeypatch.setattr(paint_export, "export_painted_3mf", geometry)
    worker = ExportWorker(example_document("cube"), tmp_path / "invalid.3mf", {"mode": "woven"})
    failures, results, finished = [], [], []
    worker.failed.connect(failures.append)
    worker.succeeded.connect(results.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()
    assert failures == ["Closed material geometry is required"]
    assert not results and finished == [True]


def test_geometry_worker_retains_recipe_overrides(monkeypatch, tmp_path):
    from layerloom import paint_export
    seen = {}
    def geometry(document, output, **settings):
        seen.update(settings)
        return {"output_3mf": str(output)}
    monkeypatch.setattr(paint_export, "export_painted_3mf", geometry)
    worker = ExportWorker(example_document("cube"), tmp_path / "woven.3mf", {
        "mode": "woven", "recipe_overrides": {"#00FFFF": "cy"},
    })
    results = []
    worker.succeeded.connect(results.append)
    worker.run()
    assert seen["recipe_overrides"] == {"#00FFFF": "cy"}
    assert results[0]["export_format"] == "volumes"


def test_worker_cancellation_is_distinct_from_failure_and_always_finishes(monkeypatch, tmp_path):
    from threading import Event
    from layerloom import paint_export
    from layerloom.paint_export_control import ExportCancelled
    stopped = Event()
    stopped.set()
    def geometry(*args, cancel_requested, **kwargs):
        assert cancel_requested()
        raise ExportCancelled()
    monkeypatch.setattr(paint_export, "export_painted_3mf", geometry)
    worker = ExportWorker(example_document("cube"), tmp_path / "stopped.3mf", {}, stopped)
    cancelled, failed, succeeded, finished = [], [], [], []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.failed.connect(failed.append)
    worker.succeeded.connect(succeeded.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()
    assert cancelled == [True] and finished == [True]
    assert not failed and not succeeded


def test_export_snapshot_skips_unused_metadata_and_owns_all_arrays():
    import numpy as np
    document = example_document("cube")
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise AssertionError("Project metadata is not used by export")
    document.metadata["unused_import_details"] = Uncopyable()
    snapshot = document.copy(include_metadata=False)
    assert snapshot.metadata == {} and document.metadata
    for original, copied in zip(document.meshes, snapshot.meshes):
        for name in ("vertices", "faces", "face_rgba", "source_rgba"):
            np.testing.assert_array_equal(getattr(copied, name), getattr(original, name))
            assert not np.shares_memory(getattr(copied, name), getattr(original, name))


def test_worker_memory_error_has_actionable_message(monkeypatch, tmp_path):
    from layerloom import paint_export
    def geometry(*args, **kwargs):
        raise MemoryError()
    monkeypatch.setattr(paint_export, "export_painted_3mf", geometry)
    worker = ExportWorker(example_document("cube"), tmp_path / "too-large.3mf", {})
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert len(failures) == 1 and "memory" in failures[0].lower()
