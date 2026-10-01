import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_reject_insecure_defaults_outside_development() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="change-me",
            POSTGRES_PASSWORD="change-me",
            ADMIN_PASSWORD="change-me",
        )


def test_settings_allow_placeholder_defaults_in_test_env() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
    )

    assert settings.is_development is True


def test_settings_reject_audit_log_drop_allowed_in_production() -> None:
    # Silently dropping accountability events is not permitted in production.
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="x" * 48,
            POSTGRES_PASSWORD="s3cure-pg-pass",
            ADMIN_PASSWORD="s3cure-admin-pass",
            AUDIT_LOG_DROP_ALLOWED=True,
        )


def test_settings_allow_audit_log_drop_allowed_in_development() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        AUDIT_LOG_DROP_ALLOWED=True,
    )

    assert settings.audit_log_drop_allowed is True


def test_cors_origin_regex_default_is_fully_anchored() -> None:
    settings = Settings(_env_file=None, APP_ENV="test")
    assert settings.cors_origin_regex.startswith("^")
    assert settings.cors_origin_regex.endswith("$")


def test_cors_origin_regex_accepts_anchored_pattern() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        CORS_ORIGIN_REGEX=r"^https://app\.example\.org$",
    )
    assert settings.cors_origin_regex == r"^https://app\.example\.org$"


def test_cors_origin_regex_empty_is_allowed() -> None:
    settings = Settings(_env_file=None, APP_ENV="test", CORS_ORIGIN_REGEX="")
    assert settings.cors_origin_regex == ""


def test_cors_origin_regex_rejects_unanchored_pattern() -> None:
    # An unanchored pattern could match a hostile origin as a substring while
    # allow_credentials=True — reject it at load time.
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="test",
            CORS_ORIGIN_REGEX=r"https://app\.example\.org",
        )


def test_cors_origin_regex_rejects_invalid_regex() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="test",
            CORS_ORIGIN_REGEX=r"^https://(unclosed$",
        )


def test_postgres_connect_args_reflects_sslmode() -> None:
    # Default (disable) -> plain connection, no ssl arg.
    assert Settings(_env_file=None, APP_ENV="test").postgres_connect_args == {}
    # An enabled mode is passed through to asyncpg as `ssl`.
    secured = Settings(_env_file=None, APP_ENV="test", POSTGRES_SSLMODE="require")
    assert secured.postgres_connect_args == {"ssl": "require"}


def test_invalid_postgres_sslmode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV="test", POSTGRES_SSLMODE="bogus")


def test_clickhouse_tls_defaults_off() -> None:
    default = Settings(_env_file=None, APP_ENV="test")
    assert default.clickhouse_secure is False
    assert default.clickhouse_verify is True

    secure = Settings(_env_file=None, APP_ENV="test", CLICKHOUSE_SECURE=True)
    assert secure.clickhouse_secure is True


# --- #522: the startup check refuses weak or missing secrets, not just literal defaults ---

_VALID_PRODUCTION = {
    "APP_ENV": "production",
    "SECRET_KEY": "k" * 48,
    "POSTGRES_PASSWORD": "s3cure-pg-pass",
    "ADMIN_PASSWORD": "s3cure-admin-pass",
    "CLICKHOUSE_PASSWORD": "s3cure-ch-pass",
    # base64 of a 32-byte seed, built here rather than written out: a literal key-shaped
    # value is exactly what the secret scan is there to catch.
    "INTEGRITY_ANCHOR_SIGNING_KEY": base64.b64encode(bytes(range(32))).decode(),
    # A deployment is same-origin and stamped with its commit, as Terraform and build.yml
    # set it up.
    "CORS_ORIGINS": ["https://coga.example.org"],
    "CORS_ORIGIN_REGEX": "",
    "GIT_SHA": "0123456789ab",
}


def test_a_complete_production_configuration_starts() -> None:
    settings = Settings(_env_file=None, **_VALID_PRODUCTION)
    assert settings.is_development is False


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("SECRET_KEY", "short-but-not-a-default", "SECRET_KEY"),  # under 32 characters
        ("SECRET_KEY", "change-me", "SECRET_KEY"),
        ("CLICKHOUSE_PASSWORD", "", "CLICKHOUSE_PASSWORD"),
        ("CLICKHOUSE_PASSWORD", "change-me", "CLICKHOUSE_PASSWORD"),  # the .env.example placeholder
        ("INTEGRITY_ANCHOR_SIGNING_KEY", "", "INTEGRITY_ANCHOR_SIGNING_KEY"),
        ("INTEGRITY_ANCHOR_SIGNING_KEY", "not-base64!", "INTEGRITY_ANCHOR_SIGNING_KEY"),
        ("INTEGRITY_ANCHOR_SIGNING_KEY", base64.b64encode(b"abc").decode(), "INTEGRITY_ANCHOR_SIGNING_KEY"),
        # Optional (migration only), but when it is given it must not be a placeholder.
        ("POSTGRES_APP_PASSWORD", "change-me", "POSTGRES_APP_PASSWORD"),
        ("POSTGRES_APP_PASSWORD", "   ", "POSTGRES_APP_PASSWORD"),
    ],
)
def test_production_refuses_weak_or_missing_secrets(field, value, named) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_VALID_PRODUCTION, field: value})
    assert named in str(excinfo.value)


def test_development_does_not_require_production_secrets() -> None:
    settings = Settings(_env_file=None, APP_ENV="development", SECRET_KEY="dev", CLICKHOUSE_PASSWORD="")
    assert settings.is_development is True


# --- the request audit log cannot be switched off outside development/test ---
# AUDIT_LOG_MODE=off makes audit_log_pg and ui_event_pg write nothing, and every action in
# the interface must stay auditable.


@pytest.mark.parametrize("mode", ["off", "OFF", " off "])
@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_production_refuses_to_switch_the_audit_log_off(app_env, mode) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_VALID_PRODUCTION, "APP_ENV": app_env, "AUDIT_LOG_MODE": mode})
    assert "AUDIT_LOG_MODE=off" in str(excinfo.value)


@pytest.mark.parametrize("mode", ["async", "sync"])
def test_production_accepts_an_audit_log_mode_that_writes(mode) -> None:
    settings = Settings(_env_file=None, **{**_VALID_PRODUCTION, "AUDIT_LOG_MODE": mode})
    assert settings.audit_log_mode == mode


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_development_may_switch_the_audit_log_off(app_env) -> None:
    settings = Settings(_env_file=None, APP_ENV=app_env, AUDIT_LOG_MODE="off")
    assert settings.audit_log_mode == "off"


# --- the session-token secret and the integrity-anchor signing key must differ ---
# One value in both means whoever can mint a session token can also sign an integrity
# anchor, which is the forgery the anchors are there to rule out.

_ANCHOR_KEY = _VALID_PRODUCTION["INTEGRITY_ANCHOR_SIGNING_KEY"]


@pytest.mark.parametrize(
    "secret_key", [_ANCHOR_KEY, f" {_ANCHOR_KEY}\n"], ids=["same", "same-but-padded"]
)
def test_production_refuses_the_anchor_signing_key_as_the_secret_key(secret_key) -> None:
    # The 44-character key passes the SECRET_KEY length check on its own, so only the
    # separation rule can refuse it.
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_VALID_PRODUCTION, "SECRET_KEY": secret_key})
    message = excinfo.value.errors()[0]["msg"]
    assert "SECRET_KEY" in message and "INTEGRITY_ANCHOR_SIGNING_KEY" in message
    assert _ANCHOR_KEY not in message  # the refusal must not print the key it refused


def test_development_does_not_require_separate_keys() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        SECRET_KEY=_ANCHOR_KEY,
        INTEGRITY_ANCHOR_SIGNING_KEY=_ANCHOR_KEY,
    )
    assert settings.is_development is True


# --- cross-origin access and the build's commit, outside development/test ---
# A deployment serves the UI from the API's own origin, so it needs no cross-origin access;
# the development defaults admit localhost with credentials. Every signed report names the
# commit the instance ran, so an unstamped build must not serve.


@pytest.mark.parametrize(
    "origins",
    [
        ["http://localhost:3000"],
        ["https://coga.example.org", "http://127.0.0.1:5173"],
        ["http://[::1]:3000"],
        ["http://0.0.0.0"],
        ["http://app.localhost"],
        ["*"],
    ],
)
def test_production_refuses_cors_origins_for_the_users_own_machine_or_any_site(origins) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_VALID_PRODUCTION, "CORS_ORIGINS": origins})
    message = str(excinfo.value)
    assert "CORS_ORIGINS entry" in message
    assert next(origin for origin in origins if origin != "https://coga.example.org") in message


@pytest.mark.parametrize(
    "pattern",
    [
        r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$",  # the default
        r"^http://127\.0\.0\.1:\d+$",
        r"^https?://.*$",
    ],
)
def test_production_refuses_a_cors_pattern_matching_them(pattern) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_VALID_PRODUCTION, "CORS_ORIGIN_REGEX": pattern})
    assert "CORS_ORIGIN_REGEX, which matches" in str(excinfo.value)


def test_production_refuses_the_default_cors_pattern_when_none_is_set(monkeypatch) -> None:
    # Terraform used to set CORS_ORIGINS only, which left the localhost default pattern live.
    monkeypatch.delenv("CORS_ORIGIN_REGEX", raising=False)
    without_pattern = {key: value for key, value in _VALID_PRODUCTION.items() if key != "CORS_ORIGIN_REGEX"}
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **without_pattern)
    assert "http://localhost" in str(excinfo.value)


def test_production_accepts_its_own_origins() -> None:
    settings = Settings(
        _env_file=None,
        **{
            **_VALID_PRODUCTION,
            "CORS_ORIGINS": ["https://coga.example.org", "https://review.example.org"],
            "CORS_ORIGIN_REGEX": r"^https://([a-z0-9-]+\.)?example\.org$",
        },
    )
    assert settings.cors_origins == ["https://coga.example.org", "https://review.example.org"]
    no_origins = Settings(_env_file=None, **{**_VALID_PRODUCTION, "CORS_ORIGINS": []})
    assert no_origins.cors_origins == []


@pytest.mark.parametrize("git_sha", ["unknown", "", "   ", "dev", "0123456789xy", "12345", "a" * 41])
def test_production_refuses_a_build_without_its_commit(monkeypatch, git_sha) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_VALID_PRODUCTION, "GIT_SHA": git_sha})
    assert "GIT_SHA" in str(excinfo.value)


def test_production_refuses_the_unstamped_default(monkeypatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    without_sha = {key: value for key, value in _VALID_PRODUCTION.items() if key != "GIT_SHA"}
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **without_sha)
    assert "GIT_SHA is 'unknown'" in str(excinfo.value)


@pytest.mark.parametrize("git_sha", ["0123456789ab", "0123456", "0123456789ABCDEF", "f" * 40])
def test_production_accepts_a_stamped_build(git_sha) -> None:
    settings = Settings(_env_file=None, **{**_VALID_PRODUCTION, "GIT_SHA": git_sha})
    assert settings.git_sha == git_sha


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_development_keeps_the_local_cors_defaults_and_an_unstamped_build(monkeypatch, app_env) -> None:
    for name in ("CORS_ORIGINS", "CORS_ORIGIN_REGEX", "GIT_SHA"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None, APP_ENV=app_env)
    assert "http://localhost:5173" in settings.cors_origins
    assert settings.cors_origin_regex
    assert settings.git_sha == "unknown"


# --- the metrics endpoint's token, outside development/test ---
# Optional: unset, GET /metrics is off. When set it guards the endpoint, so it must be a real
# secret, and not one that also mints session tokens or signs integrity anchors.


@pytest.mark.parametrize("token", ["short-token", "change-me", "secret"])
def test_production_refuses_a_weak_metrics_token(token) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_VALID_PRODUCTION, "METRICS_TOKEN": token})
    assert "METRICS_TOKEN" in str(excinfo.value)


@pytest.mark.parametrize("same_as", ["SECRET_KEY", "INTEGRITY_ANCHOR_SIGNING_KEY"])
def test_production_refuses_a_metrics_token_shared_with_another_secret(same_as) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_VALID_PRODUCTION, "METRICS_TOKEN": _VALID_PRODUCTION[same_as]})
    assert "METRICS_TOKEN is the same value" in str(excinfo.value)


def test_production_starts_with_a_separate_metrics_token_or_none(monkeypatch) -> None:
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    assert Settings(_env_file=None, **_VALID_PRODUCTION).metrics_token == ""
    token = "m" * 48
    assert Settings(_env_file=None, **{**_VALID_PRODUCTION, "METRICS_TOKEN": token}).metrics_token == token


def test_development_takes_any_metrics_token() -> None:
    assert Settings(_env_file=None, APP_ENV="development", METRICS_TOKEN="dev").metrics_token == "dev"
