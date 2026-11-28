"""A small variant's ClinVar filters, the P/LP override and the values the page serves, read in
CoGA's release on real ClickHouse.

A family's rows, written through the import's write path with their allele keys, and a ClinVar
release stored from the synthetic file (with two more alleles at one site), are searched by the
family search's own conditions (``_small_annotation_filter_clauses``), on both of its paths: the
family page's, which reads each row's own transcripts, and the shared annotation table's, which
the Variant Explorer reads. ClinVar is the release's alone: each row's annotation names a ClinVar
classification the search never reads. A search for HIGH impact and rare variants with the
override keeps a common, low-impact allele the release holds (likely) pathogenic, unless a
filter the override does not lift fails it, and never one only the annotation calls pathogenic.
The *ClinVar status* filter keeps a row with an allele the release classifies so; the excluded
statuses leave a row out only where every allele has one, so a pathogenic allele is never hidden
by a benign sibling ALT. An allele the release does not hold has no ClinVar: the status filter
drops it, the exclusion keeps it, the override does not lift it; without a release no row has
ClinVar. An untrimmed record matches ClinVar's anchored one, in the filters and in the values
the page serves, which one lookup of the release reads, the variant's own ClinVar fields its
shown ALT's.

On an assembly of its own, whose tables the test drops. Synthetic data only. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import run_closing_stores

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "clinvar" / "clinvar_20261122.vcf"
_RELEASE = date(2026, 11, 22)


def _record(variant_id: str, *, impact: str, frequency: float, canonical: bool = True, clinvar: str = "") -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    annotation: dict[str, Any] = {
        "gene": "SYNG1",
        "transcript_id": "ENST00000000001",
        "impact": impact,
        "consequence": "missense_variant" if impact == "MODERATE" else "synonymous_variant",
        "gnomad_genomes_af": frequency,
        "canonical": canonical,
    }
    if clinvar:
        annotation["clinvar"] = clinvar
    return SmallVariantRecord(
        variant_key=None,
        variant_id=variant_id,
        chr=chrom,
        start=int(pos),
        end=int(pos),
        ref=ref,
        alt=alt,
        source="haplotypecaller",
        rsid=None,
        filters=[],
        gene_symbols=["SYNG1"],
        annotations=[annotation],
        calls=[SmallVariantCall(sample="PROBAND", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None)],
    )


# Against the fixture's release: 1 1000100 G A Pathogenic; 1 1000200 AG A Likely pathogenic;
# 2 2000100 C T Benign/Likely benign; 3 4000300 CTT CT Uncertain significance; X 3000100 G C
# Pathogenic/Likely pathogenic|risk factor. And at 2 2000100 the test's own: C G Pathogenic, C A
# Likely benign. Each annotation's ClinVar is one the search never reads.
_EXTRA_ALLELES = [
    "2\t2000100\t900101\tC\tG\t.\t.\tALLELEID=910101;CLNREVSTAT=criteria_provided,_single_submitter;CLNSIG=Pathogenic",
    "2\t2000100\t900102\tC\tA\t.\t.\tALLELEID=910102;CLNREVSTAT=criteria_provided,_single_submitter;CLNSIG=Likely_benign",
]
_RECORDS = [
    # Common, low impact: kept by the release's pathogenic classification alone.
    ("1-1000100-G-A", {"impact": "LOW", "frequency": 0.2}),
    # Untrimmed, as a pipeline may write it: ClinVar's 1 1000200 AG A.
    ("1-1000200-AGG-AG", {"impact": "LOW", "frequency": 0.2, "canonical": False}),
    # Pathogenic in its annotation alone: the release does not hold it, so it has no ClinVar.
    ("1-1000400-G-A", {"impact": "LOW", "frequency": 0.2, "clinvar": "Pathogenic"}),
    # HIGH impact and rare: kept by the filters themselves. Benign/likely benign in the release,
    # pathogenic in its annotation.
    ("2-2000100-C-T", {"impact": "HIGH", "frequency": 0.0001, "clinvar": "Pathogenic"}),
    # Multi-allelic, every allele in the release: benign/likely benign and likely benign...
    ("2-2000100-C-T,A", {"impact": "LOW", "frequency": 0.2}),
    # ...and benign/likely benign beside pathogenic, benign in its annotation.
    ("2-2000100-C-T,G", {"impact": "LOW", "frequency": 0.2, "clinvar": "Benign"}),
    # Uncertain significance.
    ("3-4000300-CTT-CT", {"impact": "LOW", "frequency": 0.2}),
    # Multi-allelic, the release holding one ALT, of uncertain significance...
    ("3-4000300-CTT-CT,C", {"impact": "LOW", "frequency": 0.2, "clinvar": "Benign"}),
    # ...and here pathogenic/likely pathogenic.
    ("X-3000100-G-C,T", {"impact": "LOW", "frequency": 0.2, "clinvar": "Benign"}),
]
_EVERY_ROW = [variant_id for variant_id, _values in _RECORDS]


def test_the_clinvar_filters_and_the_override_read_cogas_release_alone_on_both_search_paths() -> None:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.core.config import settings
    from backend.app.services.clickhouse_variant_ids import _require_clickhouse_identifier, _small_table_name
    from backend.app.services.clickhouse_variant_queries import _small_annotation_filter_clauses
    from backend.app.services.clickhouse_variant_storage import insert_small_variant_records
    from backend.app.schemas import VariantOut
    from backend.app.services.clinvar_coga import ClinvarCogaRead, attach_clinvar_coga, clinvar_coga_values
    from backend.app.services.clinvar_release_store import clinvar_alleles, ensure_clinvar_table, insert_clinvar_rows
    from backend.app.services.clinvar_vcf import ClinvarFileReader
    from backend.app.services.family_metadata_context import FamilyMetadataContext
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters
    from backend.app.services.variant_explorer_service import ExplorerScope

    assembly = f"clinvar-coga-{uuid4().hex[:8]}"
    family = str(uuid4())
    family_context = FamilyMetadataContext(
        family_uuid=family,
        family_id="F1",
        project_ids=["project-1"],
        sample_rows=[],
        sample_uuid_to_name={},
        sample_name_to_uuid={},
        affected_sample_names=[],
        assembly_id=None,
        assembly_name=assembly,
    )
    explorer_scope = ExplorerScope(assembly_id="a1", assembly_name=assembly, project_ids=["project-1"])

    async def kept(context: Any, release: date | None = _RELEASE, **values: Any) -> list[str]:
        params: dict[str, Any] = {"family": family}
        filters = SmallVariantQueryFilters(page=1, page_size=10, clinvar_coga_release=release, **values)
        clauses = _small_annotation_filter_clauses(context, filters, params=params)
        rows = await execute_clickhouse(
            f"SELECT e.variantId FROM {_small_table_name(assembly, 'entries')} AS e "
            f"WHERE e.family_guid = %(family)s AND {' AND '.join(clauses or ['1'])} ORDER BY e.variantId",
            params,
        )
        return [row[0] for row in rows or []]

    async def _run() -> None:
        dataset = _require_clickhouse_identifier(assembly)
        try:
            records = [_record(variant_id, **values) for variant_id, values in _RECORDS]
            await insert_small_variant_records(assembly, family, ["project-1"], records, annotation_version="vcf_info")
            lines = [
                f"##reference={assembly}" if line.startswith("##reference=") else line
                for line in _FIXTURE.read_text().splitlines()
            ] + _EXTRA_ALLELES
            reader = ClinvarFileReader(assembly_name=assembly, release=_RELEASE, file_name=_FIXTURE.name)
            await ensure_clinvar_table(assembly)
            await insert_clinvar_rows(assembly, _RELEASE, list(reader.rows(lines)))

            # The values the page serves, read in the release: the untrimmed multi-allelic record's
            # ALTs each by its own key.
            [multi, benign, gone] = await clinvar_coga_values(
                ClinvarCogaRead(assembly, _RELEASE, True),
                [("chr1", 1000200, "AGG", "AG,AGGG"), ("2", 2000100, "C", "T"), ("3", 9, "C", "T")],
            )
            assert [(value.alt, value.status, value.classification, value.stars) for value in multi] == [
                ("AG", "in_release", "Likely_pathogenic", 3),
                ("AGGG", "not_in_release", None, None),
            ]
            assert (benign[0].classification, benign[0].variation_id) == ("Benign/Likely_benign", 900004)
            assert gone[0].status == "not_in_release"
            assert await clinvar_alleles(assembly, _RELEASE, set()) == {}
            # The variant's own ClinVar fields: its shown ALT's in the release, none where the
            # release does not hold it, whatever its annotation said.
            shown = VariantOut(_id="1-1000200-AGG-AG", chr="1", start=1000200, end=1000202, type="DEL", ref="AGG", alt="AG")
            annotated = VariantOut(
                _id="1-1000400-G-A", chr="1", start=1000400, end=1000400, type="SNV", ref="G", alt="A", clinvar="Pathogenic"
            )
            await attach_clinvar_coga(ClinvarCogaRead(assembly, _RELEASE, True), [shown, annotated])
            assert (
                shown.clinvar,
                shown.clinvar_review_status,
                shown.clinvar_stars,
                shown.clinvar_conditions,
                shown.clinvar_variation_id,
            ) == ("Likely_pathogenic", "reviewed by expert panel", 3, ["Synthetic disorder 1", "not provided"], "900002")
            assert (annotated.clinvar, annotated.clinvar_coga[0].status) == (None, "not_in_release")

            for context in (family_context, explorer_scope):
                rare = {"impact": ["HIGH"], "max_gnomad_genomes_af": 0.01}
                assert await kept(context, **rare) == ["2-2000100-C-T"]
                # The override: an allele the release holds (likely) pathogenic; never 1-1000400,
                # pathogenic in its annotation alone.
                assert await kept(context, **rare, clinvar_pathogenic_override=True) == [
                    "1-1000100-G-A",
                    "1-1000200-AGG-AG",
                    "2-2000100-C-T",
                    "2-2000100-C-T,G",
                    "X-3000100-G-C,T",
                ]
                # A filter the override does not lift still holds on a transcript.
                assert await kept(context, **rare, clinvar_pathogenic_override=True, canonical_only=True) == [
                    "1-1000100-G-A",
                    "2-2000100-C-T",
                    "2-2000100-C-T,G",
                    "X-3000100-G-C,T",
                ]
                # Without a release no allele has ClinVar: the override lifts nothing.
                assert await kept(context, release=None, **rare, clinvar_pathogenic_override=True) == [
                    "2-2000100-C-T"
                ]

                # The ClinVar status filter: an allele the release classifies so. The
                # annotation's ClinVar is not read: 1-1000400 and 2-2000100-C-T are pathogenic
                # there.
                assert await kept(context, clinvar=["Pathogenic", "Likely pathogenic"]) == [
                    "1-1000100-G-A",
                    "1-1000200-AGG-AG",
                    "2-2000100-C-T,G",
                    "X-3000100-G-C,T",
                ]
                assert await kept(context, clinvar=["Pathogenic"]) == [
                    "1-1000100-G-A",
                    "2-2000100-C-T,G",
                    "X-3000100-G-C,T",
                ]
                assert await kept(context, clinvar=["Benign"]) == [
                    "2-2000100-C-T",
                    "2-2000100-C-T,A",
                    "2-2000100-C-T,G",
                ]
                assert await kept(context, clinvar=["Uncertain significance"]) == [
                    "3-4000300-CTT-CT",
                    "3-4000300-CTT-CT,C",
                ]
                # With another transcript filter.
                assert await kept(context, clinvar=["Pathogenic", "Benign"], impact=["HIGH"]) == ["2-2000100-C-T"]
                # Without a release no allele has a status.
                assert await kept(context, release=None, clinvar=["Pathogenic"]) == []

                # The excluded statuses leave a row out only where every allele has one: the
                # pathogenic X allele, the benign sibling of 2-2000100-C-T,G and 1-1000400 (not in
                # the release) stay.
                assert await kept(context, exclude_clinvar=["Pathogenic"]) == [
                    variant_id for variant_id in _EVERY_ROW if variant_id != "1-1000100-G-A"
                ]
                assert await kept(context, exclude_clinvar=["Benign"]) == [
                    variant_id for variant_id in _EVERY_ROW if variant_id != "2-2000100-C-T"
                ]
                # Every allele of 2-2000100-C-T,A excluded: left out; 2-2000100-C-T,G keeps its
                # pathogenic allele.
                assert await kept(context, exclude_clinvar=["Benign", "Likely benign"]) == [
                    variant_id for variant_id in _EVERY_ROW if variant_id not in {"2-2000100-C-T", "2-2000100-C-T,A"}
                ]
                # Without a release no row is left out for ClinVar.
                assert await kept(context, release=None, exclude_clinvar=["Benign"]) == _EVERY_ROW
        finally:
            tables = await execute_clickhouse(
                "SELECT name FROM system.tables WHERE database = %(database)s AND startsWith(name, %(prefix)s)",
                {"database": settings.clickhouse_database, "prefix": f"{dataset}/"},
            )
            for (name,) in tables or []:
                await execute_clickhouse(f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{name}`")

    run_closing_stores(_run)
