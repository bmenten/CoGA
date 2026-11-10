"""The manifest CoGA keeps for a test package, as a delivery of its folder makes it.

The import reads no file of a package folder as its manifest, and no PED: it reads the manifest
CoGA keeps for its job, whose members are the pedigree (``family.add_members``). A test package
is either a package folder with its package file ``coga-package.json``, as a pipeline delivers it
(the demo NIPT family), or describes its datasets in a manifest and its members in a PED.
``kept_manifest`` gives the text the import reads: for a package file, the manifest the import
watcher keeps for its delivery (``package_kept_manifest``); for a manifest, the PED's members
named as Discover names them for a folder added by hand (``ped_member_entries``). All samples and
values in the tests are synthetic.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

import yaml

from backend.app.services.coga_package import PACKAGE_FILE_NAME
from backend.app.services.family_package_manifest import _parse_ped_text_strict, ped_member_entries

_MANIFEST_NAMES = ("manifest.yaml", "manifest.yml", "manifest.json")

_T = TypeVar("_T")


def _run(coroutine: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coroutine`` to its end, also for a caller that runs in an event loop (an async test,
    a seeding script): then in a thread of its own."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def package_kept_manifest(folder: str | Path) -> str:
    """The manifest a delivery of the package folder ``folder`` keeps for its import, as the import
    watcher makes it (``import_watcher.assess_delivery``): the package file read, what it leaves out
    proposed from the folder (``_proposal``), and the manifest made from that package
    (``package_manifest``, without a draft: what was proposed is written into the package)."""
    from backend.app.services.coga_package import read_package_file
    from backend.app.services.coga_package_manifest import package_manifest
    from backend.app.services.import_watcher import _proposal

    root = Path(folder)
    read = read_package_file(root)
    if read.package is None:
        raise ValueError(f"The test package file could not be read: {[issue.message for issue in read.issues]}")
    package, _warnings, issues = _run(_proposal(root, read.package))
    if issues:
        raise ValueError(f"What the test package file leaves out could not be proposed: {[issue.message for issue in issues]}")
    return json.dumps(package_manifest(package, None), indent=2, ensure_ascii=False)


def manifest_with_members(manifest: dict[str, Any], ped_text: str | None) -> dict[str, Any]:
    """``manifest`` with the members of the PED ``ped_text`` named under ``family.add_members``
    (before those it adds itself) and the carriers its rows mark under ``family.members``, and
    no ``ped``."""
    kept = copy.deepcopy(manifest)
    kept.pop("ped", None)
    if not ped_text or not ped_text.strip():
        return kept
    ped, errors = _parse_ped_text_strict(ped_text)
    if ped is None:
        raise ValueError(f"The test PED could not be read: {[issue.message for issue in errors]}")
    entries, carriers = ped_member_entries(ped)
    family = kept.get("family") if isinstance(kept.get("family"), dict) else {}
    added = [entry for entry in family.get("add_members") or [] if isinstance(entry, dict)]
    family["add_members"] = [*entries, *added]
    if carriers:
        members = family.setdefault("members", {})
        for sample_id, carrier in carriers.items():
            members[sample_id] = {**carrier, **(members.get(sample_id) or {})}
    kept["family"] = family
    return kept


def kept_manifest(folder: str | Path, manifest: dict[str, Any] | None = None) -> str:
    """The manifest the import of the test package in ``folder`` reads, as JSON text. Without
    ``manifest``, for a folder that holds a package file: the manifest its delivery keeps
    (``package_kept_manifest``). Otherwise ``manifest`` (the folder's ``manifest.yaml``, ``.yml`` or
    ``.json`` when None) with the members of the PED it names, read from the folder."""
    root = Path(folder)
    if manifest is None and (root / PACKAGE_FILE_NAME).is_file():
        return package_kept_manifest(root)
    if manifest is None:
        path = next((root / name for name in _MANIFEST_NAMES if (root / name).is_file()), None)
        if path is None:
            raise FileNotFoundError(f"No test manifest in {root}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        manifest = loaded if isinstance(loaded, dict) else {}
    ped_name = manifest.get("ped")
    ped_text = (root / str(ped_name)).read_text(encoding="utf-8") if ped_name else None
    return json.dumps(manifest_with_members(manifest, ped_text), ensure_ascii=False)


def validated(folder: str | Path, manifest: dict[str, Any] | None = None) -> Any:
    """The validation of the test package in ``folder`` with the manifest CoGA keeps for it
    (``kept_manifest``), as the import's job validates it."""
    from backend.app.services.family_package_validation import load_validated_family_package

    validation, _bundle = load_validated_family_package(folder, manifest_text=kept_manifest(folder, manifest))
    return validation
