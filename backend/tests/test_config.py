from pathlib import Path

import pytest

from app.core import config
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


@pytest.mark.parametrize(
    ("version", "reported"), [("1.4.0-rc.2", "1.4.0-rc.2.dev"), ("1.4.0", "1.4.0-dev")]
)
def test_unstamped_build_reports_the_version_file_as_a_development_build(
    monkeypatch, tmp_path, version, reported
) -> None:
    # No APP_VERSION, or the empty one the image carries when no build-arg was given: the
    # build names the version it was built from, marked as a development build, since only
    # a release build stamps a release's version (TF-18 §2). A local build of the release
    # candidate's commit, or of any later one, is never taken for that release in
    # /api/version, the footers or a signed report. The commit stays "unknown".
    version_file = tmp_path / "VERSION"
    version_file.write_text(f"{version}\n", encoding="utf-8")
    monkeypatch.setattr(config, "_VERSION_FILE", version_file)
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert Settings(_env_file=None).app_version == reported
    assert Settings(_env_file=None).git_sha == "unknown"
    monkeypatch.setenv("APP_VERSION", "")
    assert Settings(_env_file=None).app_version == reported
    monkeypatch.setenv("APP_VERSION", "  ")
    assert Settings(_env_file=None).app_version == reported


def test_a_stamped_version_is_reported_as_stamped(monkeypatch, tmp_path) -> None:
    # The release build stamps its tag's version, which the build reports exactly; a build of
    # main stamps the development version, which it reports as it is (scripts/build-version.sh).
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.4.0-rc.2\n", encoding="utf-8")
    monkeypatch.setattr(config, "_VERSION_FILE", version_file)
    for stamped in ("1.4.0-rc.2", "1.4.0-rc.2.dev"):
        monkeypatch.setenv("APP_VERSION", stamped)
        assert Settings(_env_file=None).app_version == stamped


def test_unstamped_build_without_a_version_file_records_the_sentinel(monkeypatch, tmp_path) -> None:
    # Honest sentinels, never blank, so /version and the frozen sign-out snapshot record
    # "unknown" rather than failing or naming a release.
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setattr(config, "_VERSION_FILE", tmp_path / "missing")
    settings = Settings(_env_file=None)
    assert settings.app_version == "0.0.0+unknown"
    assert settings.git_sha == "unknown"
    (tmp_path / "blank").write_text("\n", encoding="utf-8")
    monkeypatch.setattr(config, "_VERSION_FILE", tmp_path / "blank")
    assert Settings(_env_file=None).app_version == "0.0.0+unknown"
    (tmp_path / "binary").write_bytes(b"\xff\xfe\x00")
    monkeypatch.setattr(config, "_VERSION_FILE", tmp_path / "binary")
    assert Settings(_env_file=None).app_version == "0.0.0+unknown"


def test_the_version_file_is_the_repository_version() -> None:
    # In a checkout the file is the repository's VERSION (and in the image /VERSION, see
    # test_deployment_config).
    repository_version = Path(__file__).resolve().parents[2] / "VERSION"
    assert config._VERSION_FILE == repository_version
    assert config._read_version_file() == repository_version.read_text(encoding="utf-8").strip()


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
