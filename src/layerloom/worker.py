"""Console-only entry point for isolated desktop and command-line operations."""

from __future__ import annotations

import argparse
import importlib
import json
import traceback
from typing import Sequence

from layerloom.runtime import WORKER_MODULES, local_repair_available


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="layerloom-worker")
    parser.add_argument("--capabilities", action="store_true", help="Print optional worker capabilities as JSON")
    parser.add_argument("task", nargs="?", choices=tuple(WORKER_MODULES))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.capabilities:
        if args.task is not None:
            parser.error("--capabilities cannot be combined with a task")
        print(json.dumps({"repair": local_repair_available()}))
        return 0
    if args.task is None:
        parser.error("a worker task is required")
    try:
        module = importlib.import_module(WORKER_MODULES[args.task])
        result = module.main(args.arguments)
        return 0 if result is None else int(result)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
