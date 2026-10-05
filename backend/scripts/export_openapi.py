"""Export the OpenAPI schema without a running server: python scripts/export_openapi.py [path]."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cli import export_openapi  # noqa: E402

if __name__ == "__main__":
    export_openapi(
        sys.argv[1]
        if len(sys.argv) > 1
        else str(Path(__file__).resolve().parents[2] / "frontend" / "openapi.json")
    )
