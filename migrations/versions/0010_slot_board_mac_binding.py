"""0010_slot_board_mac_binding

Revision ID: 0010_slot_board_mac_binding
Revises: 0009_multi_slot_xiaozhi_tokens
Create Date: 2026-09-28 01:20:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010_slot_board_mac_binding'
down_revision: Union[str, None] = '0009_multi_slot_xiaozhi_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tambah kolom board_mac pada xiaozhi_tokens (kunci hardware MAC ke slot)
    op.execute("""
        ALTER TABLE xiaozhi_tokens ADD COLUMN IF NOT EXISTS board_mac VARCHAR(32) NOT NULL DEFAULT '';
        CREATE INDEX IF NOT EXISTS idx_tokens_board_mac ON xiaozhi_tokens(board_mac);

        -- Tambah kolom device_mac dan slot_number pada chat_history jika belum ada
        ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS device_mac VARCHAR(32) DEFAULT '';
        ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS slot_number INT DEFAULT 1;
        CREATE INDEX IF NOT EXISTS idx_chat_device_mac ON chat_history(device_mac);
        CREATE INDEX IF NOT EXISTS idx_chat_slot_number ON chat_history(owner_id, slot_number);

        -- 2. Auto-Migration Graceful Fallback:
        -- Tautkan MAC lama dari registered_devices ke Slot 1 user jika slot 1 belum memiliki board_mac
        UPDATE xiaozhi_tokens t
        SET board_mac = UPPER(d.device_id)
        FROM (
            SELECT DISTINCT ON (owner_id) owner_id, device_id
            FROM registered_devices
            WHERE owner_id IS NOT NULL AND device_id IS NOT NULL AND status = 'ACTIVE'
            ORDER BY owner_id, id DESC
        ) d
        WHERE t.user_id = d.owner_id
          AND t.slot_number = 1
          AND (t.board_mac = '' OR t.board_mac IS NULL);

        -- Update label default 'XiaoZhi 1' menjadi '[username] - Slot 1' agar ramah pengguna
        UPDATE xiaozhi_tokens t
        SET device_label = u.username || ' - Slot 1'
        FROM users u
        WHERE t.user_id = u.id
          AND t.slot_number = 1
          AND (t.device_label = 'XiaoZhi 1' OR t.device_label = '' OR t.device_label IS NULL);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS idx_chat_slot_number;
        DROP INDEX IF EXISTS idx_chat_device_mac;
        ALTER TABLE chat_history DROP COLUMN IF EXISTS slot_number;
        ALTER TABLE chat_history DROP COLUMN IF EXISTS device_mac;
        DROP INDEX IF EXISTS idx_tokens_board_mac;
        ALTER TABLE xiaozhi_tokens DROP COLUMN IF EXISTS board_mac;
    """)
