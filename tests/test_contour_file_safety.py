"""Export failures must preserve existing files and clean up closed temporaries."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import trimesh

from layerloom import contour_repair as cli
from layerloom import export


def _source(tmp_path):
    source = tmp_path / "source.stl"
    trimesh.creation.box().export(source)
    return source


def test_json_serialization_failure_closes_before_cleanup(tmp_path, monkeypatch):
    destination = tmp_path / "report.json"
    destination.write_text("previous report", encoding="utf-8")
    opened = []
    real_temporary = cli.tempfile.NamedTemporaryFile
    real_unlink = Path.unlink

    def capture_stream(*args, **kwargs):
        stream = real_temporary(*args, **kwargs)
        opened.append(stream)
        return stream

    def windows_unlink(path, *args, **kwargs):
        # Enforce Windows' restriction even when this test runs on Unix.
        assert all(stream.closed for stream in opened if Path(stream.name) == path)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cli.tempfile, "NamedTemporaryFile", capture_stream)
    monkeypatch.setattr(Path, "unlink", windows_unlink)
    with pytest.raises(ValueError, match="Out of range float"):
        cli._atomic_json(destination, {"invalid": float("nan")})
    assert destination.read_text(encoding="utf-8") == "previous report"
    assert set(tmp_path.iterdir()) == {destination}


def test_json_replace_failure_preserves_previous_report_and_cleans_temp(tmp_path, monkeypatch):
    destination = tmp_path / "report.json"
    destination.write_bytes(b"previous report")

    def fail_replace(*args):
        raise PermissionError("destination is open")

    monkeypatch.setattr(cli.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="destination is open"):
        cli._atomic_json(destination, {"status": "ok"})
    assert destination.read_bytes() == b"previous report"
    assert set(tmp_path.iterdir()) == {destination}


@pytest.mark.parametrize("pair", [(0, 1), (0, 2), (1, 2)])
def test_existing_file_aliases_are_rejected_before_writes(tmp_path, pair):
    paths = [tmp_path / "input.3mf", tmp_path / "output.3mf", tmp_path / "report.json"]
    paths[pair[0]].write_bytes(b"must survive")
    try:
        os.link(paths[pair[0]], paths[pair[1]])
    except OSError as exc:
        pytest.skip(f"Filesystem does not support hard links: {exc}")
    with pytest.raises(ValueError, match="paths must be different"):
        cli.reconstruct_file(paths[0], paths[1], report_path=paths[2], layer_height=1)
    assert all(paths[i].read_bytes() == b"must survive" for i in pair)
    assert not paths[next(i for i in range(3) if i not in pair)].exists()


def test_case_alias_is_rejected_on_case_insensitive_filesystem(tmp_path):
    source = tmp_path / "source.3mf"
    source.write_bytes(b"must survive")
    alias = tmp_path / "SOURCE.3mf"
    if not alias.exists():
        pytest.skip("Filesystem is case-sensitive")
    with pytest.raises(ValueError, match="paths must be different"):
        cli.reconstruct_file(source, alias, layer_height=1)
    assert source.read_bytes() == b"must survive"


@pytest.mark.parametrize("failure_stage", ["package", "hash", "replace"])
def test_export_failure_preserves_previous_output_and_cleans_temp(tmp_path, monkeypatch, failure_stage):
    source = _source(tmp_path)
    output = tmp_path / "result.3mf"
    output.write_bytes(b"previous output")

    def fail(*args, **kwargs):
        raise OSError(f"injected {failure_stage} failure")

    if failure_stage == "package":
        monkeypatch.setattr(cli, "_package_native_volumes", fail)
    elif failure_stage == "hash":
        monkeypatch.setattr(cli, "hashlib", SimpleNamespace(sha256=fail))
    else:
        real_replace = cli.os.replace

        def fail_output_replace(source, destination):
            if Path(destination) == output:
                fail()
            return real_replace(source, destination)

        monkeypatch.setattr(cli.os, "replace", fail_output_replace)
    report = cli.reconstruct_file(source, output, layer_height=1)
    assert report["status"] == "failed"
    assert any(f"injected {failure_stage} failure" in error for error in report["errors"])
    assert "output_sha256" not in report
    assert output.read_bytes() == b"previous output"
    report_path = output.with_suffix(".report.json")
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert set(tmp_path.iterdir()) == {source, output, report_path}


@pytest.mark.parametrize("bounds", [None, [[0, 0, float("nan")], [1, 1, 1]],
                                    [[0, 0, 0], [1, 1, float("inf")]]])
def test_invalid_mesh_bounds_still_produce_finite_failure_report(tmp_path, monkeypatch, bounds):
    source = _source(tmp_path)
    output = tmp_path / "result.3mf"
    monkeypatch.setattr(cli, "read_parts", lambda _: [("bad mesh", SimpleNamespace(bounds=bounds))])
    report = cli.reconstruct_file(source, output, layer_height=1)
    assert report["status"] == "failed"
    assert any("finite, nonempty mesh bounds" in error for error in report["errors"])
    assert "z_origin_mm" not in report
    assert not output.exists()
    assert json.loads(output.with_suffix(".report.json").read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("argument", ["layer_height", "z_gap", "precision", "max_gap",
                                      "contour_tolerance", "seam_tolerance"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_arguments_never_modify_existing_files(tmp_path, argument, value):
    source = _source(tmp_path)
    output = tmp_path / "result.3mf"
    report_path = output.with_suffix(".report.json")
    output.write_bytes(b"previous output")
    report_path.write_bytes(b"previous report")
    arguments = {"layer_height": 1, argument: value}
    with pytest.raises(ValueError, match="finite"):
        cli.reconstruct_file(source, output, **arguments)
    assert output.read_bytes() == b"previous output"
    assert report_path.read_bytes() == b"previous report"
    assert set(tmp_path.iterdir()) == {source, output, report_path}


def test_unicode_paths_export_and_hash_match_published_output(tmp_path):
    directory = tmp_path / "pièce 模型"
    directory.mkdir()
    source = _source(directory)
    output = directory / "réparé.3mf"
    report = cli.reconstruct_file(source, output, layer_height=1)
    assert report["status"] == "ok", report["errors"]
    assert report["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(output.with_suffix(".report.json").read_text(encoding="utf-8")) == report


def test_unicode_paths_reconstruct_with_legacy_windows_stdout(tmp_path, monkeypatch):
    directory = tmp_path / "pièce 模型"
    directory.mkdir()
    source = _source(directory)
    output = directory / "réparé.3mf"
    captured = io.BytesIO()
    with io.TextIOWrapper(captured, encoding="cp1252", write_through=True) as stream:
        monkeypatch.setattr(sys, "stdout", stream)
        report = cli.reconstruct_file(source, output, layer_height=1)
        assert report["status"] == "ok", report["errors"]
        assert b"[export] wrote" in captured.getvalue()
        assert stream.encoding == "cp1252"
    assert report["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert all(mesh.is_volume for _, mesh in cli._read_native_bands(output))
    assert json.loads(output.with_suffix(".report.json").read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_safe_assembly_status_respects_stdout_encoding(tmp_path, monkeypatch, encoding):
    output = tmp_path / "pièce 模型.3mf"
    captured = io.BytesIO()
    with io.TextIOWrapper(captured, encoding=encoding, write_through=True) as stream:
        monkeypatch.setattr(sys, "stdout", stream)
        stats = export.write_safe_assembly_3mf(
            {"c": [("cube", trimesh.creation.box())]}, str(output))
        text = captured.getvalue().decode(encoding)
        assert "[export] wrote safe assembly 3MF" in text
        expected_path = str(output).encode(encoding, errors="backslashreplace").decode(encoding)
        assert expected_path in text
        assert stream.encoding == encoding
    assert stats.leaf_mesh_count == 1
    assert output.exists()


def test_export_status_does_not_swallow_real_stream_errors(monkeypatch):
    def fail_write(text):
        raise OSError("broken output stream")

    monkeypatch.setattr(sys, "stdout", SimpleNamespace(encoding="cp1252", write=fail_write))
    with pytest.raises(OSError, match="broken output stream"):
        export._print_status("status → 模型")
