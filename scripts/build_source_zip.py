"""Build the beginner source ZIP from the same curated source as the sdist."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


def build(distributions: Path) -> Path:
    sources = sorted(distributions.glob("layerloom-*.tar.gz"))
    if len(sources) != 1:
        raise RuntimeError("Build the ZIP from a directory with exactly one LayerLoom source archive.")
    source = sources[0]
    root = source.name.removesuffix(".tar.gz")
    output = distributions / (root.replace("layerloom-", "LayerLoom-", 1) + "-source.zip")
    pending = output.with_suffix(".zip.tmp")
    try:
        with tarfile.open(source) as archive, zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != root:
                    raise RuntimeError(f"Unsafe source archive path: {member.name}")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise RuntimeError(f"Source ZIP requires regular files: {member.name}")
                stream = archive.extractfile(member)
                assert stream is not None
                info = zipfile.ZipInfo(member.name)
                info.create_system = 3
                info.external_attr = (0o100000 | member.mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                target.writestr(info, stream.read())
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    print(f"Created {output.name} ({output.stat().st_size:,} bytes)")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distributions", type=Path)
    build(parser.parse_args().distributions)
