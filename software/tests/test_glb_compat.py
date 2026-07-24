from __future__ import annotations

import numpy as np
import pytest


def _write_uint16_color_glb(path):
    pygltflib = pytest.importorskip("pygltflib")
    positions = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    colors = np.asarray([[0, 65535, 0, 65535], [65535, 0, 0, 65535], [0, 0, 65535, 65535]], dtype=np.uint16)
    indices = np.asarray([0, 1, 2], dtype=np.uint16)
    chunks = [positions.tobytes(), colors.tobytes(), indices.tobytes()]
    offsets = [0]
    for chunk in chunks[:-1]:
        offsets.append(offsets[-1] + len(chunk))
    blob = b"".join(chunks)
    gltf = pygltflib.GLTF2(
        asset=pygltflib.Asset(version="2.0"),
        scenes=[pygltflib.Scene(nodes=[0])],
        scene=0,
        nodes=[pygltflib.Node(mesh=0)],
        meshes=[pygltflib.Mesh(primitives=[pygltflib.Primitive(
            attributes=pygltflib.Attributes(POSITION=0, COLOR_0=1), indices=2
        )])],
        buffers=[pygltflib.Buffer(byteLength=len(blob))],
        bufferViews=[
            pygltflib.BufferView(buffer=0, byteOffset=offsets[0], byteLength=len(chunks[0])),
            pygltflib.BufferView(buffer=0, byteOffset=offsets[1], byteLength=len(chunks[1])),
            pygltflib.BufferView(buffer=0, byteOffset=offsets[2], byteLength=len(chunks[2])),
        ],
        accessors=[
            pygltflib.Accessor(bufferView=0, componentType=5126, count=3, type="VEC3"),
            pygltflib.Accessor(bufferView=1, componentType=5123, normalized=True, count=3, type="VEC4"),
            pygltflib.Accessor(bufferView=2, componentType=5123, count=3, type="SCALAR"),
        ],
    )
    gltf.set_binary_blob(blob)
    gltf.save_binary(str(path))


def test_uint16_glb_colors_are_rewritten_as_float32(tmp_path):
    from layerloom.glb_compat import convert_uint_color_accessors_to_float32
    from layerloom.glb_import import _load_gltf, _read_accessor
    from pygltflib import GLTF2

    source = tmp_path / "source.glb"
    output = tmp_path / "compat.glb"
    _write_uint16_color_glb(source)

    assert convert_uint_color_accessors_to_float32(source, output) == 1
    gltf = GLTF2().load_binary(str(output))
    accessor = gltf.accessors[gltf.meshes[0].primitives[0].attributes.COLOR_0]
    assert accessor.componentType == 5126
    assert accessor.normalized is False
    loaded = _load_gltf(str(output))
    np.testing.assert_allclose(_read_accessor(loaded, 1), [[0, 1, 0, 1], [1, 0, 0, 1], [0, 0, 1, 1]])
