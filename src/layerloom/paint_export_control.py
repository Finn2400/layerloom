"""Cooperative cancellation and portable export working-memory planning.

The budget covers estimated additional export memory, including its immutable
snapshot. It is not a process RSS limit: NumPy, GEOS and Manifold allocate in
native code, where individual calls cannot be interrupted or precisely capped.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from numbers import Integral
from typing import Any, Callable

import numpy as np

DEFAULT_MEMORY_BUDGET_BYTES = 2 * 1024**3
_FIXED_BYTES = 64 * 1024**2
# Planning allowances include native topology, Python objects, intermediate
# triangle/adjacency arrays and XML serialization. They are deliberately larger
# than the raw arrays; runtime checks also account for actual generated faces.
_TRIANGLE_BYTES = 1024
_CANDIDATE_BYTES = 320


class ExportCancelled(Exception):
    """A requested stop reached a safe point before output publication."""


class ExportMemoryBudgetExceeded(ValueError):
    """The estimated export working set exceeds the selected budget."""


def validate_memory_budget(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError("memory_budget_bytes must be a positive integer")
    return int(value)


@dataclass(frozen=True)
class ExportResourceEstimate:
    snapshot_bytes: int
    source_working_bytes: int
    estimated_peak_bytes: int
    layers: int
    cells_per_layer: int
    estimated_cells: int
    source_triangles: int
    source_vertices: int

    @property
    def base_bytes(self) -> int:
        return _FIXED_BYTES + self.snapshot_bytes + self.source_working_bytes

    def check_budget(self, memory_budget_bytes: int = DEFAULT_MEMORY_BUDGET_BYTES) -> None:
        budget = validate_memory_budget(memory_budget_bytes)
        if self.estimated_peak_bytes > budget:
            raise ExportMemoryBudgetExceeded(
                f"Estimated export working memory is {self.estimated_peak_bytes / 1024**2:,.0f} MiB; "
                f"budget is {budget / 1024**2:,.0f} MiB. Increase pitch/layer height, "
                "reduce model complexity, or raise the memory budget. "
                "This estimate excludes memory already used by the editor."
            )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def estimate_export_resources(document: Any, *, layer_height: float = 0.2,
                              pitch: float = 0.5, max_cells: int = 2_000_000
                              ) -> ExportResourceEstimate:
    """Plan an export without copying the document or constructing geometry.

    Bounds are scanned in bounded chunks to avoid a whole-mesh finite mask.
    Work limits are checked with Python integers before allocating layer/grid
    arrays, including for extremely small positive sampling settings.
    """
    for name, value in (("layer_height", layer_height), ("pitch", pitch)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if isinstance(max_cells, bool) or not isinstance(max_cells, Integral) or max_cells <= 0:
        raise ValueError("max_cells must be a positive integer")
    if document is None or not getattr(document, "meshes", None):
        raise ValueError("The paint document contains no triangle meshes")
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    snapshot = vertices_count = faces_count = 0
    for item in document.meshes:
        vertices = np.asarray(item.vertices)
        faces = np.asarray(item.faces)
        if vertices.ndim != 2 or vertices.shape[1:] != (3,) or not len(vertices):
            raise ValueError("Vertices must be a non-empty XYZ array")
        if faces.ndim != 2 or faces.shape[1:] != (3,) or not len(faces):
            raise ValueError("A non-empty triangular mesh is required")
        for start in range(0, len(vertices), 65536):
            chunk = vertices[start:start + 65536]
            if not np.isfinite(chunk).all():
                raise ValueError("Vertices must contain finite XYZ coordinates")
            low = np.minimum(low, np.min(chunk, axis=0))
            high = np.maximum(high, np.max(chunk, axis=0))
        vertices_count += len(vertices)
        faces_count += len(faces)
        # Count the canonical snapshot dtypes, even for array-like callers.
        snapshot += len(vertices) * 3 * 8 + len(faces) * (3 * 8 + 4 + 4)
    with np.errstate(over="ignore", invalid="ignore"):
        extent = high - low
    ratios = (float(extent[2]) / layer_height,
              float(extent[0]) / pitch, float(extent[1]) / pitch)
    if not all(math.isfinite(value) for value in ratios):
        raise ValueError("Export grid cells exceed the supported work limit; increase pitch/layer height")
    layers = max(1, int(math.ceil(ratios[0])))
    cells_per_layer = math.prod(int(math.ceil(value)) + 2 for value in ratios[1:])
    cells = layers * cells_per_layer
    if cells > max_cells:
        raise ValueError(f"Export would inspect approximately {cells:,} grid cells; limit is {max_cells:,}. "
                         "Increase pitch/layer height, reduce model size, or explicitly raise max_cells.")
    source_working = faces_count * 1024 + vertices_count * 256
    # Persisted layer geometry plus its final union/XML, and the live layer's
    # GEOS objects. Exact complexity depends on paint boundaries and topology.
    peak = (_FIXED_BYTES + snapshot + source_working + cells * 768 +
            cells_per_layer * 2048 + layers * 64)
    return ExportResourceEstimate(snapshot, source_working, peak, layers,
                                  cells_per_layer, cells, faces_count, vertices_count)


class ExportControl:
    """Per-job state; all accesses except the cancellation callback are local."""
    def __init__(self, estimate: ExportResourceEstimate, memory_budget_bytes: int,
                 cancel_requested: Callable[[], bool] | None = None,
                 stage: Callable[[str], None] | None = None):
        self.estimate = estimate
        self.budget = validate_memory_budget(memory_budget_bytes)
        self.cancel_requested = cancel_requested
        self.stage_callback = stage
        self.retained_triangles = 0
        self.live_layer_bytes = 0
        self.peak_estimated_bytes = estimate.estimated_peak_bytes
        estimate.check_budget(self.budget)

    def checkpoint(self) -> None:
        if self.cancel_requested is not None and self.cancel_requested():
            raise ExportCancelled("Export cancelled; existing output files are unchanged.")

    def stage(self, label: str) -> None:
        self.checkpoint()
        if self.stage_callback is not None:
            self.stage_callback(label)
        self.checkpoint()

    def check_memory(self, extra_bytes: int = 0, *, stage: str = "geometry processing") -> None:
        self.checkpoint()
        estimated = (self.estimate.base_bytes + self.retained_triangles * _TRIANGLE_BYTES +
                     self.live_layer_bytes + int(extra_bytes))
        self.peak_estimated_bytes = max(self.peak_estimated_bytes, estimated)
        if estimated > self.budget:
            raise ExportMemoryBudgetExceeded(
                f"Estimated export working memory during {stage} reached "
                f"{estimated / 1024**2:,.0f} MiB; budget is {self.budget / 1024**2:,.0f} MiB. "
                "Increase pitch/layer height or raise the memory budget. No output was written."
            )

    def nearest_batch_size(self, source_triangles: int) -> int:
        # nearby_faces itself creates Python candidate lists. Bound that query
        # conservatively before asking it to build them, then check the actual
        # candidate count before vectorized triangle/closest-point allocations.
        per_point = max(1, source_triangles) * _CANDIDATE_BYTES
        self.check_memory(per_point, stage="nearest-surface search")
        available = (self.budget - self.estimate.base_bytes - self.retained_triangles * _TRIANGLE_BYTES -
                     self.live_layer_bytes)
        return max(1, min(4096, available // per_point))

    def check_candidates(self, candidate_count: int) -> None:
        self.check_memory(candidate_count * _CANDIDATE_BYTES, stage="nearest-surface candidates")
