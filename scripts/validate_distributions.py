"""Check release archives for runnable Paint code and reproducible source tests."""
from __future__ import annotations

import argparse
from pathlib import Path
import tarfile
import zipfile


def validate(distributions: Path) -> None:
    wheels = sorted(distributions.glob("layerloom-*.whl"))
    sources = sorted(distributions.glob("layerloom-*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise RuntimeError("Validate a directory with exactly one LayerLoom wheel and source archive.")
    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names = set(archive.namelist())
    runtime = {
        "layerloom/paint_gui.py", "layerloom/paint_main_ui.py", "layerloom/paint_io.py",
        "layerloom/paint_export.py", "layerloom/paint_export_control.py", "layerloom/paint_nearest.py",
        "layerloom/paint_workspace.py", "layerloom/paint_workspace_model.py",
        "layerloom/paint_selection.py", "layerloom/paint_model.py", "layerloom/paint_repair.py",
        "layerloom/paint_sub_layer.py", "layerloom/paint_examples.py",
        "layerloom/assets/benchy_ascii_with_text.txt",
        "layerloom/examples/tutorial_cmy_cubes.3mf",
        "layerloom/examples/tutorial_cmy_benchy_cutup.3mf",
        "layerloom/palettes/normal_palette.json", "layerloom/palettes/simple_palette.json",
        "layerloom/palettes/full_palette.json", "layerloom/palettes/calibrated_cmy_normal_core065_palette.json",
        "supporting_scripts/__init__.py", "supporting_scripts/glb_split_by_vertexcolor_v3_3mf.py",
        "supporting_scripts/3mf_meshlab_repair_3mf.py",
    }
    missing = runtime - wheel_names
    if missing:
        raise RuntimeError(f"Wheel is missing runtime files: {sorted(missing)}")
    with tarfile.open(sources[0]) as archive:
        source_names = {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}
    source_root = Path(__file__).resolve().parents[1]
    source_required = {
        "src/layerloom/PAINT_PROTOTYPE.md", "src/layerloom/PROTEIN_PRINT_VISUALIZATION_STANDARD.md",
        "scripts/chimerax_prepare_layerloom.py", "scripts/chimerax_make_1e27_pmhc_presentation.py",
        "scripts/installed_paint_smoke.py", "scripts/validate_distributions.py",
        "scripts/bootstrap.py", "scripts/build_macos.py", "scripts/validate_macos.py",
        "scripts/macos_validation_matrix.py",
        "packaging/macos/bundle_config.py", "packaging/macos/smoke_gui.py",
    }
    for directory in ("tests", "src/layerloom/paint_tests"):
        source_required.update(str(path.relative_to(source_root)).replace("\\", "/")
                               for path in (source_root / directory).glob("*.py"))
    source_required.update("src/" + name for name in runtime)
    missing = source_required - source_names
    if missing:
        raise RuntimeError(f"Source archive is missing runtime/test files: {sorted(missing)}")
    for name in wheel_names | source_names:
        if any(part.startswith(("submission_", "manuscript_", "LayerLoomPhotos")) for part in name.split("/")):
            raise RuntimeError(f"Research assets must not ship in release archives: {name}")
    print(f"Validated runtime and source-test contents: {wheels[0].name}, {sources[0].name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distributions", type=Path)
    validate(parser.parse_args(argv).distributions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
