"""Configuration shared by the experimental contour backend and its frontends."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ContourOptions:
    """Limits for prepared-3MF weaving, not the GLB conversion or slicer stages.

    ``xy_tolerance_mm`` bounds contour displacement. It is not a promise about
    a downstream slicer's wall paths; those require a separate toolpath audit.
    """

    xy_tolerance_mm: float = 0.02
    first_layer_height: float | None = None
    memory_budget_mib: int = 1024
    scratch_dir: str | None = None
    reserve_disk_gib: float = 4.0

    def validate(self, step: float = 0.08) -> "ContourOptions":
        if not math.isfinite(step) or step <= 0:
            raise ValueError("Layer height must be finite and greater than zero")
        if not math.isfinite(self.xy_tolerance_mm) or self.xy_tolerance_mm < 0:
            raise ValueError("XY tolerance must be finite and nonnegative")
        if self.first_layer_height is not None and (
            not math.isfinite(self.first_layer_height) or self.first_layer_height <= 0
        ):
            raise ValueError("First-layer height must be finite and greater than zero")
        if isinstance(self.memory_budget_mib, bool) or not isinstance(self.memory_budget_mib, int) or self.memory_budget_mib < 64:
            raise ValueError("Memory budget must be an integer of at least 64 MiB")
        if not math.isfinite(self.reserve_disk_gib) or self.reserve_disk_gib < 0:
            raise ValueError("Disk reserve must be finite and nonnegative")
        return self


def add_contour_arguments(parser) -> None:
    group = parser.add_argument_group("experimental contour weaving")
    group.add_argument("--weave-mode", choices=("legacy", "layer-contour"), default="legacy",
                       help="Weaving backend (default: legacy). layer-contour streams prepared 3MF input/output.")
    group.add_argument("--xy-tolerance-mm", type=float, default=0.02,
                       help="Maximum contour displacement, not certified wall error (default: 0.02 mm)")
    group.add_argument("--first-layer-height", type=float,
                       help="First-layer height in mm; defaults to ordinary layer height")
    group.add_argument("--memory-budget-mib", type=int, default=1024,
                       help="Contour backend working-data budget (default: 1024 MiB)")
    group.add_argument("--scratch-dir", help="Directory for private disk spools (default: output folder; 4 GiB free-space reserve)")


def contour_options_from_args(args, *, step: float) -> ContourOptions | None:
    if args.weave_mode == "legacy":
        if (args.xy_tolerance_mm != 0.02 or args.first_layer_height is not None or
                args.memory_budget_mib != 1024 or args.scratch_dir is not None):
            raise ValueError("Contour options require --weave-mode layer-contour")
        return None
    return ContourOptions(
        xy_tolerance_mm=args.xy_tolerance_mm,
        first_layer_height=args.first_layer_height,
        memory_budget_mib=args.memory_budget_mib,
        scratch_dir=args.scratch_dir,
    ).validate(step)
