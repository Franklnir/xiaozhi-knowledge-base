"""0011_access_plus_controls

Revision ID: 0011_access_plus_controls
Revises: 0010_slot_board_mac_binding
Create Date: 2026-10-07 18:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011_access_plus_controls'
down_revision: Union[str, None] = '0010_slot_board_mac_binding'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tambah kolom is_active pada xiaozhi_tokens dan user_playlists
    op.execute("""
        ALTER TABLE xiaozhi_tokens ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
        CREATE INDEX IF NOT EXISTS idx_tokens_active ON xiaozhi_tokens(user_id, slot_number, is_active);

        ALTER TABLE user_playlists ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
        CREATE INDEX IF NOT EXISTS idx_user_playlists_active ON user_playlists(owner_id, is_active);

        -- 2. Buat tabel user_access_plus_settings
        CREATE TABLE IF NOT EXISTS user_access_plus_settings (
            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            mcp_multislot_allowed BOOLEAN NOT NULL DEFAULT TRUE,
            playlist_quota_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            max_playlist_tracks INT NOT NULL DEFAULT 15,
            notes TEXT DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_access_plus_user ON user_access_plus_settings(user_id);

        -- 3. RLS untuk user_access_plus_settings
        ALTER TABLE user_access_plus_settings ENABLE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_access_plus_all ON user_access_plus_settings;
        CREATE POLICY p_access_plus_all ON user_access_plus_settings
            FOR ALL
            USING (
                user_id = NULLIF(current_setting('app.current_user_id', true), '')::bigint
                OR current_setting('app.is_admin', true) = 'true'
                OR current_setting('app.user_role', true) IN ('admin', 'system')
            )
            WITH CHECK (
                user_id = NULLIF(current_setting('app.current_user_id', true), '')::bigint
                OR current_setting('app.is_admin', true) = 'true'
                OR current_setting('app.user_role', true) IN ('admin', 'system')
            );
    """)


def downgrade() -> None:
    op.execute("""
        DROP POLICY IF EXISTS p_access_plus_all ON user_access_plus_settings;
        DROP TABLE IF EXISTS user_access_plus_settings CASCADE;
        DROP INDEX IF EXISTS idx_user_playlists_active;
        ALTER TABLE user_playlists DROP COLUMN IF EXISTS is_active;
        DROP INDEX IF EXISTS idx_tokens_active;
        ALTER TABLE xiaozhi_tokens DROP COLUMN IF EXISTS is_active;
    """)
