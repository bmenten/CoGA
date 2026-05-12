from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings


@dataclass(slots=True)
class LoginThrottleState:
    retry_after_seconds: int
    blocked_until: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_remote_ip(remote_ip: str | None) -> str | None:
    if remote_ip is None:
        return None
    value = remote_ip.strip()
    return value or None


def _scope_rows(email: str, remote_ip: str | None) -> list[tuple[str, str]]:
    rows = [("email", _normalize_email(email))]
    normalized_ip = _normalize_remote_ip(remote_ip)
    if normalized_ip:
        rows.append(("remote_ip", normalized_ip))
    return rows


def _backoff_seconds(failure_count: int) -> int:
    threshold = settings.login_rate_limit_threshold
    if failure_count < threshold:
        return 0
    exponent = failure_count - threshold
    seconds = settings.login_rate_limit_base_backoff_seconds * (2**exponent)
    return min(seconds, settings.login_rate_limit_max_backoff_seconds)


def _next_failure_count(last_failure_at: datetime | None, failure_count: int, now: datetime) -> int:
    if last_failure_at is None:
        return 1
    age_seconds = (now - last_failure_at).total_seconds()
    if age_seconds > settings.login_rate_limit_window_seconds:
        return 1
    return max(int(failure_count), 0) + 1


async def get_login_throttle_state(
    session: AsyncSession,
    *,
    email: str,
    remote_ip: str | None,
    now: datetime | None = None,
) -> LoginThrottleState | None:
    current_time = now or _now()
    scopes = _scope_rows(email, remote_ip)
    if not scopes:
        return None

    blocked_until: datetime | None = None
    for scope_type, scope_value in scopes:
        result = await session.execute(
            text(
                """
                SELECT locked_until
                FROM auth_login_attempts
                WHERE scope_type = :scope_type
                  AND scope_value = :scope_value
                """
            ),
            {"scope_type": scope_type, "scope_value": scope_value},
        )
        locked_until = result.scalar_one_or_none()
        if locked_until is None or locked_until <= current_time:
            continue
        if blocked_until is None or locked_until > blocked_until:
            blocked_until = locked_until

    if blocked_until is None:
        return None
    retry_after_seconds = max(int((blocked_until - current_time).total_seconds()), 1)
    return LoginThrottleState(
        retry_after_seconds=retry_after_seconds,
        blocked_until=blocked_until,
    )


async def record_failed_login(
    session: AsyncSession,
    *,
    email: str,
    remote_ip: str | None,
    now: datetime | None = None,
) -> LoginThrottleState | None:
    current_time = now or _now()
    scopes = _scope_rows(email, remote_ip)
    blocked_until: datetime | None = None

    for scope_type, scope_value in scopes:
        result = await session.execute(
            text(
                """
                SELECT failure_count, last_failure_at
                FROM auth_login_attempts
                WHERE scope_type = :scope_type
                  AND scope_value = :scope_value
                """
            ),
            {"scope_type": scope_type, "scope_value": scope_value},
        )
        row = result.mappings().first()
        failure_count = _next_failure_count(
            row.get("last_failure_at") if row is not None else None,
            int(row.get("failure_count") or 0) if row is not None else 0,
            current_time,
        )
        backoff_seconds = _backoff_seconds(failure_count)
        scope_blocked_until = (
            current_time + timedelta(seconds=backoff_seconds)
            if backoff_seconds > 0
            else None
        )
        await session.execute(
            text(
                """
                INSERT INTO auth_login_attempts (
                    scope_type,
                    scope_value,
                    failure_count,
                    last_failure_at,
                    locked_until,
                    updated_at
                )
                VALUES (
                    :scope_type,
                    :scope_value,
                    :failure_count,
                    :last_failure_at,
                    :locked_until,
                    :updated_at
                )
                ON CONFLICT (scope_type, scope_value)
                DO UPDATE SET
                    failure_count = EXCLUDED.failure_count,
                    last_failure_at = EXCLUDED.last_failure_at,
                    locked_until = EXCLUDED.locked_until,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "scope_type": scope_type,
                "scope_value": scope_value,
                "failure_count": failure_count,
                "last_failure_at": current_time,
                "locked_until": scope_blocked_until,
                "updated_at": current_time,
            },
        )
        if scope_blocked_until is not None and (
            blocked_until is None or scope_blocked_until > blocked_until
        ):
            blocked_until = scope_blocked_until

    if blocked_until is None:
        return None
    retry_after_seconds = max(int((blocked_until - current_time).total_seconds()), 1)
    return LoginThrottleState(
        retry_after_seconds=retry_after_seconds,
        blocked_until=blocked_until,
    )


async def clear_login_failures(
    session: AsyncSession,
    *,
    email: str,
    remote_ip: str | None,
) -> None:
    scopes = _scope_rows(email, remote_ip)
    for scope_type, scope_value in scopes:
        await session.execute(
            text(
                """
                DELETE FROM auth_login_attempts
                WHERE scope_type = :scope_type
                  AND scope_value = :scope_value
                """
            ),
            {"scope_type": scope_type, "scope_value": scope_value},
        )
