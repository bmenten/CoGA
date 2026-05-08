#!/usr/bin/env python3
"""Validate a folder-based family import package without importing data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.family_package_import import validate_family_package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder_path", help="Path to the top-level family package folder")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = validate_family_package(args.folder_path)
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
