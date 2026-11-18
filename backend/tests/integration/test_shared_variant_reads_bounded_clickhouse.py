"""Each read that looks a few variants up in a table every family's import shares reads only
those variants' rows, and answers as a read of the whole table does — real ClickHouse.

The reads, and the shared table each looks its variants up in:

* the variant record a review save and the sign-out drift check read
  (``get_small_variant_family_record``): ``SNV_INDEL/variants/details``;
* a family's SV page (``_fetch_structural_variant_rows``) and an SV's classification evidence
  (``fetch_structural_variant_records``): ``SV/variants/details``;
* the NIPT artifact list's protections (``fetch_artifact_protection_flags``) and the
  auto-seed's ClinVar check (``fetch_recurrent_artifact_candidates``):
  ``SNV_INDEL/variants/annotation_index``, every annotation of the variant on the assembly.

A trio's small variants and SVs, three NIPT families' cfDNA calls and another family's
annotations of some of the same variants are written through the import's write path. The
tables are then filled with other families' rows, written in SQL, twice. After each fill,
every read answers as its reference, the same read of the whole table, and reads at most a
few granules (``read_rows`` in ``system.query_log``, found by the read's ``log_comment``),
with the same bound after the second fill; the reference reads every row of the table.

A family with more than four granules' worth of SVs is written the same way. Its SV page,
an SV's evidence and the write path's read of its stored rows
(``fetch_family_structural_variant_rows``) answer as the whole table does, and read the
family's own rows, in ``SV/entries`` and in ``SV/variants/details`` (sorted by the family
first), with at most a granule at either end: less than the details table holds.

The test writes to a ClickHouse dataset of its own (an assembly named for the run), so it
can merge each table to one part and count only its own granules. Synthetic data only.
Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from typing import Any, TypeVar
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import run_closing_stores

pytestmark = pytest.mark.integration

_T = TypeVar("_T")

# ClickHouse's index_granularity, with which the tables are created: rows per granule.
_GRANULE = 8192
# The granules each bounded read may read: a granule per variant it looks up in the shared
# table (three SVs at most here), and the family's own rows in ``entries``, a partition of
# its own, read once or twice.
_MAX_GRANULES = 6
# Other families' rows written to each shared table per fill.
_FILL_ROWS = 150_000
# The SVs of the family with many: more than four granules' worth, each with an annotation
# of its own, and the one whose evidence is read.
_MANY_SVS = 4 * _GRANULE + 1_000
_MANY_SAMPLE = "MANYPROBAND"
_MANY_EVIDENCE_INDEX = _MANY_SVS // 2

_TRIO = ("PROBAND", "MOTHER", "FATHER1")
_DIRECT_AND_IMPUTED = "7-1000101-C-T"
_TWO_DIRECT_CALLSETS = "7-1000201-G-A"
_IMPUTED_ONLY = "7-1000301-T-C"
_OTHER_FAMILYS_VARIANT = "7-1000401-A-G"
_SV_TWO_CALLERS = "7-2000001-2005000-DEL"
_SV_ONE_CALLER = "7-2100001-2100500-DUP"

_CFDNA = ("CFDNA1", "CFDNA2", "CFDNA3")
_RECURRENT = "9-3000101-C-A"
_RECURRENT_PATHOGENIC_ELSEWHERE = "9-3000201-G-T"
_COMMON_ELSEWHERE = "9-3000301-A-C"
_BENIGN_ELSEWHERE = "9-3000401-T-G"
_NOT_STORED = "9-3000501-A-T"


def _annotation(gene: str, **values: Any) -> dict[str, Any]:
    return {"gene_symbol": gene, "consequence": "missense_variant", "impact": "MODERATE", **values}


def _small(
    variant_id: str,
    calls: Sequence[Any],
    *,
    source: str = "clair3",
    annotations: Sequence[dict[str, Any]] = (),
) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(pos), end=int(pos),
        ref=ref, alt=alt, source=source, rsid=None, filters=["PASS"],
        gene_symbols=[str(annotation["gene_symbol"]) for annotation in annotations[:1]],
        annotations=list(annotations), calls=list(calls), qual=60.0,
    )


def _het(sample: str) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall

    return SmallVariantCall(sample=sample, gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)


def _low_vaf(sample: str) -> Any:
    """A cfDNA call at 5%: below the maternal boundary, above the read floors."""
    from backend.app.services.clickhouse_variant_records import SmallVariantCall

    return SmallVariantCall(sample=sample, gt="0/1", gq=99.0, dp=400, af=[0.05], ad=[380, 20], ps=None)


def _reference_call(sample: str) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall

    return SmallVariantCall(sample=sample, gt="0/0", gq=99.0, dp=400, af=[0.0], ad=[400, 0], ps=None)


def _sv(variant_id: str, source: str) -> Any:
    from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord

    chrom, start, end, sv_type = variant_id.split("-")
    return StructuralVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(start), end=int(end),
        sv_type=sv_type, source=source, remote_chr=None, remote_start=None, remote_end=None,
        sv_len=int(start) - int(end), filters=["PASS"], gene_symbols=["BNDSV"],
        annotations=[{"gene": "BNDSV", "caller": source}],
        calls=[
            StructuralVariantCall(sample=sample, gt="0/1", qual=30.0, read_support=12, filter="PASS")
            for sample in _TRIO[:2]
        ],
    )


def _many_sv(index: int) -> Any:
    """One SV of the family with many: a deletion on chromosome 8, with its own annotation."""
    from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord

    start = 1_000_001 + index * 1_000
    return StructuralVariantRecord(
        variant_key=None, variant_id=f"8-{start}-{start + 500}-DEL", chr="8", start=start, end=start + 500,
        sv_type="DEL", source="sniffles", remote_chr=None, remote_start=None, remote_end=None,
        sv_len=-500, filters=["PASS"], gene_symbols=["MANYSV"],
        annotations=[{"gene": "MANYSV", "index": index}],
        calls=[StructuralVariantCall(sample=_MANY_SAMPLE, gt="0/1", qual=30.0, read_support=12, filter="PASS")],
    )


def _family_context(
    family_uuid: str, family_id: str, samples: Sequence[str], project: str, assembly_name: str
) -> Any:
    """The family's read context: every member visible, the first one affected."""
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=family_id,
        project_ids=[project],
        sample_rows=[{"sample_id": sample} for sample in samples],
        sample_uuid_to_name={sample: sample for sample in samples},
        sample_name_to_uuid={sample: sample for sample in samples},
        affected_sample_names=[samples[0]],
        assembly_id=str(uuid4()),
        assembly_name=assembly_name,
    )


async def _write_families(
    assembly_name: str, trio_uuid: str, project: str, many_uuid: str, many_project: str
) -> None:
    from backend.app.services.clickhouse_variant_storage import (
        insert_small_variant_records,
        insert_structural_variant_records,
    )

    trio = [_het(sample) for sample in _TRIO]
    await insert_small_variant_records(
        assembly_name,
        trio_uuid,
        [project],
        [
            # The direct call and its imputation, each with an annotation of its own: the
            # record is the direct call, with the direct call's annotation.
            _small(_DIRECT_AND_IMPUTED, trio, annotations=[_annotation("BNDDIRECT")]),
            _small(_DIRECT_AND_IMPUTED, trio, source="glimpse2", annotations=[_annotation("BNDIMPUTED")]),
            # Two direct callsets: the one whose name sorts first.
            _small(_TWO_DIRECT_CALLSETS, trio, source="deepvariant", annotations=[_annotation("BNDDEEP")]),
            _small(_TWO_DIRECT_CALLSETS, trio, annotations=[_annotation("BNDCLAIR")]),
            _small(_IMPUTED_ONLY, trio, source="glimpse2", annotations=[_annotation("BNDONLYIMPUTED")]),
        ],
    )
    await insert_structural_variant_records(
        assembly_name,
        trio_uuid,
        [project],
        [_sv(_SV_TWO_CALLERS, "sniffles"), _sv(_SV_TWO_CALLERS, "spectre"), _sv(_SV_ONE_CALLER, "sniffles")],
    )
    # The family with many SVs, in a project of its own.
    await insert_structural_variant_records(
        assembly_name, many_uuid, [many_project], [_many_sv(index) for index in range(_MANY_SVS)]
    )
    # Another family: the trio's variant under another annotation version (another
    # annotation set of the same key), and the variants the NIPT protections read.
    other_uuid, other_project = str(uuid4()), str(uuid4())
    await insert_small_variant_records(
        assembly_name,
        other_uuid,
        [other_project],
        [
            _small(_DIRECT_AND_IMPUTED, [_het("OTHER1")], annotations=[_annotation("BNDOTHER")]),
            _small(_OTHER_FAMILYS_VARIANT, [_het("OTHER1")], annotations=[_annotation("BNDOTHER")]),
        ],
        annotation_version="vcf_info",
    )
    await insert_small_variant_records(
        assembly_name,
        other_uuid,
        [other_project],
        [
            _small(_RECURRENT_PATHOGENIC_ELSEWHERE, [_het("OTHER1")], annotations=[_annotation("BNDP", clinvar="Pathogenic")]),
            _small(_COMMON_ELSEWHERE, [_het("OTHER1")], annotations=[_annotation("BNDC", gnomad_af=0.31)]),
            _small(_BENIGN_ELSEWHERE, [_het("OTHER1")], annotations=[_annotation("BNDB", clinvar="Benign", gnomad_af=0.001)]),
        ],
    )
    # Three NIPT families: each plasma carries both recurrent variants at 5%, which the
    # father (a reference call) does not explain; their own rows have no ClinVar record.
    for index, cfdna in enumerate(_CFDNA, start=1):
        calls = [_low_vaf(cfdna), _reference_call(f"FATHER{index}")]
        await insert_small_variant_records(
            assembly_name,
            str(uuid4()),
            [str(uuid4())],
            [_small(_RECURRENT, calls), _small(_RECURRENT_PATHOGENIC_ELSEWHERE, calls)],
        )


_FILL_JSON = '{"annotations": [{"gene_symbol": "FILLGENE", "note": "' + "x" * 200 + '"}]}'
# Each filler row's chromosome (1 to 22, the test's own 7 and 9 among them) and position,
# spread over 1 to ~50 Mb; an indel, so no filler row is one of the test's variants.
_FILL_LOCI = (
    "SELECT number, toString(1 + modulo(number, 22)) AS c, toUInt32(1 + intDiv(number, 22) * 7 + %(round)s) AS p, "
    "toUInt64((1 + modulo(number, 22)) * 1000000000 + p) AS x, sipHash64('fill', %(round)s, number) AS k, "
    "concat('FILLFAMILY', toString(%(round)s), '-', toString(modulo(number, 40))) AS f "
    "FROM numbers(%(rows)s)"
)


async def _fill_with_other_families(assembly_name: str, round_: int) -> None:
    """``_FILL_ROWS`` rows of other families in each shared table and in each ``entries``
    table, then every table merged to one part (per partition)."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name, _structural_table_name

    params = {"round": round_, "rows": _FILL_ROWS, "json": _FILL_JSON, "project": f"FILLPROJECT{round_}"}
    statements = {
        _small_table_name(assembly_name, "variants/details"): (
            "key, variantId, annotation_version, annotationSetHash, chrom, pos, ref, alt, annotationsJson",
            "k, concat(c, '-', toString(p), '-A-AC'), 'current', sipHash64(k), c, p, 'A', 'AC', %(json)s",
        ),
        _small_table_name(assembly_name, "variants/annotation_index"): (
            "key, variantId, annotation_version, annotationSetHash, chrom, pos, ref, alt, clinvar_terms, max_gnomad_af",
            "k, concat(c, '-', toString(p), '-A-AC'), 'current', sipHash64(k), c, p, 'A', 'AC', ['benign'], 0.001",
        ),
        _small_table_name(assembly_name, "entries"): (
            "key, variantId, annotation_version, annotationSetHash, project_guid, family_guid, xpos, chrom, pos, "
            "ref, alt, source, `calls.sampleId`, `calls.gt`, sign",
            "k, concat(c, '-', toString(p), '-A-AC'), 'current', sipHash64(k), %(project)s, f, x, c, p, "
            "'A', 'AC', 'clair3', ['FILLSAMPLE'], ['0/1'], 1",
        ),
        _structural_table_name(assembly_name, "variants/details"): (
            "key, variantId, family_guid, chrom, start, end, svType, source, filters, annotationsJson",
            "k, concat(c, '-', toString(p), '-', toString(p + 1000), '-DEL'), f, c, p, p + 1000, 'DEL', "
            "'sniffles', ['PASS'], %(json)s",
        ),
        _structural_table_name(assembly_name, "entries"): (
            "key, variantId, project_guid, family_guid, chrom, start, end, svType, source, `calls.sampleId`, "
            "`calls.gt`, sign",
            "k, concat(c, '-', toString(p), '-', toString(p + 1000), '-DEL'), %(project)s, f, c, p, p + 1000, "
            "'DEL', 'sniffles', ['FILLSAMPLE'], ['0/1'], 1",
        ),
    }
    for table, (columns, values) in statements.items():
        await execute_clickhouse(f"INSERT INTO {table} ({columns}) SELECT {values} FROM ({_FILL_LOCI})", params)
    for table in statements:
        await execute_clickhouse(f"OPTIMIZE TABLE {table} FINAL")


async def _measured(label: str, read: Awaitable[_T]) -> _T:
    """``read``'s result, its queries tagged with ``label`` (``log_comment``)."""
    from backend.app.core.clickhouse import get_clickhouse_client

    client = await get_clickhouse_client()
    client.set_client_setting("log_comment", label)
    try:
        return await read
    finally:
        client.set_client_setting("log_comment", "")


async def _read_rows(label: str, *, table: str | None = None) -> int:
    """The rows the queries tagged ``label`` read; with ``table``, only the queries that read it."""
    from backend.app.core.clickhouse import execute_clickhouse

    await execute_clickhouse("SYSTEM FLUSH LOGS")
    params: dict[str, Any] = {"label": label}
    table_clause = ""
    if table is not None:
        table_clause = " AND has(tables, %(table)s)"
        params["table"] = table
    rows = await execute_clickhouse(
        "SELECT count(), sum(read_rows) FROM system.query_log"
        f" WHERE type = 'QueryFinish' AND log_comment = %(label)s{table_clause}",
        params,
    )
    assert rows and int(rows[0][0]) > 0, f"no query logged for {label}"
    return int(rows[0][1])


async def _row_count(table: str) -> int:
    from backend.app.core.clickhouse import execute_clickhouse

    return int((await execute_clickhouse(f"SELECT count() FROM {table}"))[0][0])


# The references: each read as it reads its shared table whole.


async def _reference_record(assembly_name: str, family_uuid: str, variant_id: str) -> Any:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_small_variants import _rows_to_variant_record
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.clickhouse_variant_queries import IMPUTED_SMALL_VARIANT_SOURCES

    rows = await execute_clickhouse(
        f"""
        SELECT e.key, e.variantId, e.gene_symbols, e.calls.sampleId, e.calls.gt, d.annotationsJson,
               e.annotation_version, e.annotationSetHash
        FROM {_small_table_name(assembly_name, "entries")} AS e
        LEFT JOIN {_small_table_name(assembly_name, "variants/details")} AS d
          ON d.key = e.key
         AND d.annotation_version = e.annotation_version
         AND d.annotationSetHash = e.annotationSetHash
        WHERE e.family_guid = %(family_guid)s AND e.variantId = %(variant_id)s AND e.sign = 1
        ORDER BY lowerUTF8(e.source) IN %(imputed_sources)s, e.source
        LIMIT 1
        """,
        {
            "family_guid": family_uuid,
            "variant_id": variant_id,
            "imputed_sources": tuple(IMPUTED_SMALL_VARIANT_SOURCES),
        },
    )
    return _rows_to_variant_record(rows)


def _whole_sv_details(assembly_name: str, _where_clauses: Sequence[str], _columns: Sequence[str]) -> str:
    from backend.app.services.clickhouse_variant_ids import _structural_table_name

    return _structural_table_name(assembly_name, "variants/details")


async def _reference_protection_flags(assembly_name: str, variant_ids: Sequence[str]) -> dict[str, str]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.variant_prioritization import clinvar_may_assert_pathogenic

    rows = await execute_clickhouse(
        f"""
        SELECT
            variantId,
            max(greatest(
                ifNull(max_gnomad_af, 0), ifNull(max_gnomad_exomes_af, 0),
                ifNull(max_gnomad_genomes_af, 0), ifNull(max_gnomad_popmax_af, 0),
                ifNull(max_topmed_af, 0)
            )) AS population_af,
            groupUniqArrayArray(clinvar_terms) AS terms
        FROM {_small_table_name(assembly_name, "variants/annotation_index")}
        WHERE variantId IN %(variant_ids)s
        GROUP BY variantId
        """,
        {"variant_ids": tuple(variant_ids)},
    )
    flags: dict[str, str] = {}
    for variant_id, population_af, terms in rows:
        if clinvar_may_assert_pathogenic([str(term) for term in terms or []]):
            flags[str(variant_id)] = "clinvar"
        elif population_af is not None and float(population_af) > 0.05:
            flags[str(variant_id)] = "common"
    return flags


async def _reference_recurrent(assembly_name: str, rules: Any) -> list[tuple[str, int]]:
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_family_variants import _UNEXPLAINED_LOW_VAF, _cfdna_sample_calls
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.app.services.variant_prioritization import clinvar_may_assert_pathogenic

    params: dict[str, Any] = {"carrier_ids": tuple(sorted(_CFDNA)), "min_samples": 2, "limit": 100_000}
    sample_calls = _cfdna_sample_calls(
        _small_table_name(assembly_name, "entries"),
        "NOT is_gnomad_gt_5_percent AND arrayExists(s -> s IN %(carrier_ids)s, `calls.sampleId`)",
        rules,
        params,
    )
    rows = await execute_clickhouse(
        f"""
        SELECT recurrent.variantId, recurrent.unexplained, clinvar.terms
        FROM (
            SELECT variantId, countIf({_UNEXPLAINED_LOW_VAF}) AS unexplained
            FROM ({sample_calls})
            GROUP BY variantId
            HAVING unexplained >= %(min_samples)s
        ) AS recurrent
        LEFT JOIN (
            SELECT variantId, groupUniqArrayArray(clinvar_terms) AS terms
            FROM {_small_table_name(assembly_name, "variants/annotation_index")}
            WHERE notEmpty(clinvar_terms)
            GROUP BY variantId
        ) AS clinvar ON clinvar.variantId = recurrent.variantId
        ORDER BY recurrent.unexplained DESC, recurrent.variantId
        LIMIT %(limit)s
        """,
        params,
    )
    return [
        (str(variant_id), int(unexplained or 0))
        for variant_id, unexplained, terms in rows
        if not clinvar_may_assert_pathogenic([str(term) for term in terms or []])
    ]


def test_shared_variant_reads_read_their_variants_rows_only() -> None:
    from backend.app.services import clickhouse_family_variants, structural_variant_evidence
    from backend.app.services.clickhouse_family_variants import (
        _fetch_structural_variant_rows,
        fetch_artifact_protection_flags,
        fetch_recurrent_artifact_candidates,
    )
    from backend.app.services.clickhouse_small_variants import get_small_variant_family_record
    from backend.app.services.clickhouse_variant_ids import _small_table_name, _structural_table_name
    from backend.app.services.clickhouse_variant_records import Region
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        fetch_family_structural_variant_rows,
    )
    from backend.app.services.family_variant_filters import StructuralVariantQueryFilters
    from backend.app.services.nipt_analysis import NiptQualityThresholds
    from backend.app.services.nipt_artifact_pg import recurrence_rules
    from backend.app.services.structural_variant_evidence import fetch_structural_variant_records

    tag = uuid4().hex[:8].upper()
    assembly_name = f"BOUNDREADS{tag}"
    trio_uuid, project = str(uuid4()), str(uuid4())
    context = _family_context(trio_uuid, "BOUNDED-TRIO", _TRIO, project, assembly_name)
    many_uuid, many_project = str(uuid4()), str(uuid4())
    many_context = _family_context(many_uuid, "BOUNDED-MANY", (_MANY_SAMPLE,), many_project, assembly_name)
    many_evidence_id = _many_sv(_MANY_EVIDENCE_INDEX).variant_id
    sv_page = StructuralVariantQueryFilters(page=1, page_size=50)
    rules = recurrence_rules(NiptQualityThresholds())
    protection_ids = [
        _RECURRENT,
        _RECURRENT_PATHOGENIC_ELSEWHERE,
        _COMMON_ELSEWHERE,
        _BENIGN_ELSEWHERE,
        _NOT_STORED,
    ]
    small_details = _small_table_name(assembly_name, "variants/details")
    sv_details = _structural_table_name(assembly_name, "variants/details")
    annotation_index = _small_table_name(assembly_name, "variants/annotation_index")
    bound = _MAX_GRANULES * _GRANULE
    # What a read of the family with many SVs may read: its own rows in ``SV/entries`` (a
    # partition of its own) twice, and its own rows in ``SV/variants/details``, each with at
    # most a granule at either end that it shares with other rows.
    many_bound = 3 * (_MANY_SVS + 2 * _GRANULE)

    async def _check(stage: str) -> None:
        def label(read: str) -> str:
            return f"bounded-reads-{tag}-{stage}-{read}"

        # The variant record, one per variant kind of row choice.
        for variant_id, gene in (
            (_DIRECT_AND_IMPUTED, "BNDDIRECT"),
            (_TWO_DIRECT_CALLSETS, "BNDCLAIR"),
            (_IMPUTED_ONLY, "BNDONLYIMPUTED"),
        ):
            read = label(f"record-{variant_id}")
            record = await _measured(
                read,
                get_small_variant_family_record(
                    assembly_name=assembly_name, family_guid=trio_uuid, variant_id=variant_id
                ),
            )
            reference = await _measured(
                f"{read}-whole", _reference_record(assembly_name, trio_uuid, variant_id)
            )
            assert record is not None and record == reference, (variant_id, record, reference)
            assert [annotation["gene_symbol"] for annotation in record.annotations] == [gene]
            assert record.sample_calls == {sample: "0/1" for sample in _TRIO}
            assert await _read_rows(read) <= bound, (read, await _read_rows(read))
            assert await _read_rows(f"{read}-whole") >= await _row_count(small_details)
        absent = await get_small_variant_family_record(
            assembly_name=assembly_name, family_guid=trio_uuid, variant_id=_OTHER_FAMILYS_VARIANT
        )
        assert absent is None

        # The family's SV page, the page within a region (its details read for the page's
        # scope without the region), and an SV's classification evidence.
        in_region = [Region(chr="7", start=2_100_000, end=2_100_200)]
        page = await _measured(label("sv-page"), _fetch_structural_variant_rows(context, sv_page))
        region_page = await _measured(
            label("sv-region-page"), _fetch_structural_variant_rows(context, sv_page, include_regions=in_region)
        )
        evidence = await _measured(label("sv-evidence"), fetch_structural_variant_records(context, _SV_TWO_CALLERS))
        with (
            patch.object(clickhouse_family_variants, "_structural_row_details", _whole_sv_details),
            patch.object(structural_variant_evidence, "_structural_row_details", _whole_sv_details),
        ):
            page_reference = await _measured(label("sv-page-whole"), _fetch_structural_variant_rows(context, sv_page))
            region_page_reference = await _measured(
                label("sv-region-page-whole"),
                _fetch_structural_variant_rows(context, sv_page, include_regions=in_region),
            )
            evidence_reference = await _measured(
                label("sv-evidence-whole"), fetch_structural_variant_records(context, _SV_TWO_CALLERS)
            )
        assert page == page_reference
        assert sorted((record.variant_id, record.source) for record in page) == [
            (_SV_TWO_CALLERS, "sniffles"),
            (_SV_TWO_CALLERS, "spectre"),
            (_SV_ONE_CALLER, "sniffles"),
        ]
        assert all(record.annotations and record.sv_len for record in page)
        assert region_page == region_page_reference
        assert [(record.variant_id, record.source) for record in region_page] == [(_SV_ONE_CALLER, "sniffles")]
        assert evidence == evidence_reference
        assert sorted(record.source for record in evidence) == ["sniffles", "spectre"]
        assert all(record.annotations for record in evidence)
        for read in ("sv-page", "sv-region-page", "sv-evidence"):
            assert await _read_rows(label(read)) <= bound, (read, await _read_rows(label(read)))
            assert await _read_rows(label(f"{read}-whole")) >= await _row_count(sv_details)

        # The family with many SVs: its page, an SV's evidence and the write path's read of
        # its stored rows read the family's own details, not every family's.
        many_page = await _measured(label("many-page"), _fetch_structural_variant_rows(many_context, sv_page))
        many_evidence = await _measured(
            label("many-evidence"), fetch_structural_variant_records(many_context, many_evidence_id)
        )
        many_stored = await _measured(
            label("many-stored"), fetch_family_structural_variant_rows(assembly_name, many_uuid)
        )
        with (
            patch.object(clickhouse_family_variants, "_structural_row_details", _whole_sv_details),
            patch.object(structural_variant_evidence, "_structural_row_details", _whole_sv_details),
        ):
            many_page_reference = await _measured(
                label("many-page-whole"), _fetch_structural_variant_rows(many_context, sv_page)
            )
            many_evidence_reference = await _measured(
                label("many-evidence-whole"), fetch_structural_variant_records(many_context, many_evidence_id)
            )
        assert many_page == many_page_reference
        assert len(many_page) == _MANY_SVS
        assert all(record.annotations == [{"gene": "MANYSV", "index": index}] for index, record in enumerate(many_page))
        assert many_evidence == many_evidence_reference
        assert [record.variant_id for record in many_evidence] == [many_evidence_id]

        # The write path's read gives each stored row the details the page gives it.
        def details(record: Any) -> tuple[Any, ...]:
            return (record.remote_chr, record.remote_start, record.sv_len, record.filters, record.annotations)

        assert len(many_stored) == _MANY_SVS
        assert {(row.record.variant_id, row.record.source): details(row.record) for row in many_stored} == {
            (record.variant_id, record.source): details(record) for record in many_page
        }
        # The bound is less than the details table holds: a read of the whole table exceeds it.
        assert many_bound < await _row_count(sv_details)
        for read in ("many-page", "many-evidence", "many-stored"):
            assert await _read_rows(label(read)) <= many_bound, (read, await _read_rows(label(read)))
        for read in ("many-page", "many-evidence"):
            assert await _read_rows(label(f"{read}-whole")) >= await _row_count(sv_details)

        # The NIPT protections read every annotation of the variant on the assembly: another
        # family's annotation protects a variant the plasmas' own rows do not.
        flags = await _measured(label("protection"), fetch_artifact_protection_flags(assembly_name, protection_ids))
        assert flags == await _measured(
            label("protection-whole"), _reference_protection_flags(assembly_name, protection_ids)
        )
        assert flags == {_RECURRENT_PATHOGENIC_ELSEWHERE: "clinvar", _COMMON_ELSEWHERE: "common"}
        assert await _read_rows(label("protection")) <= bound
        assert await _read_rows(label("protection-whole")) >= await _row_count(annotation_index)

        recurrent = await _measured(
            label("recurrent"),
            fetch_recurrent_artifact_candidates(
                assembly_name, carrier_samples=set(_CFDNA), rules=rules, min_unexplained_samples=2
            ),
        )
        assert recurrent == await _measured(label("recurrent-whole"), _reference_recurrent(assembly_name, rules))
        assert recurrent == [(_RECURRENT, 3)]
        # The auto-seed reads every plasma's calls in ``entries``; its annotation read is bounded.
        assert await _read_rows(label("recurrent"), table=annotation_index) <= bound
        assert await _read_rows(label("recurrent-whole"), table=annotation_index) >= await _row_count(
            annotation_index
        )

    async def _run() -> None:
        await ensure_clickhouse_variant_tables(assembly_name)
        await _write_families(assembly_name, trio_uuid, project, many_uuid, many_project)
        for round_ in (1, 2):
            await _fill_with_other_families(assembly_name, round_)
            assert await _row_count(small_details) >= round_ * _FILL_ROWS
            await _check(f"fill{round_}")

    run_closing_stores(_run)
