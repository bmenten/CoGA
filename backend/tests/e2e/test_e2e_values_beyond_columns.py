"""A value beyond its ClickHouse column no longer fails an upload or an import (SAFE-30), E2E.

The loaders fill the ClickHouse columns from file values, and the client refuses a whole
insert when one value falls outside its column. A mitochondrial call's read depth above
65,535 did not fit the UInt16 depth columns, a GQ of ``inf`` failed where the row was built,
and a negative phase set, a QUAL beyond Float32, an SVLEN beyond Int32 and a negative BED
start did not fit theirs: each upload, and the package import of a mitochondrial callset,
failed with a 500, after the rows it replaced were deleted.

Here, through the routes and a package import into real Postgres and ClickHouse (new family
ids each run; every sample and value synthetic):

* a trio's mitochondrial VCF holding a call of 120,000 reads (60,000 for each allele) is
  uploaded: 200, the call stored with its true depths;
* a VCF adding a call with an infinite GQ, a negative phase set and a QUAL beyond Float32,
  and a record whose POS no UInt32 holds, is uploaded with overwrite: 200, the deep call
  stored with its true depths and shown so in the mtDNA workspace, the infinite GQ, the
  phase set and the QUAL stored as missing, the record without a position skipped as
  malformed, and the family not flagged import-incomplete;
* a Sniffles VCF with an SVLEN beyond Int32, a read support beyond UInt32, a negative phase
  set and a QUAL beyond Float32, and a record whose END no UInt32 holds: 200, the SV stored
  without those values and the other record skipped;
* a coverage BED with a line whose start is negative: 200, that line left out;
* a package whose ``mito`` dataset holds the call of 120,000 reads with an infinite GQ is
  imported: the import completes and stores the call's depths.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why); app exceptions come back as a 500 response.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_MITO_HEADER = (
    "##fileformat=VCFv4.2\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    '##FORMAT=<ID=VAF,Number=A,Type=Float,Description="Variant allele fraction">\n'
    '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
)
# 120,000 reads, 60,000 for each allele: a UInt16 holds 65,535.
_DEEP = "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT:GQ:DP:AD:VAF\t0/1:99:120000:60000,60000:0.5\n"
# An infinite GQ, a negative phase set and a QUAL no Float32 holds.
_UNSTORABLE = "chrM\t3460\t.\tG\tA\t1e39\tPASS\t.\tGT:GQ:DP:AD:VAF:PS\t0/1:inf:500:250,250:0.5:-1\n"
# A POS no UInt32 holds: no position, so a malformed record.
_NO_POSITION = "chrM\t4294967296\t.\tA\tG\t30\tPASS\t.\tGT:DP\t0/1:50\n"
_PACKAGE_CALL = "chrM\t3243\t.\tA\tG\t30\tPASS\t.\tGT:GQ:DP:AD:VAF\t0/1:inf:120000:60000,60000:0.5\n"

_SNIFFLES = (
    "##fileformat=VCFv4.2\n##source=Sniffles2_2.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
    "1\t100000\tS.1\tN\t<DEL>\t1e39\tPASS\tSVTYPE=DEL;SVLEN=-3000000000;END=101000;SUPPORT=5000000000"
    "\tGT:PS\t0/1:-1\n"
    "1\t200000\tS.2\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;SVLEN=-10;END=5000000000;SUPPORT=9\tGT\t0/1\n"
)
_BED = "chr1\t-5\t100\t1.0\nchr1\t100\t200\t1.5\n"


def _cap(response: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": response.status_code, "text": response.text}
    if response.status_code < 400:
        out["json"] = response.json()
    return out


async def _small_variant_calls(family_uuid: str | None) -> dict[int, dict[str, Any]]:
    """The family's stored ``mito`` calls by position: each call column's first call. None
    for a family that is not stored (an import that failed removes the family it created)."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _small_table_name
    from backend.tests.e2e import _harness

    if family_uuid is None:
        return {}
    rows = await execute_clickhouse(
        "SELECT pos, qual, `calls.gq`, `calls.dp`, `calls.ad`, `calls.ps` "
        f"FROM {_small_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family_guid)s AND source = 'mito' AND sign = 1 ORDER BY pos",
        {"family_guid": family_uuid},
    )
    return {
        int(pos): {"qual": qual, "gq": gqs[0], "dp": dps[0], "ad": list(ads[0]), "ps": pss[0]}
        for pos, qual, gqs, dps, ads, pss in rows
    }


def _write_package(root: Path, family_id: str) -> str:
    """A trio's package whose ``mito`` dataset holds the proband's chrM VCF; returns the
    proband's sample id."""
    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    (root / "mito").mkdir(parents=True)
    (root / "family.ped").write_text(
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n",
        encoding="utf-8",
    )
    (root / "mito" / f"{proband}.vcf").write_text(
        _MITO_HEADER.format(sample="Sample0") + _PACKAGE_CALL, encoding="utf-8"
    )
    (root / "manifest.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                f"family_id: {family_id}",
                "ped: family.ped",
                "datasets:",
                "  mito:",
                "    per_sample:",
                f"      {proband}: {{vcf: mito/{proband}.vcf}}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return proband


async def _exercise(base: Path, family_id: str, package_family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services import family_package_import as package_import
    from backend.app.services.clickhouse_interval_tracks import fetch_interval_track_rows
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        fetch_family_structural_variant_rows,
    )
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    async def family_row(identifier: str) -> dict[str, Any]:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id::text AS family_uuid, metadata -> 'import_incomplete' AS flag "
                        "FROM families WHERE family_id = :f"
                    ),
                    {"f": identifier},
                )
            ).mappings().one_or_none()
        return dict(row) if row is not None else {"family_uuid": None, "flag": None, "missing": True}

    father, mother, proband = (f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND"))
    ped = (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n"
    )
    header = _MITO_HEADER.format(sample=proband)
    out: dict[str, Any] = {"proband": proband}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        out["ped"] = _cap(
            await ac.post(
                "/api/ped/upload",
                params={"project_id": project_id},
                files={"file": (f"{family_id}.ped", ped.encode(), "text/plain")},
            )
        )

        async def upload_mito(records: str, *, overwrite: bool) -> dict[str, Any]:
            return _cap(
                await ac.post(
                    f"/api/families/{family_id}/small-variants/upload",
                    params={"overwrite": str(overwrite).lower(), "source_format": "mito"},
                    files={"file": (f"{proband}.chrM.vcf", (header + records).encode(), "text/plain")},
                )
            )

        out["upload"] = await upload_mito(_DEEP, overwrite=False)
        family = await family_row(family_id)
        out["calls_after_upload"] = await _small_variant_calls(family["family_uuid"])
        out["overwrite"] = await upload_mito(_DEEP + _UNSTORABLE + _NO_POSITION, overwrite=True)
        family = await family_row(family_id)
        out["flag_after_overwrite"] = family["flag"]
        out["calls_after_overwrite"] = await _small_variant_calls(family["family_uuid"])
        out["mtdna"] = _cap(await ac.get(f"/api/families/{family_id}/mitochondrial-dna"))

        out["sv_upload"] = _cap(
            await ac.post(
                f"/api/structural-variants/upload/{proband}",
                params={"source_format": "sniffles"},
                files={"file": (f"{proband}.sniffles.vcf", _SNIFFLES.format(sample=proband).encode(), "text/plain")},
            )
        )
        out["sv_rows"] = [
            {
                "start": row.record.start,
                "end": row.record.end,
                "sv_len": row.record.sv_len,
                "calls": [
                    (call.sample, call.qual, call.read_support, call.phase_set) for call in row.record.calls
                ],
            }
            for row in await fetch_family_structural_variant_rows(
                _harness.ASSEMBLY, family["family_uuid"], source="sniffles"
            )
        ]
        out["bed_upload"] = _cap(
            await ac.post(
                f"/api/bed/upload/{proband}/coverage",
                # A coverage BED declares what it holds (TRACE-9).
                params={"content": "read_depth"},
                files={"file": ("coverage.bed", _BED.encode(), "text/plain")},
            )
        )
        async with sessionmaker() as session:
            proband_uuid = (
                await session.execute(
                    text("SELECT id::text FROM samples WHERE sample_id = :s"), {"s": proband}
                )
            ).scalar_one()
        out["bed_rows"] = [
            (row["start"], row["end"], row["value"])
            for row in await fetch_interval_track_rows(
                _harness.ASSEMBLY,
                sample_uuid=proband_uuid,
                track_type="coverage",
                chromosomes=["1"],
                source="web",
            )
        ]

    _write_package(base / package_family_id, package_family_id)
    async with sessionmaker() as session:
        result = await package_import.execute_family_package_import(
            session,
            folder_path=str(base / package_family_id),
            project_id=project_id,
            dry_run=False,
            user=admin,
            conflict_mode="cancel",
        )
    mito = next((dataset for dataset in result.datasets if dataset.dataset_type == "mito"), None)
    out["import"] = {
        "completed": result.completed,
        "error": result.error,
        "mito_status": mito.status if mito else None,
        "mito_message": mito.message if mito else None,
    }
    package_family = await family_row(package_family_id)
    out["package_family_missing"] = bool(package_family.get("missing"))
    out["package_flag"] = package_family["flag"]
    out["package_calls"] = await _small_variant_calls(package_family["family_uuid"])
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.core.config import settings
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    base = tmp_path_factory.mktemp("values_beyond_columns")
    mp = pytest.MonkeyPatch()
    # The uploads keep a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("values_beyond_columns_uploads"))
    mp.setattr(settings, "family_import_roots", [str(base)])
    suffix = uuid4().hex[:8].upper()
    try:
        return _harness.run_async(lambda: _exercise(base, f"BOUNDS_{suffix}", f"BOUNDS_PKG_{suffix}"))
    finally:
        mp.undo()


def test_the_family_is_created(run) -> None:
    assert run["ped"]["status"] == 200, run["ped"]["text"]


def test_a_deep_mitochondrial_call_is_uploaded_with_its_true_depths(run) -> None:
    # Before the fix: 500, the UInt16 depth columns refusing 120,000 reads.
    upload = run["upload"]
    assert upload["status"] == 200, upload["text"]
    assert (upload["json"]["inserted"], upload["json"]["skipped_malformed"]) == (1, 0)
    deep = run["calls_after_upload"][3243]
    assert (deep["dp"], deep["ad"], deep["gq"]) == (120_000, [60_000, 60_000], 99)


def test_an_overwrite_with_values_no_column_holds_answers_200_and_leaves_no_flag(run) -> None:
    # Before the fix: 500, the infinite GQ failing where the row was built, after the
    # callset was deleted; the family was left flagged import-incomplete.
    overwrite = run["overwrite"]
    assert overwrite["status"] == 200, overwrite["text"]
    assert (overwrite["json"]["inserted"], overwrite["json"]["skipped_malformed"]) == (2, 1)
    assert run["flag_after_overwrite"] is None
    assert run["calls_after_overwrite"][3243] == run["calls_after_upload"][3243]


def test_values_no_column_holds_are_stored_as_missing(run) -> None:
    unstorable = run["calls_after_overwrite"][3460]
    # The infinite GQ, the negative phase set and the QUAL beyond Float32.
    assert (unstorable["gq"], unstorable["ps"], unstorable["qual"]) == (None, None, None)
    assert (unstorable["dp"], unstorable["ad"]) == (500, [250, 250])
    # The record whose POS no UInt32 holds was skipped as malformed.
    assert sorted(run["calls_after_overwrite"]) == [3243, 3460]


def test_the_mtdna_workspace_shows_the_true_depth(run) -> None:
    mtdna = run["mtdna"]
    assert mtdna["status"] == 200, mtdna["text"]
    variants = {variant["variant_id"]: variant for variant in mtdna["json"]["variants"]}
    call = variants["M-3243-A-G"]["calls"][run["proband"]]
    assert (call["depth"], call["alt_depth"]) == (120_000, 60_000)


def test_an_sv_upload_stores_values_no_column_holds_as_missing(run) -> None:
    # Before the fix: 500, the Int32 SVLEN and the UInt32 positions refusing the rows.
    sv_upload = run["sv_upload"]
    assert sv_upload["status"] == 200, sv_upload["text"]
    assert sv_upload["json"]["processed"] == 1
    assert run["sv_rows"] == [
        {
            "start": 100_000,
            "end": 101_000,
            "sv_len": None,
            "calls": [(run["proband"], None, None, None)],
        }
    ]


def test_a_bed_line_with_a_negative_start_is_left_out(run) -> None:
    bed_upload = run["bed_upload"]
    assert bed_upload["status"] == 200, bed_upload["text"]
    assert bed_upload["json"] == {"inserted": 1}
    assert run["bed_rows"] == [(100, 200, 1.5)]


def test_a_package_import_stores_a_deep_mitochondrial_call(run) -> None:
    # Before the fix: the mito dataset failed, the depths beyond UInt16 and the infinite GQ.
    imported = run["import"]
    assert imported["completed"] is True, imported
    assert imported["mito_status"] == "imported", imported
    assert not run["package_family_missing"]
    assert run["package_flag"] is None
    call = run["package_calls"][3243]
    assert (call["dp"], call["ad"], call["gq"]) == (120_000, [60_000, 60_000], None)
