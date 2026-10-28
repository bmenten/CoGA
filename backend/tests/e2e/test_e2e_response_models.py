"""Each route that had no response model answers what its new model describes (ENG-4), E2E.

Into real Postgres and ClickHouse, over the real API (an in-process ``httpx.ASGITransport``
client, one event loop, see test_e2e_api_contract.py), with the seeded admin signed in: a trio
uploaded through the PED route, the child's coverage, segments, APCAD and PCF BEDs, and a
Sniffles VCF of the child's and one of the father's, who share a deletion. Each route that had
no model is then called, and its real answer compared with the model: validated and written
back, the answer is itself, so the model renames, drops and adds no field. The routes:

* the BED reads of each track type (one chromosome, a window, the batch), the BED and SV
  uploads, and the shared SV counts;
* the administrator's QC profile and cut-off (set and cleared), the family's projects, a NIPT
  artifact's deletion, and the deletions of the child's segments, the family's small
  variants, the mother and the family;
* the health probes.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The IDs and
files are synthetic, and the family is deleted at the end.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_COVERAGE_BED = b"1\t0\t1000\tbin1\t31.0\n1\t1000\t2000\tbin2\t29.5\n2\t0\t1000\tbin3\t30.0\n"
_SEGMENTS_BED = b"1\t0\t2000\t-0.1\n"
_APCAD_BED = b"1\t100\t101\tsnp1\t0.98\tpat\n1\t200\t201\tsnp2\t0.03\tmat\n1\t300\t301\tsnp3\t0.5\tund\n"
_PCF_BED = b"1\t0\t2000\tseg1\t0.97\tpaternal\n"
_SHARED_DELETION = "1\t30000\tSniffles2.DEL.1\tN\t<DEL>\t55\tPASS\tSVTYPE=DEL;SVLEN=-1000;END=31000;SUPPORT=14\tGT\t0/1\n"
_CHILDS_INSERTION = "1\t80000\tSniffles2.INS.2\tN\t<INS>\t45\tPASS\tSVTYPE=INS;SVLEN=500;END=80000;SUPPORT=9\tGT\t0/1\n"
_METRIC = "depth.mean_depth"
# The child's BED reads: each track type, one chromosome, a window and the batch.
_BED_READS: list[tuple[str, str, dict[str, Any]]] = [
    ("coverage", "coverage", {"chrom": "1"}),
    ("coverage_window", "coverage", {"chrom": "1", "window": 2000}),
    ("coverage_batch", "coverage/batch", {"chrom": ["1", "2"], "window": 2000}),
    ("segments", "segments", {"chrom": "1"}),
    ("apcad", "apcad", {"chrom": "1"}),
    ("apcad_window_batch", "apcad/batch", {"chrom": ["1"], "window": 1000, "limit": 100}),
    ("apcad_pcf", "apcad_pcf", {"chrom": "1"}),
    ("apcad_pcf_batch", "apcad_pcf/batch", {"chrom": ["1", "2"]}),
]

# What each answer is compared with: the model's name in backend.app.schemas (BedReadOut is
# the BED reads' union of BedTrackOut and BedApcadTrackOut).
MODEL_OF = {
    "upload_coverage": "BedUploadResult",
    "upload_segments": "BedUploadResult",
    "upload_apcad": "BedUploadResult",
    "upload_apcad_pcf": "BedUploadResult",
    "coverage": "BedReadOut",
    "coverage_window": "BedReadOut",
    "coverage_batch": "BedReadOut",
    "segments": "BedReadOut",
    "apcad": "BedReadOut",
    "apcad_window_batch": "BedReadOut",
    "apcad_pcf": "BedReadOut",
    "apcad_pcf_batch": "BedReadOut",
    "sv_child": "StructuralVariantUploadResult",
    "sv_father": "StructuralVariantUploadResult",
    "shared": "SharedStructuralVariantCountsOut",
    "qc_profile": "QcThresholdProfileSummaryOut",
    "qc_set": "QcThresholdSetOut",
    "qc_cleared": "QcThresholdSetOut",
    "projects": "FamilyProjectsUpdateOut",
    "artifact_deleted": "NiptArtifactDeleteOut",
    "segments_deleted": "DataDeletionOut",
    "small_variants_deleted": "DataDeletionOut",
    "mother_deleted": "SampleDeletionOut",
    "family_deleted": "FamilyDeletionOut",
    "health": "HealthOut",
    "ready": "ReadinessOut",
}


def _sniffles_vcf(sample: str, *records: str) -> bytes:
    return (
        "##fileformat=VCFv4.2\n"
        "##source=Sniffles2_2.6\n"
        "##contig=<ID=1,length=248956422>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n" + "".join(records)
    ).encode()


def _cap(resp: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": resp.status_code, "text": resp.text}
    try:
        out["json"] = resp.json()
    except ValueError:
        pass
    return out


async def _exercise(run: str) -> dict[str, Any]:
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
        _admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)

    family = f"ENG4_{run}"
    father, mother, child = (f"{role}_{run}" for role in ("FATHER", "MOTHER", "CHILD"))
    ped = (
        f"{family}\t{father}\t0\t0\t1\t1\n"
        f"{family}\t{mother}\t0\t0\t2\t1\n"
        f"{family}\t{child}\t{father}\t{mother}\t2\t2\n"
    )
    r: dict[str, Any] = {"names": {"family": family, "father": father, "mother": mother, "child": child}}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
            ac.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(ac)}"
            r["ped"] = _cap(
                await ac.post(
                    "/api/ped/upload",
                    params={"project_id": project_id},
                    files={"file": (f"{family}.ped", ped.encode(), "text/plain")},
                )
            )

            # --- the BED uploads and reads, the SV uploads and the shared SV counts ---------
            for bed_type, content, params in (
                ("coverage", _COVERAGE_BED, {"content": "read_depth"}),
                ("segments", _SEGMENTS_BED, {}),
                ("apcad", _APCAD_BED, {}),
                ("apcad_pcf", _PCF_BED, {}),
            ):
                r[f"upload_{bed_type}"] = _cap(
                    await ac.post(
                        f"/api/bed/upload/{child}/{bed_type}",
                        params={"overwrite": "false", **params},
                        files={"file": (f"{child}.{bed_type}.bed", content, "text/plain")},
                    )
                )
            for key, path, query in _BED_READS:
                r[key] = _cap(await ac.get(f"/api/bed/{child}/{path}", params=query))

            for key, sample, records in (
                ("sv_child", child, (_SHARED_DELETION, _CHILDS_INSERTION)),
                ("sv_father", father, (_SHARED_DELETION,)),
            ):
                r[key] = _cap(
                    await ac.post(
                        f"/api/structural-variants/upload/{sample}",
                        params={"overwrite": "false", "source_format": "sniffles"},
                        files={"file": (f"{sample}.sniffles.vcf", _sniffles_vcf(sample, *records), "text/plain")},
                    )
                )
            r["shared"] = _cap(await ac.get(f"/api/families/{family}/shared-structural-variant-counts"))

            # --- the administrator's answers -------------------------------------------------
            profile = f"eng4_{run.lower()}"
            r["qc_profile"] = _cap(
                await ac.post("/api/admin/qc-thresholds/profiles", json={"label": f"ENG-4 {run}", "key": profile})
            )
            cut_off = {"metric_key": _METRIC, "reason": "e2e: the answer's model"}
            r["qc_set"] = _cap(
                await ac.put(
                    f"/api/admin/qc-thresholds/{profile}", json={**cut_off, "warn_value": 30, "error_value": 20}
                )
            )
            r["qc_cleared"] = _cap(
                await ac.put(
                    f"/api/admin/qc-thresholds/{profile}", json={**cut_off, "warn_value": None, "error_value": None}
                )
            )
            r["projects"] = _cap(
                await ac.put(f"/api/admin/families/{family}/projects", json={"project_ids": [project_id]})
            )
            r["project_id"] = project_id
            artifact = _cap(
                await ac.post(
                    "/api/admin/nipt/artifacts",
                    json={"assembly_id": assembly_id, "variant_id": f"1-100-A-G-{run}", "assay_key": f"eng4_{run}"},
                )
            )
            r["artifact"] = artifact
            r["artifact_deleted"] = _cap(
                await ac.delete(f"/api/admin/nipt/artifacts/{(artifact.get('json') or {}).get('id')}")
            )
            r["segments_deleted"] = _cap(
                await ac.delete(f"/api/admin/data/samples/{child}/segments", params={"confirm": "true"})
            )
            r["small_variants_deleted"] = _cap(
                await ac.delete(f"/api/admin/data/families/{family}/small_variants", params={"confirm": "true"})
            )
            r["mother_deleted"] = _cap(await ac.delete(f"/api/admin/samples/{mother}", params={"confirm": "true"}))
            r["family_deleted"] = _cap(
                await ac.request(
                    "DELETE", f"/api/admin/families/{family}", params={"confirm": "true"}, json={"reason": "e2e"}
                )
            )

            # --- the health probes -----------------------------------------------------------
            r["health"] = _cap(await ac.get("/api/health"))
            r["ready"] = _cap(await ac.get("/api/health/ready"))
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM families WHERE family_id = :f"), {"f": family})
            await session.commit()
    return r


@pytest.fixture(scope="module")
def answers(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The uploads keep a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("response_models"))
    try:
        return _harness.run_async(lambda: _exercise(uuid4().hex[:8].upper()))
    finally:
        mp.undo()


def test_the_trio_is_uploaded(answers) -> None:
    assert answers["ped"]["status"] == 200, answers["ped"]["text"]


@pytest.mark.parametrize("key", list(MODEL_OF))
def test_each_answer_is_what_its_model_describes(answers, key: str) -> None:
    from pydantic import TypeAdapter

    from backend.app import schemas

    answer = answers[key]
    assert answer["status"] in (200, 201), (key, answer["status"], answer["text"])
    body = answer["json"]
    adapter: TypeAdapter[Any] = TypeAdapter(getattr(schemas, MODEL_OF[key]))
    # Validated and written back, the real answer is itself: its model renames, drops and adds
    # no field, and types each value as it is served.
    assert adapter.dump_python(adapter.validate_python(body), mode="json") == body


def test_the_bed_reads_answer_each_track_type_with_its_rows(answers) -> None:
    from backend.app.schemas import BedApcadRecordOut, BedRecordOut

    for key in ("coverage", "coverage_window", "coverage_batch", "segments"):
        body = answers[key]["json"]
        assert body["items"] and all(set(item) == set(BedRecordOut.model_fields) for item in body["items"]), body
    for key in ("apcad", "apcad_window_batch", "apcad_pcf", "apcad_pcf_batch"):
        body = answers[key]["json"]
        assert body["items"] and all(set(item) == set(BedApcadRecordOut.model_fields) for item in body["items"]), body
    # The batch reads both chromosomes; the window averages the child's two bins on chr1.
    assert [(item["chr"], item["value"]) for item in answers["coverage_batch"]["json"]["items"]] == [
        ("1", 30.25),
        ("2", 30.0),
    ]
    # The informative sites, each with its parent; the read leaves the third, of unknown origin, out.
    assert {item["origin"] for item in answers["apcad"]["json"]["items"]} == {"paternal", "maternal"}


def test_the_uploads_answer_what_they_stored(answers) -> None:
    assert [answers[f"upload_{bed_type}"]["json"]["inserted"] for bed_type in ("coverage", "segments", "apcad", "apcad_pcf")] == [
        3,
        1,
        3,
        1,
    ]
    child, father = answers["sv_child"]["json"], answers["sv_father"]["json"]
    assert (child["processed"], child["created"], child["merged"], child["source_format"]) == (2, 2, 0, "sniffles")
    assert (father["processed"], father["created"], father["merged"]) == (1, 0, 1)


def test_the_shared_counts_count_the_deletion_the_child_and_father_share(answers) -> None:
    names = answers["names"]
    father, mother, child = names["father"], names["mother"], names["child"]
    assert answers["shared"]["json"] == {
        father: {father: 0, mother: 0, child: 1},
        mother: {father: 0, mother: 0, child: 0},
        child: {father: 1, mother: 0, child: 1},
    }


def test_the_administrators_answers_say_what_was_done(answers) -> None:
    names = answers["names"]
    assert {key: answers["qc_profile"]["json"][key] for key in ("is_default", "description")} == {
        "is_default": False,
        "description": None,
    }
    assert (answers["qc_set"]["json"]["warn_value"], answers["qc_set"]["json"]["error_value"]) == (30.0, 20.0)
    assert (answers["qc_cleared"]["json"]["warn_value"], answers["qc_cleared"]["json"]["error_value"]) == (None, None)
    assert answers["projects"]["json"]["project_ids"] == [answers["project_id"]]
    assert answers["artifact"]["status"] == 200, answers["artifact"]["text"]
    assert answers["artifact_deleted"]["json"] == {"deleted": True}
    assert answers["segments_deleted"]["json"] == {"deleted": 1}
    assert answers["small_variants_deleted"]["json"] == {"deleted": 0}
    assert answers["mother_deleted"]["json"]["deleted"]["samples"] == 1
    family = answers["family_deleted"]["json"]["deleted"]
    assert (family["families"], family["samples"]) == (1, 2), (names, family)
    assert answers["ready"]["json"] == {"status": "ok", "postgres": "ok", "clickhouse": "ok"}
