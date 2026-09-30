"""Small-variant filter presets (#681) — real Postgres.

A small-variant preset is its owner's, reusable in every family they can open; there is no
family-scoped kind any more. This checks, against the real schema, that

* saving a preset under a name its owner already uses replaces its content, in one row;
* the owner's list holds their presets only;
* only the owner may delete a preset.

Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _user(username: str):
    from backend.app.services.access_control import CurrentUser

    return CurrentUser(
        id=str(uuid4()),
        username=username,
        email=f"{username}@example.com",
        role="viewer",
        created_at=datetime.now(timezone.utc),
    )


def test_a_preset_is_saved_listed_replaced_and_deleted_by_its_owner() -> None:
    from backend.app.core.postgres import (
        close_postgres_engine,
        get_postgres_sessionmaker,
        init_postgres_schema,
    )
    from backend.app.schemas import SmallVariantFilterPresetCreate
    from backend.app.services.small_variant_review_presets import (
        delete_small_variant_filter_preset_for_owner,
        list_small_variant_filter_presets_for_owner,
        save_small_variant_filter_preset,
    )

    tag = uuid4().hex[:8]
    owner, other = _user(f"owner-{tag}"), _user(f"other-{tag}")

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                first = await save_small_variant_filter_preset(
                    s,
                    payload=SmallVariantFilterPresetCreate(name=" Dominant ", filters={"impact": ["HIGH"]}),
                    user=owner,
                )
                again = await save_small_variant_filter_preset(
                    s,
                    payload=SmallVariantFilterPresetCreate(
                        name="Dominant", description="rare only", filters={"max_gnomad_af": 0.001}
                    ),
                    user=owner,
                )
                await save_small_variant_filter_preset(
                    s, payload=SmallVariantFilterPresetCreate(name="Dominant"), user=other
                )

                # Same owner and name: one row, its content replaced.
                assert again.id == first.id
                assert again.name == "Dominant"
                assert again.description == "rare only"
                assert again.filters == {"max_gnomad_af": 0.001}
                rows = (
                    await s.execute(
                        text("SELECT count(*) FROM small_variant_filter_presets WHERE owner = :o"),
                        {"o": owner.username},
                    )
                ).scalar_one()
                assert rows == 1

                listed = await list_small_variant_filter_presets_for_owner(s, user=owner)
                assert [preset.id for preset in listed] == [first.id]

                with pytest.raises(HTTPException) as refused:
                    await delete_small_variant_filter_preset_for_owner(s, preset_id=first.id, user=other)
                assert refused.value.status_code == 403

                await delete_small_variant_filter_preset_for_owner(s, preset_id=first.id, user=owner)
                assert await list_small_variant_filter_presets_for_owner(s, user=owner) == []
                await s.execute(
                    text("DELETE FROM small_variant_filter_presets WHERE owner = :o"),
                    {"o": other.username},
                )
                await s.commit()
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
