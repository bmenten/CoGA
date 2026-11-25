from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.services import clickhouse_family_variants, nipt_artifact_pg
from backend.app.services.nipt_analysis import NiptQualityThresholds
from backend.app.services.nipt_artifact_pg import (
    auto_seed_nipt_artifacts,
    bulk_upsert_nipt_artifacts,
    load_nipt_artifacts,
    recurrence_rules,
)

# How the auto-seed reads a call: the NIPT quality filter's defaults.
_RULES = recurrence_rules(NiptQualityThresholds())


@pytest.fixture(autouse=True)
def audit_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The clinical audit events the artifact service records, captured."""
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
async def test_load_nipt_artifacts_returns_each_allele_with_its_recurrence() -> None:
    session = _FakeSession([("1-100-A-G", 4), ("2-200-C-T", 0)])
    listed = await load_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        assay_key="nipt_cfdna",
    )
    assert listed == {"1-100-A-G": 4, "2-200-C-T": 0}
    # One query, scoped to the assembly and the assay.
    [(statement, params)] = session.executed
    assert "FROM nipt_artifact_variants" in statement
    assert params == {"assembly_id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "assay_key": "nipt_cfdna"}


@pytest.mark.asyncio
async def test_load_nipt_artifacts_short_circuits_without_assembly() -> None:
    session = _FakeSession([("1-100-A-G", 4)])
    listed = await load_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id=None,
        assay_key="nipt_cfdna",
    )
    assert listed == {}
    assert session.executed == []


def test_recurrence_rules_read_calls_by_the_quality_filter_and_the_fathers_classes() -> None:
    assert (_RULES.min_alt_reads, _RULES.min_alt_fraction) == (5, 0.01)
    assert _RULES.maternal_vaf_boundary == 0.25
    assert (_RULES.father_min_depth, _RULES.father_min_alt_reads, _RULES.father_min_quality, _RULES.father_min_vaf) == (
        20,
        5,
        20.0,
        0.20,
    )
    # A call the father's caller genotyped (HaplotypeCaller) is read from its GT, with the
    # R pipeline's QUAL and GQ floors.
    assert (_RULES.father_min_genotyped_quality, _RULES.father_min_gq) == (50.0, 20.0)


async def _candidates(**kwargs):
    return await clickhouse_family_variants.fetch_recurrent_artifact_candidates(
        "GRCh38",
        **{"carrier_samples": ["CF2", "CF1"], "rules": _RULES, "min_unexplained_samples": 3, **kwargs},
    )


@pytest.mark.asyncio
async def test_artifact_candidates_count_unexplained_low_vaf_calls_of_the_given_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_execute(query, params):
        if "variants/annotation_index" in query:
            # (variant, its highest population frequency, its ClinVar terms across its annotations)
            return [("2-200-C-T", 0.001, ["uncertain significance"])]
        captured.update(query=query, params=params)
        # (variant, unexplained low-VAF samples)
        return [("1-100-A-G", 12), ("2-200-C-T", 3)]

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", fake_execute)

    assert await _candidates() == [("1-100-A-G", 12), ("2-200-C-T", 3)]
    query, params = captured["query"], captured["params"]
    # Only the assay's cfDNA samples are read, each once per allele.
    assert "sample_id IN %(carrier_ids)s" in query
    assert "GROUP BY variantId, sample_id" in query
    assert params["carrier_ids"] == ("CF1", "CF2")
    # What counts: a call below the maternal boundary its family's father does not explain,
    # never every carrier (a maternal het, or the fetus's paternal allele, recurs too).
    assert "countIf(isNotNull(sample_vaf) AND sample_vaf < %(maternal_vaf)s AND NOT explained)" in query
    assert "HAVING unexplained >= %(min_samples)s" in query
    assert "s NOT IN %(carrier_ids)s" in query  # the father is a sample of the row outside the assay
    assert params["min_samples"] == 3
    assert (params["min_alt_reads"], params["min_alt_fraction"], params["maternal_vaf"]) == (5, 0.01, 0.25)
    assert (
        params["father_min_depth"],
        params["father_min_alt_reads"],
        params["father_min_vaf"],
        params["father_min_quality"],
    ) == (20, 5, 0.2, 20.0)
    # Every common variant recurs: the import-time > 5% population-frequency flag excludes it.
    assert "NOT is_gnomad_gt_5_percent" in query
    # Every family's calls count: none is left out.
    assert "family_guid" not in query


@pytest.mark.asyncio
async def test_artifact_candidates_never_hold_a_clinvar_pathogenic_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A familial founder variant (CFTR F508del is ~1% in gnomAD, so not "common") recurs in
    # a disease-focused panel; seeded, it would be excluded from every analysis of the assay.
    recurrent = [
        ("1-100-A-G", 9),
        ("1-200-A-G", 8),
        ("1-300-A-G", 8),
        ("1-400-A-G", 7),
        ("1-500-A-G", 6),
        ("1-600-A-G", 5),
    ]
    clinvar = {
        "1-200-A-G": ["pathogenic"],
        "1-300-A-G": ["likely pathogenic", "uncertain significance"],
        "1-400-A-G": ["conflicting classifications of pathogenicity"],
        "1-500-A-G": ["uncertain significance"],
        "1-600-A-G": ["benign", "likely benign"],
    }
    annotation_reads: list = []

    async def fake_execute(query, params):
        if "variants/annotation_index" not in query:
            return recurrent
        annotation_reads.append(params)
        # Every annotation of each candidate on the assembly, read at its own locus.
        assert "clinvar_terms" in query and "(chrom, pos) IN %(loci)s" in query
        return [(variant_id, 0.001, terms) for variant_id, terms in clinvar.items()]

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", fake_execute)

    assert await _candidates() == [("1-100-A-G", 9), ("1-500-A-G", 6), ("1-600-A-G", 5)]
    [params] = annotation_reads
    assert params["variant_ids"] == tuple(variant_id for variant_id, _count in recurrent)
    assert params["loci"] == tuple(("1", position) for position in (100, 200, 300, 400, 500, 600))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"carrier_samples": []}, {"min_unexplained_samples": 0}],
    ids=["no cfDNA sample in scope", "no threshold"],
)
async def test_artifact_candidates_without_samples_or_threshold_read_nothing(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict
) -> None:
    async def no_query(query, params):
        raise AssertionError("nothing to count")

    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", no_query)
    assert await _candidates(**kwargs) == []
    assert await clickhouse_family_variants.fetch_recurrent_artifact_candidates(
        "", carrier_samples=["CF1"], rules=_RULES, min_unexplained_samples=3
    ) == []


class _AutoSeedResult:
    def __init__(self, first_row):
        self._first = first_row

    def first(self):
        return self._first


# (sample ID, assay_panel) rows the cfDNA-sample lookup returns.
_DEFAULT_CFDNA_SAMPLES = [("CF1", None), ("CF2", None)]


class _AutoSeedSession:
    def __init__(self, assembly_name, cfdna_samples=None, listed=None):
        self._assembly_name = assembly_name
        self._cfdna_samples = _DEFAULT_CFDNA_SAMPLES if cfdna_samples is None else cfdna_samples
        # (variant ID, source) of the entries the scope's list holds already.
        self._listed = listed or []
        self.sample_queries: list = []
        self.listed_queries: list = []
        self.bulk_rows = None
        self.commits = 0

    async def execute(self, statement, params=None):
        if "FROM assemblies" in str(statement):
            return _AutoSeedResult((self._assembly_name,) if self._assembly_name else None)
        if "FROM samples" in str(statement):
            self.sample_queries.append(params)
            return _FakeResult(self._cfdna_samples)
        if "SELECT variant_id, source" in str(statement):
            self.listed_queries.append(params)
            return _FakeResult(self._listed)
        self.bulk_rows = params
        return _AutoSeedResult(None)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_auto_seed_nipt_artifacts_upserts_the_unexplained_recurrent_alleles(
    monkeypatch: pytest.MonkeyPatch, audit_events: list[dict]
) -> None:
    async def fake_candidates(assembly_name, *, carrier_samples, rules, min_unexplained_samples):
        assert assembly_name == "GRCh38"
        assert (rules, min_unexplained_samples) == (_RULES, 4)
        return [("1-100-A-G", 12), ("2-200-C-T", 7)]

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_artifact_candidates", fake_candidates)
    # The second allele is curated on the list already: the seed refreshes its count only.
    session = _AutoSeedSession("GRCh38", listed=[("2-200-C-T", "curated"), ("3-300-G-A", "auto")])

    result = await auto_seed_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        assay_key="nipt_cfdna",
        min_unexplained_samples=4,
        actor="curator",
    )

    # What it wrote: the samples it read and each seeded allele, with the source of the entry
    # it was on the list with already.
    assert result == {
        "assay_key": "nipt_cfdna",
        "min_unexplained_samples": 4,
        "cfdna_samples": 2,
        "variants": [
            {"variant_id": "1-100-A-G", "recurrence_count": 12, "listed_source": None},
            {"variant_id": "2-200-C-T", "recurrence_count": 7, "listed_source": "curated"},
        ],
        "seeded": 2,
    }
    assert session.listed_queries == [
        {"assembly_id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "assay_key": "nipt_cfdna"}
    ]
    assert session.bulk_rows is not None and len(session.bulk_rows) == 2
    # Each entry keeps its unexplained count as its recurrence.
    assert [(row["variant_id"], row["recurrence_count"], row["source"]) for row in session.bulk_rows] == [
        ("1-100-A-G", 12, "auto"),
        ("2-200-C-T", 7, "auto"),
    ]
    # One audit event on the list's own chain names every seeded variant, the threshold and
    # how the calls were read, then one commit.
    [event] = audit_events
    assert event["family_identifier"] == nipt_artifact_pg.NIPT_ARTIFACT_AUDIT_CHAIN
    assert event["action"] == "nipt_artifacts_auto_seeded"
    assert event["actor"] == "curator"
    assert "an unexplained low-VAF call in at least 4 cfDNA samples" in event["summary"]
    assert event["after"] == {
        "variants": [
            {"variant_id": "1-100-A-G", "recurrence_count": 12},
            {"variant_id": "2-200-C-T", "recurrence_count": 7},
        ]
    }
    assert event["metadata"]["min_unexplained_samples"] == 4
    assert event["metadata"]["rules"] == {
        "min_alt_reads": 5,
        "min_alt_fraction": 0.01,
        "maternal_vaf_boundary": 0.25,
        "father_min_depth": 20,
        "father_min_alt_reads": 5,
        "father_min_quality": 20.0,
        "father_min_vaf": 0.2,
        "father_min_genotyped_quality": 50.0,
        "father_min_gq": 20.0,
    }
    assert session.commits == 1


@pytest.mark.asyncio
async def test_auto_seed_counts_recurrence_among_the_assays_own_cfdna_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Counted over every sample on the assembly, recurrence would take in other assays'
    # artefacts and the germline calls of every other application.
    seen: dict = {}

    async def fake_candidates(assembly_name, *, carrier_samples, rules, min_unexplained_samples):
        seen.update(carrier_samples=carrier_samples)
        return [("1-100-A-G", 3)]

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_artifact_candidates", fake_candidates)
    cfdna = [
        ("CF1", None),  # no panel: the default scope
        ("CF2", "  PANEL_A "),  # a panel of its own (trimmed, as nipt_assay_key does)
        ("CF3", ""),  # a blank panel is the default scope too
    ]

    session = _AutoSeedSession("GRCh38", cfdna)
    result = await auto_seed_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        assay_key="nipt_cfdna",
        min_unexplained_samples=3,
    )
    assert session.sample_queries == [{"assays": "[\"nipt_cfdna\"]", "separator": ":", "analysis_type": "monogenic_nipt"}]
    assert seen["carrier_samples"] == ["CF1", "CF3"]
    assert (result["seeded"], result["cfdna_samples"]) == (1, 2)

    await auto_seed_nipt_artifacts(
        _AutoSeedSession("GRCh38", cfdna),  # type: ignore[arg-type]
        assembly_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        assay_key="PANEL_A",
        min_unexplained_samples=3,
    )
    assert seen["carrier_samples"] == ["CF2"]


def test_a_cfdna_samples_scope_is_read_from_the_monogenic_nipts_panel() -> None:
    # The SQL reads the panel the monogenic NIPT's package named, kept under that analysis: a
    # conventional NIPT of the same plasma names its own, which no artifact list reads.
    import inspect

    from backend.app.services.family_analysis_types import SAMPLE_ASSAY_PANELS_KEY
    from backend.app.services.nipt import MONOGENIC_NIPT_ANALYSIS_TYPE

    assert nipt_artifact_pg.CFDNA_PANEL_PATH == (SAMPLE_ASSAY_PANELS_KEY, MONOGENIC_NIPT_ANALYSIS_TYPE)
    source = inspect.getsource(nipt_artifact_pg._cfdna_sample_scopes)
    panels, analysis = nipt_artifact_pg.CFDNA_PANEL_PATH
    # Under the key of the sample's monogenic NIPT (monogenic_nipt:ZW1): its analysis type.
    assert f"metadata -> '{panels}'" in source
    assert '"analysis_type": CFDNA_PANEL_PATH[1]' in source
    assert analysis == MONOGENIC_NIPT_ANALYSIS_TYPE


@pytest.mark.asyncio
async def test_auto_seed_without_cfdna_samples_for_the_assay_seeds_nothing(
    monkeypatch: pytest.MonkeyPatch, audit_events: list[dict]
) -> None:
    async def fake_candidates(assembly_name, *, carrier_samples, rules, min_unexplained_samples):
        assert carrier_samples == []
        return []

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_artifact_candidates", fake_candidates)
    session = _AutoSeedSession("GRCh38", [("CF2", "PANEL_A")])

    result = await auto_seed_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        assay_key="nipt_cfdna",
        min_unexplained_samples=3,
    )

    assert result == {
        "assay_key": "nipt_cfdna",
        "min_unexplained_samples": 3,
        "cfdna_samples": 0,
        "variants": [],
        "seeded": 0,
    }
    assert session.bulk_rows is None
    assert session.listed_queries == []  # no candidate: the list is not read
    assert audit_events == []  # nothing changed, nothing to audit


def test_the_api_seeds_from_three_unexplained_samples_by_default() -> None:
    from pydantic import ValidationError

    from backend.app.schemas.nipt import NiptArtifactAutoSeed

    assert NiptArtifactAutoSeed(assembly_id="GRCh38").min_unexplained_samples == 3
    with pytest.raises(ValidationError):
        NiptArtifactAutoSeed(assembly_id="GRCh38", min_unexplained_samples=0)


@pytest.mark.asyncio
async def test_auto_seed_nipt_artifacts_missing_assembly_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_candidates(*_args, **_kwargs):
        return []

    monkeypatch.setattr(nipt_artifact_pg, "fetch_recurrent_artifact_candidates", fake_candidates)
    session = _AutoSeedSession(None)

    with pytest.raises(HTTPException) as excinfo:
        await auto_seed_nipt_artifacts(
            session,  # type: ignore[arg-type]
            assembly_id="5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b",
            assay_key="nipt_cfdna",
            min_unexplained_samples=3,
        )
    assert (excinfo.value.status_code, excinfo.value.detail) == (404, "Assembly not found")
    # Nothing was seeded or committed.
    assert (session.bulk_rows, session.commits) == (None, 0)


@pytest.mark.asyncio
async def test_bulk_upsert_nipt_artifacts_empty_is_noop() -> None:
    session = _AutoSeedSession("GRCh38")
    seeded = await bulk_upsert_nipt_artifacts(
        session,  # type: ignore[arg-type]
        assembly_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
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
        assembly_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
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
        # Read by the index's sort key, at each variant's own locus.
        assert "(chrom, pos) IN %(loci)s" in query
        assert params["loci"] == (("1", 1), ("1", 2), ("1", 3))
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


@pytest.mark.parametrize(
    ("chrom", "pos", "ref", "alt"),
    [
        ("1", 100, "A", "G"),
        ("chrX", 5_000_000, "CAG", "C"),
        ("MT", 73, "A", "*"),
        # A contig name and an ALT allele can hold a "-" themselves.
        ("HLA-A*01:01:01:01", 120, "G", "T"),
        ("chrUn_KI270302v1", 9, "N", "]HLA-B*07:02:01:4]N"),
    ],
)
def test_the_protections_read_every_locus_a_stored_variant_can_sit_at(
    chrom: str, pos: int, ref: str, alt: str
) -> None:
    # The protections read the shared annotation index at the loci of the ids asked about:
    # the locus the id was built from is always among them, or a stored annotation would go
    # unread and a protected variant could be listed.
    from backend.app.services.clickhouse_variant_ids import build_small_variant_id, small_variant_loci
    from backend.app.services.data_scope import normalize_chromosome

    variant_id = build_small_variant_id(chrom, pos, ref, alt)

    assert (normalize_chromosome(chrom), pos) in small_variant_loci(variant_id)


def test_an_id_without_a_position_names_no_locus() -> None:
    from backend.app.services.clickhouse_variant_ids import small_variant_loci

    assert small_variant_loci("1-100-A") == set()
    assert small_variant_loci("rs123") == set()
    assert small_variant_loci("1-A-C-G") == set()
