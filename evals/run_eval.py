"""Run the eval: the pure review engine over evals/cases, scored against planted bugs.

    cd backend
    uv run python ../evals/run_eval.py --prompt v1                 # all cases
    uv run python ../evals/run_eval.py --prompt v2 --cases 'ts-*'  # a subset
    uv run python ../evals/run_eval.py --provider fake             # offline plumbing check

Writes evals/results/<timestamp>_<prompt>_<model>.{json,md}. The logic lives in
backend/app/evals/ (type-checked and unit-tested with the rest of the backend).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.evals.cli import run_main

if __name__ == "__main__":
    raise SystemExit(run_main())
