"""Who signs in, with Microsoft sign-in configured (SEC-4).

With Azure AD (Microsoft Entra ID) configured, users sign in with their Microsoft account. A
user's first sign-in makes their CoGA account: bound to their object ID in the directory, the
``oid`` of the token (``users.azure_object_id``), by which every later sign-in finds it, never
by an e-mail address. The new account is a viewer's, active, and a member of no project, so it
sees no family, sample or variant until an administrator adds it to a project. Its e-mail
address, user name and name are the token's, and follow the token when they change. It has no
password: ``hashed_password`` holds a marker no password matches.

An existing account is never taken over by an e-mail address: a first sign-in whose address (or
user name) another account already has is refused, and nothing is linked.

Beside Microsoft sign-in there is one local account, the emergency (break-glass)
administrator: the account ``ADMIN_USERNAME`` names, which the deployment seeds
(``db_migrate.init_postgres_admin_user``) and which is bound to no object ID. It signs in with
its password only while ``AZURE_ADMIN_OVERRIDE`` is on, and keeps an administrator's role.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.azure import AzureIdentity
from ..core.config import settings
from .access_control import ADMIN_ROLES, CurrentUser
from .metadata_service import get_current_user_by_email

logger = logging.getLogger(__name__)

# What hashed_password holds for an account that signs in with Microsoft: no bcrypt hash, so
# verify_password matches no password against it (dependencies.verify_password).
MICROSOFT_ACCOUNT_HASH_MARKER = "!microsoft-sign-in"
# The role a first Microsoft sign-in gets; with no project it sees no patient data.
NEW_MICROSOFT_ACCOUNT_ROLE = "viewer"

_EMAIL = TypeAdapter(EmailStr)

NO_EMAIL_DETAIL = (
    "Your Microsoft account has no e-mail address CoGA can use. Ask your IT department to "
    "add the email claim to CoGA's tokens."
)
EMAIL_TAKEN_DETAIL = (
    "Another CoGA account already uses the e-mail address or user name of your Microsoft "
    "account, and it is not linked to it. Ask a CoGA administrator."
)


def is_emergency_admin(account: Mapping[str, Any]) -> bool:
    """Whether a ``users`` row is the emergency administrator: the local account
    ``ADMIN_USERNAME`` names, with an administrator's role."""
    return (
        account.get("username") == settings.admin_username
        and not account.get("azure_object_id")
        and account.get("role") in ADMIN_ROLES
    )


def is_emergency_admin_account(account: Mapping[str, Any]) -> bool:
    """Whether a ``users`` row is the emergency administrator's account, whatever its role."""
    return account.get("username") == settings.admin_username and not account.get("azure_object_id")


def _email_address(value: str | None) -> str | None:
    """The value as an e-mail address CoGA can store (as ``EmailStr`` reads it), or None."""
    if not value:
        return None
    try:
        return str(_EMAIL.validate_python(value))
    except ValidationError:
        return None


def _profile(identity: AzureIdentity) -> dict[str, str] | None:
    """The account fields the token gives: its e-mail address (the ``email`` claim, else the
    ``preferred_username`` when it is one), its user name and its name. None without an
    e-mail address."""
    email = _email_address(identity.email) or _email_address(identity.preferred_username)
    if email is None:
        return None
    profile = {"email": email, "username": identity.preferred_username or email}
    if identity.given_name or identity.family_name:
        profile["first_name"] = identity.given_name or ""
        profile["last_name"] = identity.family_name or ""
    elif identity.name:
        profile["first_name"] = identity.name
        profile["last_name"] = ""
    return profile


async def _user_by_object_id(session: AsyncSession, object_id: str) -> CurrentUser | None:
    found = await session.execute(
        text("SELECT email FROM users WHERE azure_object_id = CAST(:object_id AS uuid)"),
        {"object_id": object_id},
    )
    email = found.scalar_one_or_none()
    if email is None:
        return None
    # The one reader of an account and its projects, as for a password sign-in.
    return await get_current_user_by_email(session, email)


async def _follow_profile(session: AsyncSession, user: CurrentUser, profile: dict[str, str]) -> CurrentUser:
    """The account with the token's e-mail address, user name and name, when they changed.

    A value another account already has is not taken: the account keeps its own.
    """
    current = {
        "email": user.email,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }
    changed = {field: value for field, value in profile.items() if current.get(field) != value}
    if not changed:
        return user
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    UPDATE users
                    SET email = :email,
                        username = :username,
                        first_name = :first_name,
                        last_name = :last_name
                    WHERE id = CAST(:user_id AS uuid)
                    """
                ),
                {**current, **changed, "user_id": user.id},
            )
    except IntegrityError:
        logger.warning(
            "microsoft sign-in: account %s keeps its e-mail address and user name; another "
            "account has the token's",
            user.id,
        )
        return user
    await session.commit()
    return await get_current_user_by_email(session, changed.get("email", user.email)) or user


async def microsoft_account_user(
    session: AsyncSession, identity: AzureIdentity
) -> tuple[CurrentUser, dict[str, Any] | None]:
    """The account a verified Microsoft token signs in, made at the user's first sign-in.

    Returns the user and, when this sign-in made the account, what the request audit records
    of it (``request_meta.account_change``). Raises 403 when the token gives no e-mail address,
    or when another account has its address or user name.
    """
    profile = _profile(identity)
    user = await _user_by_object_id(session, identity.object_id)
    if user is not None:
        if profile is not None:
            user = await _follow_profile(session, user, profile)
        return user, None
    if profile is None:
        raise HTTPException(status_code=403, detail=NO_EMAIL_DETAIL)
    created = await session.execute(
        text(
            """
            INSERT INTO users (
                username, hashed_password, role, email, first_name, last_name, affiliation,
                is_active, metadata, created_at, azure_object_id
            )
            VALUES (
                :username, :hashed_password, :role, :email, :first_name, :last_name, '',
                true, '{}'::jsonb, :created_at, CAST(:object_id AS uuid)
            )
            ON CONFLICT DO NOTHING
            RETURNING id::text
            """
        ),
        {
            "username": profile["username"],
            "hashed_password": MICROSOFT_ACCOUNT_HASH_MARKER,
            "role": NEW_MICROSOFT_ACCOUNT_ROLE,
            "email": profile["email"],
            "first_name": profile.get("first_name", ""),
            "last_name": profile.get("last_name", ""),
            "created_at": datetime.now(timezone.utc),
            "object_id": identity.object_id,
        },
    )
    user_id = created.scalar_one_or_none()
    await session.commit()
    if user_id is None:
        # Another request of the same first sign-in made the account a moment ago, or another
        # account has this e-mail address or user name.
        user = await _user_by_object_id(session, identity.object_id)
        if user is not None:
            return user, None
        logger.warning(
            "microsoft sign-in refused for object id %s: another account has its e-mail "
            "address or user name",
            identity.object_id,
        )
        raise HTTPException(status_code=403, detail=EMAIL_TAKEN_DETAIL)
    user = await _user_by_object_id(session, identity.object_id)
    if user is None:  # pragma: no cover - the row was committed above
        raise HTTPException(status_code=500, detail="Failed to create user")
    logger.info("microsoft sign-in made account %s (role %s, no project)", user.id, user.role)
    return user, {
        "user_id": user.id,
        "email": user.email,
        "created": "microsoft_sign_in",
        "role": {"from": None, "to": user.role},
    }
