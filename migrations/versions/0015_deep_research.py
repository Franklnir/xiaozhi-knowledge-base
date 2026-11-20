"""Persist isolated Deep Riset topics, sessions, and source references."""

from alembic import op

from xiaozhi.database.research_store import RESEARCH_SCHEMA

revision = "0015_deep_research"
down_revision = "0014_private_account_rls"
branch_labels = None
depends_on = None

TABLES = ("research_counters", "research_topics", "research_runs", "research_sources")


def upgrade():
    for statement in RESEARCH_SCHEMA.strip().split(";"):
        if statement.strip():
            op.execute(statement)
    for table in TABLES:
        op.execute(f"""
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO app_tenant_role;
            CREATE POLICY own_research ON public.{table} FOR ALL TO app_tenant_role
                USING (owner_id = public.app_current_user_id())
                WITH CHECK (owner_id = public.app_current_user_id());
        """)


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
