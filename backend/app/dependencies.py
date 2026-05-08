from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from .core.azure import verify_azure_token
from .core.config import settings
from .core.postgres import get_postgres_session
from .services.metadata_service import CurrentUser, get_current_user_by_email

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


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
        except Exception:
            if settings.azure_admin_override:
                try:
                    payload = jwt.decode(
                        token, settings.secret_key, algorithms=[settings.algorithm]
                    )
                    email = payload.get("sub")
                    local_override = True
                except JWTError as exc:
                    raise credentials_exception from exc
            else:
                raise credentials_exception
    else:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            email = payload.get("sub")
        except JWTError as exc:
            raise credentials_exception from exc
    if email is None:
        raise credentials_exception
    user = await get_current_user_by_email(session, email)
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise credentials_exception
    if local_override and user.role != "admin":
        raise credentials_exception
    request.state.current_user = user
    return user


async def get_current_admin_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
