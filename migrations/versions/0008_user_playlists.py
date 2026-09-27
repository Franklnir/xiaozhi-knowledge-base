"""0008_user_playlists

Revision ID: 0008_user_playlists
Revises: 0007_board_binding_history
Create Date: 2026-09-27 22:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008_user_playlists'
down_revision: Union[str, None] = '0007_board_binding_history'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Buat tabel user_playlists
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_playlists (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            track_number INT NOT NULL DEFAULT 1,
            title VARCHAR(255) NOT NULL,
            youtube_url TEXT NOT NULL,
            video_id VARCHAR(64) NOT NULL,
            artist VARCHAR(255) DEFAULT '',
            duration VARCHAR(50) DEFAULT '',
            play_count INT NOT NULL DEFAULT 0,
            last_played_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_user_playlists_owner ON user_playlists(owner_id, track_number);
        CREATE INDEX IF NOT EXISTS idx_user_playlists_video ON user_playlists(video_id);
        CREATE INDEX IF NOT EXISTS idx_user_playlists_top ON user_playlists(owner_id, play_count DESC);
    """)

    # 2. Aktifkan Row-Level Security (RLS) pada user_playlists
    op.execute("""
        ALTER TABLE user_playlists ENABLE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_user_playlists_owner ON user_playlists;
        CREATE POLICY p_user_playlists_owner ON user_playlists
            FOR ALL
            USING (
                owner_id = NULLIF(current_setting('app.current_user_id', true), '')::bigint
                OR current_setting('app.is_admin', true) = 'true'
            )
            WITH CHECK (
                owner_id = NULLIF(current_setting('app.current_user_id', true), '')::bigint
                OR current_setting('app.is_admin', true) = 'true'
            );
    """)


def downgrade() -> None:
    op.execute("""
        DROP POLICY IF EXISTS p_user_playlists_owner ON user_playlists;
        DROP TABLE IF EXISTS user_playlists CASCADE;
    """)
