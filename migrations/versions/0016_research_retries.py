"""Link retries to their original Deep Riset session."""

from alembic import op

from xiaozhi.database.research_store import RESEARCH_RETRY_INDEX

revision = "0016_research_retries"
down_revision = "0015_deep_research"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE public.research_runs ADD COLUMN retry_of INTEGER")
    op.execute(RESEARCH_RETRY_INDEX)


def downgrade():
    op.drop_index("research_one_retry_per_session", table_name="research_runs")
    op.drop_column("research_runs", "retry_of")
