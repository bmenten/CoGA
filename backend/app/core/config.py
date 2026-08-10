from pathlib import Path
import json
import re
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# Every application router is mounted under this prefix (see ``app/main.py``). Kept here
# as the single source of truth so request-scoped consumers (e.g. the audit-entity
# derivation in request_logging) can strip it without re-hardcoding the path.
API_PATH_PREFIX = "/api"

_DEVELOPMENT_ENVIRONMENTS = {"dev", "development", "local", "test"}
_INSECURE_SECRET_VALUES = {"secret", "change-me"}
_INSECURE_PASSWORD_VALUES = {"admin", "change-me"}
# libpq/asyncpg SSL modes for the Postgres connection (TF-13 S-2).
_POSTGRES_SSLMODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_env: str = Field(default="production", alias="APP_ENV")
    # Build identity, injected at image-build time via the APP_VERSION/GIT_SHA
    # build-args. Free-form strings with permissive defaults so a missing/malformed
    # value never crash-loops the clinical app; "unknown" is recorded honestly (and
    # frozen into every signed report's content hash).
    app_version: str = Field(default="0.0.0+unknown", alias="APP_VERSION")
    git_sha: str = Field(default="unknown", alias="GIT_SHA")
    # Emit HSTS only where TLS terminates in front of the app (never over plain HTTP),
    # so it is safe to leave off for the no-TLS local/compose stack.
    enable_hsts: bool = Field(default=False, alias="ENABLE_HSTS")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    # Ed25519 private signing key (base64 of the 32-byte seed) for integrity anchors —
    # MUST be distinct from SECRET_KEY and stored outside the DB. Unset ⇒ anchors are
    # written 'unsigned' (a non-owner-only checkpoint; see integrity_anchor_service.py).
    integrity_anchor_signing_key: str = Field(default="", alias="INTEGRITY_ANCHOR_SIGNING_KEY")
    algorithm: str = "HS256"
    # Session token lifetime. Kept short (2h) to bound the blast radius of a leaked
    # token, since tokens live in localStorage (no HttpOnly cookie); see
    # security-posture.md §1. Tunable per deployment.
    access_token_expire_minutes: int = Field(
        default=120, ge=1, alias="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="coga", alias="POSTGRES_DB")
    postgres_user: str = Field(default="coga", alias="POSTGRES_USER")
    postgres_password: str = Field(default="change-me", alias="POSTGRES_PASSWORD")
    # Whether the API applies the Postgres schema (DDL) + admin seed on startup.
    #   True  (default): the app boots as the table OWNER and self-migrates — the current
    #                    single-DSN deployment, unchanged.
    #   False: schema migrations are run out-of-band as the owner (``python -m
    #          backend.app.db_migrate``) and the app boots as the restricted runtime role
    #          ``coga_app``, which cannot run DDL. This is the P1-3/P1-4 owner-bypass flip
    #          (see docs/db-runtime-role-runbook.md).
    postgres_run_schema_migrations_on_startup: bool = Field(
        default=True, alias="POSTGRES_RUN_SCHEMA_MIGRATIONS_ON_STARTUP"
    )
    clickhouse_host: str = Field(default="localhost", alias="CLICKHOUSE_HOST")
    clickhouse_http_port: int = Field(default=8123, alias="CLICKHOUSE_HTTP_PORT")
    clickhouse_database: str = Field(default="coga", alias="CLICKHOUSE_DATABASE")
    clickhouse_user: str = Field(default="default", alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="", alias="CLICKHOUSE_PASSWORD")
    # TLS to the datastores (TF-13 S-2). Defaults preserve the plain local/dev
    # connection; production sets these to require encrypted links.
    #   POSTGRES_SSLMODE: libpq mode — disable|allow|prefer|require|verify-ca|verify-full
    #   CLICKHOUSE_SECURE: connect to ClickHouse over HTTPS (set CLICKHOUSE_HTTP_PORT=8443)
    #   CLICKHOUSE_VERIFY: verify the ClickHouse server certificate when secure
    postgres_sslmode: str = Field(default="disable", alias="POSTGRES_SSLMODE")
    # Cloud SQL Python Connector. When enabled, connect through the connector (mTLS +
    # full server-identity verification — verify-full grade — over private IP, with
    # automatic ephemeral-cert rotation) instead of a direct DSN, which avoids the
    # Cloud SQL hostname/CN mismatch that blocks plain sslmode=verify-full. Requires
    # the instance connection name (project:region:instance).
    postgres_use_cloud_sql_connector: bool = Field(
        default=False, alias="POSTGRES_USE_CLOUD_SQL_CONNECTOR"
    )
    postgres_instance_connection_name: str | None = Field(
        default=None, alias="POSTGRES_INSTANCE_CONNECTION_NAME"
    )
    cloud_sql_ip_type: str = Field(default="PRIVATE", alias="CLOUD_SQL_IP_TYPE")
    clickhouse_secure: bool = Field(default=False, alias="CLICKHOUSE_SECURE")
    clickhouse_verify: bool = Field(default=True, alias="CLICKHOUSE_VERIFY")
    # CA certificate used to verify the ClickHouse server cert when CLICKHOUSE_SECURE
    # is set. Accepts inline PEM content or a file path. Required to verify a private
    # CA (e.g. the self-managed ClickHouse VM); unset falls back to the system CA bundle.
    clickhouse_ca_cert: str | None = Field(default=None, alias="CLICKHOUSE_CA_CERT")
    # Hostname checked against the server cert (SNI + verification). Set to a DNS SAN
    # present in the cert when connecting by IP, so verification succeeds over an IP host.
    clickhouse_server_host_name: str | None = Field(
        default=None, alias="CLICKHOUSE_SERVER_HOST_NAME"
    )
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
    # P2-6: scheduled ClickHouse variant-integrity monitor (CHECK TABLE sweep). Detects
    # corruption proactively (logs ERROR on 'corrupt'/'missing') before it surfaces as 500s.
    clickhouse_integrity_monitor_enabled: bool = Field(
        default=True, alias="CLICKHOUSE_INTEGRITY_MONITOR_ENABLED"
    )
    clickhouse_integrity_interval_seconds: int = Field(
        default=21_600, ge=60, alias="CLICKHOUSE_INTEGRITY_INTERVAL_SECONDS"  # 6h
    )
    clickhouse_integrity_startup_delay_seconds: int = Field(
        default=60, ge=0, alias="CLICKHOUSE_INTEGRITY_STARTUP_DELAY_SECONDS"
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
    # Outbound resilience for external reference APIs (HGNC/Ensembl/NCBI/ClinGen/...):
    # per-phase timeouts + capped exponential-backoff retry (idempotent requests only).
    external_http_connect_timeout_seconds: float = Field(
        default=5.0, gt=0, alias="EXTERNAL_HTTP_CONNECT_TIMEOUT_SECONDS"
    )
    external_http_read_timeout_seconds: float = Field(
        default=25.0, gt=0, alias="EXTERNAL_HTTP_READ_TIMEOUT_SECONDS"
    )
    external_http_max_attempts: int = Field(
        default=3, ge=1, le=10, alias="EXTERNAL_HTTP_MAX_ATTEMPTS"
    )
    external_http_backoff_base_seconds: float = Field(
        default=0.5, ge=0, alias="EXTERNAL_HTTP_BACKOFF_BASE_SECONDS"
    )
    external_http_backoff_max_seconds: float = Field(
        default=8.0, ge=0, alias="EXTERNAL_HTTP_BACKOFF_MAX_SECONDS"
    )
    audit_log_mode: str = Field(default="async", alias="AUDIT_LOG_MODE")
    audit_log_batch_size: int = Field(default=50, ge=1, le=500, alias="AUDIT_LOG_BATCH_SIZE")
    audit_log_flush_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30.0,
        alias="AUDIT_LOG_FLUSH_INTERVAL_SECONDS",
    )
    audit_log_queue_size: int = Field(default=10_000, ge=1, le=100_000, alias="AUDIT_LOG_QUEUE_SIZE")
    # When false (production default) the async pipeline never silently drops: a
    # full queue applies backpressure and then writes the event synchronously.
    # True is a dev/test-only escape hatch (refused in production below) that
    # restores the old drop-on-full behaviour for low overhead.
    audit_log_drop_allowed: bool = Field(default=False, alias="AUDIT_LOG_DROP_ALLOWED")
    audit_log_backpressure_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60.0,
        alias="AUDIT_LOG_BACKPRESSURE_TIMEOUT_SECONDS",
    )
    audit_log_max_write_attempts: int = Field(
        default=3, ge=1, le=10, alias="AUDIT_LOG_MAX_WRITE_ATTEMPTS"
    )
    # Upload caps: bound the bytes read from an upload and the size it may expand to
    # once decompressed, so a small gzip "bomb" cannot exhaust memory. Generous by
    # default (real genomic files sit well under these); tune down per deployment.
    max_upload_bytes: int = Field(
        default=1024 * 1024 * 1024, ge=1, alias="MAX_UPLOAD_BYTES"
    )
    max_decompressed_upload_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024, ge=1, alias="MAX_DECOMPRESSED_UPLOAD_BYTES"
    )
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
        default=r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$",
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
    # HGNC is the naming authority, so its complete set defines which human genes exist
    # and which symbols they used to carry. Every other gene source is joined onto it.
    gene_reference_hgnc_complete_set_url: str = Field(
        default="https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt",
        alias="GENE_REFERENCE_HGNC_COMPLETE_SET_URL",
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
        default="/data/ref-data/dbNSFP5.4_gene.gz",
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
    # "local" reads from the local filesystem (dev); "s3"/"gcs" read from a cloud
    # object store (production) via presigned/signed URLs for IGV and temp staging
    # for package import.
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
    # S3 client resilience (botocore Config): bounded connect/read timeouts + adaptive
    # retries, so a slow/throttling object store cannot hang or fail a request unbounded.
    s3_connect_timeout_seconds: float = Field(
        default=5.0, gt=0, alias="S3_CONNECT_TIMEOUT_SECONDS"
    )
    s3_read_timeout_seconds: float = Field(
        default=60.0, gt=0, alias="S3_READ_TIMEOUT_SECONDS"
    )
    s3_max_attempts: int = Field(default=5, ge=1, le=10, alias="S3_MAX_ATTEMPTS")
    # Google Cloud Storage backend (STORAGE_BACKEND=gcs). Credentials come from
    # Application Default Credentials (Workload Identity on Cloud Run); signed URLs
    # use IAM SignBlob, so no key file is needed. The presign TTL reuses
    # S3_PRESIGN_EXPIRY_SECONDS.
    gcs_bucket: str | None = Field(default=None, alias="GCS_BUCKET")
    gcs_prefix: str = Field(default="", alias="GCS_PREFIX")
    # Optional GCS project (mainly for emulators where it can't be inferred from ADC).
    gcs_project: str | None = Field(default=None, alias="GCS_PROJECT")
    # Optional override for a GCS-compatible endpoint (e.g. fake-gcs-server in tests,
    # or a self-hosted store). When set, the client uses anonymous credentials.
    gcs_endpoint_url: str | None = Field(default=None, alias="GCS_ENDPOINT_URL")
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

    @field_validator("cors_origin_regex")
    @classmethod
    def validate_cors_origin_regex(cls, value: str) -> str:
        # `allow_credentials=True` combined with a broad origin regex is a CORS
        # bypass (a hostile origin could be matched and receive credentialed
        # responses). Reject anything that does not compile or is not fully
        # anchored so an operator can't accidentally widen it to a substring match.
        if not value:
            # Empty pattern matches nothing under Starlette's fullmatch — safe.
            return value
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(
                f"CORS_ORIGIN_REGEX is not a valid regular expression: {exc}"
            ) from exc
        if not (value.startswith("^") and value.endswith("$")):
            raise ValueError(
                "CORS_ORIGIN_REGEX must be fully anchored (start with '^' and end "
                "with '$') so it cannot match a hostile origin as a substring"
            )
        return value

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
        if self.postgres_sslmode not in _POSTGRES_SSLMODES:
            raise ValueError(
                "POSTGRES_SSLMODE must be one of: " + ", ".join(sorted(_POSTGRES_SSLMODES))
            )
        if self.postgres_use_cloud_sql_connector and not self.postgres_instance_connection_name:
            raise ValueError(
                "POSTGRES_USE_CLOUD_SQL_CONNECTOR=true requires "
                "POSTGRES_INSTANCE_CONNECTION_NAME (project:region:instance)."
            )
        if self.storage_backend not in {"local", "s3", "gcs"}:
            raise ValueError("STORAGE_BACKEND must be one of: local, s3, gcs")
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("STORAGE_BACKEND=s3 requires S3_BUCKET to be set")
        if self.storage_backend == "gcs" and not self.gcs_bucket:
            raise ValueError("STORAGE_BACKEND=gcs requires GCS_BUCKET to be set")
        if self.login_rate_limit_base_backoff_seconds > self.login_rate_limit_max_backoff_seconds:
            raise ValueError(
                "LOGIN_RATE_LIMIT_BASE_BACKOFF_SECONDS must be less than or equal to LOGIN_RATE_LIMIT_MAX_BACKOFF_SECONDS"
            )
        if self.is_development:
            return self

        if self.audit_log_drop_allowed:
            raise ValueError(
                "AUDIT_LOG_DROP_ALLOWED=true is only permitted in development/test: "
                "production must not silently drop accountability events. Set "
                "APP_ENV=development for local work or AUDIT_LOG_DROP_ALLOWED=false."
            )

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

    @property
    def postgres_connect_args(self) -> dict[str, str]:
        """asyncpg connect args. Passes the libpq SSL mode through as `ssl` when
        TLS is requested; empty (plain connection) when `POSTGRES_SSLMODE=disable`."""
        if self.postgres_sslmode == "disable":
            return {}
        return {"ssl": self.postgres_sslmode}


settings = Settings()
