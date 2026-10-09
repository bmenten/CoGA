"""An uploaded SV VCF is read from the uploaded sample's own column (E2E).

``POST /structural-variants/upload/{sample_id}`` stores one sample's calls from one Sniffles
or Spectre VCF. It used to read the file's first sample column whatever that column was
named, and store it under the sample in the URL: a joint Sniffles2 VCF uploaded for the
child stored the mother's genotypes as the child's, and the mother's own file uploaded for
the child replaced the child's calls without a word. The column is now chosen by the rule of
the TRGT upload and the package's per-sample files (``per_sample_vcf_column``).

The family here is its own, created through the PED upload under a new id each run, so the
golden trio is never touched:

* a joint Sniffles VCF whose columns are the mother, the child and the father, in that
  order, uploaded for the child stores the child's column only: its genotypes and phase set;
* the same file uploaded for the mother adds the mother's column to the same SVs, so a joint
  VCF can be uploaded member by member;
* the mother's own Sniffles VCF uploaded for the child, with ``overwrite=true``, is refused
  (400) before anything is written: the stored calls and the child's recorded SV file stay.

Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client for the
API (see test_e2e_api_contract.py for why); app exceptions come back as a 500 response rather
than being raised in the test.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_DEL_30000 = "1-30000-31000-DEL---"
_DUP_50000 = "1-50000-50500-DUP---"
_DEL_70000 = "1-70000-71000-DEL---"

# Each member's own call (GT:PS) on the deletion and the duplication, so a stored call shows
# whose column it was read from.
_CALLS = {
    "MOTHER": ("0|1:1001", "1/1:."),
    "PROBAND": ("1|1:2002", "0/0:."),
    "FATHER": ("0/0:.", "0/1:."),
}


def _vcf(columns: list[str], records: list[str]) -> bytes:
    return (
        "##fileformat=VCFv4.2\n"
        "##source=Sniffles2_2.2\n"
        "##contig=<ID=1,length=248956422>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT"
        + "".join(f"\t{column}" for column in columns)
        + "\n"
        + "".join(f"{record}\n" for record in records)
    ).encode()


def _joint_vcf(samples: dict[str, str]) -> bytes:
    """The family's joint Sniffles VCF: the mother's, the child's and the father's columns."""
    order = ("MOTHER", "PROBAND", "FATHER")
    return _vcf(
        [samples[role] for role in order],
        [
            "1\t30000\tSniffles2.DEL.1\tN\t<DEL>\t55\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=14\tGT:PS\t"
            + "\t".join(_CALLS[role][0] for role in order),
            "1\t50000\tSniffles2.DUP.2\tN\t<DUP>\t35\tPASS\tSVTYPE=DUP;SVLEN=500;END=50500;SUPPORT=7\tGT:PS\t"
            + "\t".join(_CALLS[role][1] for role in order),
        ],
    )


def _mothers_own_vcf(mother: str) -> bytes:
    """The mother's own Sniffles VCF: her column only, a call on the shared deletion and a
    deletion of her own."""
    return _vcf(
        [mother],
        [
            "1\t30000\tSniffles2.DEL.1\tN\t<DEL>\t50\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=12\tGT:PS\t0|1:1001",
            "1\t70000\tSniffles2.DEL.3\tN\t<DEL>\t44\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=71000;SUPPORT=10\tGT:PS\t0/1:.",
        ],
    )


def _cap(resp) -> dict:
    out = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:
        pass
    return out


async def _sniffles_rows(family_uuid: str) -> list[tuple[str, dict[str, tuple[str, int | None]]]]:
    """The family's live Sniffles rows as stored, one ``(SV, {sample: (GT, phase set)})`` per
    row, so that an SV stored twice shows as two entries."""
    from backend.app.core.clickhouse import execute_clickhouse
    from backend.app.services.clickhouse_variant_ids import _structural_table_name
    from backend.tests.e2e import _harness

    rows = await execute_clickhouse(
        f"SELECT variantId, `calls.sampleId`, `calls.gt`, `calls.ps` "
        f"FROM {_structural_table_name(_harness.ASSEMBLY, 'entries')} "
        "WHERE family_guid = %(family_guid)s AND sign = 1 AND source = 'sniffles' ORDER BY variantId",
        {"family_guid": family_uuid},
    )
    return [
        (
            str(variant_id),
            {
                str(sample): (str(gt), None if ps is None else int(ps))
                for sample, gt, ps in zip(sample_ids, gts, phase_sets)
            },
        )
        for variant_id, sample_ids, gts, phase_sets in rows
    ]


async def _sv_files(session, sample_id: str):
    from sqlalchemy import text

    return (
        await session.execute(
            text("SELECT metadata -> 'sv_files' FROM samples WHERE sample_id = :sample_id"),
            {"sample_id": sample_id},
        )
    ).scalar_one_or_none()


async def _exercise(family_id: str) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    samples = {role: f"{role}_{family_id}" for role in ("FATHER", "MOTHER", "PROBAND")}
    father, mother, proband = samples["FATHER"], samples["MOTHER"], samples["PROBAND"]
    ped = (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n"
    )
    out: dict = {"samples": samples}

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
        async with sessionmaker() as session:
            family_uuid = (
                await session.execute(
                    text("SELECT id::text FROM families WHERE family_id = :f"), {"f": family_id}
                )
            ).scalar_one()

        async def upload(sample: str, body: bytes, filename: str, *, overwrite: bool = False) -> dict:
            return _cap(
                await ac.post(
                    f"/api/structural-variants/upload/{sample}",
                    params={"overwrite": str(overwrite).lower(), "source_format": "sniffles"},
                    files={"file": (filename, body, "text/plain")},
                )
            )

        joint = _joint_vcf(samples)
        out["proband_joint"] = await upload(proband, joint, "family.sniffles.vcf")
        out["after_proband_joint"] = await _sniffles_rows(family_uuid)

        out["mother_joint"] = await upload(mother, joint, "family.sniffles.vcf")
        out["after_mother_joint"] = await _sniffles_rows(family_uuid)
        async with sessionmaker() as session:
            out["proband_sv_files_before"] = await _sv_files(session, proband)

        out["wrong_member"] = await upload(
            proband, _mothers_own_vcf(mother), "mother.sniffles.vcf", overwrite=True
        )
        out["after_wrong_member"] = await _sniffles_rows(family_uuid)
        async with sessionmaker() as session:
            out["proband_sv_files_after"] = await _sv_files(session, proband)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("sv_upload_sample_column"))
    try:
        return _harness.run_async(lambda: _exercise(f"SVC_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_the_family_is_created_by_the_ped_upload(run) -> None:
    assert run["ped"]["status"] == 200, run["ped"]["text"]


def test_a_joint_vcf_uploaded_for_the_child_stores_the_childs_column_only(run) -> None:
    proband = run["samples"]["PROBAND"]
    upload = run["proband_joint"]
    assert upload["status"] == 200, upload["text"]
    assert (upload["json"]["processed"], upload["json"]["created"]) == (2, 2)
    # Before the fix: the first column, the mother's ("0|1" with her phase set, "1/1").
    assert run["after_proband_joint"] == [
        (_DEL_30000, {proband: ("1|1", 2002)}),
        (_DUP_50000, {proband: ("0/0", None)}),
    ]


def test_the_same_joint_vcf_uploaded_for_the_mother_adds_her_column(run) -> None:
    mother, proband = run["samples"]["MOTHER"], run["samples"]["PROBAND"]
    upload = run["mother_joint"]
    assert upload["status"] == 200, upload["text"]
    assert (upload["json"]["created"], upload["json"]["merged"]) == (0, 2)
    assert run["after_mother_joint"] == [
        (_DEL_30000, {mother: ("0|1", 1001), proband: ("1|1", 2002)}),
        (_DUP_50000, {mother: ("1/1", None), proband: ("0/0", None)}),
    ]


def test_the_mothers_own_file_uploaded_for_the_child_is_refused_and_nothing_is_written(run) -> None:
    mother, proband = run["samples"]["MOTHER"], run["samples"]["PROBAND"]
    refused = run["wrong_member"]
    # Before the fix: 200, and the child's calls replaced by the mother's.
    assert refused["status"] == 400, refused["text"]
    assert refused["json"]["detail"] == (
        f"Sniffles VCF has no sample column for {proband}: '{mother}' is {mother}. "
        "Upload this sample's own file."
    )
    # The stored calls, the mother's deletion absent among them, and the child's recorded
    # SV file are as the joint uploads left them.
    assert run["after_wrong_member"] == run["after_mother_joint"]
    assert _DEL_70000 not in {variant_id for variant_id, _calls in run["after_wrong_member"]}
    assert run["proband_sv_files_after"] == run["proband_sv_files_before"] == {
        "sniffles": "family.sniffles.vcf"
    }
