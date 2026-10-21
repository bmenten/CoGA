import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .core.azure import AzureTokenError, verify_azure_token
from .core.config import LOCAL_TOKEN_ALGORITHM, settings
from .core.postgres import get_postgres_session
from .services.access_control import ADMIN_ROLES, CurrentUser, request_changes_records
from .services.family_identifiers import request_value_problem
from .services.metadata_service import get_auth_user_mapping_by_email, get_current_user_by_email
from .services.user_accounts import is_emergency_admin, microsoft_account_user

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

# bcrypt reads at most the first 72 bytes of a password. passlib, which made the stored
# hashes, passed longer passwords through and bcrypt truncated them silently; bcrypt 5
# refuses them instead. Truncating here keeps every stored hash verifiable and makes new
# hashes the same way (#525).
_BCRYPT_MAX_PASSWORD_BYTES = 72


def _bcrypt_secret(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_PASSWORD_BYTES]


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_secret(password), bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_secret(plain_password), hashed_password.encode("ascii"))
    except (AttributeError, UnicodeEncodeError, ValueError):
        # Not a bcrypt hash, e.g. an account without a local password: no match.
        return False


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=LOCAL_TOKEN_ALGORITHM)


# A refused bearer token, the same answer whatever the reason (no account, a bad signature, an
# expired token): it tells a caller nothing about which accounts exist.
_CREDENTIALS_DETAIL = "Could not validate credentials"
# An account of a verified Microsoft sign-in that an administrator has deactivated.
INACTIVE_MICROSOFT_ACCOUNT_DETAIL = (
    "Your CoGA account is not active. Ask a CoGA administrator to activate it."
)


def _refused(detail: str = _CREDENTIALS_DETAIL) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _local_token_email(token: str) -> str | None:
    """The account a token this server issued names (``sub``): HS256, signed with SECRET_KEY,
    unexpired. Raises the 401 for any other token."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[LOCAL_TOKEN_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise _refused() from exc
    email = payload.get("sub")
    return email if isinstance(email, str) and email else None


async def _microsoft_user(token: str, session: AsyncSession, request: Request | None) -> CurrentUser:
    """The account a Microsoft access token signs in, made at its first sign-in (SEC-4)."""
    try:
        identity = await verify_azure_token(
            token,
            tenant_id=str(settings.azure_tenant_id),
            client_id=str(settings.azure_client_id),
            scope_name=settings.azure_api_scope_name,
        )
    except AzureTokenError as exc:
        if str(exc) != "expired":
            # Why, never the token: a refusal other than expiry usually means the app
            # registration and CoGA's settings disagree (docs/deployment-gcp.md).
            logger.warning("microsoft sign-in: token refused: %s", exc)
        raise _refused() from exc
    user, created = await microsoft_account_user(session, identity)
    if created is not None and request is not None:
        request.state.account_change = created
    if not user.is_active:
        raise _refused(INACTIVE_MICROSOFT_ACCOUNT_DETAIL)
    return user


async def _emergency_admin(token: str, session: AsyncSession, request: Request | None) -> CurrentUser:
    """With Microsoft sign-in, a token this server issued signs in the emergency administrator
    only, and only while AZURE_ADMIN_OVERRIDE is on; each use is logged and flagged in the
    request audit (SEC-4)."""
    if not settings.azure_admin_override:
        raise _refused()
    email = _local_token_email(token)
    account = await get_auth_user_mapping_by_email(session, email) if email else None
    if account is None or not account["is_active"] or not is_emergency_admin(account):
        raise _refused()
    user = await get_current_user_by_email(session, account["email"])
    if user is None:
        raise _refused()
    logger.warning(
        "azure_admin_override: the emergency administrator %s (role=%s) used a password "
        "sign-in beside Microsoft sign-in; switch AZURE_ADMIN_OVERRIDE off when the emergency "
        "is over",
        user.id,
        user.role,
    )
    if request is not None:
        request.state.emergency_sign_in = True
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_postgres_session),
    request: Request = None,
) -> CurrentUser:
    if settings.azure_ad_enabled:
        # A Microsoft access token is RS256; a token this server issued is HS256. The header
        # only picks the check: each check takes its own algorithm alone.
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
        except jwt.PyJWTError as exc:
            raise _refused() from exc
        if algorithm == "RS256":
            user = await _microsoft_user(token, session, request)
        elif algorithm == LOCAL_TOKEN_ALGORITHM:
            user = await _emergency_admin(token, session, request)
        else:
            raise _refused()
    else:
        email = _local_token_email(token)
        found = await get_current_user_by_email(session, email) if email else None
        if found is None or not found.is_active:
            raise _refused()
        user = found
    request.state.current_user = user
    # Whether this request changes something a reader may not change (SEC-1). The family,
    # sample and project checkpoints read it, so every route that names a record is held to
    # the user's access level there: a route can only be let off by being marked
    # changes_only_own_data (access_control).
    user.changes_records = request_changes_records(request.method, request.scope.get("endpoint"))
    # Every route that looks a request's path or query value up signs its caller in here
    # (the QC-report download checks its own signed link first), so this is where those
    # values are held to the control-character rule (family_identifiers): after the 401, so
    # the refusal's audit row names the caller, and before any route reads a value, with the
    # same answer whoever asks, so it says nothing about which families exist or who may
    # see them.
    problem = request_value_problem(request.path_params, request.query_params.multi_items())
    if problem is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
    return user


async def get_current_admin_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
