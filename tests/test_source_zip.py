"""Ensure the novice download is intact and cannot leak out of its folder."""
import importlib.util
import io
from pathlib import Path
import tarfile
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("build_source_zip", Path(__file__).parents[1] / "scripts/build_source_zip.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def source_archive(tmp_path, name="layerloom-1.0/Launch LayerLoom.command", link=False):
    with tarfile.open(tmp_path / "layerloom-1.0.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.mode = 0o755
        payload = b"#!/bin/bash\n"
        member.size = len(payload)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = "/outside"
            member.size = 0
        archive.addfile(member, io.BytesIO(payload))


def test_source_zip_preserves_bytes_and_executable_mode(tmp_path):
    source_archive(tmp_path)
    output = builder.build(tmp_path)
    with zipfile.ZipFile(output) as archive:
        info = archive.infolist()[0]
        assert info.filename == "layerloom-1.0/Launch LayerLoom.command"
        assert archive.read(info) == b"#!/bin/bash\n"
        assert (info.external_attr >> 16) & 0o777 == 0o755


@pytest.mark.parametrize("name,link", [("../outside", False), ("/outside", False),
                                       ("other-root/file", False), ("layerloom-1.0/link", True)])
def test_source_zip_rejects_unsafe_entries_and_preserves_previous_download(tmp_path, name, link):
    output = tmp_path / "LayerLoom-1.0-source.zip"
    output.write_bytes(b"previous good archive")
    source_archive(tmp_path, name, link)
    with pytest.raises(RuntimeError):
        builder.build(tmp_path)
    assert output.read_bytes() == b"previous good archive"
    assert not list(tmp_path.glob("*.tmp"))
