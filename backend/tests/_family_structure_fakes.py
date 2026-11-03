"""A family-structure change (``PUT /families/{id}/structure``) run against fakes of what it
reads, and the admin who runs it, for the tests of what the change writes and in what order.

``fake_structure_reads`` patches ``family_structure_service``'s reads: the family's mapping,
its current structure version (1), its member rows, no relationships, the genomic data it
holds on GRCh38, no pedigree text, and the family record the change answers with. The test
fakes the writes it is about itself. Synthetic throughout.

Import as ``from backend.tests._family_structure_fakes import ...``; it patches the
``backend.app`` module tree.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.schemas import FamilyOut
from backend.app.services import family_structure_service
from backend.app.services.access_control import CurrentUser


def admin_user() -> CurrentUser:
    return CurrentUser(
        id="11111111-1111-1111-1111-111111111111",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def member_row(*, family_uuid: str, sample_uuid: str, sample_id: str, role: str, sex: str) -> dict[str, Any]:
    """A current member's row, unaffected and of unknown carrier status."""
    return {
        "family_uuid": family_uuid,
        "sample_uuid": sample_uuid,
        "sample_id": sample_id,
        "sex": sex,
        "role": role,
        "clinical_status": "unaffected",
        "carrier_status": "unknown",
        "carrier_type": None,
        "carrier_evidence": {},
        "active": True,
    }


def fake_structure_reads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    family_uuid: str,
    family_id: str,
    members: list[dict[str, Any]],
    data_counts: dict[str, int],
) -> None:
    """The family ``family_id`` (``family_uuid``) with ``members`` (``member_row``) and
    ``data_counts`` of genomic data, as the structure change reads it."""
    current = {family_structure_service._sample_key(row["sample_id"]): row for row in members}

    async def mapping(*_args, **_kwargs):
        return {"id": family_uuid, "family_id": family_id}

    async def version(*_args, **_kwargs):
        return 1

    async def current_members(*_args, **_kwargs):
        return {key: dict(row) for key, row in current.items()}

    async def no_relationships(*_args, **_kwargs):
        return []

    async def counts(*_args, **_kwargs):
        return dict(data_counts)

    async def assemblies(*_args, **_kwargs):
        return {"GRCh38": ["project-1"]}

    async def family_record(*_args, **_kwargs):
        return FamilyOut(
            _id=family_uuid,
            family_id=family_id,
            created_at=datetime.now(timezone.utc),
            members=[],
            relationships=[],
            projects=[],
            metadata={},
        )

    for name, value in {
        "get_accessible_family_mapping": mapping,
        "_fetch_current_structure_version": version,
        "_fetch_family_member_rows": current_members,
        "_fetch_current_relationship_rows": no_relationships,
        "_family_genomic_data_counts": counts,
        "_family_assembly_groups": assemblies,
        "_pedigree_text_from_target": lambda **_kwargs: "",
        "get_family_record": family_record,
    }.items():
        monkeypatch.setattr(family_structure_service, name, value)
