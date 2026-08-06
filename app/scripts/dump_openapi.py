#!/usr/bin/env python3
"""Write the OpenAPI schema to a file without running the server.

The frontend used to obtain this by curling a live server
(`npm run sync:openapi`), which meant the richest machine-readable index of the
API — 172 paths, 270 schemas — silently went stale whenever the backend was not
up, and was gitignored so nobody could tell how old it was.

FastAPI can produce the schema from the app object alone: no server, no
database, no network. That makes it a checked-in build artifact instead of a
local scratch file.

    poetry run python app/scripts/dump_openapi.py [output_path]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "openapi.json"

# Runnable directly, without PYTHONPATH: pytest gets `pythonpath = ["."]` from
# pyproject, but a plain `python app/scripts/...` invocation does not.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from app.factory import create_app

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    schema = create_app().openapi()

    out.parent.mkdir(parents=True, exist_ok=True)
    # Sorted and indented so a regeneration produces a reviewable diff rather
    # than one 640 KB line that git cannot show meaningfully.
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"{out}: {len(schema['paths'])} paths, "
        f"{len(schema.get('components', {}).get('schemas', {}))} schemas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
