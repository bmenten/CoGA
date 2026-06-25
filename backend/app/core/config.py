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
    clickhouse_http_port: int = Field(default=8123, alias="CLICKHOUSE_HTTP_PORT")
    clickhouse_database: str = Field(default="coga", alias="CLICKHOUSE_DATABASE")
    clickhouse_user: str = Field(default="default", alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="", alias="CLICKHOUSE_PASSWORD")
    # Per-query ClickHouse guardrails. These let heavy variant-filter queries
    # spill to disk instead of being killed for memory, and bound their runtime
    # so a single broad query cannot hang the request indefinitely.
    clickhouse_max_execution_time: int = Field(
        default=110,
        ge=1,
        alias="CLICKHOUSE_MAX_EXECUTION_TIME",
    )
    clickhouse_send_receive_timeout: int = Field(
        default=120,
        ge=1,
        alias="CLICKHOUSE_SEND_RECEIVE_TIMEOUT",
    )
    clickhouse_max_memory_usage: int = Field(
        default=0,
        ge=0,
        alias="CLICKHOUSE_MAX_MEMORY_USAGE",
    )
    clickhouse_external_spill_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=0,
        alias="CLICKHOUSE_EXTERNAL_SPILL_BYTES",
    )
    # Large gene panels (e.g. the ~5,300-gene Mendeliome) expand into the query text
    # as gene/region arrays; the ClickHouse default of 256 KiB overflows, so raise it.
    clickhouse_max_query_size: int = Field(
        default=16 * 1024 * 1024,
        ge=262144,
        alias="CLICKHOUSE_MAX_QUERY_SIZE",
    )
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
    # "keys" records which query parameters a request used (e.g. the filters on a
    # variant search) without their values, so searches are logged structurally
    # while clinical identifiers stay out of the audit trail. Override with
    # "none" to disable or "sanitized" to keep masked values.
    audit_log_query_string_mode: str = Field(default="keys", alias="AUDIT_LOG_QUERY_STRING_MODE")
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
        default="/data/ref-data/dbNSFP5.3_gene.gz",
        alias="GENE_REFERENCE_DBNSFP_GENE_PATH",
    )
    gene_reference_bootstrap_on_startup: bool = Field(
        default=True,
        alias="GENE_REFERENCE_BOOTSTRAP_ON_STARTUP",
    )
    hpo_bootstrap_on_startup: bool = Field(default=True, alias="HPO_BOOTSTRAP_ON_STARTUP")
    hpo_ontology_path: str | None = Field(
        default="/data/ref-data/hpo/hp.obo",
        alias="HPO_ONTOLOGY_PATH",
    )
    hpo_ontology_url: str = Field(
        default="http://purl.obolibrary.org/obo/hp.obo",
        alias="HPO_ONTOLOGY_URL",
    )
    hpo_download_if_missing: bool = Field(default=True, alias="HPO_DOWNLOAD_IF_MISSING")
    reads_path: str | None = None
    # Storage backend for raw family data (IGV alignments + family-package sources).
    # "local" reads from the local filesystem (dev); "s3" reads from an S3 bucket
    # (production) via presigned URLs for IGV and temp staging for package import.
    storage_backend: str = Field(default="local", alias="STORAGE_BACKEND")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    s3_region: str | None = Field(default=None, alias="S3_REGION")
    # Optional override for S3-compatible endpoints (e.g. MinIO) and local testing.
    s3_endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT_URL")
    # Optional key prefix prepended to every object key (e.g. "families").
    s3_prefix: str = Field(default="", alias="S3_PREFIX")
    s3_presign_expiry_seconds: int = Field(
        default=3600,
        ge=60,
        le=604_800,
        alias="S3_PRESIGN_EXPIRY_SECONDS",
    )
    # Roots that Package Import may read family folders from. Defaults to the
    # local /data/families; cloud deployments (Terraform, etc.) override this
    # with an s3:// bucket prefix via FAMILY_IMPORT_ROOTS.
    family_import_roots: list[str] = Field(
        default_factory=lambda: ["/data/families"], alias="FAMILY_IMPORT_ROOTS"
    )
    family_import_worker_count: int = Field(default=1, ge=1, le=8, alias="FAMILY_IMPORT_WORKER_COUNT")
    trgt_strchive_loci_path: str | None = Field(
        default="/data/ref-data/STRchive-loci.json",
        alias="TRGT_STRCHIVE_LOCI_PATH",
    )
    reference_bootstrap_enabled: bool = Field(
        default=True,
        alias="REFERENCE_BOOTSTRAP_ENABLED",
    )
    reference_bootstrap_assembly_name: str = Field(
        default="GRCh38",
        alias="REFERENCE_BOOTSTRAP_ASSEMBLY_NAME",
    )
    reference_clinical_cnvs_path: str | None = Field(
        default="/data/ref-data/clinical_cnv_syndromes_hg38_combined.tsv",
        alias="REFERENCE_CLINICAL_CNVS_PATH",
    )
    reference_segmental_duplications_path: str | None = Field(
        default="/data/ref-data/clinical_cnv_syndromes_hg38_bundle/ClinGen_recurrent_CNV_V2.1-hg38.bed",
        alias="REFERENCE_SEGMENTAL_DUPLICATIONS_PATH",
    )
    clinical_cnv_kb_script_path: str | None = Field(
        default="/app/scripts/clinical_cnv_knowledgebase.py",
        alias="CLINICAL_CNV_KB_SCRIPT_PATH",
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

    @field_validator("storage_backend", mode="before")
    @classmethod
    def normalize_storage_backend(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "local"
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
            return value.strip().lower() or "keys"
        return value

    @model_validator(mode="after")
    def validate_security_defaults(self) -> "Settings":
        if self.audit_log_mode not in {"async", "sync", "off"}:
            raise ValueError("AUDIT_LOG_MODE must be one of: async, sync, off")
        if self.audit_log_query_string_mode not in {"none", "keys", "sanitized"}:
            raise ValueError("AUDIT_LOG_QUERY_STRING_MODE must be one of: none, keys, sanitized")
        if self.storage_backend not in {"local", "s3"}:
            raise ValueError("STORAGE_BACKEND must be one of: local, s3")
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("STORAGE_BACKEND=s3 requires S3_BUCKET to be set")
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
