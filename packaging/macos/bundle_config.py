"""Auditable resource inventory shared by the macOS specs and their tests."""
from __future__ import annotations

import ast
from pathlib import Path

GUI_FILES = tuple(f"3mf_gui_v{version}.py" for version in (58, 60, 61, 62, 65))
EXAMPLES = ("tutorial_cmy_cubes.3mf", "tutorial_cmy_benchy_cutup.3mf")
PALETTES = (
    "simple_palette.json", "normal_palette.json", "full_palette.json",
    "calibrated_cmy_normal_core065_palette.json",
)
EXCLUDES = [
    "tkinter", "_tkinter", "pymeshlab", "pytest", "IPython", "jupyter",
    "PySide2", "PySide6", "PyQt6", "layerloom.legacy", "layerloom.experimental_viewer",
]
# qtpy's broad hook otherwise collects every Qt module, including SQL plugins
# linked to a developer's Homebrew PostgreSQL and unused QtQuick/QML runtimes.
EXCLUDES += [f"PyQt5.{name}" for name in (
    "QtBluetooth", "QtDBus", "QtDesigner", "QtHelp", "QtLocation",
    "QtMultimedia", "QtMultimediaWidgets", "QtNetwork", "QtNfc", "QtPositioning",
    "QtQml", "QtQuick", "QtQuick3D", "QtQuickWidgets", "QtRemoteObjects",
    "QtSensors", "QtSerialPort", "QtSql", "QtTest", "QtTextToSpeech",
    "QtWebChannel", "QtWebSockets", "QtX11Extras", "QtXml", "QtXmlPatterns",
)]


def resource_files(root: Path) -> list[tuple[str, str]]:
    """Never recursively collect the source tree: it contains research assets."""
    package = root / "src" / "layerloom"
    result = [(str(package / name), "layerloom") for name in GUI_FILES]
    result += [(str(package / "examples" / name), "layerloom/examples") for name in EXAMPLES]
    result += [(str(package / "palettes" / name), "layerloom/palettes") for name in PALETTES]
    for source, _ in result:
        if not Path(source).is_file():
            raise FileNotFoundError(f"Required desktop resource is missing: {source}")
    return result


def gui_hidden_imports(root: Path) -> list[str]:
    """The numbered GUI files are loaded by filename, outside static analysis."""
    names = {"pyvista", "pyvistaqt", "qtpy", "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets"}
    for filename in GUI_FILES:
        tree = ast.parse((root / "src/layerloom" / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module)
    # The old direct-script fallback imports have package-qualified equivalents.
    names = {name for name in names if not (root / "src/layerloom" / (name + ".py")).is_file()}
    return sorted(name for name in names if name.split(".", 1)[0] not in EXCLUDES)


def native_dependencies():
    """lib3mf locates its own dylib beside its Python package at runtime."""
    from PyInstaller.utils.hooks import collect_dynamic_libs, copy_metadata
    binaries = []
    for package in ("lib3mf", "manifold3d", "rtree", "shapely"):
        binaries += collect_dynamic_libs(package, search_patterns=["*.dylib", "*.so", "lib*.so.*"])
    datas = []
    for package in ("layerloom", "pyvista", "pyvistaqt", "trimesh", "lib3mf"):
        datas += copy_metadata(package)
    return datas, binaries
