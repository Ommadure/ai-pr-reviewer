"""Compare two eval runs side by side (prompt v1 vs v2, or model A vs B).

cd backend
uv run python ../evals/compare.py ../evals/results/A.json ../evals/results/B.json
uv run python ../evals/compare.py A.json B.json --out comparison.md
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.evals.cli import compare_main

if __name__ == "__main__":
    raise SystemExit(compare_main())
