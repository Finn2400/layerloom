"""Compatibility helpers for colored GLB exports from newer ChimeraX releases."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np


_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
_COLOR_UINT_TYPES = {5121: np.uint8, 5123: np.uint16}


def _align4(blob: bytearray) -> None:
    blob.extend(b"\0" * ((-len(blob)) % 4))


def convert_uint_color_accessors_to_float32(input_path: str | Path, output_path: str | Path) -> int:
    """Write a GLB whose integer ``COLOR_0`` accessors are normalized float32.

    ChimeraX 1.5 accepts float32 and uint8 vertex colors but rejects the valid
    normalized uint16 color accessors emitted by current ChimeraX.  Geometry,
    nodes, materials, and color values are otherwise left unchanged.
    """
    try:
        from pygltflib import BufferView, GLTF2
    except ImportError as exc:  # pragma: no cover - exercised by runtime setup
        raise RuntimeError("GLB compatibility conversion requires pygltflib.") from exc

    input_path = Path(input_path).expanduser()
    output_path = Path(output_path).expanduser()
    gltf = GLTF2().load_binary(str(input_path))
    blob = bytearray(gltf.binary_blob() or b"")
    converted: set[int] = set()

    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives or []:
            accessor_index = getattr(primitive.attributes, "COLOR_0", None)
            if accessor_index is None or accessor_index in converted:
                continue
            accessor = gltf.accessors[accessor_index]
            dtype = _COLOR_UINT_TYPES.get(accessor.componentType)
            if dtype is None:
                continue
            if accessor.bufferView is None:
                raise RuntimeError(f"COLOR_0 accessor {accessor_index} has no buffer view.")
            buffer_view = gltf.bufferViews[accessor.bufferView]
            if buffer_view.buffer != 0:
                raise RuntimeError("Only embedded GLB buffer 0 is supported.")
            components = _COMPONENTS.get(accessor.type)
            if components is None:
                raise RuntimeError(f"Unsupported COLOR_0 type: {accessor.type}")

            item_size = np.dtype(dtype).itemsize
            packed_size = components * item_size
            stride = buffer_view.byteStride or packed_size
            offset = (buffer_view.byteOffset or 0) + (accessor.byteOffset or 0)
            values = np.ndarray(
                shape=(accessor.count, components),
                dtype=dtype,
                buffer=blob,
                offset=offset,
                strides=(stride, item_size),
            ).astype(np.float32)
            if getattr(accessor, "normalized", False):
                values /= float(np.iinfo(dtype).max)

            _align4(blob)
            new_offset = len(blob)
            packed = values.tobytes(order="C")
            blob.extend(packed)
            gltf.bufferViews.append(
                BufferView(buffer=0, byteOffset=new_offset, byteLength=len(packed), target=buffer_view.target)
            )
            accessor.bufferView = len(gltf.bufferViews) - 1
            accessor.byteOffset = 0
            accessor.componentType = 5126
            accessor.normalized = False
            converted.add(accessor_index)

    if gltf.buffers:
        gltf.buffers[0].byteLength = len(blob)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gltf.set_binary_blob(bytes(blob))
    gltf.save_binary(str(output_path))
    return len(converted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert uint16 GLB vertex colors to float32 for ChimeraX 1.5.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)
    count = convert_uint_color_accessors_to_float32(args.input, args.output)
    print(f"Converted {count} COLOR_0 accessor(s): {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
