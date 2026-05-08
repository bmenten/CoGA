import os
import logging
import smtplib
from email.message import EmailMessage
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import (
    create_access_token,
    get_password_hash,
    verify_password,
    get_current_user,
    get_current_admin_user,
)
from ..schemas import (
    SmallVariantFilterPresetOut,
    Token,
    UserCreate,
    UserRead,
    UserLogin,
    UserUpdate,
)
from ..services.metadata_service import (
    CurrentUser,
    create_user_account,
    get_auth_user_mapping_by_email,
    list_user_accounts,
    update_user_account,
)
from ..services.small_variant_review_pg import (
    delete_small_variant_filter_preset_for_owner,
    list_small_variant_filter_presets_for_owner,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def notify_admin(email: str) -> None:
    admin_email = os.getenv("ADMIN_EMAIL")
    if not admin_email:
        logging.info("ADMIN_EMAIL not set; skipping notification for %s", email)
        return
    msg = EmailMessage()
    msg["Subject"] = "New user signup"
    msg["From"] = admin_email
    msg["To"] = admin_email
    msg.set_content(f"A new user has signed up with email: {email}")
    try:
        with smtplib.SMTP(os.getenv("SMTP_HOST", "localhost")) as server:
            server.send_message(msg)
    except Exception as exc:
        logging.error("Failed to send signup notification: %s", exc)


@router.post("/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def signup(
    user_in: UserCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_postgres_session),
):
    created = await create_user_account(
        session,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        affiliation=user_in.affiliation,
    )
    background_tasks.add_task(notify_admin, created.email)
    return created


@router.post("/login", response_model=Token)
async def login(
    credentials: UserLogin,
    session: AsyncSession = Depends(get_postgres_session),
):
    user = await get_auth_user_mapping_by_email(session, credentials.email)
    if user is None:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not verify_password(credentials.password, user["hashed_password"]):

        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="User not active")
    access_token = create_access_token(data={"sub": user["email"]})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user["role"],
    }


@router.get("/me", response_model=UserRead)
async def read_me(user: CurrentUser = Depends(get_current_user)):
    return UserRead(
        id=user.id,
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        affiliation=user.affiliation,
        is_active=user.is_active,
        role=user.role,
        projects=user.metadata_project_ids,
        created_at=user.created_at,
    )


@router.get("/small-variant-filter-presets", response_model=List[SmallVariantFilterPresetOut])
async def list_my_small_variant_filter_presets(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_postgres_session),
):
    return await list_small_variant_filter_presets_for_owner(
        session=session,
        user=user,
    )


@router.delete("/small-variant-filter-presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_small_variant_filter_preset(
    preset_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_postgres_session),
):
    await delete_small_variant_filter_preset_for_owner(
        preset_id=preset_id,
        session=session,
        user=user,
    )


@router.get("/users", response_model=List[UserRead])
async def list_users(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_postgres_session),
):
    if current.role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    return await list_user_accounts(session)


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: str,
    update: UserUpdate,
    current: CurrentUser = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_postgres_session),
):
    try:
        UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user id") from exc
    if update.projects is not None:
        raise HTTPException(
            status_code=400,
            detail="Project access is managed from project settings",
        )
    return await update_user_account(
        session,
        user_id=user_id,
        is_active=update.is_active,
    )
