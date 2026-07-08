"""Dump the live FastAPI OpenAPI schema to backend/openapi.json.

Run after any route change: `python -m backend.export_openapi` (from repo root).
"""

import json
from pathlib import Path

try:
    from .server import app
except ImportError:
    from server import app


def main() -> None:
    schema = app.openapi()
    output_path = Path(__file__).parent / "openapi.json"
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
