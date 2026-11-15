"""Enforce account isolation for MCP, memory, reminders, and feature access."""

from alembic import op

revision = "0014_private_account_rls"
down_revision = "0013_tenant_resource_quotas"
branch_labels = None
depends_on = None

ADMIN_SETTINGS = {
    "mcp_user_settings": "p_mcp_user_settings_all",
    "mcp_tool_toggles": "p_mcp_tool_toggles_all",
    "user_access_plus_settings": "p_access_plus_all",
}


def upgrade() -> None:
    for table, policy in ADMIN_SETTINGS.items():
        op.execute(f"""
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            DROP POLICY IF EXISTS {policy} ON public.{table};
            CREATE POLICY p_owner_read ON public.{table} FOR SELECT
                USING (user_id = public.app_current_user_id());
            CREATE POLICY p_admin_manage ON public.{table} FOR ALL
                USING (public.app_is_admin_or_system())
                WITH CHECK (public.app_is_admin_or_system());
        """)
    op.execute("""
        ALTER TABLE public.user_access_plus_settings
            ADD COLUMN disabled_mcp_slots smallint[] NOT NULL DEFAULT '{}';
        INSERT INTO public.user_access_plus_settings (user_id, disabled_mcp_slots)
            SELECT t.user_id, array_agg(t.slot_number::smallint ORDER BY t.slot_number)
            FROM public.xiaozhi_tokens t
            LEFT JOIN public.user_access_plus_settings a ON a.user_id = t.user_id
            WHERE NOT t.is_active AND (t.slot_number = 1 OR COALESCE(a.mcp_multislot_allowed, true))
            GROUP BY t.user_id
            ON CONFLICT (user_id) DO UPDATE SET disabled_mcp_slots = EXCLUDED.disabled_mcp_slots;

        ALTER TABLE public.feature_settings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.feature_settings FORCE ROW LEVEL SECURITY;
        CREATE POLICY p_feature_owner_read ON public.feature_settings FOR SELECT
            USING (user_id = public.app_current_user_id());
        CREATE POLICY p_feature_owner_insert ON public.feature_settings FOR INSERT
            WITH CHECK (user_id = public.app_current_user_id());
        CREATE POLICY p_feature_owner_update ON public.feature_settings FOR UPDATE
            USING (user_id = public.app_current_user_id())
            WITH CHECK (user_id = public.app_current_user_id());
        CREATE POLICY p_feature_admin_manage ON public.feature_settings FOR ALL
            USING (public.app_is_admin_or_system())
            WITH CHECK (public.app_is_admin_or_system());

        CREATE FUNCTION public.protect_managed_features()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NOT public.app_is_admin_or_system() THEN
                IF (TG_OP = 'INSERT' AND NEW.youtube_music_enabled IS DISTINCT FROM TRUE) OR
                   (TG_OP = 'UPDATE' AND NEW.youtube_music_enabled IS DISTINCT FROM OLD.youtube_music_enabled)
                THEN
                    RAISE EXCEPTION 'Pengaturan YouTube Music hanya dapat diubah admin.' USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_protect_managed_features
            BEFORE INSERT OR UPDATE ON public.feature_settings
            FOR EACH ROW EXECUTE FUNCTION public.protect_managed_features();

        CREATE FUNCTION public.protect_managed_slot_access()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NOT public.app_is_admin_or_system() THEN
                IF (TG_OP = 'INSERT' AND NEW.is_active IS DISTINCT FROM TRUE) OR
                   (TG_OP = 'UPDATE' AND NEW.is_active IS DISTINCT FROM OLD.is_active)
                THEN
                    RAISE EXCEPTION 'Status aktif slot hanya dapat diubah admin.' USING ERRCODE = '42501';
                END IF;
                IF (TG_OP = 'INSERT' OR NEW.slot_number <> OLD.slot_number)
                   AND EXISTS (SELECT 1 FROM public.user_access_plus_settings a
                               WHERE a.user_id = NEW.user_id AND (
                                   (NEW.slot_number > 1 AND NOT a.mcp_multislot_allowed)
                                   OR NEW.slot_number = ANY(a.disabled_mcp_slots)
                               ))
                THEN
                    RAISE EXCEPTION 'Slot tambahan dibatasi oleh admin.' USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_protect_managed_slot_access
            BEFORE INSERT OR UPDATE ON public.xiaozhi_tokens
            FOR EACH ROW EXECUTE FUNCTION public.protect_managed_slot_access();

        CREATE FUNCTION public.release_owned_boards(mac text, released_at timestamptz)
        RETURNS integer LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            WITH released AS (
                UPDATE public.registered_devices
                SET owner_id = NULL, status = 'DETACHED', last_active_at = released_at
                WHERE owner_id = public.app_current_user_id() AND (
                    mac IS NULL OR lower(translate(device_id, ':-', '')) = lower(translate(mac, ':-', ''))
                )
                RETURNING 1
            )
            SELECT count(*)::integer FROM released;
        $$;
        REVOKE ALL ON FUNCTION public.release_owned_boards(text, timestamptz) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.release_owned_boards(text, timestamptz) TO app_tenant_role;

        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_mcp_connector') THEN
                CREATE ROLE app_mcp_connector NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_reminder_worker') THEN
                CREATE ROLE app_reminder_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
        END
        $$;
        GRANT app_mcp_connector, app_reminder_worker TO CURRENT_USER;
        GRANT USAGE ON SCHEMA public TO app_mcp_connector, app_reminder_worker;
        GRANT SELECT ON public.xiaozhi_tokens TO app_mcp_connector;
        GRANT SELECT (user_id, mcp_blocked) ON public.mcp_user_settings TO app_mcp_connector;
        GRANT SELECT (user_id, mcp_multislot_allowed, disabled_mcp_slots) ON public.user_access_plus_settings TO app_mcp_connector;
        CREATE POLICY p_connector_block_state ON public.mcp_user_settings FOR SELECT TO app_mcp_connector
            USING (true);
        CREATE POLICY p_connector_slot_entitlement ON public.user_access_plus_settings FOR SELECT TO app_mcp_connector
            USING (true);
        CREATE POLICY p_mcp_connector_read ON public.xiaozhi_tokens FOR SELECT TO app_mcp_connector
            USING (is_active AND NOT EXISTS (
                SELECT 1 FROM public.mcp_user_settings s WHERE s.user_id = xiaozhi_tokens.user_id AND s.mcp_blocked
            ) AND NOT EXISTS (
                SELECT 1 FROM public.user_access_plus_settings a
                WHERE a.user_id = xiaozhi_tokens.user_id AND (
                    (xiaozhi_tokens.slot_number > 1 AND NOT a.mcp_multislot_allowed)
                    OR xiaozhi_tokens.slot_number = ANY(a.disabled_mcp_slots)
                )
            ));

        GRANT SELECT, UPDATE (status) ON public.reminders TO app_reminder_worker;
        CREATE POLICY p_reminder_worker_read ON public.reminders FOR SELECT TO app_reminder_worker
            USING (scheduled_at <= CURRENT_TIMESTAMP AND status IN ('pending', 'processing'));
        CREATE POLICY p_reminder_worker_delivery ON public.reminders FOR UPDATE TO app_reminder_worker
            USING (scheduled_at <= CURRENT_TIMESTAMP AND status = 'pending')
            WITH CHECK (scheduled_at <= CURRENT_TIMESTAMP AND status = 'processing');

        CREATE FUNCTION public.complete_due_reminder(reminder_id text, delivered_at timestamptz)
        RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            UPDATE public.reminders SET status = 'sent', sent_at = delivered_at
            WHERE id = reminder_id AND status = 'processing' AND scheduled_at <= CURRENT_TIMESTAMP;
        $$;
        REVOKE ALL ON FUNCTION public.complete_due_reminder(text, timestamptz) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.complete_due_reminder(text, timestamptz) TO app_reminder_worker;
    """)


def downgrade() -> None:
    op.execute("""
        DROP POLICY p_mcp_connector_read ON public.xiaozhi_tokens;
        DROP POLICY p_connector_block_state ON public.mcp_user_settings;
        DROP POLICY p_connector_slot_entitlement ON public.user_access_plus_settings;
        DROP POLICY p_reminder_worker_read ON public.reminders;
        DROP POLICY p_reminder_worker_delivery ON public.reminders;
        REVOKE SELECT ON public.xiaozhi_tokens FROM app_mcp_connector;
        REVOKE SELECT (user_id, mcp_blocked) ON public.mcp_user_settings FROM app_mcp_connector;
        REVOKE SELECT (user_id, mcp_multislot_allowed, disabled_mcp_slots) ON public.user_access_plus_settings FROM app_mcp_connector;
        REVOKE SELECT, UPDATE (status) ON public.reminders FROM app_reminder_worker;
        DROP FUNCTION public.complete_due_reminder(text, timestamptz);
        REVOKE USAGE ON SCHEMA public FROM app_mcp_connector, app_reminder_worker;

        DROP TRIGGER trg_protect_managed_slot_access ON public.xiaozhi_tokens;
        DROP FUNCTION public.protect_managed_slot_access();
        DROP FUNCTION public.release_owned_boards(text, timestamptz);
        DROP TRIGGER trg_protect_managed_features ON public.feature_settings;
        DROP FUNCTION public.protect_managed_features();
        DROP POLICY p_feature_owner_read ON public.feature_settings;
        DROP POLICY p_feature_owner_insert ON public.feature_settings;
        DROP POLICY p_feature_owner_update ON public.feature_settings;
        DROP POLICY p_feature_admin_manage ON public.feature_settings;
        ALTER TABLE public.user_access_plus_settings DROP COLUMN disabled_mcp_slots;
        ALTER TABLE public.feature_settings NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.feature_settings DISABLE ROW LEVEL SECURITY;
        ALTER TABLE public.user_access_plus_settings NO FORCE ROW LEVEL SECURITY;
    """)
    for table, policy in ADMIN_SETTINGS.items():
        op.execute(f"""
            DROP POLICY p_owner_read ON public.{table};
            DROP POLICY p_admin_manage ON public.{table};
            CREATE POLICY {policy} ON public.{table} FOR ALL
                USING (user_id = public.app_current_user_id() OR public.app_is_admin_or_system())
                WITH CHECK (user_id = public.app_current_user_id() OR public.app_is_admin_or_system());
        """)
