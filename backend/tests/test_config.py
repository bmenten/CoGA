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
