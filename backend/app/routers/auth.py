import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import List
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.postgres import get_postgres_session
from ..dependencies import (
    create_access_token,
    get_password_hash,
    verify_password,
    get_current_user,
    get_current_admin_user,
)
from ..schemas import (
    SignupAck,
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
from ..services.auth_rate_limit_pg import (
    clear_login_failures,
    get_login_throttle_state,
    get_signup_throttle_state,
    record_failed_login,
    record_signup_attempt,
)
from ..services.small_variant_review_pg import (
    delete_small_variant_filter_preset_for_owner,
    list_small_variant_filter_presets_for_owner,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# A constant bcrypt hash used to equalize the cost of the login path when the
# submitted email does not exist. Without it, only real accounts pay the bcrypt
# verify cost, leaking account existence through a response-time side-channel.
# Computed once at import so it matches the configured bcrypt work factor.
_DUMMY_LOGIN_PASSWORD_HASH = get_password_hash("coga-login-timing-equalizer")


def _request_remote_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


def notify_admin(email: str) -> None:
    # Only when ADMIN_EMAIL is configured: its default is the seed admin's placeholder.
    admin_email = settings.admin_email.strip() if "admin_email" in settings.model_fields_set else ""
    if not admin_email:
        logging.info("ADMIN_EMAIL not set; skipping notification for %s", email)
        return
    msg = EmailMessage()
    msg["Subject"] = "New user signup"
    msg["From"] = admin_email
    msg["To"] = admin_email
    msg.set_content(f"A new user has signed up with email: {email}")
    try:
        with smtplib.SMTP(settings.smtp_host) as server:
            server.send_message(msg)
    except Exception as exc:
        logging.error("Failed to send signup notification: %s", exc)


async def _authenticate_and_issue_token(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    remote_ip: str | None,
) -> Token:
    throttle_state = await get_login_throttle_state(
        session,
        email=email,
        remote_ip=remote_ip,
    )
    if throttle_state is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(throttle_state.retry_after_seconds)},
        )

    user = await get_auth_user_mapping_by_email(session, email)
    if user is None:
        # Run a throwaway verify so the no-such-account path costs roughly the
        # same as a wrong-password path; otherwise the response time reveals
        # which emails are registered (account enumeration). bcrypt is slow on purpose,
        # so every hash and check runs in a worker thread, not on the event loop (#527).
        await asyncio.to_thread(verify_password, password, _DUMMY_LOGIN_PASSWORD_HASH)
        await record_failed_login(
            session,
            email=email,
            remote_ip=remote_ip,
        )
        await session.commit()
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not await asyncio.to_thread(verify_password, password, user["hashed_password"]):
        await record_failed_login(
            session,
            email=email,
            remote_ip=remote_ip,
        )
        await session.commit()
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not user["is_active"]:
        await record_failed_login(
            session,
            email=email,
            remote_ip=remote_ip,
        )
        await session.commit()
        raise HTTPException(status_code=403, detail="User not active")
    await clear_login_failures(
        session,
        email=email,
        remote_ip=remote_ip,
    )
    await session.commit()
    access_token = create_access_token(data={"sub": user["email"]})
    return Token(access_token=access_token, token_type="bearer", role=user["role"])


@router.post(
    "/signup",
    response_model=SignupAck,
    status_code=status.HTTP_202_ACCEPTED,
)
async def signup(
    user_in: UserCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    session: AsyncSession = Depends(get_postgres_session),
):
    # Signup is unauthenticated; throttle per source IP so it cannot be used to
    # enumerate accounts, flood the admin-notification mailbox, or burn CPU on
    # password hashing. Every attempt (success or duplicate-email) counts toward
    # the IP's rate, and the bcrypt hash + create only run once past the gate.
    remote_ip = _request_remote_ip(request)
    throttle_state = await get_signup_throttle_state(session, remote_ip=remote_ip)
    if throttle_state is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many signup attempts. Try again later.",
            headers={"Retry-After": str(throttle_state.retry_after_seconds)},
        )
    await record_signup_attempt(session, remote_ip=remote_ip)
    await session.commit()

    # Always hash the password and return an identical acknowledgement whether or
    # not the email already exists, so the endpoint cannot be used to enumerate
    # accounts. `create_user_account` returns None (instead of raising) for a
    # duplicate email; we simply skip the insert + admin notification in that case.
    created = await create_user_account(
        session,
        email=user_in.email,
        hashed_password=await asyncio.to_thread(get_password_hash, user_in.password),
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        affiliation=user_in.affiliation,
    )
    if created is not None:
        background_tasks.add_task(notify_admin, created.email)
    return SignupAck(
        detail=(
            "Registration received. An administrator will review the request and "
            "activate the account if approved."
        )
    )


@router.post("/login", response_model=Token)
async def login(
    credentials: UserLogin,
    request: Request,
    session: AsyncSession = Depends(get_postgres_session),
):
    return await _authenticate_and_issue_token(
        session,
        email=credentials.email,
        password=credentials.password,
        remote_ip=_request_remote_ip(request),
    )

@router.post(
    "/token",
    response_model=Token,
    summary="Swagger-compatible OAuth2 password token",
    description="Use the user's email address in the username field.",
)
async def token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_postgres_session),
):
    return await _authenticate_and_issue_token(
        session,
        email=form_data.username,
        password=form_data.password,
        remote_ip=_request_remote_ip(request),
    )


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
