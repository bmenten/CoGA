"""CoGA's ClinVar release, faked for the unit tests: a release that holds the alleles a test
names, each with its classification, and no other (``clinvar_coga.clinvar_alleles``, the one
lookup every reader of a small variant's ClinVar goes through). Synthetic alleles only."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest

from backend.app.services import clinvar_coga
from backend.app.services.clickhouse_variant_ids import minimal_allele_id, small_allele_key
from backend.app.services.clickhouse_variant_records import _status_terms
from backend.app.services.clinvar_coga import ClinvarCogaRead
from backend.app.services.clinvar_release_store import ClinvarStoredAllele

RELEASE = date(2026, 11, 22)
READ = ClinvarCogaRead("GRCh38", RELEASE, True)


def stored_allele(
    variant_id: str,
    classification: str,
    *,
    review_status: str = "criteria_provided,_single_submitter",
    variation_id: int = 900001,
    conditions: tuple[str, ...] = ("Synthetic disorder",),
) -> ClinvarStoredAllele:
    """An allele as the release stores it: its classification with the terms the filters read
    (``_status_terms``, as ``clinvar_vcf`` stores them)."""
    return ClinvarStoredAllele(
        variant_id=variant_id,
        classification=classification,
        classification_terms=tuple(sorted(_status_terms(classification))),
        review_status=review_status,
        variation_id=variation_id,
        included_only=False,
        conditions=conditions,
    )


def allele_key(variant_id: str, assembly: str = "GRCh38") -> int:
    """The allele key of a ``chrom-pos-ref-alt`` id's minimal allele, as the release keys it."""
    chrom, pos, ref, alt = variant_id.split("-")
    allele = minimal_allele_id(chrom, int(pos), ref, alt)
    assert allele is not None
    return small_allele_key(assembly, allele)


def wire_clinvar_alleles(
    monkeypatch: pytest.MonkeyPatch,
    held: Mapping[str, str | ClinvarStoredAllele],
    *,
    read: ClinvarCogaRead = READ,
) -> list[set[int]]:
    """The release ``read`` names holds each ``chrom-pos-ref-alt`` allele of ``held`` with its
    classification (or as stored), and no other. Returns each lookup's keys, as asked."""
    stored = {
        allele_key(variant_id, read.assembly_name): value
        if isinstance(value, ClinvarStoredAllele)
        else stored_allele(variant_id, value)
        for variant_id, value in held.items()
    }
    asked: list[set[int]] = []

    async def alleles(assembly: str, release: date, keys: Any) -> dict[int, ClinvarStoredAllele]:
        assert (assembly, release) == (read.assembly_name, read.release)
        asked.append(set(keys))
        return {key: stored[key] for key in keys if key in stored}

    monkeypatch.setattr(clinvar_coga, "clinvar_alleles", alleles)
    return asked
