"""The clinical CNV knowledgebase and the DGV variants an assembly gets without being asked.

Adding GRCh38 (at a start, or from UCSC on the Reference page) loads its cytobands and genes,
and the clinical CNV knowledgebase and DGV need no administrator's rebuild or manual load: an
assembly import from UCSC queues both for a GRCh37 or GRCh38 assembly that has neither, as the importing administrator, and says so in its result; a start does the same as no
account, the bootstrap assembly first, unless switched off (``REFERENCE_BOOTSTRAP_CLINICAL_CNVS``,
``REFERENCE_BOOTSTRAP_DGV``), and never stops on a failure. What each queue does is tested with
its job (``test_clinical_cnv_kb_jobs.py``, ``test_dgv_load_jobs.py``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.routers import assemblies as assemblies_router
from backend.app.schemas import ReferenceAutoImportRequest, ReferenceAutoImportResult
from backend.app.services import reference_auto_load as auto
from backend.app.services.access_control import CurrentUser

ADMIN = CurrentUser(
    id="00000000-0000-0000-0000-0000000000d3",
    username="lab.admin",
    email="lab.admin@example.org",
    role="admin",
    created_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
)


class _Result:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def scalars(self) -> list[str]:
        return self.values


class _Session:
    def __init__(self, assemblies: list[str], *, fail: bool = False) -> None:
        self.assemblies, self.fail = assemblies, fail
        self.rolled_back = 0

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        if self.fail:
            raise RuntimeError("database unavailable")
        return _Result(self.assemblies)

    async def rollback(self) -> None:
        self.rolled_back += 1


@pytest.fixture()
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, Any]]:
    calls: list[tuple[str, str, Any]] = []

    async def cnv(_session: Any, *, assembly_name: str, requested_by: Any) -> tuple[None, str]:
        calls.append(("clinical_cnvs", assembly_name, requested_by))
        return None, f"CNV {assembly_name}"

    async def dgv(_session: Any, *, assembly_name: str, requested_by: Any) -> tuple[None, str | None]:
        calls.append(("dgv", assembly_name, requested_by))
        return None, None

    monkeypatch.setattr(auto, "queue_clinical_cnv_kb_rebuild_if_missing", cnv)
    monkeypatch.setattr(auto, "queue_dgv_load_if_missing", dgv)
    monkeypatch.setattr(auto.settings, "reference_bootstrap_enabled", True)
    monkeypatch.setattr(auto.settings, "reference_bootstrap_clinical_cnvs", True)
    monkeypatch.setattr(auto.settings, "reference_bootstrap_dgv", True)
    monkeypatch.setattr(auto.settings, "reference_bootstrap_assembly_name", "GRCh38")
    return calls


def test_an_assembly_gets_both_loads_and_the_notes_say_what_started(queued: list) -> None:
    notes = asyncio.run(auto.queue_missing_reference_loads(object(), assembly_name="GRCh38", requested_by=ADMIN))

    assert queued == [("clinical_cnvs", "GRCh38", ADMIN), ("dgv", "GRCh38", ADMIN)]
    assert notes == ["CNV GRCh38"]  # the DGV queue had nothing to say


def test_a_start_queues_for_each_grch37_and_grch38_assembly_the_bootstrap_one_first(queued: list) -> None:
    asyncio.run(auto.queue_startup_reference_loads(_Session(["GRCh37", "T2T-CHM13", "GRCh38"])))

    assert queued == [
        ("clinical_cnvs", "GRCh38", None),
        ("dgv", "GRCh38", None),
        ("clinical_cnvs", "GRCh37", None),
        ("dgv", "GRCh37", None),
    ]


@pytest.mark.parametrize(
    ("switches", "expected"),
    [
        ({"reference_bootstrap_enabled": False}, []),
        ({"reference_bootstrap_clinical_cnvs": False, "reference_bootstrap_dgv": False}, []),
        ({"reference_bootstrap_dgv": False}, [("clinical_cnvs", "GRCh38", None)]),
        ({"reference_bootstrap_clinical_cnvs": False}, [("dgv", "GRCh38", None)]),
    ],
)
def test_a_start_queues_only_what_the_deployment_lets_it(
    queued: list, monkeypatch: pytest.MonkeyPatch, switches: dict, expected: list
) -> None:
    for name, value in switches.items():
        monkeypatch.setattr(auto.settings, name, value)
    asyncio.run(auto.queue_startup_reference_loads(_Session(["GRCh38"])))

    assert queued == expected


def test_a_start_goes_on_when_an_assembly_cannot_be_queued(queued: list, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(_session: Any, *, assembly_name: str, requested_by: Any) -> tuple[None, None]:
        queued.append(("clinical_cnvs", assembly_name, requested_by))
        if assembly_name == "GRCh38":
            raise RuntimeError("database unavailable")
        return None, None

    monkeypatch.setattr(auto, "queue_clinical_cnv_kb_rebuild_if_missing", failing)
    session = _Session(["GRCh38", "GRCh37"])
    asyncio.run(auto.queue_startup_reference_loads(session))

    assert [entry[1] for entry in queued] == ["GRCh38", "GRCh37", "GRCh37"]
    assert session.rolled_back == 1


def test_a_start_goes_on_when_the_assemblies_cannot_be_read(queued: list) -> None:
    session = _Session([], fail=True)
    asyncio.run(auto.queue_startup_reference_loads(session))

    assert queued == [] and session.rolled_back == 1


def test_an_assembly_import_from_ucsc_starts_the_loads_as_the_importing_administrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def imported(_session: Any, **_kwargs: Any) -> ReferenceAutoImportResult:
        return ReferenceAutoImportResult(
            species_id="s",
            species_name="Homo sapiens",
            assembly_id="a",
            assembly_name="GRCh38",
            assembly_version="p14",
            ucsc_genome="hg38",
            created_species=False,
            created_assembly=True,
            cytobands_inserted=24,
            genes_inserted=100,
            cytobands_replaced=False,
            genes_replaced=False,
            cytoband_source_url="https://example.org/c",
            gene_source_url="https://example.org/g",
            gene_source="gencode",
        )

    asked: list[tuple[str, Any]] = []

    async def follow_up(_session: Any, *, assembly_name: str, requested_by: Any) -> list[str]:
        asked.append((assembly_name, requested_by))
        return ["The DGV 2025-12-01 load for GRCh38 has started in the background."]

    monkeypatch.setattr(assemblies_router, "import_reference_from_ucsc", imported)
    monkeypatch.setattr(assemblies_router, "queue_missing_reference_loads", follow_up)
    result = asyncio.run(
        assemblies_router.import_reference_data(
            ReferenceAutoImportRequest(tax_id=9606, ucsc_genome="hg38", overwrite=False), session=object(), user=ADMIN
        )
    )

    assert asked == [("GRCh38", ADMIN)]
    assert result.follow_up_notes == ["The DGV 2025-12-01 load for GRCh38 has started in the background."]
