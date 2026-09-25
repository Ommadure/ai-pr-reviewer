"""Write the API's OpenAPI schema to frontend/openapi.json (source of the TS types).

    cd backend && uv run python scripts/export_openapi.py
    cd ../frontend && npm run gen:api
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")  # no secrets needed just to describe the API
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402

target = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"
target.write_text(json.dumps(create_app().openapi(), indent=2) + "\n")
print(f"wrote {target}")
