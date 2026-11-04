"""The images ``ci/pinned-images.Dockerfile`` pins, where Dependabot refreshes them (OPS-12).

Each FROM line pins one image by tag and digest and names it (``AS <stage>``). The scripts
read their image from there (``scripts/pinned-image.sh``); the tests read it here.
"""

from __future__ import annotations

from pathlib import Path
import re

PINNED_IMAGES = Path(__file__).resolve().parents[2] / "ci" / "pinned-images.Dockerfile"
_FROM = re.compile(r"^FROM (?P<image>(?P<tag>[^\s@]+)@sha256:[0-9a-f]{64}) AS (?P<stage>[a-z0-9-]+)$")


def pinned_images() -> dict[str, str]:
    """Each image the file pins, ``<name>:<tag>@sha256:<digest>``, by its stage name.
    Raises on a FROM line in another shape, or a stage named twice."""
    images: dict[str, str] = {}
    for line in PINNED_IMAGES.read_text().splitlines():
        if not line.startswith("FROM "):
            continue
        match = _FROM.match(line)
        if match is None:
            raise ValueError(f"not <name>:<tag>@sha256:<digest> AS <stage>: {line}")
        if match["stage"] in images:
            raise ValueError(f"stage named twice: {match['stage']}")
        images[match["stage"]] = match["image"]
    return images


def pinned_image(stage: str) -> str:
    """The image the file pins as ``stage``."""
    return pinned_images()[stage]
