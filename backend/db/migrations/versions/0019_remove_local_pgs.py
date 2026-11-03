"""Remove local PGS scoring; retain independent WGS sources and ancestry history."""
from alembic import context, op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    prefixes = (
        "CREATE TABLE IF NOT EXISTS wgs_sources", "CREATE INDEX IF NOT EXISTS wgs_sources",
        "CREATE OR REPLACE FUNCTION wgs_sources_", "DROP TRIGGER IF EXISTS wgs_sources_",
        "CREATE TRIGGER wgs_sources_",
    )
    for statement in context.config.attributes["baseline_statements"]("04_traceability.sql"):
        if statement.startswith(prefixes):
            bind.exec_driver_sql(statement)
    # A source is ancestry input/provenance, not a PGS result. Preserve every UUID,
    # checksum, qualification and timestamp before removing the retired namespace.
    bind.exec_driver_sql("""DO $$ BEGIN
        IF to_regclass('pgs_sources') IS NOT NULL THEN
            INSERT INTO wgs_sources(id,family_uuid,family_identifier,project_id,import_job_id,
                package_path,relative_vcf,sha256,storage_version,storage_fingerprint,samples,
                absent_record_is_hom_ref,file_size,file_mtime_ns,raw_file_id,qualification,imported_at)
            SELECT id,family_uuid,family_identifier,project_id,import_job_id,
                package_path,relative_vcf,sha256,storage_version,storage_fingerprint,samples,
                absent_record_is_hom_ref,file_size,file_mtime_ns,raw_file_id,qualification,imported_at
            FROM pgs_sources ON CONFLICT (id) DO NOTHING;
            IF EXISTS (
                SELECT id,family_uuid,family_identifier,project_id,import_job_id,package_path,
                    relative_vcf,sha256,storage_version,storage_fingerprint,samples,
                    absent_record_is_hom_ref,file_size,file_mtime_ns,raw_file_id,qualification,imported_at
                FROM pgs_sources
                EXCEPT
                SELECT id,family_uuid,family_identifier,project_id,import_job_id,package_path,
                    relative_vcf,sha256,storage_version,storage_fingerprint,samples,
                    absent_record_is_hom_ref,file_size,file_mtime_ns,raw_file_id,qualification,imported_at
                FROM wgs_sources
            ) THEN
                RAISE EXCEPTION 'WGS source preservation failed; local PGS removal was rolled back';
            END IF;
        END IF;
    END $$""")
    for table in ("ancestry_runs", "ancestry_results"):
        # Table and constraint names are a fixed migration allowlist.
        bind.exec_driver_sql(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_source_id_fkey")
        bind.exec_driver_sql(f"ALTER TABLE {table} ADD CONSTRAINT {table}_source_id_fkey FOREIGN KEY (source_id) REFERENCES wgs_sources(id)")
    for table in ("pgs_result_dismissals", "pgs_results", "pgs_runs", "pgs_catalog_imports", "pgs_model_versions", "pgs_models", "pgs_sources"):
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    for function in (
        "delete_unused_pgs_model(text)", "pgs_model_has_family_use(text)",
        "pgs_model_versions_block_mutation()", "pgs_sources_block_mutation()",
        "pgs_results_block_mutation()", "pgs_runs_pin_models()",
        "pgs_result_dismissals_guard()", "pgs_result_dismissals_block_mutation()",
    ):
        bind.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function}")
    bind.exec_driver_sql("GRANT SELECT, INSERT ON wgs_sources TO coga_app")
    bind.exec_driver_sql("REVOKE UPDATE, DELETE, TRUNCATE ON wgs_sources FROM coga_app")


def downgrade() -> None:
    raise NotImplementedError("Forward-only: restore the database from the backup taken before this migration.")
