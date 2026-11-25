"""Prepare the canonical tutorial Benchy upright BEFORE layer-aware weaving.

This small-fixture preparation uses the ordinary XML transform utility. Its
memory use is deliberately outside the measured streaming weave workers.
The source hash pins the measured hull-bottom plane; it must not be applied to
an arbitrary model. The original fixture is never changed.
"""
from pathlib import Path
import argparse
import hashlib
import json
import tempfile

import numpy as np
from layerloom.contour_store import TriangleStore
from layerloom.contour_weave import layer_regions
from layerloom.transform_3mf import write_transformed_3mf

SOURCE_HASH = "278e21e71ce899cda1dab6b392828729d58a492d9c24ef9d8c91659b0c684d2e"
TRANSFORM = np.array([
    [.8648280259348113, -.2681890541420092, .4244374121070285, 52.46675844466196],
    [.40297513276538377, .8750346699480746, -.2681890541420092, -21.900612118147393],
    [-.2994720820551027, .40297513276538377, .8648280259348113, -15.612945060842634],
    [0., 0., 0., 1.],
])


def prepare(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Source and destination must differ")
    digest = hashlib.sha256()
    with source.open("rb") as fp:
        for block in iter(lambda: fp.read(1024**2), b""):
            digest.update(block)
    if digest.hexdigest() != SOURCE_HASH:
        raise ValueError("This transform belongs only to the pinned tutorial Benchy")
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_transformed_3mf(str(source), str(destination), TRANSFORM)
    with tempfile.TemporaryDirectory(prefix="benchy-prep-") as tmp:
        with TriangleStore(destination, Path(tmp)) as store:
            regions = layer_regions(store, float(store.bounds[0, 2]) + .08, 1)
            manifest = dict(
                source=str(source), source_sha256=SOURCE_HASH,
                transform=TRANSFORM.tolist(), destination=str(destination),
                method="Rotate measured exterior hull-bottom normal to -Z, ground, and center XY at 135 mm before weaving",
                bounds=store.bounds.tolist(),
                first_layer_slice_z_mm=.08,
                first_layer_area_mm2=sum(g.area for g in regions.values()),
            )
    destination.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="examples/tutorial_cmy_benchy_cutup.3mf")
    parser.add_argument("--output", default="experiments/layer_contour/upright/source.3mf")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), indent=2))
