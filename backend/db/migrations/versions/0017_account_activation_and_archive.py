"""An account records when it was first active, and can be archived.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-04

``users`` in 01_access.sql gained two columns and a check, so that an administrator can tell an
account that waits for approval from one that was deactivated, delete the first and archive
either: ``activated_at``, when the account was first active (its creation's time by default,
NULL for a sign-up or a first CAS sign-in until an administrator activates it), and
``archived_at``, when it was archived, with ``users_archived_inactive_check``: an archived
account is inactive. ``CREATE TABLE IF NOT EXISTS`` brings none of it to a table that already
exists, so it is done here.

An account stored before has no record of whether it was ever active. An active one was, from
its creation. An inactive one with a row of its own in the request audit (``audit_log_events``,
whose every authenticated request names its user) signed in once, so it was active and was
deactivated since: it is given its creation's time too. An inactive one without such a row
never signed in: it is taken to wait for approval, and keeps no time. No account is archived.
On a database that already has the columns, nothing is changed but the check, replaced by
itself.
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# As 01_access.sql defines them.
_ACTIVATED_DEFAULT = "timezone('utc', now())"
_ARCHIVED_CHECK = "CHECK (archived_at IS NULL OR NOT is_active)"


def upgrade() -> None:
    bind = op.get_bind()
    had_column = bind.exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'users' "
        "AND column_name = 'activated_at')"
    ).scalar()
    if not had_column:
        # Added empty, filled from what the database knows, and only then given its default:
        # with the default first, every stored account would read as activated now.
        bind.exec_driver_sql("ALTER TABLE users ADD COLUMN activated_at timestamptz")
        bind.exec_driver_sql(
            "UPDATE users u SET activated_at = u.created_at "
            "WHERE u.is_active OR EXISTS ("
            "SELECT 1 FROM audit_log_events a WHERE a.user_id = u.id)"
        )
        bind.exec_driver_sql(
            f"ALTER TABLE users ALTER COLUMN activated_at SET DEFAULT {_ACTIVATED_DEFAULT}"
        )
    bind.exec_driver_sql(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS archived_at timestamptz, "
        "DROP CONSTRAINT IF EXISTS users_archived_inactive_check, "
        f"ADD CONSTRAINT users_archived_inactive_check {_ARCHIVED_CHECK}"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "CoGA's schema revisions are forward-only. To go back, restore the database from the "
        "backup taken before the upgrade (docs/database.md, \"Schema Migrations\")."
    )
