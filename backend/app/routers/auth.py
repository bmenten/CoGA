import asyncio
import logging
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import cas
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
    AccountRequestMailOut,
    AccountRequestMailTestOut,
    ApiId,
    CasSignInOut,
    CasTicketIn,
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
from ..services.account_notifications import (
    AccountRequest,
    MailNotSent,
    administrator_addresses,
    mail_check_message,
    mail_enabled,
    notify_account_request,
    send_mail,
)
from ..services.metadata_service import (
    create_user_account,
    delete_waiting_user_account,
    end_account_sessions,
    get_auth_user_mapping_by_email,
    get_user_account_mapping,
    list_user_accounts,
    organisation_sign_in_name,
    update_user_account,
)
from ..services.user_accounts import (
    CAS_SIGN_IN,
    is_emergency_admin,
    is_emergency_admin_account,
    microsoft_account_user,
)
from ..services.access_control import CurrentUser, changes_only_own_data
from ..services.auth_rate_limit_pg import (
    clear_login_failures,
    get_cas_throttle_state,
    get_login_throttle_state,
    get_signup_throttle_state,
    record_failed_login,
    record_refused_cas_ticket,
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

# With the organisation's sign-in (Microsoft, or its CAS server; SEC-4), the password sign-in
# serves the emergency administrator alone, and only while AZURE_ADMIN_OVERRIDE is on; accounts
# come from the first sign-in through the organisation.
PASSWORD_SIGN_IN_OFF_DETAIL = (
    "Sign in with your organisation account: the password sign-in is switched off."
)
EMERGENCY_ONLY_DETAIL = (
    "Only the emergency administrator signs in with a password. Sign in with your organisation "
    "account."
)
SIGNUP_OFF_DETAIL = (
    "Accounts are made at the first sign-in with an organisation account: there is no sign-up."
)
# A first CAS sign-in makes an account that waits for an administrator's approval (SEC-4).
CAS_ACCOUNT_WAITING_DETAIL = (
    "Your CoGA account waits for an administrator's approval. You can sign in once it is "
    "activated."
)
# An account that was active once, and was deactivated or archived since, waits for nothing.
CAS_ACCOUNT_INACTIVE_DETAIL = "Your CoGA account is not active. Ask a CoGA administrator."
CAS_TICKET_REFUSED_DETAIL = "The sign-in could not be confirmed. Sign in again."
CAS_SIGN_IN_OFF_DETAIL = "The organisation sign-in through CAS is not configured."
OWN_ROLE_DETAIL = "You cannot change your own role: ask another administrator."
OWN_ACTIVE_DETAIL = "You cannot deactivate your own account: ask another administrator."
OWN_ARCHIVE_DETAIL = "You cannot archive your own account: ask another administrator."
OWN_DELETE_DETAIL = "You cannot delete your own account."
EMERGENCY_ROLE_DETAIL = "The emergency administrator's role cannot be changed."
EMERGENCY_ACTIVE_DETAIL = (
    "The emergency administrator cannot be deactivated: its sign-in is switched on and off "
    "with AZURE_ADMIN_OVERRIDE."
)
EMERGENCY_ARCHIVE_DETAIL = (
    "The emergency administrator cannot be archived or deleted: its sign-in is switched on and "
    "off with AZURE_ADMIN_OVERRIDE."
)
ARCHIVE_ACTIVE_DETAIL = "An archived account is inactive: it cannot be archived and activated at once."
NO_MAIL_SERVER_DETAIL = "No mail server is set: set SMTP_HOST (docs/deployment-vm.md)."
NO_ADMIN_ADDRESS_DETAIL = "There is no active administrator to send the mail to."
# The account fields an administrator changes, in the order the log line names them. The log
# names the account and its changed fields only: who changed them is the request audit row's
# user, and what each changed from and to is in its ``request_meta.account_change``, so no value
# of the request, its token included, reaches the application log.
ACCOUNT_CHANGE_FIELDS = ("role", "is_active", "archived", "project_ids", "reader_project_ids")


def _request_remote_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


async def _authenticate_and_issue_token(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    request: Request,
) -> Token:
    remote_ip = _request_remote_ip(request)
    if settings.organisation_sign_in_enabled and not settings.azure_admin_override:
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
    if settings.organisation_sign_in_enabled and not is_emergency_admin(user):
        # The organisation's sign-in is the way in; the password one is the emergency
        # administrator's.
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
    if settings.organisation_sign_in_enabled:
        logger.warning(
            "azure_admin_override: the emergency administrator %s signed in with its password "
            "beside %s",
            user["id"],
            "Microsoft sign-in" if settings.azure_ad_enabled else "the CAS sign-in",
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
    if settings.organisation_sign_in_enabled:
        # Accounts come from the first sign-in through the organisation (SEC-4); refused before
        # anything is read, so the answer says nothing about accounts either.
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
        # Every active administrator is told, after the answer (account_notifications.py).
        background_tasks.add_task(
            notify_account_request,
            AccountRequest(
                email=created.email,
                first_name=created.first_name or "",
                last_name=created.last_name or "",
                affiliation=created.affiliation or "",
                sign_in="password",
                created_at=created.created_at,
            ),
            await administrator_addresses(session),
        )
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
    if settings.cas_enabled:
        return SignInConfigOut(
            cas=CasSignInOut(
                display_name=settings.cas_display_name,
                login_url=cas.login_url(),
                logout_url=cas.logout_url(),
            )
        )
    if not settings.azure_ad_enabled:
        return SignInConfigOut(microsoft=None)
    return SignInConfigOut(
        microsoft=MicrosoftSignInOut(
            client_id=str(settings.azure_client_id),
            authority=azure_authority(str(settings.azure_tenant_id)),
            api_scope=settings.azure_api_scope_value,
        )
    )


@router.post(
    "/cas/session",
    response_model=Token,
    summary="Sign in with a CAS service ticket",
    description=(
        "Checks the service ticket the organisation's CAS server sent the browser back with, "
        "for CoGA's own service, and starts the session of the account it signs in. A first "
        "sign-in makes an account that waits for an administrator's approval (403 until then)."
    ),
)
async def cas_session(
    ticket_in: CasTicketIn,
    request: Request,
    session: AsyncSession = Depends(get_postgres_session),
) -> Token:
    if not settings.cas_enabled:
        raise HTTPException(status_code=404, detail=CAS_SIGN_IN_OFF_DETAIL)
    remote_ip = _request_remote_ip(request)
    throttle_state = await get_cas_throttle_state(session, remote_ip=remote_ip)
    if throttle_state is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Try again later.",
            headers={"Retry-After": str(throttle_state.retry_after_seconds)},
        )
    try:
        identity = await cas.verify_service_ticket(ticket_in.ticket)
    except cas.CasError as exc:
        # Why, never the ticket or the CAS server's answer.
        logger.warning("cas sign-in: ticket refused: %s", exc)
        await record_refused_cas_ticket(session, remote_ip=remote_ip)
        await session.commit()
        raise HTTPException(status_code=401, detail=CAS_TICKET_REFUSED_DETAIL) from exc
    user, created = await microsoft_account_user(session, identity, CAS_SIGN_IN)
    if created is not None:
        request.state.account_change = created
        # Every active administrator is told now, not as a background task: the answer below
        # is a refusal, after which FastAPI runs no background task.
        account = AccountRequest(
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            affiliation=user.affiliation,
            sign_in="cas",
            created_at=user.created_at,
        )
        await asyncio.to_thread(
            notify_account_request, account, await administrator_addresses(session)
        )
    if not user.is_active:
        waiting = user.activated_at is None
        raise HTTPException(
            status_code=403,
            detail=CAS_ACCOUNT_WAITING_DETAIL if waiting else CAS_ACCOUNT_INACTIVE_DETAIL,
        )
    access_token = create_access_token(data={"sub": user.email})
    return Token(access_token=access_token, token_type="bearer", role=user.role)


@router.get("/me", response_model=UserRead)
async def read_me(user: CurrentUser = Depends(get_current_user)):
    return UserRead(
        id=ApiId(user.id),
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        affiliation=user.affiliation,
        sign_in=organisation_sign_in_name() if user.azure_object_id else "password",
        is_active=user.is_active,
        role=user.role,
        projects=user.metadata_project_ids,
        reader_projects=user.reader_project_ids,
        created_at=user.created_at,
        activated_at=user.activated_at,
        archived_at=user.archived_at,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out: end every session of the signed-in account",
    description=(
        "Every token issued to the account before now, CoGA's own and Microsoft's, is refused "
        "from now on (401), on every device and in every browser, instead of staying valid until "
        "it expires. A later sign-in starts a new session. Microsoft's own sign-in in the browser "
        "stays: the sign-in page offers to end it too."
    ),
)
@changes_only_own_data
async def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_postgres_session),
) -> Response:
    await end_account_sessions(
        session,
        user_id=user.id,
        # The token this logout was made with is refused too (end_account_sessions).
        token_issued_at=getattr(request.state, "token_issued_at", None),
    )
    # The log names no account: who signed out, and when, is this request's audit row.
    logger.info("an account signed out: every token issued to it before now is refused")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    """Activate or deactivate an account, set its global role, archive or restore it (SEC-4),
    or set its projects and its level in each (SEC-1).

    Nobody changes their own role, deactivates or archives their own account, and the emergency
    administrator keeps its role and stays active, so the break-glass sign-in works when it is
    switched on. Archiving deactivates the account; an archived account is restored before it is
    activated. ``project_ids`` and ``reader_project_ids`` set the account's memberships as a
    project's ``user_ids`` and ``reader_user_ids`` set its members: the same rows, from the
    account's side. A project that does not exist, or names no UUID, answers 404 and nothing is
    changed. What changed, each field from and to, is recorded with the request in the audit
    log (``request_meta.account_change``).
    """
    user_uuid = require_uuid(user_id, "Invalid user id")
    project_ids = (
        None
        if update.project_ids is None
        else [require_uuid(value, "Project not found", status_code=404) for value in update.project_ids]
    )
    reader_project_ids = (
        None
        if update.reader_project_ids is None
        else [require_uuid(value, "Project not found", status_code=404) for value in update.reader_project_ids]
    )
    if update.archived is True and update.is_active is True:
        raise HTTPException(status_code=400, detail=ARCHIVE_ACTIVE_DETAIL)
    if update.role is not None or update.is_active is False or update.archived is True:
        target = await get_user_account_mapping(session, user_uuid)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        own = str(target["id"]) == current.id
        if update.role is not None and target["role"] != update.role:
            if own:
                raise HTTPException(status_code=403, detail=OWN_ROLE_DETAIL)
            if is_emergency_admin_account(target):
                raise HTTPException(status_code=403, detail=EMERGENCY_ROLE_DETAIL)
        if update.is_active is False and own:
            # An administrator deactivating their own account locks themselves out (SEC-32).
            raise HTTPException(status_code=403, detail=OWN_ACTIVE_DETAIL)
        if update.is_active is False and target["is_active"] and is_emergency_admin_account(target):
            raise HTTPException(status_code=403, detail=EMERGENCY_ACTIVE_DETAIL)
        if update.archived is True and target.get("archived_at") is None:
            if own:
                raise HTTPException(status_code=403, detail=OWN_ARCHIVE_DETAIL)
            if is_emergency_admin_account(target):
                raise HTTPException(status_code=403, detail=EMERGENCY_ARCHIVE_DETAIL)
    updated, changes = await update_user_account(
        session,
        user_id=user_uuid,
        is_active=update.is_active,
        role=update.role,
        archived=update.archived,
        project_ids=project_ids,
        reader_project_ids=reader_project_ids,
    )
    if changes:
        request.state.account_change = {"user_id": updated.id, "email": updated.email, **changes}
        logger.info(
            "account %s changed: %s (who changed it, and from and to, in the request audit)",
            updated.id,
            ", ".join(field for field in ACCOUNT_CHANGE_FIELDS if field in changes),
        )
    return updated


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    request: Request,
    current: CurrentUser = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_postgres_session),
):
    """Delete an account that was never active: a sign-up or a first CAS sign-in that is not
    approved (SEC-4).

    Any other account is refused (409) and archived instead, so that every record naming it
    still does. The deleted account (its id, e-mail address, name and how it signed in) is
    recorded with the request in the audit log (``request_meta.account_change``). A later
    sign-in of the same person makes a new account, which waits for approval again.
    """
    user_uuid = require_uuid(user_id, "Invalid user id")
    target = await get_user_account_mapping(session, user_uuid)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if str(target["id"]) == current.id:
        raise HTTPException(status_code=403, detail=OWN_DELETE_DETAIL)
    if is_emergency_admin_account(target):
        raise HTTPException(status_code=403, detail=EMERGENCY_ARCHIVE_DETAIL)
    deleted = await delete_waiting_user_account(session, user_id=user_uuid)
    request.state.account_change = deleted
    logger.info(
        "account %s deleted (who deleted it, and the account, in the request audit)",
        deleted["user_id"],
    )


@router.get(
    "/account-request-mail",
    response_model=AccountRequestMailOut,
    summary="Who is told of an account that waits for approval",
    description=(
        "Whether CoGA e-mails the administrators when an account waits for approval (a mail "
        "server is set with SMTP_HOST), and the addresses: every active administrator's."
    ),
)
async def account_request_mail(
    current: CurrentUser = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_postgres_session),
) -> AccountRequestMailOut:
    return AccountRequestMailOut(
        enabled=mail_enabled(),
        recipients=await administrator_addresses(session),
    )


@router.post(
    "/account-request-mail/test",
    response_model=AccountRequestMailTestOut,
    summary="Send a test of the account-request mail",
    description=(
        "Sends a test mail to every active administrator, through the mail server SMTP_HOST "
        "names: 409 without one, 502 when it does not take the mail."
    ),
)
async def send_account_request_test_mail(
    current: CurrentUser = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_postgres_session),
) -> AccountRequestMailTestOut:
    if not mail_enabled():
        raise HTTPException(status_code=409, detail=NO_MAIL_SERVER_DETAIL)
    recipients = await administrator_addresses(session)
    if not recipients:
        raise HTTPException(status_code=409, detail=NO_ADMIN_ADDRESS_DETAIL)
    try:
        await asyncio.to_thread(send_mail, mail_check_message(recipients))
    except MailNotSent as exc:
        logger.warning("the test of the account-request mail was not sent: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info("the test of the account-request mail went to %d administrator(s)", len(recipients))
    return AccountRequestMailTestOut(recipients=recipients)
