"""Exercise the installed Paint package from outside its source checkout."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def main() -> int:
    import layerloom
    import numpy as np
    import trimesh
    from layerloom.example_assets import EXAMPLE_FILES, find_example_file
    from layerloom.gui_app import _load_current_gui_module
    from layerloom.paint_model import PaintDocument, PaintMesh
    from layerloom.paint_io import load_paint_document, save_paint_document
    from layerloom.paint_export import export_painted_3mf
    from layerloom.paint_export_control import ExportCancelled, ExportMemoryBudgetExceeded

    assert "site-packages" in str(layerloom.__file__), layerloom.__file__
    for filename in EXAMPLE_FILES:
        assert find_example_file(filename), filename
    assert _load_current_gui_module().QtAssignColorsApp
    mesh = trimesh.creation.box(extents=(2, 2, 2))
    mesh.apply_translation([0, 0, 1])
    colors = np.tile([0, 255, 255, 255], (len(mesh.faces), 1)).astype(np.uint8)
    colors[mesh.triangles_center[:, 0] > 0] = [255, 0, 255, 255]
    document = PaintDocument([PaintMesh("cube", "Paint cube", mesh.vertices, mesh.faces, colors)])
    with tempfile.TemporaryDirectory(prefix="LayerLoom Paint café 染 ") as directory:
        project = Path(directory) / "paint café 染.llpaint"
        output = Path(directory) / "geometry café 染.3mf"
        save_paint_document(document, project)
        loaded = load_paint_document(project)
        np.testing.assert_array_equal(loaded.meshes[0].vertices, document.meshes[0].vertices)
        np.testing.assert_array_equal(loaded.meshes[0].face_rgba, document.meshes[0].face_rgba)
        report = export_painted_3mf(loaded, output, layer_height=.5, pitch=.5, mode="woven")
        original = output.read_bytes()
        with zipfile.ZipFile(output) as archive:
            for name in archive.namelist():
                if name.endswith(".model"):
                    root = ET.fromstring(archive.read(name))
                    assert not root.findall(".//{*}colorgroup")
                    assert not any("paint_color" in element.attrib for element in root.iter())
        try:
            export_painted_3mf(loaded, output, cancel_requested=lambda: True)
        except ExportCancelled:
            pass
        else:
            raise AssertionError("A cancelled export was published")
        assert output.read_bytes() == original
        try:
            export_painted_3mf(loaded, output, memory_budget_bytes=1)
        except ExportMemoryBudgetExceeded:
            pass
        else:
            raise AssertionError("Memory budget was ignored")
        assert output.read_bytes() == original
        print(json.dumps({"status": "passed", "package": str(layerloom.__file__),
                          "output_bytes": len(original), "layers": report["layers"],
                          "checks": ["main GUI imports", "packaged examples", "editable paint roundtrip",
                                     "geometry-only woven export", "cancellation", "memory budget"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
