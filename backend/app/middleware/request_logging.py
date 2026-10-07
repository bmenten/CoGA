from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode

from fastapi import Request, Response

from ..core.coga_logging import CoGALogger, describe_error, describe_traceback
from ..core.config import API_PATH_PREFIX, settings
from ..services.audit_log_pg import (
    AuditLogEventPayload,
    log_model_update,
    write_audit_log_event,
)
from ..services.operational_metrics import record_request

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


def _request_url_for_logging(request: Request, query_string: str | None = None) -> str:
    # The middleware threads in the already-sanitized query_string so it is not
    # parsed/sorted a second time on the hot path; standalone callers compute it.
    if query_string is None:
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

    # Form-encoded bodies (e.g. the OAuth2 password flow at ``/api/auth/token``) never
    # parse as JSON. Without explicit handling they fall through to the raw decode below
    # and persist ``username=...&password=<PLAINTEXT>`` verbatim to the audit DB. Parse
    # them and run through the same sensitive-key mask applied to JSON bodies.
    if "application/x-www-form-urlencoded" in content_type:
        try:
            form_items = parse_qsl(body_bytes.decode("utf-8"), keep_blank_values=True)
        except Exception:  # noqa: BLE001 - an unparseable form body is recorded as a placeholder
            return {"_captured": False, "_reason": "unparsed_body", "_bytes": len(body_bytes)}
        return _sanitize_for_logging({key: value for key, value in form_items})

    try:
        parsed = json.loads(body_bytes.decode("utf-8"))
    except Exception:  # noqa: BLE001 - fail closed: record a placeholder, never the raw bytes
        # Unknown/unparseable body: do not persist raw bytes, which may carry
        # credentials or other secrets we cannot key-mask. Record a placeholder
        # instead so the audit trail stays useful without leaking (fail closed).
        return {
            "_captured": False,
            "_reason": "unparsed_body",
            "_content_type": content_type or None,
            "_bytes": len(body_bytes),
        }
    return _sanitize_for_logging(parsed)


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
    # Every router is mounted under the API prefix (e.g. ``/api/families/...``), so the
    # first raw segment is the mount ("api"), not the entity. Drop it so the audit trail
    # records the real collection ("families") instead of "api" for every mutation.
    prefix_parts = [part for part in API_PATH_PREFIX.split("/") if part]
    if path_parts[: len(prefix_parts)] == prefix_parts:
        path_parts = path_parts[len(prefix_parts) :]
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
        except Exception:  # noqa: BLE001 - a body that cannot be read is recorded as empty
            raw_body = b""
        request_body = _parse_request_body(request, raw_body)
    elif request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        request_body = {"_captured": False, "_reason": "stream_or_large_body"}

    response: Response | None = None
    status_code = 500
    failure: Exception | None = None
    response_size: int | None = None

    try:
        response = await call_next(request)
        status_code = response.status_code
        response_size = int(response.headers.get("content-length", "0") or 0)
    except Exception as exc:
        failure = exc
        raise
    finally:
        elapsed = time.perf_counter() - start
        duration_ms = int(elapsed * 1000)
        user = _get_request_user(request)
        db_update = _derive_db_update(request, request_body)
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        # Labelled by the route template, never the path: no identifier reaches a metric.
        record_request(request.method, route_path, status_code, elapsed)
        query_string = _query_string_for_logging(request)

        http_request_json = {
            "requestMethod": request.method,
            "requestUrl": _request_url_for_logging(request, query_string),
            "status": status_code,
            "responseSize": response_size,
            "userAgent": request.headers.get("user-agent"),
            "remoteIp": request.client.host if request.client else None,
            "referer": request.headers.get("referer"),
            "protocol": request.scope.get("http_version"),
        }

        # NOTE: neither request_body (clinical PHI, up to 25KB) nor an exception's
        # text is emitted to the application log: a failed statement's text quotes
        # its SQL and parameters, values from the request or read for it. Both are
        # persisted only to the access-controlled audit DB (audit_log_events
        # .request_body and .error) via the write_audit_log_event call below.
        detail: dict[str, Any] = {"durationMs": duration_ms}
        log_kwargs: dict[str, Any] = {"http_request_json": http_request_json, "detail": detail}
        if db_update:
            log_kwargs["db_update"] = db_update

        if failure is not None or status_code >= 500:
            if route_path:
                detail["route"] = route_path
            message = "Unhandled server error"
            if failure is not None:
                # Named by its kind (exception type, SQLSTATE or ClickHouse code), with
                # the frames of every exception in its chain but none of their messages.
                message += f": {describe_error(failure)}"
                log_kwargs["traceback"] = describe_traceback(failure)
            logger.error(message, user=user, **log_kwargs)
        elif status_code >= 400:
            logger.warning("Request returned warning status", user=user, **log_kwargs)
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
                    error=str(failure) if failure is not None else None,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a lost audit row is logged; it never fails the request
            # The error's own text quotes the row it could not insert, the request body
            # among it, so only its kind is logged.
            logger.warning(
                f"Failed to persist audit log: {describe_error(exc)}",
                user=user,
                detail={"path": request.url.path, "method": request.method},
            )

    assert response is not None
    return response
