"""The SV second-hit badge follows every write to the family's SVs, not only the package import.

The small-variant page flags a variant whose gene is also hit by a structural variant
(the SNV + SV "second hit") from a per-family SV→gene index built on first use. Only the
package import used to clear that index, so after an admin SV delete or a per-sample SV
upload the badge went on describing SVs that no longer matched the family's data. The
index now records the storage-level SV data version it was built from (every SV insert,
delete and snapshot restore moves it), and a moved version rebuilds it on the next read.

This imports the golden trio into real Postgres + ClickHouse, opens the small variants
(which builds the index), then:

* uploads a per-sample SV file for the mother with a duplication over BRCA2. Each SV
  gets its own phase (CLIN-28): the mother, unaffected, carries the SNV and her own
  duplication but has a reference call at the deletion, so the deletion stays in trans
  and the badge biallelic. Any SV of hers used to count as the deletion, which made her a
  carrier of both hits and the gene cis.
* deletes the proband's SVs through the admin data endpoint. The BRCA2 badge must stop
  calling the maternal SNV and the paternal deletion biallelic: the father's deletion is
  still stored, but the proband no longer carries it.
* uploads a per-sample SV file for the proband with a deletion over GENE_CH. The GENE_CH
  variants must carry the badge on the next open; the index built before had no GENE_CH
  entry. The upload holds no parental calls, so nothing places the deletion: unknown.

The proband's and the mother's SVs are deleted again, the gene rows the test seeds
removed and the golden trio re-imported at the end, so later modules see the usual
state. Everything runs in one event loop through an in-process ``httpx.ASGITransport``
client (see test_e2e_api_contract.py for why).

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_trio"
FAMILY = "FAM_TRIO"
# Gene rows the test seeds (gene_id, symbol, start, end on chr1): the per-sample upload
# takes an SV's gene symbols from the gene table, and the fixture ships none. BRCA2 covers
# its SNV (chr1:6000), not the GENE_CH variants or the GENE_CH deletion.
_SEEDED_GENES = [
    ("ENSG_GENE_CH_E2E_SVINDEX", "GENE_CH", 3500, 5500),
    ("ENSG_BRCA2_E2E_SVINDEX", "BRCA2", 5600, 6400),
]
_DELETE_PROBAND_SVS = "/api/admin/data/samples/PROBAND/structural_variants"
_DELETE_MOTHER_SVS = "/api/admin/data/samples/MOTHER/structural_variants"


def _sniffles_vcf(sample: str, record: list[str]) -> str:
    """A single-sample Sniffles VCF with one record: the shape of a per-sample SV upload."""
    return "\n".join(
        [
            "##fileformat=VCFv4.2",
            "##source=Sniffles2",
            "#" + "\t".join(["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", sample]),
            "\t".join(record),
        ]
    ) + "\n"


# One proband deletion over both GENE_CH variants (chr1:4000 and chr1:5000).
_PROBAND_SV_VCF = _sniffles_vcf(
    "PROBAND",
    ["1", "3600", "svup1", "N", "<DEL>", "60", "PASS", "SVTYPE=DEL;END=5400;SVLEN=-1800;SUPPORT=12", "GT", "0/1"],
)
# A duplication of the mother's own over the BRCA2 SNV (chr1:6000): another SV of the gene
# than the paternal deletion (chr1:7000-12000), which she has a reference call for.
_MOTHER_SV_VCF = _sniffles_vcf(
    "MOTHER",
    ["1", "5800", "svupm1", "N", "<DUP>", "60", "PASS", "SVTYPE=DUP;END=6300;SVLEN=500;SUPPORT=10", "GT", "0/1"],
)


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    if resp.status_code < 400:
        out["json"] = resp.json()
    return out


def _second_hits(page: dict) -> list[dict | None]:
    return [variant.get("sv_second_hit") for variant in page["json"]["variants"]]


async def _set_seeded_genes(assembly_id: str | None) -> None:
    """Seed (or, with None, remove) the gene rows of ``_SEEDED_GENES``."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        for gene_id, symbol, start, end in _SEEDED_GENES:
            await session.execute(
                text("DELETE FROM genes WHERE gene_id = :gene_id"), {"gene_id": gene_id}
            )
            if assembly_id is None:
                continue
            await session.execute(
                text(
                    """
                    INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end",
                                       strand, biotype, description, source)
                    VALUES (CAST(:assembly_id AS uuid), :gene_id, :symbol, '1', :start, :end,
                            1, 'protein_coding', 'E2E second-hit index', 'e2e')
                    """
                ),
                {
                    "assembly_id": assembly_id,
                    "gene_id": gene_id,
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                },
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

    await _set_seeded_genes(facts["assembly_id"])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as ac:
            token = await _harness.login_admin_token(ac)
            ac.headers["Authorization"] = f"Bearer {token}"
            url = f"/api/families/{FAMILY}/small-variants"

            def page(gene: str):
                return ac.get(url, params={"gene": gene, "page": 1, "page_size": 100})

            def upload(sample: str, vcf: str):
                return ac.post(
                    f"/api/structural-variants/upload/{sample}",
                    files={"file": (f"{sample}.sniffles.vcf", vcf.encode(), "text/plain")},
                )

            try:
                # First opens: build the index from the imported SVs.
                out["ch_before"] = _cap(await page("GENE_CH"))
                out["brca2_before"] = _cap(await page("BRCA2"))

                out["mother_upload"] = _cap(await upload("MOTHER", _MOTHER_SV_VCF))
                out["brca2_with_maternal_dup"] = _cap(await page("BRCA2"))

                out["version_before"] = await sv_version()

                out["delete"] = _cap(await ac.delete(_DELETE_PROBAND_SVS, params={"confirm": "true"}))
                out["version_after_delete"] = await sv_version()
                out["brca2_after_delete"] = _cap(await page("BRCA2"))

                out["upload"] = _cap(await upload("PROBAND", _PROBAND_SV_VCF))
                out["version_after_upload"] = await sv_version()
                out["ch_after_upload"] = _cap(await page("GENE_CH"))
            finally:
                # Drop the uploaded deletion and duplication (each holds one sample's call);
                # the re-import below restores the two samples' calls in the fixture's SVs.
                out["cleanup_delete"] = _cap(
                    await ac.delete(_DELETE_PROBAND_SVS, params={"confirm": "true"})
                )
                out["cleanup_delete_mother"] = _cap(
                    await ac.delete(_DELETE_MOTHER_SVS, params={"confirm": "true"})
                )
    finally:
        await _set_seeded_genes(None)
        # Restore the fixture state (the proband's NeedlR SV calls) for later modules.
        out["restored"] = await _harness.import_golden_trio(root)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory, request) -> dict:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    if not (_FIXTURE / "manifest.yaml").exists():
        pytest.fail("golden_trio fixture missing: it is committed, so restore it (scripts/generate_golden_trio.py rebuilds it)")

    base = tmp_path_factory.mktemp("golden_sv_index")
    root = base / "FAM_TRIO"
    shutil.copytree(_FIXTURE, root)

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "family_import_roots", [str(root.parent)])
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", base / "data")
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


def test_a_parents_own_sv_of_another_type_does_not_hide_the_deletion_in_trans(run) -> None:
    assert run["mother_upload"]["status"] == 200, run["mother_upload"]["text"]
    page = run["brca2_with_maternal_dup"]
    assert page["status"] == 200, page["text"]
    hits = [hit for hit in _second_hits(page) if hit]
    assert hits, "BRCA2 is hit by the paternal deletion and the maternal duplication"
    hit = hits[0]
    assert hit["sv_types"] == ["DEL", "DUP"], hit
    # The unaffected mother carries the SNV and her own duplication, and has a reference
    # call at the deletion. Before CLIN-28 any SV of hers counted as the deletion: she
    # carried both hits, so the gene was cis and the deletion in trans hidden.
    assert hit["phase"] == "trans", hit
    assert hit["phase_evidence"] == "segregation", hit
    assert hit["deletion_unmasked"] is True, hit
    by_type = {sv["sv_type"]: sv for sv in hit["svs"]}
    deletion, duplication = by_type["DEL"], by_type["DUP"]
    assert (deletion["phase"], deletion["phase_evidence"], deletion["copy_loss"]) == (
        "trans",
        "segregation",
        True,
    ), deletion
    # The proband does not carry the mother's duplication: no phase of its own.
    assert (duplication["affected_zygosity"], duplication["phase"]) == (None, "unknown"), duplication


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
    assert run["cleanup_delete_mother"]["status"] == 200, run["cleanup_delete_mother"]["text"]
    assert run["restored"]["completed"] is True
    assert run["restored"]["n_structural_variants"] == run["facts"]["n_structural_variants"]
    assert run["restored"]["brca2_sv_second_hits"] == run["facts"]["brca2_sv_second_hits"]
