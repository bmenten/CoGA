"""The NIPT service's reads, faked for its unit tests: ``test_nipt_service.py`` (a joint VCF)
and ``test_nipt_pair_service.py`` (one VCF per parent).

``wire_nipt_reads`` answers the family record, its metadata context, the two variant loads
(the cohort the fetal fraction is read from, without a gene filter, and the filtered variant
list, with one), the assay's artifact list and the other cfDNA samples of the assay; the
review and internal-cohort hydration of the listed rows is a database concern exercised
elsewhere, and is a no-op here. Synthetic families only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.schemas import FamilyMemberOut, FamilyOut, FamilyRegionOfInterestOut
from backend.app.services import nipt_service
from backend.app.services.nipt_artifact_pg import recurrence_rules
from backend.app.services.clickhouse_family_variants import CfdnaRecurrence
from backend.app.services.clickhouse_variant_records import SmallVariantRecord


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
            FamilyMemberOut(sample_id=plasma, role="mother", affected=False, sample_metadata={"assay": "nipt_cfdna"}),
        ],
        metadata={"analysis_type": "monogenic_nipt"},
        roi=roi,
    )


def wire_nipt_reads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    family: FamilyOut,
    cohort: list[SmallVariantRecord],
    filtered: list[SmallVariantRecord],
    artifacts: set[str] | None = None,
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

    async def metadata_context(_session: Any, *, family_identifier: str, user: Any, project_id: Any = None) -> Any:
        return SimpleNamespace(
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
            family_uuid="family-uuid",
            sample_name_to_uuid=dict(sample_name_to_uuid or {}),
        )

    async def fetch(_context: Any, filters: Any, *, limit: int | None = None, **_kwargs: Any) -> list[SmallVariantRecord]:
        # The cohort (FF) load carries no gene filter; the variant load does. A limit is the
        # SQL LIMIT: the first rows, in genomic order.
        if filters.gene is None:
            return cohort
        return filtered if limit is None else filtered[:limit]

    async def listed_artifacts(_session: Any, *, assembly_id: str, assay_key: str) -> set[str]:
        return set(artifacts or ())

    async def no_hydration(_session: Any, *, context: Any, variants: Any) -> None:
        return None

    async def assay_carriers(_session: Any, *, assay_key: str) -> list[str]:
        return list(carrier_samples or [])

    monkeypatch.setattr(nipt_service, "get_family_record", family_record)
    monkeypatch.setattr(nipt_service, "build_family_metadata_context", metadata_context)
    monkeypatch.setattr(nipt_service, "_fetch_small_variant_rows", fetch)
    monkeypatch.setattr(nipt_service, "load_nipt_artifact_ids", listed_artifacts)
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
