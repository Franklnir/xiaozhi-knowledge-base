"""0007_board_binding_history

Revision ID: 0007_board_binding_history
Revises: 0006_complete_rls_all_tables
Create Date: 2026-09-27 02:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007_board_binding_history'
down_revision: Union[str, None] = '0006_complete_rls_all_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update registered_devices: allow owner_id to be NULL (so boards can be detached without deletion)
    op.execute("""
        ALTER TABLE registered_devices ALTER COLUMN owner_id DROP NOT NULL;
        ALTER TABLE registered_devices DROP CONSTRAINT IF EXISTS registered_devices_owner_id_fkey;
        ALTER TABLE registered_devices ADD CONSTRAINT registered_devices_owner_id_fkey 
            FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE SET NULL;
            
        ALTER TABLE registered_devices ADD COLUMN IF NOT EXISTS is_protected BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE registered_devices ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';
        ALTER TABLE registered_devices ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
    """)

    # 2. Tandai Board ID Master E8:3D:C1:9B:B5:14 sebagai protected (jangan pernah dihapus)
    op.execute("""
        UPDATE registered_devices 
        SET is_protected = TRUE 
        WHERE UPPER(device_id) = 'E8:3D:C1:9B:B5:14' 
           OR UPPER(REPLACE(REPLACE(device_id, ':', ''), '-', '')) = 'E83DC19BB514';
    """)

    # 3. Buat PostgreSQL Trigger untuk memblokir DELETE fisik pada board protected (E8:3D:C1:9B:B5:14)
    op.execute("""
        CREATE OR REPLACE FUNCTION protect_master_board_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.is_protected = TRUE 
               OR UPPER(OLD.device_id) = 'E8:3D:C1:9B:B5:14' 
               OR UPPER(REPLACE(REPLACE(OLD.device_id, ':', ''), '-', '')) = 'E83DC19BB514' THEN
                RAISE EXCEPTION 'Board ID % adalah perangkat terlindungi (protected) dan TIDAK BOLEH dihapus dari database. Gunakan detach/unpair.', OLD.device_id;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_protect_master_board ON registered_devices;
        CREATE TRIGGER trg_protect_master_board
        BEFORE DELETE ON registered_devices
        FOR EACH ROW
        EXECUTE FUNCTION protect_master_board_delete();
    """)

    # 4. Buat tabel board_binding_history (riwayat lengkap siapa, kapan pernah tertaut, kapan terakhir tertaut)
    op.execute("""
        CREATE TABLE IF NOT EXISTS board_binding_history (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            device_mac VARCHAR(32) NOT NULL,
            user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            username VARCHAR(50),
            device_name VARCHAR(100),
            device_type VARCHAR(50),
            linked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_active_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            unlinked_at TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_board_hist_mac ON board_binding_history(device_mac);
        CREATE INDEX IF NOT EXISTS idx_board_hist_user ON board_binding_history(user_id);
        CREATE INDEX IF NOT EXISTS idx_board_hist_status ON board_binding_history(status);
    """)

    # 5. Terapkan Native PostgreSQL RLS pada board_binding_history
    op.execute("""
        ALTER TABLE board_binding_history ENABLE ROW LEVEL SECURITY;
        ALTER TABLE board_binding_history FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_board_binding_history_all ON board_binding_history;
        CREATE POLICY p_board_binding_history_all ON board_binding_history
        FOR ALL USING (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            user_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 6. Backfill / migrasi awal data dari registered_devices ke board_binding_history
    op.execute("""
        INSERT INTO board_binding_history (device_mac, user_id, username, device_name, device_type, linked_at, last_active_at, status, notes)
        SELECT 
            rd.device_id, 
            rd.owner_id, 
            COALESCE(u.username, 'user_' || rd.owner_id), 
            rd.device_name, 
            rd.device_type, 
            rd.created_at, 
            CURRENT_TIMESTAMP, 
            'ACTIVE', 
            CASE 
                WHEN UPPER(rd.device_id) = 'E8:3D:C1:9B:B5:14' THEN 'Master Protected Board ID' 
                ELSE 'Initial registered board binding' 
            END
        FROM registered_devices rd
        LEFT JOIN users u ON rd.owner_id = u.id
        WHERE rd.owner_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM board_binding_history bbh 
            WHERE LOWER(bbh.device_mac) = LOWER(rd.device_id) AND bbh.user_id = rd.owner_id
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP POLICY IF EXISTS p_board_binding_history_all ON board_binding_history;
        ALTER TABLE IF EXISTS board_binding_history NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE IF EXISTS board_binding_history DISABLE ROW LEVEL SECURITY;
        DROP TABLE IF EXISTS board_binding_history;

        DROP TRIGGER IF EXISTS trg_protect_master_board ON registered_devices;
        DROP FUNCTION IF EXISTS protect_master_board_delete();

        ALTER TABLE registered_devices DROP COLUMN IF EXISTS last_active_at;
        ALTER TABLE registered_devices DROP COLUMN IF EXISTS status;
        ALTER TABLE registered_devices DROP COLUMN IF EXISTS is_protected;
    """)
