"""The members of a long-read (nf-core/lrsvar) package that has no PED.

The long-read pipeline lays its output out per sample (``<dataset>/<sample>/...``, each
file named after its sample) and writes no PED for a couple screened for carriership.
Discovery then takes the members from those folders: a sample is the name of a folder
under one of the pipeline's per-sample datasets that holds a file named after it.

Each member's sex is the karyotype TRGT genotyped its repeats with (``--karyotype XX`` or
``XY`` in the TRGT VCF's ``##trgtCommand``): the pipeline sets it from its samplesheet, so
it is the sex the lab recorded, which the sample-integrity QC then checks against the
reads. Without a TRGT VCF the sex is left unknown. Two members of opposite sex are
proposed as a couple (the female partner the mother, the male the father); any other
folder holds members whose relationships nothing in the package states.

Nothing here is read at import: the import reads the manifest's ``family.add_members``
and ``family.relationships``, which the user checks and can edit first.
"""

from __future__ import annotations

import gzip
import logging
import os
from pathlib import Path
import re
from typing import Any

from fastapi import HTTPException

from ..schemas import FamilyImportValidationIssue
from .family_package_common import _issue, _resolve_package_path


logger = logging.getLogger(__name__)


# The pipeline's per-sample dataset folders: <dataset>/<sample>/[<subfolder>/]<sample>...
LONG_READ_SAMPLE_DATASET_FOLDERS = ("snv", "sv", "cnv", "repeats", "paraphase", "mito")

# Where a long-read sample's TRGT VCF is; its header names the karyotype it was run with.
_TRGT_VCF_PATTERNS = (
    "repeats/{sample_id}/{sample_id}_tr.vcf.gz",
    "repeats/{sample_id}/{sample_id}.trgt.vcf.gz",
    "repeats/{sample_id}/{sample_id}_tr.vcf",
)

_KARYOTYPE = re.compile(r"--karyotype[ =](XX|XY)\b")
_KARYOTYPE_SEX = {"XX": "female", "XY": "male"}

# The couple's context, as the Family Builder records it.
CARRIER_SCREENING_COUPLE_CONTEXT = "carrier screening"


def _holds_file_named_after(folder: Path, sample_id: str) -> bool:
    """Whether ``folder`` or one of its subfolders holds a file named after ``sample_id``."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return False
    for entry in entries:
        if entry.is_file() and entry.name.startswith(sample_id):
            return True
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            if any(sub.is_file() and sub.name.startswith(sample_id) for sub in os.scandir(entry.path)):
                return True
        except OSError:
            continue
    return False


def long_read_sample_ids(root: Path) -> list[str]:
    """The samples of a long-read package laid out per sample, in name order: every folder
    under one of the pipeline's per-sample datasets that holds a file named after it.
    Empty for any other layout."""
    samples: set[str] = set()
    for dataset_folder in LONG_READ_SAMPLE_DATASET_FOLDERS:
        base = root / dataset_folder
        try:
            children = list(os.scandir(base)) if base.is_dir() else []
        except OSError:
            continue
        for child in children:
            name = child.name
            if not child.is_dir() or name.startswith(".") or name == "annotation" or any(ch.isspace() for ch in name):
                continue
            if _holds_file_named_after(Path(child.path), name):
                samples.add(name)
    return sorted(samples)


def trgt_karyotype_sex(path: Path) -> str | None:
    """``female`` or ``male`` from the karyotype a TRGT VCF was genotyped with, read from its
    header only; None when the header does not name XX or XY."""
    opener = gzip.open if path.name.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:  # type: ignore[operator]
            for line in handle:
                if not line.startswith("##"):
                    break
                if line.startswith("##trgtCommand"):
                    match = _KARYOTYPE.search(line)
                    return _KARYOTYPE_SEX[match.group(1)] if match else None
    except (OSError, EOFError, UnicodeError):
        return None
    return None


def long_read_sample_sex(root: Path, sample_id: str) -> str | None:
    """The sex a long-read sample's TRGT VCF names (see :func:`trgt_karyotype_sex`)."""
    for pattern in _TRGT_VCF_PATTERNS:
        try:
            path = _resolve_package_path(root, pattern.format(sample_id=sample_id))
        except HTTPException:
            continue
        if path is not None and path.is_file():
            return trgt_karyotype_sex(path)
    return None


def long_read_family_block(
    root: Path, sample_ids: list[str]
) -> tuple[dict[str, Any], list[FamilyImportValidationIssue]]:
    """The manifest ``family`` block for a PED-less long-read package of ``sample_ids``,
    and the warning that says what was proposed and what the user still has to check."""
    sexes = {sample_id: long_read_sample_sex(root, sample_id) for sample_id in sample_ids}

    def described(sample_id: str) -> str:
        sex = sexes[sample_id]
        return f"{sample_id} ({sex})" if sex else f"{sample_id} (sex unknown)"

    females = [sample_id for sample_id in sample_ids if sexes[sample_id] == "female"]
    males = [sample_id for sample_id in sample_ids if sexes[sample_id] == "male"]
    if len(sample_ids) == 2 and len(females) == 1 and len(males) == 1:
        mother, father = females[0], males[0]
        block: dict[str, Any] = {
            "add_members": [
                {"sample_id": sample_id, "sex": sexes[sample_id], "role": "mother" if sample_id == mother else "father"}
                for sample_id in sample_ids
            ],
            "relationships": {
                "couples": [{"partners": [mother, father], "context": CARRIER_SCREENING_COUPLE_CONTEXT}],
            },
        }
        message = (
            f"The package has no PED. Discover took its two samples for a couple screened for "
            f"carriership: {described(mother)} and {described(father)}, each sexed by the karyotype "
            "TRGT genotyped its repeats with. Check both before importing; their clinical status "
            "is left unknown."
        )
        return block, [_issue("ped_proposed_from_folders", message)]
    members: list[dict[str, Any]] = []
    for sample_id in sample_ids:
        member: dict[str, Any] = {"sample_id": sample_id, "role": "proband" if len(sample_ids) == 1 else "relative"}
        if sexes[sample_id]:
            member["sex"] = sexes[sample_id]
        members.append(member)
    if len(sample_ids) == 1:
        message = (
            f"The package has no PED. Discover took its one sample for the proband: "
            f"{described(sample_ids[0])}. Check it before importing."
        )
    else:
        message = (
            "The package has no PED. Discover took the members from the per-sample folders: "
            f"{', '.join(described(sample_id) for sample_id in sample_ids)}. Nothing in the "
            "package says how they are related: add their links under family.relationships "
            "(or their parents under family.add_members), or give a PED, before importing."
        )
    if any(sexes[sample_id] is None for sample_id in sample_ids):
        message += " A member without a TRGT VCF has no recorded sex: set it under family.add_members."
    return {"add_members": members}, [_issue("ped_proposed_from_folders", message)]
