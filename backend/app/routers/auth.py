import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.azure import authority as azure_authority
from ..core.config import settings
from ..core.postgres import get_postgres_session
from ..core.sql import require_uuid
from ..dependencies import (
    create_access_token,
    get_password_hash,
    verify_password,
    get_current_user,
    get_current_admin_user,
)
from ..schemas import (
    MicrosoftSignInOut,
    SignInConfigOut,
    SignupAck,
    SmallVariantFilterPresetOut,
    Token,
    UserCreate,
    UserRead,
    UserLogin,
    UserUpdate,
)
from ..services.metadata_service import (
    create_user_account,
    get_auth_user_mapping_by_email,
    get_user_account_mapping,
    list_user_accounts,
    update_user_account,
)
from ..services.user_accounts import is_emergency_admin, is_emergency_admin_account
from ..services.access_control import CurrentUser, changes_only_own_data
from ..services.auth_rate_limit_pg import (
    clear_login_failures,
    get_login_throttle_state,
    get_signup_throttle_state,
    record_failed_login,
    record_signup_attempt,
)
from ..services.small_variant_review_presets import (
    delete_small_variant_filter_preset_for_owner,
    list_small_variant_filter_presets_for_owner,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# A constant bcrypt hash used to equalize the cost of the login path when the
# submitted email does not exist. Without it, only real accounts pay the bcrypt
# verify cost, leaking account existence through a response-time side-channel.
# Computed once at import so it matches the configured bcrypt work factor.
_DUMMY_LOGIN_PASSWORD_HASH = get_password_hash("coga-login-timing-equalizer")

# With Microsoft sign-in (SEC-4), the password sign-in serves the emergency administrator alone,
# and only while AZURE_ADMIN_OVERRIDE is on; accounts come from the first Microsoft sign-in.
PASSWORD_SIGN_IN_OFF_DETAIL = (
    "Sign in with your Microsoft account: the password sign-in is switched off."
)
EMERGENCY_ONLY_DETAIL = (
    "Only the emergency administrator signs in with a password. Sign in with your Microsoft "
    "account."
)
SIGNUP_OFF_DETAIL = (
    "Accounts are made at the first sign-in with a Microsoft account: there is no sign-up."
)
OWN_ROLE_DETAIL = "You cannot change your own role: ask another administrator."
EMERGENCY_ROLE_DETAIL = "The emergency administrator's role cannot be changed."
EMERGENCY_ACTIVE_DETAIL = (
    "The emergency administrator cannot be deactivated: its sign-in is switched on and off "
    "with AZURE_ADMIN_OVERRIDE."
)
# The account fields an administrator changes, in the order the log line names them. The log
# names a changed field only: what it changed from and to is in the request audit
# (``request_meta.account_change``), so no value of the request reaches the application log.
ACCOUNT_CHANGE_FIELDS = ("role", "is_active")


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
    except Exception as exc:  # noqa: BLE001 - the notification is best-effort; the sign-up stands
        logging.error("Failed to send signup notification: %s", exc)


async def _authenticate_and_issue_token(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    request: Request,
) -> Token:
    remote_ip = _request_remote_ip(request)
    if settings.azure_ad_enabled and not settings.azure_admin_override:
        # Refused before any account is looked up: the answer says nothing about accounts.
        raise HTTPException(status_code=403, detail=PASSWORD_SIGN_IN_OFF_DETAIL)
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
    if settings.azure_ad_enabled and not is_emergency_admin(user):
        # Microsoft sign-in is the way in; the password one is the emergency administrator's.
        await record_failed_login(
            session,
            email=email,
            remote_ip=remote_ip,
        )
        await session.commit()
        raise HTTPException(status_code=403, detail=EMERGENCY_ONLY_DETAIL)
    await clear_login_failures(
        session,
        email=email,
        remote_ip=remote_ip,
    )
    await session.commit()
    if settings.azure_ad_enabled:
        logger.warning(
            "azure_admin_override: the emergency administrator %s signed in with its password "
            "beside Microsoft sign-in",
            user["id"],
        )
        request.state.emergency_sign_in = True
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
    if settings.azure_ad_enabled:
        # Accounts come from the first Microsoft sign-in (SEC-4); refused before anything is
        # read, so the answer says nothing about accounts either.
        raise HTTPException(status_code=403, detail=SIGNUP_OFF_DETAIL)
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
        request=request,
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
        request=request,
    )


@router.get(
    "/config",
    response_model=SignInConfigOut,
    summary="How users sign in",
    description=(
        "With Microsoft sign-in configured, what the browser needs for it: the application "
        "(client) ID, the directory's authority and CoGA's API scope. None of it is secret."
    ),
)
async def sign_in_config() -> SignInConfigOut:
    if not settings.azure_ad_enabled:
        return SignInConfigOut(microsoft=None)
    return SignInConfigOut(
        microsoft=MicrosoftSignInOut(
            client_id=str(settings.azure_client_id),
            authority=azure_authority(str(settings.azure_tenant_id)),
            api_scope=settings.azure_api_scope_value,
        )
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
        sign_in="microsoft" if user.azure_object_id else "password",
        is_active=user.is_active,
        role=user.role,
        projects=user.metadata_project_ids,
        reader_projects=user.reader_project_ids,
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
@changes_only_own_data
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
    current: CurrentUser = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_postgres_session),
):
    return await list_user_accounts(session)


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: str,
    update: UserUpdate,
    request: Request,
    current: CurrentUser = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_postgres_session),
):
    """Activate or deactivate an account, or set its global role (SEC-4).

    Nobody changes their own role, and the emergency administrator keeps its role and stays
    active, so the break-glass sign-in works when it is switched on. What changed, each field
    from and to, is recorded with the request in the audit log (``request_meta.account_change``).
    """
    user_uuid = require_uuid(user_id, "Invalid user id")
    if update.projects is not None:
        raise HTTPException(
            status_code=400,
            detail="Project access is managed from project settings",
        )
    if update.role is not None or update.is_active is False:
        target = await get_user_account_mapping(session, user_uuid)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        if update.role is not None and target["role"] != update.role:
            if str(target["id"]) == current.id:
                raise HTTPException(status_code=403, detail=OWN_ROLE_DETAIL)
            if is_emergency_admin_account(target):
                raise HTTPException(status_code=403, detail=EMERGENCY_ROLE_DETAIL)
        if update.is_active is False and target["is_active"] and is_emergency_admin_account(target):
            raise HTTPException(status_code=403, detail=EMERGENCY_ACTIVE_DETAIL)
    updated, changes = await update_user_account(
        session,
        user_id=user_uuid,
        is_active=update.is_active,
        role=update.role,
    )
    if changes:
        request.state.account_change = {"user_id": updated.id, "email": updated.email, **changes}
        logger.info(
            "account %s changed by %s: %s (from and to in the request audit)",
            updated.id,
            current.id,
            ", ".join(field for field in ACCOUNT_CHANGE_FIELDS if field in changes),
        )
    return updated
