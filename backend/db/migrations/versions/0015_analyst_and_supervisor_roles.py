"""An account's global role can be analyst or supervisor; each viewer becomes an analyst.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-04

``users_role_check`` in 01_access.sql gained two roles between viewer and superuser, the owner's
decision of 2026-11-04: an *analyst* analyses the records of their projects, a *supervisor* also
signs an analysis out (TF-06 H15). A *viewer* now only reads, whatever its access level in a
project, and a superuser no longer has the administration an admin has
(services/access_control.py).

``CREATE TABLE IF NOT EXISTS`` does not bring the new values to an existing table's check, so a
database created before them gets the baseline's check here. Until now a viewer changed the
records of each project where it is an analyst, so each viewer becomes an analyst and keeps
what it could do: its access level in each project still decides there. A superuser stays a
superuser and an admin an admin. On a database that already has the check, there is no viewer
from before to change, and the check is replaced by itself.

The rows are changed here, not through ``PATCH /api/auth/users/{id}``: the request audit has no
row for them. This revision, in the version's release, is their record.
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# As 01_access.sql defines it, and as pg_get_constraintdef reads it back.
_ROLE_CHECK = (
    "CHECK ((role = ANY (ARRAY['viewer'::text, 'analyst'::text, 'supervisor'::text, "
    "'superuser'::text, 'admin'::text])))"
)


def upgrade() -> None:
    bind = op.get_bind()
    # Only a database whose check is from before the new roles holds a viewer from before them:
    # once the check is the baseline's, a viewer is one an administrator made, and stays one.
    before = bind.exec_driver_sql(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'users'::regclass AND conname = 'users_role_check'"
    ).scalar()
    if before != _ROLE_CHECK:
        bind.exec_driver_sql("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check")
        bind.exec_driver_sql("UPDATE users SET role = 'analyst' WHERE role = 'viewer'")
    # One statement: the table is never without its check, and is read once to validate it.
    bind.exec_driver_sql(
        "ALTER TABLE users "
        "DROP CONSTRAINT IF EXISTS users_role_check, "
        f"ADD CONSTRAINT users_role_check {_ROLE_CHECK}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
