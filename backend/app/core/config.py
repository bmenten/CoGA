from pathlib import Path
import json
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

_DEVELOPMENT_ENVIRONMENTS = {"dev", "development", "local", "test"}
_INSECURE_SECRET_VALUES = {"secret", "change-me"}
_INSECURE_PASSWORD_VALUES = {"admin", "change-me"}


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_env: str = Field(default="production", alias="APP_ENV")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    algorithm: str = "HS256"
    # Token lifetime set to 6 hours for user sessions
    access_token_expire_minutes: int = 360
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="coga", alias="POSTGRES_DB")
    postgres_user: str = Field(default="coga", alias="POSTGRES_USER")
    postgres_password: str = Field(default="change-me", alias="POSTGRES_PASSWORD")
    clickhouse_host: str = Field(default="localhost", alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=9000, alias="CLICKHOUSE_PORT")
    clickhouse_http_port: int = Field(default=8123, alias="CLICKHOUSE_HTTP_PORT")
    clickhouse_database: str = Field(default="coga", alias="CLICKHOUSE_DATABASE")
    clickhouse_user: str = Field(default="default", alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="", alias="CLICKHOUSE_PASSWORD")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="change-me", alias="ADMIN_PASSWORD")
    admin_email: str = Field(default="admin@example.com", alias="ADMIN_EMAIL")
    login_rate_limit_window_seconds: int = Field(default=900, ge=60, alias="LOGIN_RATE_LIMIT_WINDOW_SECONDS")
    login_rate_limit_threshold: int = Field(default=5, ge=1, alias="LOGIN_RATE_LIMIT_THRESHOLD")
    login_rate_limit_base_backoff_seconds: int = Field(
        default=30,
        ge=1,
        alias="LOGIN_RATE_LIMIT_BASE_BACKOFF_SECONDS",
    )
    login_rate_limit_max_backoff_seconds: int = Field(
        default=900,
        ge=1,
        alias="LOGIN_RATE_LIMIT_MAX_BACKOFF_SECONDS",
    )
    audit_log_mode: str = Field(default="async", alias="AUDIT_LOG_MODE")
    audit_log_batch_size: int = Field(default=50, ge=1, le=500, alias="AUDIT_LOG_BATCH_SIZE")
    audit_log_flush_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30.0,
        alias="AUDIT_LOG_FLUSH_INTERVAL_SECONDS",
    )
    audit_log_queue_size: int = Field(default=1000, ge=1, le=100_000, alias="AUDIT_LOG_QUEUE_SIZE")
    audit_log_query_string_mode: str = Field(default="none", alias="AUDIT_LOG_QUERY_STRING_MODE")
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        alias="CORS_ORIGINS",
    )
    cors_origin_regex: str = Field(
        default=r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$",
        alias="CORS_ORIGIN_REGEX",
    )
    reference_fasta_path: str | None = None
    reference_alias_path: str | None = None
    reference_cytoband_path: str | None = None
    gene_reference_clingen_validity_url: str = Field(
        default="https://search.clinicalgenome.org/kb/gene-validity/download",
        alias="GENE_REFERENCE_CLINGEN_VALIDITY_URL",
    )
    gene_reference_clingen_dosage_url: str = Field(
        default="https://search.clinicalgenome.org/kb/gene-dosage/download",
        alias="GENE_REFERENCE_CLINGEN_DOSAGE_URL",
    )
    gene_reference_gencc_url: str = Field(
        default="https://search.thegencc.org/download/action/submissions-export-csv",
        alias="GENE_REFERENCE_GENCC_URL",
    )
    gene_reference_clinvar_gene_condition_url: str = Field(
        default="https://ftp.ncbi.nlm.nih.gov/pub/clinvar/gene_condition_source_id",
        alias="GENE_REFERENCE_CLINVAR_GENE_CONDITION_URL",
    )
    github_repository: str = Field(default="bmenten/coga", alias="GITHUB_REPOSITORY")
    github_repository_url: str = Field(
        default="https://github.com/bmenten/coga",
        alias="GITHUB_REPOSITORY_URL",
    )
    github_releases_url: str = Field(
        default="https://github.com/bmenten/coga/releases",
        alias="GITHUB_RELEASES_URL",
    )
    github_issues_url: str = Field(
        default="https://github.com/bmenten/coga/issues/new/choose",
        alias="GITHUB_ISSUES_URL",
    )
    github_api_token: str | None = Field(default=None, alias="GITHUB_API_TOKEN")
    github_repo_visibility: str = Field(default="private", alias="GITHUB_REPO_VISIBILITY")
    github_release_cache_ttl_seconds: int = Field(
        default=300,
        alias="GITHUB_RELEASE_CACHE_TTL_SECONDS",
    )
    gene_reference_dbnsfp_gene_path: str | None = Field(
        default=None,
        alias="GENE_REFERENCE_DBNSFP_GENE_PATH",
    )
    reads_path: str | None = None
    family_import_roots: list[str] = Field(default_factory=list, alias="FAMILY_IMPORT_ROOTS")
    family_import_worker_count: int = Field(default=1, ge=1, le=8, alias="FAMILY_IMPORT_WORKER_COUNT")
    trgt_strchive_loci_path: str | None = Field(
        default="/data/ref-data/STRchive-loci.json",
        alias="TRGT_STRCHIVE_LOCI_PATH",
    )
    paraphase_medical_regions_path: str | None = Field(
        default="/data/ref-data/paraphase-medical-regions.json",
        alias="PARAPHASE_MEDICAL_REGIONS_PATH",
    )
    azure_tenant_id: str | None = Field(default=None, alias="AZURE_TENANT_ID")
    azure_client_id: str | None = Field(default=None, alias="AZURE_CLIENT_ID")
    azure_admin_override: bool = Field(default=False, alias="AZURE_ADMIN_OVERRIDE")

    # Resolve the project root .env if present (repo root), otherwise fallback to CWD
    _env_path = Path(__file__).resolve().parents[3] / ".env"
    model_config = SettingsConfigDict(
        env_file=str(_env_path) if _env_path.exists() else ".env",
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value

    @field_validator("family_import_roots", mode="before")
    @classmethod
    def parse_family_import_roots(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [entry.strip() for entry in stripped.split(",") if entry.strip()]
        return value

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "production"
        return value

    @field_validator("audit_log_mode", mode="before")
    @classmethod
    def normalize_audit_log_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "async"
        return value

    @field_validator("audit_log_query_string_mode", mode="before")
    @classmethod
    def normalize_audit_log_query_string_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "none"
        return value

    @model_validator(mode="after")
    def validate_security_defaults(self) -> "Settings":
        if self.audit_log_mode not in {"async", "sync", "off"}:
            raise ValueError("AUDIT_LOG_MODE must be one of: async, sync, off")
        if self.audit_log_query_string_mode not in {"none", "keys", "sanitized"}:
            raise ValueError("AUDIT_LOG_QUERY_STRING_MODE must be one of: none, keys, sanitized")
        if self.login_rate_limit_base_backoff_seconds > self.login_rate_limit_max_backoff_seconds:
            raise ValueError(
                "LOGIN_RATE_LIMIT_BASE_BACKOFF_SECONDS must be less than or equal to LOGIN_RATE_LIMIT_MAX_BACKOFF_SECONDS"
            )
        if self.is_development:
            return self

        insecure_fields: list[str] = []
        if self.secret_key.strip() in _INSECURE_SECRET_VALUES:
            insecure_fields.append("SECRET_KEY")
        if self.postgres_password.strip() in _INSECURE_PASSWORD_VALUES:
            insecure_fields.append("POSTGRES_PASSWORD")
        if self.admin_password.strip() in _INSECURE_PASSWORD_VALUES:
            insecure_fields.append("ADMIN_PASSWORD")
        if self.admin_username.strip().lower() == "admin" and self.admin_password.strip() in _INSECURE_PASSWORD_VALUES:
            insecure_fields.append("ADMIN_USERNAME")
        if insecure_fields:
            raise ValueError(
                "Refusing to start outside development/test with insecure default credentials: "
                + ", ".join(sorted(set(insecure_fields)))
                + ". Set APP_ENV=development for local-only work or provide real secrets."
            )
        return self

    @property
    def is_development(self) -> bool:
        return self.app_env in _DEVELOPMENT_ENVIRONMENTS

    @property
    def postgres_dsn(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


settings = Settings()
