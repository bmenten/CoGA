"""Alembic's environment for CoGA's Postgres schema revisions.

It runs only inside CoGA's migration step (``backend/app/core/postgres_migrations.py``), which
hands it its connection: by then that step holds the migration lock, has refused the
restricted runtime role and has applied the baselines, all in one transaction, as the owner of
the tables. Run the step with ``python -m app.db_migrate`` (or start the backend as the owner);
``alembic upgrade`` and the other commands that need a database stop here. Writing a revision
(``alembic revision``) needs no database. See docs/database.md, "Schema Migrations".
"""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None or context.is_offline_mode():
    raise RuntimeError(
        "CoGA's schema revisions run only through its migration step: python -m app.db_migrate "
        "as the owner of the tables, or the backend's start as that owner "
        "(docs/database.md, \"Schema Migrations\")."
    )

# The caller's transaction is open, so Alembic adds none of its own: every revision commits
# with the baselines, or none does.
context.configure(connection=connection, target_metadata=None, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
