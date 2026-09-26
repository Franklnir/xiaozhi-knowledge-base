"""0006_complete_rls_all_tables

Revision ID: 0006_complete_rls_all_tables
Revises: 0005_enable_rls_iot_privacy
Create Date: 2026-09-27 01:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006_complete_rls_all_tables'
down_revision: Union[str, None] = '0005_enable_rls_iot_privacy'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. categories: Mengisolasi kategori materi per akun (owner_id)
    op.execute("""
        ALTER TABLE categories ENABLE ROW LEVEL SECURITY;
        ALTER TABLE categories FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_categories_all ON categories;
        CREATE POLICY p_categories_all ON categories
        FOR ALL USING (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 2. audio_queue: Mengisolasi antrean lagu/audio streaming per akun (owner_id)
    op.execute("""
        ALTER TABLE audio_queue ENABLE ROW LEVEL SECURITY;
        ALTER TABLE audio_queue FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_audio_queue_all ON audio_queue;
        CREATE POLICY p_audio_queue_all ON audio_queue
        FOR ALL USING (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 3. mcp_user_settings: Mengisolasi preferensi personal MCP tools per user (user_id)
    op.execute("""
        ALTER TABLE mcp_user_settings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mcp_user_settings FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_mcp_user_settings_all ON mcp_user_settings;
        CREATE POLICY p_mcp_user_settings_all ON mcp_user_settings
        FOR ALL USING (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 4. mcp_tool_toggles: Mengisolasi toggle aktif/nonaktif tool per user (user_id)
    op.execute("""
        ALTER TABLE mcp_tool_toggles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mcp_tool_toggles FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_mcp_tool_toggles_all ON mcp_tool_toggles;
        CREATE POLICY p_mcp_tool_toggles_all ON mcp_tool_toggles
        FOR ALL USING (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 5. user_limits: Mengisolasi batas pemakaian/kuota per user (user_id)
    op.execute("""
        ALTER TABLE user_limits ENABLE ROW LEVEL SECURITY;
        ALTER TABLE user_limits FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_user_limits_all ON user_limits;
        CREATE POLICY p_user_limits_all ON user_limits
        FOR ALL USING (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)


def downgrade() -> None:
    tables = [
        ("categories", "p_categories_all"),
        ("audio_queue", "p_audio_queue_all"),
        ("mcp_user_settings", "p_mcp_user_settings_all"),
        ("mcp_tool_toggles", "p_mcp_tool_toggles_all"),
        ("user_limits", "p_user_limits_all"),
    ]
    for tbl, pol in tables:
        op.execute(f"""
            DROP POLICY IF EXISTS {pol} ON {tbl};
            ALTER TABLE IF EXISTS {tbl} NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE IF EXISTS {tbl} DISABLE ROW LEVEL SECURITY;
        """)
