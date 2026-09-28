import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .core.azure import verify_azure_token
from .core.config import settings
from .core.postgres import get_postgres_session
from .services.access_control import ADMIN_ROLES, CurrentUser
from .services.metadata_service import get_current_user_by_email

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


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_postgres_session),
    request: Request = None,
) -> CurrentUser:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    email = None
    local_override = False
    if settings.azure_client_id and settings.azure_tenant_id:
        try:
            payload = verify_azure_token(
                token, settings.azure_tenant_id, settings.azure_client_id
            )
            email = payload.get("preferred_username") or payload.get("email")
        except Exception as azure_exc:
            if settings.azure_admin_override:
                try:
                    payload = jwt.decode(
                        token, settings.secret_key, algorithms=[settings.algorithm]
                    )
                    email = payload.get("sub")
                    local_override = True
                except jwt.PyJWTError as exc:
                    raise credentials_exception from exc
            else:
                raise credentials_exception from azure_exc
    else:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            email = payload.get("sub")
        except jwt.PyJWTError as exc:
            raise credentials_exception from exc
    if email is None:
        raise credentials_exception
    user = await get_current_user_by_email(session, email)
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise credentials_exception
    if local_override and user.role not in ADMIN_ROLES:
        raise credentials_exception
    if local_override:
        # The Azure-SSO break-glass path accepted a locally-issued HS256 token for
        # an admin. This is gated (AZURE_ADMIN_OVERRIDE, default off) and admin-only,
        # but every use must leave an audit trail so it can be caught if enabled in
        # production by mistake.
        logger.warning(
            "azure_admin_override: accepted a locally-issued HS256 token for admin "
            "user %s (role=%s); AZURE_ADMIN_OVERRIDE should be disabled in production",
            user.email,
            user.role,
        )
    request.state.current_user = user
    return user


async def get_current_admin_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
