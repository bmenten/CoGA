"""Small-variant filter presets (#681) — real Postgres.

A small-variant preset is its owner's, reusable in every family they can open; there is no
family-scoped kind any more. Its owner is the account, whatever its user name is now (SEC-32).
This checks, against the real schema, that

* saving a preset under a name its owner already uses replaces its content, in one row;
* the owner's list holds their presets only;
* only the owner may delete a preset;
* the preset stays its owner's when the account's user name changes, as a Microsoft sign-in
  may change it, and the user name it was saved under is still what it shows.

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


async def _account(session, username: str, email: str | None = None):
    """A viewer's account, stored: a preset's owner is an account."""
    from backend.app.services.access_control import CurrentUser

    email = email or f"{username}@example.com"
    user_id = (
        await session.execute(
            text(
                "INSERT INTO users (username, hashed_password, role, email) "
                "VALUES (:u, '', 'viewer', :e) RETURNING id::text"
            ),
            {"u": username, "e": email},
        )
    ).scalar_one()
    await session.commit()
    return CurrentUser(
        id=user_id,
        username=username,
        email=email,
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

    async def _run() -> None:
        try:
            await init_postgres_schema()
            sm = get_postgres_sessionmaker()
            async with sm() as s:
                owner = await _account(s, f"owner-{tag}")
                other = await _account(s, f"other-{tag}")
                try:
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
                            text("SELECT count(*) FROM small_variant_filter_presets WHERE owner_id = CAST(:o AS uuid)"),
                            {"o": owner.id},
                        )
                    ).scalar_one()
                    assert rows == 1

                    listed = await list_small_variant_filter_presets_for_owner(s, user=owner)
                    assert [preset.id for preset in listed] == [first.id]

                    # The owner's user name changes (SEC-32): the preset stays theirs, shown
                    # under the user name it was saved with, and the account that takes the
                    # old user name gets none of it.
                    await s.execute(
                        text("UPDATE users SET username = :u WHERE id = CAST(:id AS uuid)"),
                        {"u": f"renamed-{tag}", "id": owner.id},
                    )
                    await s.commit()
                    renamed = owner.model_copy(update={"username": f"renamed-{tag}"})
                    newcomer = await _account(s, f"owner-{tag}", email=f"newcomer-{tag}@example.com")
                    listed = await list_small_variant_filter_presets_for_owner(s, user=renamed)
                    assert [(preset.id, preset.owner) for preset in listed] == [(first.id, f"owner-{tag}")]
                    assert await list_small_variant_filter_presets_for_owner(s, user=newcomer) == []
                    with pytest.raises(HTTPException) as not_theirs:
                        await delete_small_variant_filter_preset_for_owner(s, preset_id=first.id, user=newcomer)
                    assert not_theirs.value.status_code == 404

                    with pytest.raises(HTTPException) as refused:
                        await delete_small_variant_filter_preset_for_owner(s, preset_id=first.id, user=other)
                    # Another user's preset answers as an unknown one does (SEC-9).
                    assert (refused.value.status_code, refused.value.detail) == (404, "Preset not found")

                    # Saved again, it shows the owner's user name now, still in one row.
                    resaved = await save_small_variant_filter_preset(
                        s, payload=SmallVariantFilterPresetCreate(name="Dominant"), user=renamed
                    )
                    assert (resaved.id, resaved.owner) == (first.id, f"renamed-{tag}")

                    await delete_small_variant_filter_preset_for_owner(s, preset_id=first.id, user=renamed)
                    assert await list_small_variant_filter_presets_for_owner(s, user=renamed) == []
                finally:
                    # The accounts' deletion deletes their presets.
                    await s.rollback()
                    await s.execute(
                        text("DELETE FROM users WHERE username IN (:a, :b, :c)"),
                        {"a": f"owner-{tag}", "b": f"other-{tag}", "c": f"renamed-{tag}"},
                    )
                    await s.commit()
                left = (
                    await s.execute(
                        text("SELECT count(*) FROM small_variant_filter_presets WHERE owner IN (:a, :b)"),
                        {"a": f"owner-{tag}", "b": f"other-{tag}"},
                    )
                ).scalar_one()
                assert left == 0
        finally:
            await close_postgres_engine()

    asyncio.run(_run())
