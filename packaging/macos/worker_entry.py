"""Independent console worker, including its own Python and native libraries."""
from layerloom.worker import main

if __name__ == "__main__":
    raise SystemExit(main())
