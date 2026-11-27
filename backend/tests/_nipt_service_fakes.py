"""The NIPT service's reads, faked for its unit tests: ``test_nipt_service.py`` (a joint VCF)
and ``test_nipt_pair_service.py`` (one VCF per parent).

``wire_nipt_reads`` answers the family record, its metadata context, the two variant loads
(the cohort the fetal fraction is read from, without a gene filter, and the filtered variant
list, with one), the family's data version, the assay's artifact list, the QC gate's bound
(provisional: QA has set none), CoGA's ClinVar release (``wire_clinvar_release``: none) and the
other cfDNA samples of the assay; the review and
internal-cohort hydration of the listed rows is a database concern exercised elsewhere, and
is a no-op here. Synthetic families only.

A load without annotation (``annotated=False``, the cohort's) answers its records without it,
as ClickHouse does, and ``annotate_small_variant_records`` gives a record back its own
(``wire_annotation``): a record the service reads the annotation of without annotating it
reads none.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.schemas import FamilyMemberOut, FamilyOut, FamilyRegionOfInterestOut
from backend.app.services.analysis_scope import AnalysisScope
from backend.app.services import nipt_service
from backend.app.services.clinvar_coga import NO_CLINVAR_COGA, ClinvarCogaRead
from backend.app.services.nipt_artifact_pg import recurrence_rules
from backend.app.services.nipt_qc_gate import NiptGateBound, resolve_nipt_gate_bound
from backend.app.services.clickhouse_family_variants import CfdnaRecurrence
from backend.app.services.clickhouse_variant_records import SmallVariantRecord
from backend.tests._clinvar_release_fakes import wire_clinvar_alleles


# The recurrence a fake artifact list records for an allele given by id alone: a typical
# listed artefact, seen in more cfDNA samples than a father's allele may be
# (nipt_service.PATERNAL_CARRIER_PROTECTION_MAX_RECURRENCE).
LISTED_ARTIFACT_RECURRENCE = 10


def gate_bound_loader(configured: dict[str, dict[str, float | None]] | None = None) -> Any:
    """A ``load_nipt_gate_bound`` answering the QC gate's bound as QA set it in
    ``configured`` (metric key -> bounds), else the provisional one."""

    async def load(_session: Any) -> NiptGateBound:
        return resolve_nipt_gate_bound(configured or {})

    return load


def listed_artifact_recurrence(artifacts: set[str] | dict[str, int] | None) -> dict[str, int]:
    """The artifact list ``load_nipt_artifacts`` answers: each listed allele with its
    recurrence, ``LISTED_ARTIFACT_RECURRENCE`` for an allele given by id alone."""
    if isinstance(artifacts, dict):
        return dict(artifacts)
    return {variant_id: LISTED_ARTIFACT_RECURRENCE for variant_id in artifacts or ()}


def as_loaded(records: list[SmallVariantRecord], annotated: bool) -> list[SmallVariantRecord]:
    """``records`` as a variant load answers them: without their annotation when it reads none
    (``_fetch_small_variant_rows(annotated=False)``)."""
    return records if annotated else [replace(record, annotations=[]) for record in records]


def wire_annotation(monkeypatch: pytest.MonkeyPatch, records: list[SmallVariantRecord]) -> None:
    """``annotate_small_variant_records`` answering each record with its annotation as
    ``records`` hold it."""
    stored = {record.variant_id: record.annotations for record in records}

    async def annotate(_context: Any, loaded: list[SmallVariantRecord]) -> list[SmallVariantRecord]:
        return [replace(record, annotations=list(stored[record.variant_id])) for record in loaded]

    monkeypatch.setattr(nipt_service, "annotate_small_variant_records", annotate)


def wire_clinvar_release(
    monkeypatch: pytest.MonkeyPatch,
    read: ClinvarCogaRead = NO_CLINVAR_COGA,
    *,
    held: Mapping[str, str] | None = None,
) -> None:
    """CoGA's ClinVar release the analysis reads (``clinvar_coga_read``): none by default, so no
    variant has a ClinVar classification; with ``held``, a release that holds those alleles
    (``chrom-pos-ref-alt``) with their classification and no other (``wire_clinvar_alleles``)."""

    async def release(_session: Any, _assembly_name: Any) -> ClinvarCogaRead:
        return read

    monkeypatch.setattr(nipt_service, "clinvar_coga_read", release)
    if held is not None:
        wire_clinvar_alleles(monkeypatch, held, read=read)


def wire_data_version(monkeypatch: pytest.MonkeyPatch, version: str = "1:1") -> None:
    """The family's small-variant data version (``get_family_small_variant_data_version``), as
    ClickHouse answers it while nothing writes the family's variants."""

    async def data_version(_context: Any) -> str:
        return version

    monkeypatch.setattr(nipt_service, "_family_small_variant_data_version", data_version)


def nipt_family(
    *, family_id: str, father: str, plasma: str, roi: FamilyRegionOfInterestOut | None = None
) -> FamilyOut:
    """A monogenic NIPT family: the father and the mother's plasma (tagged as the cfDNA)."""
    return FamilyOut(
        id="family-uuid",
        family_id=family_id,
        created_at=datetime.now(timezone.utc),
        members=[
            FamilyMemberOut(sample_id=father, role="father", affected=False),
            FamilyMemberOut(sample_id=plasma, role="mother", affected=False, sample_metadata={"assays": ["nipt_cfdna"]}),
        ],
        metadata={"analyses": ["monogenic_nipt"]},
        roi=roi,
    )


def wire_nipt_reads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    family: FamilyOut,
    cohort: list[SmallVariantRecord],
    filtered: list[SmallVariantRecord],
    artifacts: set[str] | dict[str, int] | None = None,
    sample_name_to_uuid: dict[str, str] | None = None,
    carrier_samples: list[str] | None = None,
    recurrence: dict[str, CfdnaRecurrence] | None = None,
    other_samples: int = 0,
) -> list[list[str]]:
    """Answer the NIPT service's reads for ``family``. ``carrier_samples`` are the assay's other
    cfDNA samples (none by default: no allele recurs). Each variant's recurrence in them is
    read from ``recurrence`` (none: no other sample carries it), and ``other_samples`` is how
    many of them have a call. Returns the variant ids of each recurrence count, as made."""

    async def family_record(_session: Any, _family_id: str, _user: Any) -> FamilyOut:
        return family

    async def metadata_context(
        _session: Any, *, family_identifier: str, user: Any, project_id: Any = None, analysis: Any = None
    ) -> Any:
        return SimpleNamespace(
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
            family_uuid="family-uuid",
            project_ids=["project-uuid"],
            sample_name_to_uuid=dict(sample_name_to_uuid or {}),
            # A family that holds no analysis: its reviews are stored under none.
            scope=AnalysisScope(),
        )

    async def fetch(
        _context: Any, filters: Any, *, limit: int | None = None, annotated: bool = True, **_kwargs: Any
    ) -> list[SmallVariantRecord]:
        # The cohort (FF) load carries no gene filter; the variant load does. A limit is the
        # SQL LIMIT: the first rows, in genomic order.
        if filters.gene is None:
            return as_loaded(cohort, annotated)
        return as_loaded(filtered if limit is None else filtered[:limit], annotated)

    async def listed_artifacts(_session: Any, *, assembly_id: str, assay_key: str) -> dict[str, int]:
        return listed_artifact_recurrence(artifacts)

    async def no_hydration(_session: Any, *, context: Any, variants: Any) -> None:
        return None

    async def assay_carriers(_session: Any, *, assay_key: str) -> list[str]:
        return list(carrier_samples or [])

    monkeypatch.setattr(nipt_service, "get_family_record", family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", metadata_context)
    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", fetch)
    wire_data_version(monkeypatch)
    wire_annotation(monkeypatch, [*cohort, *filtered])
    monkeypatch.setattr(nipt_service, "load_nipt_artifacts", listed_artifacts)
    monkeypatch.setattr(nipt_service, "load_nipt_gate_bound", gate_bound_loader())
    wire_clinvar_release(monkeypatch)
    monkeypatch.setattr(nipt_service, "_hydrate_small_variant_outs", no_hydration)
    monkeypatch.setattr(nipt_service, "assay_cfdna_carrier_samples", assay_carriers)
    counts: list[list[str]] = []

    async def count_recurrence(
        _assembly: str, variant_ids: Any, *, carrier_samples: Any, exclude_family_uuid: str, rules: Any
    ) -> dict[str, CfdnaRecurrence]:
        assert exclude_family_uuid == "family-uuid"
        assert rules == recurrence_rules(nipt_service.NiptQualityThresholds())
        counts.append(list(variant_ids))
        return {variant_id: value for variant_id, value in (recurrence or {}).items() if variant_id in variant_ids}

    async def count_others(_assembly: str, *, carrier_samples: Any, exclude_family_uuid: str) -> int:
        assert exclude_family_uuid == "family-uuid"
        return other_samples

    monkeypatch.setattr(nipt_service, "count_cfdna_recurrence", count_recurrence)
    monkeypatch.setattr(nipt_service, "count_other_cfdna_samples", count_others)
    return counts
