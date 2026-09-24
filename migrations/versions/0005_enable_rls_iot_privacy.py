"""0005_enable_rls_iot_privacy

Revision ID: 0005_enable_rls_iot_privacy
Revises: 0004_add_stl_assets_support
Create Date: 2026-09-24 18:50:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005_enable_rls_iot_privacy'
down_revision: Union[str, None] = '0004_add_stl_assets_support'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. registered_devices: Melindungi MAC Address & ID Perangkat ESP32
    op.execute("""
        ALTER TABLE registered_devices ENABLE ROW LEVEL SECURITY;
        ALTER TABLE registered_devices FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_registered_devices_all ON registered_devices;
        CREATE POLICY p_registered_devices_all ON registered_devices
        FOR ALL USING (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 2. chat_history: Mengisolasi total log percakapan suara AI XiaoZhi per akun
    op.execute("""
        ALTER TABLE chat_history ENABLE ROW LEVEL SECURITY;
        ALTER TABLE chat_history FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_chat_history_all ON chat_history;
        CREATE POLICY p_chat_history_all ON chat_history
        FOR ALL USING (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 3. xiaozhi_tokens: Mengunci token autentikasi firmware/hardware ESP32
    op.execute("""
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

    # 4. relay_rooms: Mencegah pembajakan kamar/ruang Smart Home fisik
    op.execute("""
        ALTER TABLE relay_rooms ENABLE ROW LEVEL SECURITY;
        ALTER TABLE relay_rooms FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_relay_rooms_all ON relay_rooms;
        CREATE POLICY p_relay_rooms_all ON relay_rooms
        FOR ALL USING (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 5. relay_devices: Mencegah kontrol saklar listrik nyata IoT milik orang lain
    op.execute("""
        ALTER TABLE relay_devices ENABLE ROW LEVEL SECURITY;
        ALTER TABLE relay_devices FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_relay_devices_all ON relay_devices;
        CREATE POLICY p_relay_devices_all ON relay_devices
        FOR ALL USING (
            app_is_admin_or_system() OR EXISTS (
                SELECT 1 FROM relay_rooms r
                WHERE r.id = relay_devices.room_id
                  AND r.owner_id = app_current_user_id()
            )
        ) WITH CHECK (
            app_is_admin_or_system() OR EXISTS (
                SELECT 1 FROM relay_rooms r
                WHERE r.id = relay_devices.room_id
                  AND r.owner_id = app_current_user_id()
            )
        );
    """)

    # 6. materials: Melindungi dokumen rahasia Knowledge Base / RAG kustom
    op.execute("""
        ALTER TABLE materials ENABLE ROW LEVEL SECURITY;
        ALTER TABLE materials FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_materials_all ON materials;
        CREATE POLICY p_materials_all ON materials
        FOR ALL USING (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 7. user_persona: Menjaga kerahasiaan memori profil & preferensi AI pribadi
    op.execute("""
        ALTER TABLE user_persona ENABLE ROW LEVEL SECURITY;
        ALTER TABLE user_persona FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_user_persona_all ON user_persona;
        CREATE POLICY p_user_persona_all ON user_persona
        FOR ALL USING (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)

    # 8. reminders: Mengisolasi jadwal pengingat & alarm IoT
    op.execute("""
        ALTER TABLE reminders ENABLE ROW LEVEL SECURITY;
        ALTER TABLE reminders FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_reminders_all ON reminders;
        CREATE POLICY p_reminders_all ON reminders
        FOR ALL USING (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        ) WITH CHECK (
            owner_id = app_current_user_id() OR app_is_admin_or_system()
        );
    """)


def downgrade() -> None:
    tables = [
        ("registered_devices", "p_registered_devices_all"),
        ("chat_history", "p_chat_history_all"),
        ("xiaozhi_tokens", "p_xiaozhi_tokens_all"),
        ("relay_rooms", "p_relay_rooms_all"),
        ("relay_devices", "p_relay_devices_all"),
        ("materials", "p_materials_all"),
        ("user_persona", "p_user_persona_all"),
        ("reminders", "p_reminders_all"),
    ]
    for tbl, pol in tables:
        op.execute(f"""
            DROP POLICY IF EXISTS {pol} ON {tbl};
            ALTER TABLE IF EXISTS {tbl} NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE IF EXISTS {tbl} DISABLE ROW LEVEL SECURITY;
        """)
