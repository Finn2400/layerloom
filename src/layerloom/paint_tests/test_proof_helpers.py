"""Proof utilities must also work on the supported Python 3.10 runtime."""
import hashlib

import pytest

pytest.importorskip("pyvistaqt")

from layerloom.paint_tests.gui_proof import digest


@pytest.mark.parametrize("contents", [b"", b"paint\x00\xff" * 300_000])
def test_file_digest_streams_without_python_311_api(tmp_path, monkeypatch, contents):
    monkeypatch.delattr(hashlib, "file_digest", raising=False)
    source = tmp_path / "paint colors \u03b2.llpaint"
    source.write_bytes(contents)
    assert digest(source) == hashlib.sha256(contents).hexdigest()
