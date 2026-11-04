"""Loosely typed stored values read back: a JSON object a driver returns as text or as a dict,
and a whole number written as text, an int or a float.

Shared by the services that read them (mitochondrial, repeat and Paraphase analyses, reference
and panel metadata, the annotation manifest, raw import files, family-package jobs).
"""

from __future__ import annotations

import json
from typing import Any


def json_object(value: Any) -> dict[str, Any]:
    """A stored JSON object as a dict: a dict as it is, JSON text parsed; anything else, text
    that is no JSON or JSON that is no object, is empty."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def int_or_none(value: Any) -> int | None:
    """A whole number: an int, or text or a float read as one (``"3"``, ``"3.7"`` and ``3.7``
    are 3). None for None, ``""`` and anything that reads as no number."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
