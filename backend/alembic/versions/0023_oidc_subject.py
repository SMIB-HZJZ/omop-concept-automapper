"""Accounts that sign in through an OpenID Connect provider (AUTH_MODE=oidc, e.g. Keycloak) are
linked by the provider's stable user id (the ``sub`` claim), not by email, which can change.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-27
"""

from app.core.migration_sql import run_sql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        ALTER TABLE app_user ADD COLUMN IF NOT EXISTS oidc_subject TEXT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_oidc_subject
            ON app_user (oidc_subject) WHERE oidc_subject IS NOT NULL;
        """
    )


def downgrade() -> None:
    run_sql(
        """
        DROP INDEX IF EXISTS uq_app_user_oidc_subject;
        ALTER TABLE app_user DROP COLUMN IF EXISTS oidc_subject;
        """
    )
