"""M2 end-to-end: the HTTP API contracts that feed the frontend / visualization.

Imports the golden-trio package (M1 harness) into real Postgres + ClickHouse,
then drives the viz-feeding endpoints through a real authenticated client and
asserts their response contracts: small-variant + structural-variant pages, the
variant explorer (keyset pagination + carriers + MNV classification), BED tracks,
haplotypes, and track-availability. Auth is exercised too (401 without a token).
Expected values come from ``fixtures/golden_trio/EXPECTED.yaml``.

Everything (import + every request) runs in ONE event loop via an in-process
``httpx.ASGITransport`` client, so the loop-bound Postgres engine / ClickHouse
client never cross loops. ASGITransport also skips the lifespan, avoiding the
reference/HPO bootstrap (schema + admin + data are already in place via the
harness import). Responses are captured up front; tests assert on the snapshot.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from backend.tests.e2e._harness import capture

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"


async def _collect_api_responses(root: Path) -> dict:
    """Import the golden trio and snapshot every viz-feeding endpoint, all on one
    event loop."""
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    transport = ASGITransport(app=app)
    base = "http://e2e"
    out: dict = {"facts": facts, "responses": {}}
    r = out["responses"]

    # Unauthenticated request must be rejected.
    async with AsyncClient(transport=transport, base_url=base) as anon:
        r["anon"] = capture(await anon.get(f"/api/families/{FAMILY}/small-variants"))

    async with AsyncClient(transport=transport, base_url=base) as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        aid = facts["assembly_id"]

        r["family"] = capture(await ac.get(f"/api/families/{FAMILY}"))
        r["small_full"] = capture(await ac.get(f"/api/families/{FAMILY}/small-variants", params={"page": 1, "page_size": 100}))
        r["small_paged"] = capture(await ac.get(f"/api/families/{FAMILY}/small-variants", params={"page": 1, "page_size": 2}))
        r["comp_het"] = capture(await ac.get(f"/api/families/{FAMILY}/small-variants", params={"inheritance": "compound_het"}))
        # A frequency ceiling below the ClinVar-pathogenic GENE_PATH variant
        # (gnomAD AF 1e-5). With the override the variant must survive the ClickHouse
        # filter, not only the Python matcher; without it, it must not.
        freq_ceiling = {"page": 1, "page_size": 100, "max_gnomad_af": 0.000001}
        r["freq_rescue_on"] = capture(
            await ac.get(
                f"/api/families/{FAMILY}/small-variants",
                params={**freq_ceiling, "clinvar_overrides_frequency": "true"},
            )
        )
        r["freq_rescue_off"] = capture(
            await ac.get(
                f"/api/families/{FAMILY}/small-variants",
                params={**freq_ceiling, "clinvar_overrides_frequency": "false"},
            )
        )

        r["sv"] = capture(await ac.get(f"/api/families/{FAMILY}/structural-variants"))
        # The NeedlR breakend's mate is on 5:3000000 (its ALT, N]chr5:3000000]).
        r["sv_by_mate"] = capture(
            await ac.get(
                f"/api/families/{FAMILY}/structural-variants",
                params={"remote_chr": "5", "remote_start": 3_000_000},
            )
        )
        r["sv_by_other_mate"] = capture(
            await ac.get(f"/api/families/{FAMILY}/structural-variants", params={"remote_chr": "7"})
        )
        r["sv_track"] = capture(await ac.get(f"/api/families/{FAMILY}/structural-variants", params={"track_mode": "true"}))
        r["sv_lengths"] = capture(await ac.get(f"/api/families/{FAMILY}/structural-variant-lengths"))
        r["shared"] = capture(await ac.get(f"/api/families/{FAMILY}/shared-structural-variant-counts"))
        r["sv_sample_counts"] = capture(await ac.get(f"/api/families/{FAMILY}/structural-variant-sample-counts"))
        r["small_stats"] = capture(await ac.get(f"/api/families/{FAMILY}/small-variant-statistics"))

        r["bed_json"] = capture(await ac.get("/api/bed/PROBAND/coverage", params={"chrom": "1"}))
        r["bed_bad"] = capture(await ac.get("/api/bed/PROBAND/not-a-track", params={"chrom": "1"}))
        r["bed_empty"] = capture(await ac.get("/api/bed/PROBAND/coverage", params={"chrom": "22"}))

        r["hap"] = capture(await ac.get(f"/api/families/{FAMILY}/haplotypes", params={"chr": "1"}))
        r["hap_batch"] = capture(await ac.get(f"/api/families/{FAMILY}/haplotypes/batch", params={"chr": ["1"]}))
        r["track_avail"] = capture(await ac.get(f"/api/families/{FAMILY}/track-availability"))

        r["exp_denovo"] = capture(await ac.get("/api/variant-explorer/small-variants", params={"assembly_id": aid, "gene": "GENE_DENOVO"}))
        r["exp_mnv"] = capture(await ac.get("/api/variant-explorer/small-variants", params={"assembly_id": aid, "gene": "GENE_MNV"}))

        # carriers for the de novo variant
        denovo = next(
            (v for v in (r["exp_denovo"]["json"] or {}).get("variants", []) if v["variant_id"] == "1-3000-G-A"),
            None,
        )
        if denovo is not None:
            r["carriers"] = capture(await ac.get(f"/api/variant-explorer/small-variants/{denovo['key']}/carriers"))

        # keyset pagination over GENE_CH's two variants (page_size=1)
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            params = {"assembly_id": aid, "gene": "GENE_CH", "page_size": 1, "sort": "total_samples", "order": "desc"}
            if cursor:
                params["cursor"] = cursor
            page = capture(await ac.get("/api/variant-explorer/small-variants", params=params))
            pages += 1
            if page["status"] != 200 or pages > 10:
                r["keyset_error"] = page
                break
            body = page["json"]
            seen.extend(v["variant_id"] for v in body["variants"])
            cursor = body.get("next_cursor")
            if not cursor:
                break
        r["keyset_seen"] = seen

    return out


@pytest.fixture(scope="module")
def expected() -> dict:
    return yaml.safe_load((_FIXTURE / "EXPECTED.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def api(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    root = tmp_path_factory.mktemp("golden_api") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _collect_api_responses(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def _resp(api: dict, key: str) -> dict:
    return api["responses"][key]


# --------------------------------------------------------------------------- auth


def test_unauthenticated_request_is_rejected(api):
    assert _resp(api, "anon")["status"] == 401, _resp(api, "anon")


# ------------------------------------------------------------------ small variants


def test_small_variants_page_contract(api, expected):
    resp = _resp(api, "small_full")
    assert resp["status"] == 200, resp["text"]
    body = resp["json"]
    for key in ("total", "count_limit", "total_is_estimated", "variants"):
        assert key in body, list(body)
    assert body["total"] == expected["snv"]["count"]
    assert body["total_is_estimated"] is False
    variants = body["variants"]
    assert variants and all("_id" in v for v in variants), "VariantOut must serialize id as _id"
    types = {v["type"] for v in variants}
    assert "SNV" in types and "INDEL" in types, types
    snv = next(v for v in variants if v["type"] == "SNV")
    assert snv["length"] == 0, snv  # 1bp ref -> end - start == 0


def test_small_variants_offset_pagination(api):
    resp = _resp(api, "small_paged")
    assert resp["status"] == 200, resp["text"]
    assert len(resp["json"]["variants"]) <= 2


def test_small_variants_compound_het_group(api, expected):
    resp = _resp(api, "comp_het")
    assert resp["status"] == 200, resp["text"]
    groups = resp["json"].get("variant_groups", [])
    assert any(g["gene"] == expected["snv"]["compound_het_gene"] for g in groups), groups


# -------------------------------------------------------------- structural variants


def test_structural_variants_page_contract(api, expected):
    resp = _resp(api, "sv")
    assert resp["status"] == 200, resp["text"]
    body = resp["json"]
    assert body["total"] == expected["structural_variants"]["count"]
    summary = body.get("summary") or {}
    assert "DEL" in summary and "BND" in summary, summary
    by_type = {v["type"]: v for v in body["variants"]}
    assert by_type["DEL"]["length"] == -5000, by_type["DEL"]  # svLen, not end-start
    assert by_type["DEL"]["gene_count"] >= 1
    # Both a DEL and a BND ingest via the Needlr path. The BND has the mate its ALT names
    # (N]chr5:3000000]), read as an upload reads it. The DEL has none.
    assert "BND" in by_type, by_type
    assert (by_type["BND"]["remote_chr"], by_type["BND"]["remote_start"]) == ("5", 3_000_000), by_type["BND"]
    assert (by_type["DEL"]["remote_chr"], by_type["DEL"]["remote_start"]) == (None, None), by_type["DEL"]
    # Each is named by its coordinates and type (the BND's with its mate), not by
    # its record ID (SVDEL1, SVBND1), as an uploaded call of the same event is.
    assert {v["_id"] for v in body["variants"]} == set(expected["structural_variants"]["ids"])


def test_structural_variants_found_by_their_mate(api):
    # A search by the mate's position never found a Needlr breakend.
    resp = _resp(api, "sv_by_mate")
    assert resp["status"] == 200, resp["text"]
    assert [(v["_id"], v["type"], v["start"]) for v in resp["json"]["variants"]] == [
        # Its END is its POS, as an upload puts a breakend without END (CLIN-39).
        ("1-20000-20000-BND-5-3000000-3000000", "BND", 20000)
    ]
    assert resp["json"]["total"] == 1
    other = _resp(api, "sv_by_other_mate")
    assert other["status"] == 200, other["text"]
    assert other["json"]["variants"] == [] and other["json"]["total"] == 0


def test_structural_variants_track_mode_omits_nulls(api):
    resp = _resp(api, "sv_track")
    assert resp["status"] == 200, resp["text"]
    rows = resp["json"]
    rows = rows.get("variants", rows) if isinstance(rows, dict) else rows
    assert rows, "expected SV rows in track mode"
    assert all(value is not None for row in rows for value in row.values()), rows[0]


def test_structural_variant_lengths(api):
    resp = _resp(api, "sv_lengths")
    assert resp["status"] == 200, resp["text"]
    assert any(item["length"] == -5000 for item in resp["json"]), resp["json"]


def test_shared_structural_variant_counts_symmetric(api):
    resp = _resp(api, "shared")
    assert resp["status"] == 200, resp["text"]
    matrix = resp["json"]
    assert "PROBAND" in matrix, matrix
    for a in matrix:
        for b, n in matrix[a].items():
            assert matrix.get(b, {}).get(a) == n, (a, b, matrix)


def test_structural_variant_sample_counts_by_type(api):
    # The father's call of the deletion carries it (the proband's second hit is in trans).
    resp = _resp(api, "sv_sample_counts")
    assert resp["status"] == 200, resp["text"]
    counts = {row["sample_id"]: (row["total"], row["by_type"]) for row in resp["json"]}
    assert counts["PROBAND"] == (2, {"BND": 1, "DEL": 1}), counts
    assert counts["FATHER"] == (1, {"DEL": 1}), counts
    assert counts["MOTHER"] == (0, {}), counts


def test_small_variant_statistics_of_the_trio(api, expected):
    # The variant summary's small-variant statistics (PROD-1) of the golden trio's eight
    # variants on chr1: the proband carries all eight, heterozygous.
    resp = _resp(api, "small_stats")
    assert resp["status"] == 200, resp["text"]
    body = resp["json"]
    assert body["total_variants"] == expected["snv"]["count"]
    assert (body["snv_count"], body["insertion_count"], body["deletion_count"], body["other_count"]) == (6, 0, 1, 1)
    samples = {row["sample_id"]: row for row in body["samples"]}
    assert list(samples) == ["FATHER", "MOTHER", "PROBAND"]
    proband = samples["PROBAND"]
    assert (proband["non_ref_count"], proband["het_count"], proband["hom_alt_count"]) == (8, 8, 0)
    assert (proband["transition_count"], proband["transversion_count"], proband["passing_count"]) == (4, 2, 8)
    consequences = {row["consequence"]: row["count"] for row in proband["consequences"]}
    assert consequences == {"stop_gained": 1, "frameshift_variant": 1, "missense_variant": 5, "intron_variant": 1}
    assert proband["chromosomes"] == [{"chrom": "1", "het_count": 8, "hom_alt_count": 0}]
    # Every call has GQ 99: the last bin of 5, 95+.
    assert {row["low"]: row["count"] for row in proband["genotype_quality_histogram"]}[95.0] == 8
    assert {row["low"]: row["count"] for row in proband["allele_balance_histogram"]}[0.5] == 8
    assert (samples["MOTHER"]["non_ref_count"], samples["FATHER"]["non_ref_count"]) == (4, 1)
    # The de novo, the pathogenic stop and the MNV: in neither parent.
    (trio,) = body["trios"]
    assert (trio["child"], trio["father"], trio["mother"]) == ("PROBAND", "FATHER", "MOTHER")
    assert trio["chromosomes"] == [
        {"chrom": "1", "sites": 8, "not_from_father": 0, "not_from_mother": 0, "from_neither": 3}
    ]


# ------------------------------------------------------------------- Pedigree


def test_the_family_pedigree_text_is_its_stored_structure(api):
    # The text the family page draws is written from the family's members and their parents
    # one row of six fields per member, as the stored structure has it.
    family = _resp(api, "family")
    assert family["status"] == 200, family["text"]
    body = family["json"]
    rows = [row.split() for row in body["pedigree"].splitlines()]
    assert all(len(row) == 6 and row[0] == FAMILY for row in rows), rows
    by_sample = {row[1]: row for row in rows}
    assert set(by_sample) == {member["sample_id"] for member in body["members"] if member["active"]}
    parents = {
        (link["sample_id_b"], link["role_a"]): link["sample_id_a"]
        for link in body["relationships"]
        if link["relationship_type"] == "parent_child"
    }
    for sample, row in by_sample.items():
        assert (row[2], row[3]) == (parents.get((sample, "father"), "0"), parents.get((sample, "mother"), "0"))


# ------------------------------------------------------------------------- BED


def test_bed_coverage_answers_json_only(api):
    j = _resp(api, "bed_json")
    assert j["status"] == 200, j["text"]
    body = j["json"]
    assert body["bed_type"] == "coverage"
    assert body["items"] and {"chr", "start", "end", "value"} <= set(body["items"][0])


def test_bed_invalid_type_and_empty_region(api):
    assert _resp(api, "bed_bad")["status"] == 400, _resp(api, "bed_bad")["text"]
    assert _resp(api, "bed_empty")["status"] == 404, _resp(api, "bed_empty")["text"]


# ------------------------------------------------------------------ haplotypes / tracks


def test_haplotypes_endpoints(api):
    one = _resp(api, "hap")
    assert one["status"] == 200, one["text"]
    assert one["json"]["chr"] == "1"
    batch = _resp(api, "hap_batch")
    assert batch["status"] == 200, batch["text"]
    assert batch["json"]["chr"] == "genome"


def test_track_availability(api):
    resp = _resp(api, "track_avail")
    assert resp["status"] == 200, resp["text"]
    proband = resp["json"]["samples"]["PROBAND"]
    assert proband["coverage"] is True, proband
    assert proband["small_variants"] is True, proband
    assert proband["haplotypes"] is False, proband  # haplotypes deferred from the golden import


# ------------------------------------------------------------------ variant explorer


def test_explorer_counts_and_carriers(api):
    resp = _resp(api, "exp_denovo")
    assert resp["status"] == 200, resp["text"]
    row = next(v for v in resp["json"]["variants"] if v["variant_id"] == "1-3000-G-A")
    assert row["total_samples"] == 1, row
    assert row["het_samples"] == 1 and row["hom_samples"] == 0, row

    carriers = _resp(api, "carriers")
    assert carriers["status"] == 200, carriers["text"]
    cbody = carriers["json"]
    assert cbody["total_samples"] == 1
    carrier_samples = [s["sample_id"] for fam in cbody["families"] for s in fam["samples"]]
    assert "PROBAND" in carrier_samples, cbody


def test_explorer_mnv_classification(api):
    resp = _resp(api, "exp_mnv")
    assert resp["status"] == 200, resp["text"]
    variants = resp["json"]["variants"]
    assert variants and variants[0]["type"] == "MNV", variants


def test_explorer_keyset_pagination(api):
    assert "keyset_error" not in api["responses"], api["responses"].get("keyset_error")
    seen = api["responses"]["keyset_seen"]
    # GENE_CH has exactly the two compound-het variants; each seen once.
    assert len(seen) == len(set(seen)) == 2, seen


def _page_ids(resp: dict) -> set[str]:
    assert resp["status"] == 200, resp["text"]
    return {v["_id"] for v in resp["json"]["variants"]}


def test_clinvar_pathogenic_survives_the_frequency_ceiling_in_clickhouse(api):
    # "ClinVar P/LP overrides frequency" holds in the ClickHouse fetch itself, not only in
    # the Python matcher.
    rescued = _page_ids(_resp(api, "freq_rescue_on"))
    strict = _page_ids(_resp(api, "freq_rescue_off"))
    assert "1-2000-C-T" in rescued, rescued
    assert "1-2000-C-T" not in strict, strict
    # The rescue is ClinVar-scoped: the common benign GENE_BENIGN variant (AF 0.42, no
    # ClinVar) stays filtered either way.
    assert "1-1000-A-G" not in rescued
