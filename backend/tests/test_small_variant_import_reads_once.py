"""A whole-genome small-variant import reads each annotation once, and stores what it stored
when it read each one many times.

The import's time goes on reading VEP's CSQ entries and building each variant's rows from
them. A CSQ header's entries are read by alias tables narrowed to the fields that header
declares; a record's INFO without an annotation field is not read at all; each annotation is
read and serialized once for its own row, the variant's index rows and both hashes. These
tests hold each shortcut to the full reading it replaces, and the variant tables to
synchronous inserts.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.app.services import clickhouse_variant_rows, clickhouse_variant_storage
from backend.app.services.clickhouse_variant_ids import _stable_uint64
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.clickhouse_variant_rows import (
    _annotation_canonical_json,
    _annotation_set_hash,
    _small_variant_entry_rows,
)
from backend.app.services.variant_annotation_parser import (
    _ALL_ALIASES,
    _base_info_annotation,
    _header_alias_tables,
    _normalize_annotation_entry,
)

_FIELDS = (
    "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature|BIOTYPE|HGVSc|HGVSp|Existing_variation|"
    "gnomADe_AF|gnomADg_AF|MAX_AF|gnomADe_nhomalt|gnomADg_nhomalt|CLIN_SIG|SpliceAI_pred_DS_AG|"
    "SpliceAI_pred_DS_DL|CADD_PHRED|REVEL|LoF|UNKNOWN_FIELD|SYMBOL"
).split("|")


def _entry(values: dict[str, str], *, length: int | None = None) -> dict[str, str]:
    row = [values.get(field, "") for field in _FIELDS]
    return dict(zip(_FIELDS, row[: len(row) if length is None else length]))


_ENTRIES = [
    _entry(
        {
            "Allele": "G",
            "Consequence": "missense_variant&splice_region_variant",
            "IMPACT": "MODERATE",
            "SYMBOL": "GENE1",
            "Gene": "ENSG1",
            "Feature": "ENST1",
            "HGVSc": " ENST1:c.1A>G ",
            "gnomADe_AF": "0.01",
            "gnomADg_AF": "0.02",
            "MAX_AF": "0.03",
            "gnomADe_nhomalt": "4",
            "gnomADg_nhomalt": "NA",
            "CLIN_SIG": "Pathogenic/Likely_pathogenic",
            "SpliceAI_pred_DS_AG": "0.5",
            "CADD_PHRED": "25.1",
            "REVEL": ".",
            "LoF": "HC",
            "UNKNOWN_FIELD": "kept nowhere",
        }
    ),
    _entry({"Consequence": "intron_variant", "IMPACT": "MODIFIER", "SYMBOL": "GENE2"}),
    _entry({"Consequence": "upstream_gene_variant", "SYMBOL": "GENE3", "gnomADe_AF": "x"}, length=6),
    _entry({}),
]


@pytest.mark.parametrize(
    "base_annotation",
    [
        {},
        # What a record's INFO set: the entries of a header without CANONICAL or MANE_SELECT
        # clear the flags, as the full tables do.
        {"canonical": True, "mane_select": True, "gene": "INFO_GENE", "population_frequencies": {"topmed_af": 0.2}},
    ],
    ids=["no INFO annotation", "INFO annotation"],
)
def test_a_csq_entry_reads_the_same_by_its_headers_own_alias_tables(base_annotation: dict[str, Any]) -> None:
    tables = _header_alias_tables(tuple(_FIELDS))

    for entry in _ENTRIES:
        narrowed = _normalize_annotation_entry(entry, base_annotation, tables)
        full = _normalize_annotation_entry(entry, base_annotation, _ALL_ALIASES)
        # The same values, in the same order: the details' annotation JSON keeps the order.
        assert list(narrowed.items()) == list(full.items())
    assert "canonical" not in _normalize_annotation_entry(_ENTRIES[0], base_annotation, tables)


def test_a_headers_alias_tables_keep_only_the_aliases_it_declares_and_the_flags() -> None:
    tables = _header_alias_tables(tuple(_FIELDS))

    ann = dict(tables.ann)
    assert ann["gene"] == ("SYMBOL",)
    assert ann["gene_id"] == ("Gene",)
    assert ann["canonical"] == ann["mane_select"] == ann["mane_plus_clinical"] == ()
    assert "exon" not in ann and "sift" not in ann
    assert dict(tables.population) == {"gnomad_exomes_af": ("gnomADe_AF",), "gnomad_genomes_af": ("gnomADg_AF",), "gnomad_popmax_af": ("MAX_AF",)}
    assert dict(tables.count) == {"gnomad_hom_count": ((), ("gnomADe_nhomalt",), ("gnomADg_nhomalt",))}
    assert dict(tables.extra_count) == {}
    assert _header_alias_tables(tuple(_FIELDS)) is tables  # made once per header


@pytest.mark.parametrize(
    "info, expected",
    [
        ({"CSQ": "G|intron_variant"}, {}),
        ({"CSQ": "G|intron_variant", "DP": "30", "UNKNOWN_FIELD": "x"}, {}),
        ({}, {}),
        # Read by name, not by an alias table.
        (
            {"CSQ": "G|x", "SpliceAI_pred": "G|GENE1|0.10|0.20|0.70|0.00|1|2|3|4"},
            {"spliceai_ds_ag": 0.1, "spliceai_ds_al": 0.2, "spliceai_ds_dg": 0.7, "spliceai_ds_dl": 0.0, "spliceai_max": 0.7},
        ),
        ({"CANONICAL": "YES", "CADD_PHRED": "12"}, {"canonical": True, "cadd_phred": 12.0}),
    ],
    ids=["CSQ alone", "no annotation field", "empty", "SpliceAI_pred", "annotation fields"],
)
def test_a_records_info_is_read_only_when_it_holds_an_annotation_field(info: dict[str, str], expected: dict[str, Any]) -> None:
    assert _base_info_annotation(info) == expected


@pytest.mark.parametrize(
    "annotations",
    [
        [],
        [{}],
        [{"gene": "GENE1", "population_frequencies": {"gnomad_af": 0.1}, "extra": {"b": [1, 2]}}, {"impact": "HIGH"}],
        [{"z": 1.5e-07, "a": "ü", "m": None, "n": float("inf"), "s": {"value"}}],
    ],
    ids=["none", "empty", "two", "odd values"],
)
def test_the_annotation_set_hash_from_each_annotations_json_is_the_hash_of_their_list(
    annotations: list[dict[str, Any]],
) -> None:
    payloads = [_annotation_canonical_json(annotation) for annotation in annotations]
    listed = json.dumps(annotations, sort_keys=True, separators=(",", ":"), default=str)

    assert _annotation_set_hash(annotations, payloads) == _annotation_set_hash(annotations)
    assert _annotation_set_hash(annotations, payloads) == _stable_uint64("small_annotation_set", listed)


def _record(annotations: list[dict[str, Any]], *, gene_symbols: list[str] | None = None) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=None,
        variant_id="1-1000-A-G",
        chr="chr1",
        start=1000,
        end=1000,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=["PASS"],
        gene_symbols=gene_symbols or [],
        annotations=annotations,
        calls=[SmallVariantCall(sample="PROBAND", gt="0/1", gq=40.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
    )


_ANNOTATIONS: list[dict[str, Any]] = [
    {
        "gene": "GENE1",
        "gene_id": "ENSG1",
        "transcript_id": "ENST1",
        "impact": "MODERATE",
        "effect": "missense_variant&splice_region_variant",
        "clinvar": "Pathogenic/Likely_pathogenic",
        "canonical": True,
        "gnomad_af": 0.01,
        "population_frequencies": {"gnomad_exomes_af": 0.04, "topmed_af": "0.2"},
        "extra": {"gnomad_ac": 7},
        "cadd_phred": 25.0,
        "spliceai_ds_dl": 0.3,
        "rsid": "rs1",
    },
    {
        "geneSymbol": "Gene2",
        "geneId": "ENSG2",
        "majorConsequence": "intron_variant",
        "maneSelect": "yes",
        "gnomadAf": 0.5,
        "gnomad_hom_count": "3",
        "lof": "HC",
        "revel": 0.9,
    },
    {"gene": "GENE1", "impact": "MODIFIER", "effect": "intron_variant"},
]


def test_a_variants_index_rows_gather_its_annotations_rows() -> None:
    _details, _entries, annotation_rows, (index_row,), gene_rows = _small_variant_entry_rows(
        "GRCh38", "family-uuid", ["project-uuid"], [_record(_ANNOTATIONS, gene_symbols=["Gene0"])]
    )

    # An annotations row's values, from column 9: gene, gene id, transcript, HGVS c. and p.,
    # impact, effects, ClinVar terms, the three flags, LoF, five frequencies, three counts,
    # CADD, REVEL, SpliceAI, SIFT and PolyPhen. The index row's, from column 8: the rsid,
    # then each gathered over the annotations.
    assert [row[9] for row in annotation_rows] == ["gene1", "gene2", "gene1"]
    assert index_row[8] == "rs1"
    assert index_row[9:14] == (["gene1", "gene2"], ["ensg1", "ensg2"], [], [], ["moderate", "modifier"])
    assert sorted(index_row[14]) == ["intron_variant", "missense_variant", "splice_region_variant"]
    assert sorted(index_row[15]) == ["likely pathogenic", "pathogenic"]
    assert index_row[16:20] == (True, True, False, True)  # canonical, MANE Select, MANE Plus, LoF
    assert index_row[20] == max(row[21] for row in annotation_rows if row[21] is not None) == 0.5
    assert index_row[21] == annotation_rows[0][22] == pytest.approx(0.04)
    assert index_row[24] == annotation_rows[0][25] == pytest.approx(0.2)
    assert index_row[25:28] == (7, 3, None)
    assert index_row[28:31] == (25.0, 0.9, 0.3)
    assert [row[4] for row in gene_rows] == ["gene0", "gene1", "gene2", "ensg1", "ensg2"]


def test_a_variant_without_annotations_stores_one_empty_annotations_row() -> None:
    _details, _entries, annotation_rows, (index_row,), gene_rows = _small_variant_entry_rows(
        "GRCh38", "family-uuid", ["project-uuid"], [_record([])]
    )

    (row,) = annotation_rows
    assert row[3] == _stable_uint64("small_annotation_set", "[]")
    assert row[4] == _stable_uint64("small_annotation", "{}")
    assert index_row[9:17] == ([], [], [], [], [], [], [], False)
    assert gene_rows == []


def test_each_annotation_is_read_and_serialized_once_for_all_its_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[dict[str, Any]] = []
    serialized: list[Any] = []
    read = clickhouse_variant_rows._annotation_population_frequencies
    serialize = clickhouse_variant_rows._annotation_canonical_json

    def counted_read(annotation: dict[str, Any]) -> dict[str, float]:
        reads.append(annotation)
        return read(annotation)

    def counted_serialize(annotation: Any) -> str:
        serialized.append(annotation)
        return serialize(annotation)

    monkeypatch.setattr(clickhouse_variant_rows, "_annotation_population_frequencies", counted_read)
    monkeypatch.setattr(clickhouse_variant_rows, "_annotation_canonical_json", counted_serialize)

    _small_variant_entry_rows("GRCh38", "family-uuid", ["project-uuid"], [_record(_ANNOTATIONS)])

    # Once each: before, the five frequencies of the annotations row and of the index row
    # each read the annotation again (ten reads), and the set hash serialized them again.
    assert reads == _ANNOTATIONS
    assert serialized == _ANNOTATIONS


@pytest.mark.asyncio
async def test_the_variant_rows_are_written_as_synchronous_inserts(monkeypatch: pytest.MonkeyPatch) -> None:
    inserts: list[tuple[str, int, dict[str, Any] | None]] = []

    async def insert(query: str, data: list[tuple[Any, ...]], *, settings: dict[str, Any] | None = None) -> None:
        table = query.split("INSERT INTO", 1)[1].split("(", 1)[0].strip()
        inserts.append((table, len(data), settings))

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(clickhouse_variant_storage, "insert_clickhouse", insert)
    monkeypatch.setattr(clickhouse_variant_storage, "ensure_clickhouse_variant_tables", no_op)

    await clickhouse_variant_storage.insert_small_variant_records(
        "GRCh38", "family-uuid", ["project-uuid"], [_record(_ANNOTATIONS)]
    )

    assert [table.rsplit("/", 1)[-1] for table, _rows, _settings in inserts] == [
        "details`",
        "entries`",
        "annotations`",
        "annotation_index`",
        "gene_index`",
        "family_data_version`",
    ]
    # Stored when the insert returns, not buffered until ClickHouse's async-insert timer.
    assert all(settings == {"async_insert": 0} for _table, _rows, settings in inserts)


@pytest.mark.asyncio
async def test_an_insert_passes_its_settings_to_clickhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core import clickhouse

    calls: list[dict[str, Any]] = []

    class Client:
        async def insert(self, **kwargs: Any) -> str:
            calls.append(kwargs)
            return "summary"

    async def client() -> Client:
        return Client()

    monkeypatch.setattr(clickhouse, "get_clickhouse_client", client)

    query = "INSERT INTO coga.`GRCh38/SNV_INDEL/entries` (key, sign) VALUES"
    assert await clickhouse.insert_clickhouse(query, [(1, 1)], settings={"async_insert": 0}) == "summary"
    assert await clickhouse.insert_clickhouse(query, [(2, 1)]) == "summary"

    assert [call.get("settings") for call in calls] == [{"async_insert": 0}, None]
    assert "settings" not in calls[1]  # an insert without settings is sent as before
    assert calls[0]["column_names"] == ["key", "sign"]
