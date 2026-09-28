import pytest

from app.core.config import Settings


def test_default_cors_origins_cover_local_frontend_ports() -> None:
    settings = Settings(_env_file=None)

    assert "http://localhost:3000" in settings.cors_origins
    assert "http://localhost:5173" in settings.cors_origins
    assert settings.postgres_db == "coga"
    assert settings.clickhouse_database == "coga"


def test_cors_origins_support_comma_separated_env_values() -> None:
    settings = Settings(
        _env_file=None,
        CORS_ORIGINS="http://localhost:3000, http://localhost:5173",
    )

    assert settings.cors_origins == [
        "http://localhost:3000",
        "http://localhost:5173",
    ]


def test_build_identity_has_safe_local_defaults() -> None:
    # Unstamped local/dev build: honest sentinels, never blank — so /version and the
    # frozen sign-out snapshot record "unknown" rather than failing or hiding it.
    settings = Settings(_env_file=None)
    assert settings.app_version == "0.0.0+unknown"
    assert settings.git_sha == "unknown"


def test_build_identity_reads_from_env() -> None:
    settings = Settings(_env_file=None, APP_VERSION="1.2.3", GIT_SHA="abc1234def5")
    assert settings.app_version == "1.2.3"
    assert settings.git_sha == "abc1234def5"


@pytest.mark.parametrize(
    ("variable", "field", "raw", "expected"),
    [
        ("FAMILY_IMPORT_ROOTS", "family_import_roots", "/data/families", ["/data/families"]),
        ("FAMILY_IMPORT_ROOTS", "family_import_roots", "/a, s3://b/families", ["/a", "s3://b/families"]),
        ("CORS_ORIGINS", "cors_origins", "https://a.example,https://b.example", ["https://a.example", "https://b.example"]),
        ("CORS_ORIGINS", "cors_origins", '["https://a.example"]', ["https://a.example"]),
        ("VALIDATED_ASSEMBLIES", "validated_assemblies", "GRCh38", ["GRCh38"]),
        ("VALIDATED_ASSEMBLIES", "validated_assemblies", "GRCh38, T2T-CHM13v2.0", ["GRCh38", "T2T-CHM13v2.0"]),
    ],
)
def test_list_settings_parse_from_the_real_environment(monkeypatch, variable, field, raw, expected) -> None:
    # Set as a process environment variable, which is how docker compose (env_file) and
    # Cloud Run pass them. Passed as a constructor argument, as above, the value skips
    # the environment source that used to JSON-decode it and reject the comma form.
    monkeypatch.setenv(variable, raw)
    assert getattr(Settings(_env_file=None), field) == expected


def test_validated_assemblies_default_to_grch38_only() -> None:
    # TF-01 §4: the validated scope is GRCh38; T2T-CHM13 is out of scope until validated.
    assert Settings(_env_file=None).validated_assemblies == ["GRCh38"]
