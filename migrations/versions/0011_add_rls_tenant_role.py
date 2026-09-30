"""Add a non-login role used to verify and exercise row-level security.

Revision ID: 0011_add_rls_tenant_role
Revises: 0010_slot_board_mac_binding
"""

from alembic import op

revision = "0011_add_rls_tenant_role"
down_revision = "0010_slot_board_mac_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_tenant_role') THEN
                CREATE ROLE app_tenant_role NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
        END
        $$;

        GRANT app_tenant_role TO CURRENT_USER;
        GRANT USAGE ON SCHEMA public TO app_tenant_role;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_tenant_role;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_tenant_role;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_tenant_role;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO app_tenant_role;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE app_tenant_role FROM CURRENT_USER;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM app_tenant_role;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM app_tenant_role;
        REVOKE USAGE ON SCHEMA public FROM app_tenant_role;
        """
    )
