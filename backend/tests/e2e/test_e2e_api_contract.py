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

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"


def _cap(resp, *, as_json: bool = True) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if as_json and resp.status_code < 400:
        try:
            out["json"] = resp.json()
        except Exception:  # pragma: no cover - capture for the failing assertion
            out["json"] = None
    return out


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
        r["anon"] = _cap(await anon.get(f"/api/families/{FAMILY}/small-variants"))

    async with AsyncClient(transport=transport, base_url=base) as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        aid = facts["assembly_id"]

        r["small_full"] = _cap(await ac.get(f"/api/families/{FAMILY}/small-variants", params={"page": 1, "page_size": 100}))
        r["small_paged"] = _cap(await ac.get(f"/api/families/{FAMILY}/small-variants", params={"page": 1, "page_size": 2}))
        r["comp_het"] = _cap(await ac.get(f"/api/families/{FAMILY}/small-variants", params={"inheritance": "compound_het"}))
        # #534: a frequency ceiling below the ClinVar-pathogenic GENE_PATH variant
        # (gnomAD AF 1e-5). With the override the variant must survive the ClickHouse
        # filter, not only the Python matcher; without it, it must not.
        freq_ceiling = {"page": 1, "page_size": 100, "max_gnomad_af": 0.000001}
        r["freq_rescue_on"] = _cap(
            await ac.get(
                f"/api/families/{FAMILY}/small-variants",
                params={**freq_ceiling, "clinvar_overrides_frequency": "true"},
            )
        )
        r["freq_rescue_off"] = _cap(
            await ac.get(
                f"/api/families/{FAMILY}/small-variants",
                params={**freq_ceiling, "clinvar_overrides_frequency": "false"},
            )
        )

        r["sv"] = _cap(await ac.get(f"/api/families/{FAMILY}/structural-variants"))
        r["sv_track"] = _cap(await ac.get(f"/api/families/{FAMILY}/structural-variants", params={"track_mode": "true"}))
        r["sv_lengths"] = _cap(await ac.get(f"/api/families/{FAMILY}/structural-variant-lengths"))
        r["shared"] = _cap(await ac.get(f"/api/families/{FAMILY}/shared-structural-variant-counts"))

        r["bed_json"] = _cap(await ac.get("/api/bed/PROBAND/coverage", params={"chrom": "1", "format": "json"}))
        r["bed_text"] = _cap(await ac.get("/api/bed/PROBAND/coverage", params={"chrom": "1", "format": "text"}), as_json=False)
        r["bed_bad"] = _cap(await ac.get("/api/bed/PROBAND/not-a-track", params={"chrom": "1"}), as_json=False)
        r["bed_empty"] = _cap(await ac.get("/api/bed/PROBAND/coverage", params={"chrom": "22", "format": "json"}), as_json=False)

        r["hap"] = _cap(await ac.get(f"/api/families/{FAMILY}/haplotypes", params={"chr": "1"}))
        r["hap_batch"] = _cap(await ac.get(f"/api/families/{FAMILY}/haplotypes/batch", params={"chr": ["1"]}))
        r["track_avail"] = _cap(await ac.get(f"/api/families/{FAMILY}/track-availability"))

        r["exp_denovo"] = _cap(await ac.get("/api/variant-explorer/small-variants", params={"assembly_id": aid, "gene": "GENE_DENOVO"}))
        r["exp_mnv"] = _cap(await ac.get("/api/variant-explorer/small-variants", params={"assembly_id": aid, "gene": "GENE_MNV"}))

        # carriers for the de novo variant
        denovo = next(
            (v for v in r["exp_denovo"].get("json", {}).get("variants", []) if v["variant_id"] == "1-3000-G-A"),
            None,
        )
        if denovo is not None:
            r["carriers"] = _cap(await ac.get(f"/api/variant-explorer/small-variants/{denovo['key']}/carriers"))

        # keyset pagination over GENE_CH's two variants (page_size=1)
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            params = {"assembly_id": aid, "gene": "GENE_CH", "page_size": 1, "sort": "total_samples", "order": "desc"}
            if cursor:
                params["cursor"] = cursor
            page = _cap(await ac.get("/api/variant-explorer/small-variants", params=params))
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
    from backend.app.services import family_package_import as package_import
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    root = tmp_path_factory.mktemp("golden_api") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(package_import.settings, "family_import_roots", [str(root.parent)])
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
    # Both a DEL and a BND ingest via the Needlr path. Remote breakend coords are
    # a sniffles-path feature (the Needlr importer doesn't parse ALT mate coords),
    # so remote_chr stays null here — assert the BND is present, not its mate.
    assert "BND" in by_type, by_type


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


# ------------------------------------------------------------------------- BED


def test_bed_coverage_json_and_text(api):
    j = _resp(api, "bed_json")
    assert j["status"] == 200, j["text"]
    body = j["json"]
    assert body["bed_type"] == "coverage"
    assert body["items"] and {"chr", "start", "end", "value"} <= set(body["items"][0])

    t = _resp(api, "bed_text")
    assert t["status"] == 200, t["text"]
    assert t["text"].splitlines()[0].startswith("chr\tstart\tend")


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
    # #534: "ClinVar P/LP overrides frequency" used to exist only in the Python matcher;
    # the ClickHouse fetch dropped the variant first.
    rescued = _page_ids(_resp(api, "freq_rescue_on"))
    strict = _page_ids(_resp(api, "freq_rescue_off"))
    assert "1-2000-C-T" in rescued, rescued
    assert "1-2000-C-T" not in strict, strict
    # The rescue is ClinVar-scoped: the common benign GENE_BENIGN variant (AF 0.42, no
    # ClinVar) stays filtered either way.
    assert "1-1000-A-G" not in rescued
