#!/usr/bin/env python3
"""Write the JSON Schema of the package file ``coga-package.json`` (format ``coga-package/1``).

The format is defined once, by the Pydantic model in backend/app/services/coga_package.py, which
CoGA reads the file with. This script writes that model's JSON Schema to
docs/schemas/coga-package-1.schema.json, the file a pipeline checks its package file against
before writing it (docs/coga-package.md). With --check it writes nothing and exits 1 when the file
is out of date; the unit test test_coga_package.py checks the same.

    python scripts/generate-coga-package-schema.py          # regenerate
    python scripts/generate-coga-package-schema.py --check  # fail if stale

The schema depends on the Pydantic version, so generate in the backend's environment, installed
from backend/requirements-dev.txt as CI does.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "schemas" / "coga-package-1.schema.json"


def schema_text() -> str:
    os.environ.setdefault("APP_ENV", "test")
    sys.path.insert(0, str(ROOT))
    from backend.app.services.coga_package import coga_package_json_schema_text  # needs the path above

    return coga_package_json_schema_text()


def main(argv: list[str]) -> int:
    text = schema_text()
    if "--check" in argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else None
        if current != text:
            print(f"{OUTPUT.relative_to(ROOT)} is out of date: run python scripts/generate-coga-package-schema.py")
            return 1
        print(f"{OUTPUT.relative_to(ROOT)} is up to date")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
