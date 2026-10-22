"""Run the existing controlled worker with current or frozen contour modules.

Imports and implementation fingerprints are outside all measured intervals.
The frozen modules are installed before importing the worker, so its warmup,
source reconstruction, partition, extrusion and native checks use that version.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODULES = ("planar_bands", "planar_materials", "contour_repair")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--height", required=True, type=float)
    parser.add_argument("--method", required=True)
    parser.add_argument("--frozen-directory", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    import layerloom
    directory = (args.frozen_directory.resolve() if args.frozen_directory else
                 ROOT / "src/layerloom")
    if args.frozen_directory:
        if args.method != "contour_optimized":
            parser.error("Frozen modules are only intended for the previous 2D arm")
        for name in MODULES:
            qualified = "layerloom." + name
            spec = importlib.util.spec_from_file_location(qualified, directory / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[qualified] = module
            setattr(layerloom, name, module)
            spec.loader.exec_module(module)
    import controlled_worker as worker
    bindings = {}
    for name in ("section_profile", "reconstruct_layer_profiles", "extrude_profile",
                 "partition_materials", "_float32_mesh_checks"):
        function = getattr(worker, name)
        path = Path(inspect.getsourcefile(function)).resolve()
        if path.parent != directory:
            raise ValueError(f"Wrong implementation bound for {name}: {path}")
        bindings[name] = str(path)
    result = worker.run_case(args.case, args.height, args.method, audit=False)
    result["implementation"] = {
        "frozen": args.frozen_directory is not None,
        "bindings": bindings,
        "source_sha256": {name + ".py": hashlib.sha256((directory / (name + ".py")).read_bytes()).hexdigest()
                          for name in MODULES},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=worker._json_default, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"case": args.case, "method": args.method,
                      "status": result["status"], "timings": result["timings"]}))


if __name__ == "__main__":
    main()
