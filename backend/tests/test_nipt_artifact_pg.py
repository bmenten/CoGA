from __future__ import annotations

import pytest

from backend.app.services import clickhouse_family_variants, nipt_artifact_pg
from backend.app.services.nipt_artifact_pg import (
    auto_seed_nipt_artifacts,
    bulk_upsert_nipt_artifacts,
    load_nipt_artifact_ids,
)


@pytest.fixture(autouse=True)
def audit_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The clinical audit events the artifact service records (#683), captured."""
    recorded: list[dict] = []

    async def record(_session, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(nipt_artifact_pg, "record_clinical_event", record)
    return recorded


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_load_nipt_artifact_ids_returns_variant_id_set() -> None:
    session = _FakeSession([("1-100-A-G",), ("2-200-C-T",)])
    ids = await load_nipt_artifact_ids(
        session,  # type: ignore[arg-type]
        assembly_id="assembly-uuid",
        assay_key="nipt_cfdna",
    )
    assert ids == {"1-100-A-G", "2-200-C-T"}
    assert session.executed  # the query ran


@pytest.mark.asyncio
async def test_load_nipt_artifact_ids_short_circuits_without_assembly() -> None:
    session = _FakeSession([("1-100-A-G",)])
    ids = await load_nipt_artifact_ids(
        session,  # type: ignore[arg-type]
        assembly_id=None,
        assay_key="nipt_cfdna",
    )
    assert ids == set()
    assert session.executed == []


@pytest.mark.asyncio
async def test_fetch_recurrent_small_variant_ids_parses_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(query, params):
        assert "HAVING carriers >=" in query
        assert params["min_samples"] == 5
        # (variant, carriers, the variant's ClinVar terms across its annotations)
        return [("1-100-A-G", 12, []), ("2-200-C-T", 7, ["uncertain significance"])]

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", fake_execute)
    result = await clickhouse_family_variants.fetch_recurrent_small_variant_ids(
        "GRCh38", min_carrier_samples=5
    )
    assert result == [("1-100-A-G", 12), ("2-200-C-T", 7)]


@pytest.mark.asyncio
async def test_fetch_recurrent_small_variant_ids_guards() -> None:
    assert await clickhouse_family_variants.fetch_recurrent_small_variant_ids(
        "", min_carrier_samples=5
    ) == []


@pytest.mark.asyncio
async def test_fetch_recurrent_small_variant_ids_counts_only_the_given_samples_and_skips_common(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_execute(query, params):
        captured.update(query=query, params=params)
        return [("1-100-A-G", 5, [])]

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", fake_execute)
    result = await clickhouse_family_variants.fetch_recurrent_small_variant_ids(
        "GRCh38",
        min_carrier_samples=5,
        # ClickHouse stores a sample under its name or its UUID; both map to one sample.
        carrier_samples={"CF1": "CF1", "uuid-1": "CF1", "CF2": "CF2", "uuid-2": "CF2"},
    )

    assert result == [("1-100-A-G", 5)]
    assert "sample_id IN %(carrier_ids)s" in captured["query"]
    assert sorted(captured["params"]["carrier_ids"]) == ["CF1", "CF2", "uuid-1", "uuid-2"]
    # Every common variant recurs: the import-time > 5% population-frequency flag excludes it.
    assert "NOT is_gnomad_gt_5_percent" in captured["query"]


@pytest.mark.asyncio
async def test_fetch_recurrent_small_variant_ids_never_returns_a_clinvar_pathogenic_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A familial founder variant (CFTR F508del is ~1% in gnomAD, so not "common") reaches
    # five cfDNA samples of a disease-focused panel easily; seeded, it would be excluded
    # from every analysis of the assay.
    async def fake_execute(query, params):
        assert "clinvar_terms" in query
        return [
            ("1-100-A-G", 9, []),
            ("1-200-A-G", 8, ["pathogenic"]),
            ("1-300-A-G", 8, ["likely pathogenic", "uncertain significance"]),
            ("1-400-A-G", 7, ["conflicting classifications of pathogenicity"]),
            ("1-500-A-G", 6, ["uncertain significance"]),
            ("1-600-A-G", 5, ["benign", "likely benign"]),
        ]

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", fake_execute)
    result = await clickhouse_family_variants.fetch_recurrent_small_variant_ids(
        "GRCh38", min_carrier_samples=5
    )

    assert result == [("1-100-A-G", 9), ("1-500-A-G", 6), ("1-600-A-G", 5)]


@pytest.mark.asyncio
async def test_fetch_recurrent_small_variant_ids_with_no_carrier_samples_counts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_query(query, params):
        raise AssertionError("no sample in scope, so there is nothing to count")

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", no_query)
    assert await clickhouse_family_variants.fetch_recurrent_small_variant_ids(
        "GRCh38", min_carrier_samples=5, carrier_samples={}
    ) == []


class _AutoSeedResult:
    def __init__(self, first_row):
        self._first = first_row

    def first(self):
        return self._first


# (sample uuid, sample name, assay_panel) rows the cfDNA-sample lookup returns.
_DEFAULT_CFDNA_SAMPLES = [("uuid-1", "CF1", None), ("uuid-2", "CF2", None)]


class _AutoSeedSession:
    def __init__(self, assembly_name, cfdna_samples=None):
        self._assembly_name = assembly_name
        self._cfdna_samples = _DEFAULT_CFDNA_SAMPLES if cfdna_samples is None else cfdna_samples
        self.sample_queries: list = []
        self.bulk_rows = None
        self.commits = 0

    async def execute(self, statement, params=None):
        if "FROM assemblies" in str(statement):
            return _AutoSeedResult((self._assembly_name,) if self._assembly_name else None)
        if "FROM samples" in str(statement):
            self.sample_queries.append(params)
            return _FakeResult(self._cfdna_samples)
        self.bulk_rows = params
        return _AutoSeedResult(None)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_auto_seed_nipt_artifacts_upserts_recurrent(
    monkeypatch: pytest.MonkeyPatch, audit_events: list[dict]
) -> None:
    async def fake_recurrent(assembly_name, *, min_carrier_samples, **_kwargs):
        assert assembly_name == "GRCh38"
        assert min_carrier_samples == 5
        return [("1-100-A-G", 12), ("2-200-C-T", 7)]

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_small_variant_ids", fake_recurrent)
    session = _AutoSeedSession("GRCh38")

    result = await auto_seed_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="assembly-uuid",
        assay_key="nipt_cfdna",
        min_carrier_samples=5,
        actor="curator",
    )

    assert result == {"seeded": 2, "min_carrier_samples": 5}
    assert session.bulk_rows is not None and len(session.bulk_rows) == 2
    assert session.bulk_rows[0]["source"] == "auto"
    # One audit event on the list's own chain names every seeded variant, then one commit.
    [event] = audit_events
    assert event["family_identifier"] == nipt_artifact_pg.NIPT_ARTIFACT_AUDIT_CHAIN
    assert event["action"] == "nipt_artifacts_auto_seeded"
    assert event["actor"] == "curator"
    assert event["after"] == {
        "variants": [
            {"variant_id": "1-100-A-G", "recurrence_count": 12},
            {"variant_id": "2-200-C-T", "recurrence_count": 7},
        ]
    }
    assert session.commits == 1


@pytest.mark.asyncio
async def test_auto_seed_counts_recurrence_among_the_assays_own_cfdna_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Recurrence used to be counted over every sample on the assembly: all common SNPs
    # (the paternal sites FF is read from) and every other assay's artifacts qualified.
    seen: dict = {}

    async def fake_recurrent(assembly_name, *, min_carrier_samples, carrier_samples=None, **kwargs):
        seen.update(carrier_samples=carrier_samples, **kwargs)
        return [("1-100-A-G", 5)]

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_small_variant_ids", fake_recurrent)
    cfdna = [
        ("uuid-1", "CF1", None),  # no panel: the default scope
        ("uuid-2", "CF2", "  PANEL_A "),  # a panel of its own (trimmed, as nipt_assay_key does)
        ("uuid-3", "CF3", ""),  # a blank panel is the default scope too
    ]

    session = _AutoSeedSession("GRCh38", cfdna)
    result = await auto_seed_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="assembly-uuid",
        assay_key="nipt_cfdna",
    )
    assert session.sample_queries == [{"assay": "nipt_cfdna"}]
    assert seen["carrier_samples"] == {"CF1": "CF1", "uuid-1": "CF1", "CF3": "CF3", "uuid-3": "CF3"}
    assert seen["exclude_common"] is True
    assert seen["exclude_clinvar_pathogenic"] is True
    assert result == {"seeded": 1, "min_carrier_samples": 5}

    await auto_seed_nipt_artifacts(
        _AutoSeedSession("GRCh38", cfdna),  # type: ignore[arg-type]
        assembly_id="assembly-uuid",
        assay_key="PANEL_A",
    )
    assert seen["carrier_samples"] == {"CF2": "CF2", "uuid-2": "CF2"}


@pytest.mark.asyncio
async def test_auto_seed_without_cfdna_samples_for_the_assay_seeds_nothing(
    monkeypatch: pytest.MonkeyPatch, audit_events: list[dict]
) -> None:
    async def fake_recurrent(assembly_name, *, min_carrier_samples, carrier_samples=None, **_kwargs):
        assert carrier_samples == {}
        return []

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_small_variant_ids", fake_recurrent)
    session = _AutoSeedSession("GRCh38", [("uuid-2", "CF2", "PANEL_A")])

    result = await auto_seed_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="assembly-uuid",
        assay_key="nipt_cfdna",
    )

    assert result == {"seeded": 0, "min_carrier_samples": 5}
    assert session.bulk_rows is None
    assert audit_events == []  # nothing changed, nothing to audit


@pytest.mark.asyncio
async def test_auto_seed_nipt_artifacts_missing_assembly_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_recurrent(*_args, **_kwargs):
        return []

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_small_variant_ids", fake_recurrent)
    session = _AutoSeedSession(None)

    with pytest.raises(Exception) as excinfo:
        await auto_seed_nipt_artifacts(
            session,  # type: ignore[arg-type]
            assembly_id="missing",
            assay_key="nipt_cfdna",
            min_carrier_samples=5,
        )
    assert "Assembly not found" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bulk_upsert_nipt_artifacts_empty_is_noop() -> None:
    session = _AutoSeedSession("GRCh38")
    seeded = await bulk_upsert_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="assembly-uuid",
        assay_key="nipt_cfdna",
        items=[],
    )
    assert seeded == 0
    assert session.bulk_rows is None


# --------------------------------------------------------------------------- #
# Importing a recurrent-artefact table (the R NIPT-M pipeline's list)
# --------------------------------------------------------------------------- #

_R_TABLE = (
    "variant_key\tCHROM\tPOS\tREF\tALT\tn_cfdna_families\trecurrent_filter_profile\tfilter_as_recurrent_artifact\n"
    "chr1:100:A:G\tchr1\t100\tA\tG\t3\trecurrent_low_vaf_noise\tTRUE\n"
    "chr1:200:C:T\tchr1\t200\tC\tT\t4\trecurrent_high_vaf_background\tTRUE\n"
    "chr1:300:G:A\tchr1\t300\tG\tA\t5\trecurrent_moderate_vaf_background\tTRUE\n"
    "chr2:400:T:C\tchr2\t400\tT\tC\t2\trecurrent_truth_protected\tFALSE\n"
    "chr2:500:T:C,G\tchr2\t500\tT\tC,G\t2\trecurrent_multi_alt_site\tTRUE\n"
)


def test_parse_artifact_table_reads_the_flagged_alleles() -> None:
    table = nipt_artifact_pg.parse_artifact_table(_R_TABLE)
    assert [(item.variant_id, item.recurrence_count, item.label) for item in table.items] == [
        ("1-100-A-G", 3, "recurrent (recurrent_low_vaf_noise)"),
        ("1-200-C-T", 4, "recurrent (recurrent_high_vaf_background)"),
        ("1-300-G-A", 5, "recurrent (recurrent_moderate_vaf_background)"),
    ]
    assert (table.rows_read, table.not_flagged, table.invalid) == (5, 1, 1)


def test_parse_artifact_table_reads_a_plain_allele_list() -> None:
    table = nipt_artifact_pg.parse_artifact_table("CHROM\tPOS\tREF\tALT\nchr7\t1000\ta\tg\n")
    assert [item.variant_id for item in table.items] == ["7-1000-A-G"]
    assert table.items[0].label == "recurrent (imported)"


def test_parse_artifact_table_refuses_a_table_without_alleles() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        nipt_artifact_pg.parse_artifact_table("gene\tcount\nGENEA\t3\n")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_import_never_lists_a_common_or_pathogenic_allele(
    monkeypatch: pytest.MonkeyPatch, audit_events: list[dict]
) -> None:
    async def assembly_name(_session, _assembly_id):
        return "GRCh38"

    async def flags(_assembly_name, variant_ids, **_kwargs):
        assert variant_ids == ["1-100-A-G", "1-200-C-T", "1-300-G-A"]
        return {"1-200-C-T": "common", "1-300-G-A": "clinvar"}

    class _Session:
        def __init__(self) -> None:
            self.executed: list = []
            self.commits = 0

        async def execute(self, statement, params=None):
            self.executed.append((str(statement), params))

        async def commit(self) -> None:
            self.commits += 1

    monkeypatch.setattr(nipt_artifact_pg, "_resolve_assembly_name", assembly_name)
    monkeypatch.setattr(nipt_artifact_pg, "fetch_artifact_protection_flags", flags)
    session = _Session()
    summary = await nipt_artifact_pg.import_nipt_artifact_table(
        session,  # type: ignore[arg-type]
        assembly_id="assembly-uuid",
        assay_key="panel-v1",
        text_value=_R_TABLE,
        filename="recurrent.tsv",
        actor="admin",
    )
    assert summary == {
        "rows_read": 5,
        "not_flagged": 1,
        "invalid": 1,
        "imported": 1,
        "protected_common": 1,
        "protected_clinvar": 1,
    }
    [(statement, rows)] = session.executed
    assert "INSERT INTO nipt_artifact_variants" in statement and "'curated'" in statement
    assert [row["variant_id"] for row in rows] == ["1-100-A-G"]
    assert session.commits == 1
    # One audit event names the file and every allele it added.
    [event] = audit_events
    assert event["action"] == "nipt_artifacts_imported"
    assert event["metadata"]["filename"] == "recurrent.tsv"
    assert event["metadata"]["protected"] == ["1-200-C-T", "1-300-G-A"]
    assert [item["variant_id"] for item in event["after"]["variants"]] == ["1-100-A-G"]


@pytest.mark.asyncio
async def test_protection_flags_read_the_pooled_annotation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute(query, params):
        assert "variants/annotation_index" in query
        assert params["variant_ids"] == ("1-1-A-G", "1-2-A-G", "1-3-A-G")
        return [
            ("1-1-A-G", 0.12, []),
            ("1-2-A-G", 0.001, ["Pathogenic"]),
            ("1-3-A-G", 0.001, ["Benign"]),
        ]

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", fake_execute)
    flags = await clickhouse_family_variants.fetch_artifact_protection_flags(
        "GRCh38", ["1-1-A-G", "1-2-A-G", "1-3-A-G"]
    )
    assert flags == {"1-1-A-G": "common", "1-2-A-G": "clinvar"}
