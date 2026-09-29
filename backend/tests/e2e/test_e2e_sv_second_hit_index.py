"""The SV second-hit badge follows every write to the family's SVs, not only the package import.

The small-variant page flags a variant whose gene is also hit by a structural variant
(the SNV + SV "second hit") from a per-family SV→gene index built on first use. Only the
package import used to clear that index, so after an admin SV delete or a per-sample SV
upload the badge went on describing SVs that no longer matched the family's data. The
index now records the storage-level SV data version it was built from (every SV insert,
delete and snapshot restore moves it), and a moved version rebuilds it on the next read.

This imports the golden trio into real Postgres + ClickHouse, opens the small variants
(which builds the index), then:

* deletes the proband's SVs through the admin data endpoint. The BRCA2 badge must stop
  calling the maternal SNV and the paternal deletion biallelic: the father's deletion is
  still stored, but the proband no longer carries it.
* uploads a per-sample SV file for the proband with a deletion over GENE_CH. The GENE_CH
  variants must carry the badge on the next open; the index built before had no GENE_CH
  entry. The upload holds no parental calls, so nothing places the deletion: unknown.

The proband's SVs are deleted again, the gene row the test seeds removed and the golden
trio re-imported at the end, so later modules see the usual state. Everything runs in
one event loop through an in-process ``httpx.ASGITransport`` client (see
test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
_SEEDED_GENE_ID = "ENSG_GENE_CH_E2E_SVINDEX"
_DELETE_PROBAND_SVS = "/api/admin/data/samples/PROBAND/structural_variants"

# One proband deletion over both GENE_CH variants (chr1:4000 and chr1:5000), as a
# single-sample Sniffles VCF: the shape of a per-sample SV upload.
_PROBAND_SV_VCF = "\n".join(
    [
        "##fileformat=VCFv4.2",
        "##source=Sniffles2",
        "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", "PROBAND"]),
        "\t".join(
            ["1", "3600", "svup1", "N", "<DEL>", "60", "PASS", "SVTYPE=DEL;END=5400;SVLEN=-1800;SUPPORT=12", "GT", "0/1"]
        ),
    ]
) + "\n"


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


def _second_hits(page: dict) -> list[dict | None]:
    return [variant.get("sv_second_hit") for variant in page["json"]["variants"]]


async def _set_seeded_gene(assembly_id: str | None) -> None:
    """Seed (or, with None, remove) a gene row for GENE_CH: the per-sample upload takes
    an SV's gene symbols from the gene table, and the fixture ships none."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(
            text("DELETE FROM genes WHERE gene_id = :gene_id"), {"gene_id": _SEEDED_GENE_ID}
        )
        if assembly_id is not None:
            await session.execute(
                text(
                    """
                    INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end",
                                       strand, biotype, description, source)
                    VALUES (CAST(:assembly_id AS uuid), :gene_id, 'GENE_CH', '1', 3500, 5500,
                            1, 'protein_coding', 'E2E second-hit index', 'e2e')
                    """
                ),
                {"assembly_id": assembly_id, "gene_id": _SEEDED_GENE_ID},
            )
        await session.commit()


async def _exercise(root: Path) -> dict:
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import (
        get_family_structural_variant_data_version,
    )
    from backend.tests.e2e import _harness

    facts = await _harness.import_golden_trio(root)
    family_uuid = facts["family_uuid"]
    out: dict = {"facts": facts}

    async def sv_version() -> str:
        return await get_family_structural_variant_data_version(_harness.ASSEMBLY, family_uuid)

    await _set_seeded_gene(facts["assembly_id"])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            url = f"/api/families/{FAMILY}/small-variants"

            def page(gene: str):
                return ac.get(url, params={"gene": gene, "page": 1, "page_size": 100})

            try:
                # First opens: build the index from the imported SVs.
                out["ch_before"] = _cap(await page("GENE_CH"))
                out["brca2_before"] = _cap(await page("BRCA2"))
                out["version_before"] = await sv_version()

                out["delete"] = _cap(await ac.delete(_DELETE_PROBAND_SVS, params={"confirm": "true"}))
                out["version_after_delete"] = await sv_version()
                out["brca2_after_delete"] = _cap(await page("BRCA2"))

                out["upload"] = _cap(
                    await ac.post(
                        "/api/structural-variants/upload/PROBAND",
                        files={"file": ("PROBAND.sniffles.vcf", _PROBAND_SV_VCF.encode(), "text/plain")},
                    )
                )
                out["version_after_upload"] = await sv_version()
                out["ch_after_upload"] = _cap(await page("GENE_CH"))
            finally:
                # Drop the uploaded deletion (it only has the proband's call).
                out["cleanup_delete"] = _cap(
                    await ac.delete(_DELETE_PROBAND_SVS, params={"confirm": "true"})
                )
    finally:
        await _set_seeded_gene(None)
        # Restore the fixture state (the proband's NeedlR SV calls) for later modules.
        out["restored"] = await _harness.import_golden_trio(root)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.skip("golden_trio fixture missing; run scripts/generate_golden_trio.py")

    root = tmp_path_factory.mktemp("golden_sv_index") / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    request.addfinalizer(mp.undo)

    snapshot = _harness.run_async(lambda: _exercise(root))
    assert snapshot["facts"]["completed"] is True, snapshot["facts"]
    return snapshot


def test_the_first_open_sees_the_imported_second_hit_only(run) -> None:
    before = run["ch_before"]
    assert before["status"] == 200, before["text"]
    assert before["json"]["variants"], "the golden trio has two GENE_CH variants"
    assert _second_hits(before) == [None] * len(before["json"]["variants"])

    brca2 = run["brca2_before"]
    assert brca2["status"] == 200, brca2["text"]
    hits = [hit for hit in _second_hits(brca2) if hit]
    assert hits and hits[0]["phase"] == "trans" and hits[0]["deletion_unmasked"] is True


def test_an_admin_sv_delete_moves_the_sv_data_version(run) -> None:
    assert run["delete"]["status"] == 200, run["delete"]["text"]
    assert run["version_after_delete"] != run["version_before"]


def test_the_badge_stops_claiming_a_deletion_the_proband_no_longer_carries(run) -> None:
    after = run["brca2_after_delete"]
    assert after["status"] == 200, after["text"]
    hits = [hit for hit in _second_hits(after) if hit]
    # The father's deletion is still stored, so BRCA2 is still hit by an SV...
    assert hits, "the father's BRCA2 deletion keeps the gene in the index"
    hit = hits[0]
    # ...but not in the proband: no zygosity, no phase, and above all not "biallelic".
    # Before the fix the index built on the first open was served again, unchanged.
    assert hit["affected_zygosity"] is None, hit
    assert hit["phase"] == "unknown", hit
    assert hit["deletion_unmasked"] is False, hit


def test_a_per_sample_sv_upload_moves_the_sv_data_version(run) -> None:
    assert run["upload"]["status"] == 200, run["upload"]["text"]
    assert run["version_after_upload"] != run["version_after_delete"]


def test_the_uploaded_deletion_reaches_the_second_hit_badge(run) -> None:
    after = run["ch_after_upload"]
    assert after["status"] == 200, after["text"]
    hits = _second_hits(after)
    # Before the fix the index had no GENE_CH entry and was served again: no badge.
    assert hits and all(hit is not None for hit in hits), hits
    for hit in hits:
        assert hit["gene"] == "GENE_CH"
        assert hit["sv_types"] == ["DEL"]
        assert hit["affected_zygosity"] == "het"
        # A per-sample callset has no parental SV calls, so nothing places the deletion.
        assert hit["phase"] == "unknown", hit
        assert hit["deletion_unmasked"] is False


def test_golden_trio_is_restored_for_later_modules(run) -> None:
    assert run["cleanup_delete"]["status"] == 200, run["cleanup_delete"]["text"]
    assert run["restored"]["completed"] is True
    assert run["restored"]["n_structural_variants"] == run["facts"]["n_structural_variants"]
    assert run["restored"]["brca2_sv_second_hits"] == run["facts"]["brca2_sv_second_hits"]
