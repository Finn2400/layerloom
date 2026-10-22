"""Export dispatch checks without opening a native GUI window."""
import pytest

pytest.importorskip("pyvistaqt")
from layerloom.paint_gui import ExportWorker
from layerloom.paint_examples import example_document


def test_native_worker_uses_source_export_without_volume_settings(monkeypatch, tmp_path):
    from layerloom import paint_export
    seen = {}
    def native(document, output, *, progress=None):
        seen["document"] = document
        progress(1, 2)
        return {"output_3mf": str(output)}
    monkeypatch.setattr(paint_export, "export_native_painted_3mf", native)
    document = example_document("cube")
    worker = ExportWorker(document, tmp_path / "native.3mf", {
        "export_format": "native", "mode": "source", "palette_name": "Normal",
        "layer_height": .2, "pitch": .5,
    })
    progress, results, failures = [], [], []
    worker.progress.connect(lambda done, total: progress.append((done, total)))
    worker.succeeded.connect(results.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert seen["document"] is document
    assert progress == [(1, 2)] and not failures
    assert results[0]["export_format"] == "native"


def test_native_worker_rejects_mixed_recipe_mode(monkeypatch, tmp_path):
    worker = ExportWorker(example_document("cube"), tmp_path / "bad.3mf", {
        "export_format": "native", "mode": "woven",
    })
    failures, results = [], []
    worker.failed.connect(failures.append)
    worker.succeeded.connect(results.append)
    worker.run()
    assert "requires original paint colors" in failures[0]
    assert not results


def test_volume_worker_retains_recipe_overrides(monkeypatch, tmp_path):
    from layerloom import paint_export
    seen = {}
    def volumes(document, output, **settings):
        seen.update(settings)
        return {"output_3mf": str(output)}
    monkeypatch.setattr(paint_export, "export_painted_3mf", volumes)
    worker = ExportWorker(example_document("cube"), tmp_path / "woven.3mf", {
        "export_format": "volumes", "mode": "woven", "recipe_overrides": {"#00FFFF": "cy"},
    })
    results = []
    worker.succeeded.connect(results.append)
    worker.run()
    assert "export_format" not in seen
    assert seen["recipe_overrides"] == {"#00FFFF": "cy"}
    assert results[0]["export_format"] == "volumes"
