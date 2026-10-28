"""The alignment endpoints serve a sample's reads only from its own family's folders (E2E).

In local storage mode the CRAM endpoints look for a sample's reads in its family's folders,
``<data>/<family>`` and ``<data>/families/<family>``. The lookup used to check only that a
path stayed inside the data directory, so a sample whose ID climbs into another family
(``../F2/S2``; the ID rule accepted it until SEC-15) was listed in the alignment manifest with
that family's reads, and a folder linked into another family's folder served that family's
file (SEC-20).

Over the real API (an in-process ``httpx.ASGITransport`` client, one event loop, see
test_e2e_api_contract.py), with the families in Postgres made through the Family Builder and
the seeded admin signed in: family F2 has sample S2, family F1 has S1, a sample ``../F2/S2``
and S3. Every writer refuses ``../F2/S2`` since SEC-15, so that sample is made under another
ID and renamed in Postgres, as a sample stored before could be named. The reads lie in a data
directory of the test's own: F2's S2 in every layout, F1's S1 in its flat folder, and F1's
package ``bams`` folder is a link to F2's, which holds a file named after S3.

* F1's alignment manifest lists S1 alone (before: also ``../F2/S2`` with F2's reads, and S3
  with the file the link leads to), and following S1's URLs fetches S1's reads and index;
* S3's reads and index answer 404 to GET and HEAD (before: 200 with F2's file);
* F2's manifest still lists S2, and its URLs fetch S2's reads.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job sets it. The IDs
and files are synthetic, and the families are dropped at the end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

# The five layouts, under the data directory.
_LAYOUTS = (
    "{family}/{sample}.cram",
    "families/{family}/bams/{sample}.cram",
    "families/{family}/alignments/{sample}.cram",
    "families/{family}/{sample}.cram",
    "{family}/bams/{sample}.cram",
)


def _content(family: str, sample: str) -> bytes:
    return f"{family} | {sample}".encode()


def _place(data: Path, relative: str, content: bytes) -> None:
    """A CRAM and its index at ``relative``, both holding ``content``."""
    for name in (relative, f"{relative}.crai"):
        path = data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


async def _exercise(run: str, data: Path) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from backend.app.core.config import settings
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.routers import cram
    from backend.tests.e2e import _harness

    f1, f2 = f"ALIGN_{run}_F1", f"ALIGN_{run}_F2"
    s1, s2, s3 = f"ALIGN_{run}_S1", f"ALIGN_{run}_S2", f"ALIGN_{run}_S3"
    climbing, stand_in = f"../{f2}/{s2}", f"ALIGN_{run}_CLIMBING"

    for layout in _LAYOUTS:
        _place(data, layout.format(family=f2, sample=s2), _content(f2, s2))
    _place(data, f"{f1}/{s1}.cram", _content(f1, s1))
    _place(data, f"families/{f2}/bams/{s3}.cram", _content(f2, s3))
    (data / "families" / f1).mkdir(parents=True)
    (data / "families" / f1 / "bams").symlink_to(data / "families" / f2 / "bams")

    await init_postgres_schema()
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    out: dict[str, Any] = {}
    saved = cram.DATA_DIR, settings.storage_backend
    cram.DATA_DIR, settings.storage_backend = data, "local"
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://e2e") as admin:
            admin.headers["Authorization"] = f"Bearer {await _harness.login_admin_token(admin)}"
            out["created"] = [
                (
                    await admin.post(
                        "/api/ped/manual",
                        json={
                            "family_id": family_id,
                            "project_id": project_id,
                            "members": [
                                {"sample_id": sample_id, "sex": "female", "is_proband": index == 0}
                                for index, sample_id in enumerate(sample_ids)
                            ],
                        },
                    )
                ).status_code
                for family_id, sample_ids in ((f2, [s2]), (f1, [s1, stand_in, s3]))
            ]
            # The Family Builder refuses an ID holding '/' since SEC-15: the climbing sample is
            # renamed in Postgres, as one stored before could be named.
            async with sessionmaker() as session:
                renamed = await session.execute(
                    text("UPDATE samples SET sample_id = :climbing WHERE sample_id = :stand_in"),
                    {"climbing": climbing, "stand_in": stand_in},
                )
                out["renamed"] = renamed.rowcount
                await session.commit()

            async def manifest(family_id: str, *sample_ids: str) -> tuple[int, list[dict[str, Any]]]:
                response = await admin.get(
                    f"/api/cram/{quote(family_id, safe='')}/manifest", params={"sample": list(sample_ids)}
                )
                return response.status_code, response.json()

            async def follow(url: str) -> tuple[int, bytes]:
                response = await admin.get(f"/api{url}")
                return response.status_code, response.content

            status, entries = await manifest(f1, s1, climbing, s3)
            out["f1_manifest"] = (status, [entry["sample_id"] for entry in entries])
            out["f1_reads"] = [
                await follow(entry[key]) for entry in entries if entry["sample_id"] == s1 for key in ("url", "index_url")
            ]
            out["s3_files"] = []
            for name in (f"{s3}.cram", f"{s3}.cram.crai"):
                got = await admin.get(f"/api/cram/{f1}/{name}")
                head = await admin.head(f"/api/cram/{f1}/{name}")
                out["s3_files"].append((got.status_code, head.status_code, got.content == _content(f2, s3)))

            status, entries = await manifest(f2, s2)
            out["f2_manifest"] = (status, [entry["sample_id"] for entry in entries])
            out["f2_reads"] = [await follow(entry[key]) for entry in entries for key in ("url", "index_url")]
    finally:
        cram.DATA_DIR, settings.storage_backend = saved
        async with sessionmaker() as session:
            await session.execute(
                text("DELETE FROM families WHERE family_id IN (:f1, :f2)"), {"f1": f1, "f2": f2}
            )
            await session.commit()
    out["ids"] = {"f1": f1, "f2": f2, "s1": s1, "s2": s2}
    return out


@pytest.fixture(scope="module")
def looked_up(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from backend.tests.e2e import _harness

    run = uuid4().hex[:12]
    data = tmp_path_factory.mktemp("alignment-data")
    return _harness.run_async(lambda: _exercise(run, data))


def test_the_families_are_made(looked_up: dict[str, Any]) -> None:
    # Without this the answers below could come from families that do not exist.
    assert looked_up["created"] == [200, 200]
    assert looked_up["renamed"] == 1


def test_the_manifest_lists_only_reads_in_the_familys_own_folders(looked_up: dict[str, Any]) -> None:
    ids = looked_up["ids"]
    assert looked_up["f1_manifest"] == (200, [ids["s1"]])
    assert looked_up["f1_reads"] == [(200, _content(ids["f1"], ids["s1"]))] * 2


def test_a_folder_linked_into_another_family_serves_none_of_its_files(looked_up: dict[str, Any]) -> None:
    # (GET status, HEAD status, whether the GET returned F2's file), for the reads and the index.
    assert looked_up["s3_files"] == [(404, 404, False)] * 2


def test_the_other_family_still_reaches_its_own_reads(looked_up: dict[str, Any]) -> None:
    ids = looked_up["ids"]
    assert looked_up["f2_manifest"] == (200, [ids["s2"]])
    assert looked_up["f2_reads"] == [(200, _content(ids["f2"], ids["s2"]))] * 2
