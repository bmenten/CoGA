from __future__ import annotations

import json
import time
import traceback
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode

from fastapi import Request, Response

from ..core.coga_logging import CoGALogger
from ..core.config import settings
from ..services.audit_log_pg import (
    AuditLogEventPayload,
    log_model_update,
    write_audit_log_event,
)

logger = CoGALogger(__name__)

_SENSITIVE_PREFIXES = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "access_key",
)
_SENSITIVE_QUERY_KEYS = (
    "family",
    "sample",
    "subject",
    "participant",
    "patient",
    "pedigree",
    "project",
    "token",
    "auth",
)
_MAX_REQUEST_BODY_BYTES = 25_000


def _sanitize_for_logging(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(lowered.startswith(prefix) for prefix in _SENSITIVE_PREFIXES):
                sanitized[key_text] = "***"
            else:
                sanitized[key_text] = _sanitize_for_logging(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_logging(item) for item in value]
    return value


def _sanitize_query_param(key: str, value: str) -> str:
    lowered = key.lower()
    if any(lowered.startswith(prefix) for prefix in _SENSITIVE_PREFIXES):
        return "***"
    if any(token in lowered for token in _SENSITIVE_QUERY_KEYS):
        return "***"
    if len(value) > 128:
        return value[:125] + "..."
    return value


def _query_string_for_logging(request: Request) -> str | None:
    raw_query = request.url.query
    if not raw_query:
        return None

    mode = settings.audit_log_query_string_mode
    if mode == "none":
        return None

    query_items = parse_qsl(raw_query, keep_blank_values=True)
    if not query_items:
        return None

    if mode == "keys":
        keys = sorted({key for key, _value in query_items if key})
        return "&".join(keys) or None

    sanitized_items = [
        (key, _sanitize_query_param(key, value))
        for key, value in query_items
    ]
    return urlencode(sanitized_items) or None


def _request_url_for_logging(request: Request) -> str:
    query_string = _query_string_for_logging(request)
    if not query_string:
        return request.url.path
    return f"{request.url.path}?{query_string}"


def _parse_request_body(request: Request, body_bytes: bytes) -> Any | None:
    if not body_bytes:
        return None

    content_type = request.headers.get("content-type", "")
    if any(
        marker in content_type
        for marker in ("multipart/form-data", "application/octet-stream", "application/pdf")
    ):
        return {"_content_type": content_type, "_bytes": len(body_bytes)}

    if len(body_bytes) > _MAX_REQUEST_BODY_BYTES:
        return {"_truncated": True, "_bytes": len(body_bytes)}

    try:
        parsed = json.loads(body_bytes.decode("utf-8"))
        return _sanitize_for_logging(parsed)
    except Exception:
        return body_bytes.decode("utf-8", errors="replace")


def _should_capture_body(request: Request) -> bool:
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    content_type = request.headers.get("content-type", "")
    if any(
        marker in content_type
        for marker in ("multipart/form-data", "application/octet-stream", "application/pdf")
    ):
        return False
    try:
        content_length = int(request.headers.get("content-length", "0") or "0")
    except ValueError:
        content_length = 0
    if content_length > _MAX_REQUEST_BODY_BYTES * 4:
        return False
    return True


def _get_request_user(request: Request) -> dict[str, str | None] | None:
    user = getattr(request.state, "current_user", None)
    if user is None:
        return None
    return {
        "id": str(getattr(user, "id", "")) or None,
        "email": str(getattr(user, "email", "")) or None,
        "role": str(getattr(user, "role", "")) or None,
    }


def _derive_db_update(
    request: Request,
    request_body: Any | None,
) -> dict[str, Any] | None:
    method = request.method.upper()
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    update_type = {
        "POST": "create",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
    }[method]

    path_parts = [part for part in request.url.path.split("/") if part]
    if not path_parts:
        return None
    entity = path_parts[0]

    path_params = request.path_params or {}
    entity_id = None
    if path_params:
        first_key = next(iter(path_params.keys()))
        entity_id = str(path_params[first_key])

    update_fields: list[str] | None = None
    if isinstance(request_body, dict):
        update_fields = [key for key in request_body.keys() if not str(key).startswith("_")]

    return log_model_update(
        entity=entity,
        entity_id=entity_id,
        update_type=update_type,
        update_fields=update_fields,
    )


async def log_request_response(request: Request, call_next) -> Response:
    start = time.perf_counter()
    request_body = None
    raw_body = b""
    if _should_capture_body(request):
        try:
            raw_body = await request.body()
        except Exception:
            raw_body = b""
        request_body = _parse_request_body(request, raw_body)
    elif request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        request_body = {"_captured": False, "_reason": "stream_or_large_body"}

    response: Response | None = None
    status_code = 500
    error_message: str | None = None
    tb_text: str | None = None
    response_size: int | None = None

    try:
        response = await call_next(request)
        status_code = response.status_code
        response_size = int(response.headers.get("content-length", "0") or 0)
    except Exception as exc:
        error_message = str(exc)
        tb_text = traceback.format_exc()
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        user = _get_request_user(request)
        db_update = _derive_db_update(request, request_body)
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        query_string = _query_string_for_logging(request)

        http_request_json = {
            "requestMethod": request.method,
            "requestUrl": _request_url_for_logging(request),
            "status": status_code,
            "responseSize": response_size,
            "userAgent": request.headers.get("user-agent"),
            "remoteIp": request.client.host if request.client else None,
            "referer": request.headers.get("referer"),
            "protocol": request.scope.get("http_version"),
        }

        log_kwargs: dict[str, Any] = {
            "http_request_json": http_request_json,
            "request_body": request_body,
            "detail": {"durationMs": duration_ms},
        }
        if db_update:
            log_kwargs["db_update"] = db_update
        if error_message:
            log_kwargs["traceback"] = tb_text

        if error_message or status_code >= 500:
            logger.error(error_message or "Unhandled server error", user=user, **log_kwargs)
        elif status_code >= 400:
            logger.warning(error_message or "Request returned warning status", user=user, **log_kwargs)
        else:
            logger.info("", user=user, **log_kwargs)

        request_meta = {
            "headers": {
                "content-type": request.headers.get("content-type"),
                "accept": request.headers.get("accept"),
            }
        }
        try:
            await write_audit_log_event(
                AuditLogEventPayload(
                    user_id=user.get("id") if user else None,
                    user_email=user.get("email") if user else None,
                    user_role=user.get("role") if user else None,
                    method=request.method.upper(),
                    route_path=route_path,
                    path=request.url.path,
                    query_string=query_string,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    remote_ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    referer=request.headers.get("referer"),
                    protocol=request.scope.get("http_version"),
                    request_body=request_body,
                    request_meta=request_meta,
                    db_update=db_update,
                    error=error_message,
                )
            )
        except Exception as exc:
            logger.warning(
                f"Failed to persist audit log: {exc}",
                user=user,
                detail={"path": request.url.path, "method": request.method},
            )

    assert response is not None
    return response
