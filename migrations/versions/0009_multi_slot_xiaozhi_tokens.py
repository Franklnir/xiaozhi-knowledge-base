"""0009_multi_slot_xiaozhi_tokens

Revision ID: 0009_multi_slot_xiaozhi_tokens
Revises: 0008_user_playlists
Create Date: 2026-09-28 00:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009_multi_slot_xiaozhi_tokens'
down_revision: Union[str, None] = '0008_user_playlists'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tambah kolom slot_number dan device_label pada xiaozhi_tokens
    op.execute("""
        ALTER TABLE xiaozhi_tokens ADD COLUMN IF NOT EXISTS slot_number INT NOT NULL DEFAULT 1;
        ALTER TABLE xiaozhi_tokens ADD COLUMN IF NOT EXISTS device_label VARCHAR(60) NOT NULL DEFAULT 'XiaoZhi 1';

        -- Update constraint Primary Key menjadi komposit (user_id, slot_number)
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints 
                WHERE table_name = 'xiaozhi_tokens' AND constraint_type = 'PRIMARY KEY' AND constraint_name = 'xiaozhi_tokens_pkey'
            ) THEN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.key_column_usage 
                    WHERE table_name = 'xiaozhi_tokens' AND constraint_name = 'xiaozhi_tokens_pkey' AND column_name = 'slot_number'
                ) THEN
                    ALTER TABLE xiaozhi_tokens DROP CONSTRAINT xiaozhi_tokens_pkey;
                    ALTER TABLE xiaozhi_tokens ADD CONSTRAINT xiaozhi_tokens_pkey PRIMARY KEY (user_id, slot_number);
                END IF;
            ELSE
                ALTER TABLE xiaozhi_tokens ADD CONSTRAINT xiaozhi_tokens_pkey PRIMARY KEY (user_id, slot_number);
            END IF;
        END $$;

        -- Batasi maksimal 3 slot (1, 2, 3) secara ketat di level PostgreSQL
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.check_constraints 
                WHERE constraint_name = 'chk_xiaozhi_tokens_slot'
            ) THEN
                ALTER TABLE xiaozhi_tokens ADD CONSTRAINT chk_xiaozhi_tokens_slot CHECK (slot_number >= 1 AND slot_number <= 3);
            END IF;
        END $$;

        CREATE INDEX IF NOT EXISTS idx_tokens_user_slot ON xiaozhi_tokens(user_id, slot_number);
    """)

    # 2. Perkuat Row-Level Security (RLS) pada xiaozhi_tokens
    op.execute("""
        CREATE OR REPLACE FUNCTION app_current_user_id()
        RETURNS BIGINT LANGUAGE sql STABLE AS $$
            SELECT NULLIF(COALESCE(current_setting('app.user_id', true), current_setting('app.current_user_id', true)), '')::bigint;
        $$;

        CREATE OR REPLACE FUNCTION app_is_admin_or_system()
        RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
            SELECT COALESCE(
                current_setting('app.user_role', true) IN ('admin', 'system') 
                OR current_setting('app.is_admin', true) = 'true', 
                false
            );
        $$;

        ALTER TABLE xiaozhi_tokens ENABLE ROW LEVEL SECURITY;
        ALTER TABLE xiaozhi_tokens FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_xiaozhi_tokens_all ON xiaozhi_tokens;
        CREATE POLICY p_xiaozhi_tokens_all ON xiaozhi_tokens
        FOR ALL USING (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE xiaozhi_tokens DROP CONSTRAINT IF EXISTS chk_xiaozhi_tokens_slot;
        DROP INDEX IF EXISTS idx_tokens_user_slot;
        -- Revert primary key to (user_id)
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints 
                WHERE table_name = 'xiaozhi_tokens' AND constraint_type = 'PRIMARY KEY' AND constraint_name = 'xiaozhi_tokens_pkey'
            ) THEN
                ALTER TABLE xiaozhi_tokens DROP CONSTRAINT xiaozhi_tokens_pkey;
                ALTER TABLE xiaozhi_tokens ADD CONSTRAINT xiaozhi_tokens_pkey PRIMARY KEY (user_id);
            END IF;
        END $$;
        ALTER TABLE xiaozhi_tokens DROP COLUMN IF EXISTS device_label;
        ALTER TABLE xiaozhi_tokens DROP COLUMN IF EXISTS slot_number;
    """)
