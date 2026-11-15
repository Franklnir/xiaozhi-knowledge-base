"""Enforce material, live API, and relay room quotas in PostgreSQL."""

from alembic import op

revision = "0013_tenant_resource_quotas"
down_revision = "0012_access_plus_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS p_user_limits_all ON public.user_limits;
        CREATE POLICY p_user_limits_owner_read ON public.user_limits
            FOR SELECT USING (user_id = public.app_current_user_id());
        CREATE POLICY p_user_limits_admin ON public.user_limits
            FOR ALL USING (public.app_is_admin_or_system())
            WITH CHECK (public.app_is_admin_or_system());

        CREATE FUNCTION public.lock_resource_quota(account_id bigint)
        RETURNS void LANGUAGE sql VOLATILE SET search_path = pg_catalog AS $$
            SELECT pg_advisory_xact_lock(hashtextextended('xiaozhi:resource-quota:' || account_id, 0));
        $$;

        CREATE FUNCTION public.lock_user_limit_change()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                PERFORM public.lock_resource_quota(OLD.user_id);
                RETURN OLD;
            END IF;
            PERFORM public.lock_resource_quota(NEW.user_id);
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_lock_user_limit_change
            BEFORE INSERT OR UPDATE OR DELETE ON public.user_limits
            FOR EACH ROW EXECUTE FUNCTION public.lock_user_limit_change();

        CREATE FUNCTION public.enforce_material_quota()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE
            material_limit int;
            words_limit int;
            api_limit int;
            new_material boolean;
            new_live_api boolean;
        BEGIN
            IF EXISTS (SELECT 1 FROM public.users WHERE id = NEW.owner_id AND role = 'admin') THEN
                RETURN NEW;
            END IF;

            PERFORM public.lock_resource_quota(NEW.owner_id);
            SELECT COALESCE(max_materials, 3), COALESCE(max_words_per_material, 6000),
                   COALESCE(max_live_apis, 2)
            INTO material_limit, words_limit, api_limit
            FROM (SELECT NEW.owner_id AS id) account
            LEFT JOIN public.user_limits ON user_id = account.id;

            new_material := TG_OP = 'INSERT';
            new_live_api := NEW.source_type IS NOT DISTINCT FROM 'api_live';
            IF TG_OP = 'UPDATE' THEN
                new_material := OLD.owner_id <> NEW.owner_id;
                new_live_api := new_live_api AND (
                    new_material OR OLD.source_type IS DISTINCT FROM 'api_live'
                );
            END IF;

            IF new_material AND material_limit > 0 AND
                (SELECT COUNT(*) FROM public.materials WHERE owner_id = NEW.owner_id) >= material_limit
            THEN
                RAISE EXCEPTION 'Batas materi akun Anda telah tercapai (% materi).', material_limit
                    USING ERRCODE = '23514', CONSTRAINT = 'quota_material_count';
            END IF;

            IF new_live_api AND api_limit > 0 AND
                (SELECT COUNT(*) FROM public.materials
                 WHERE owner_id = NEW.owner_id AND source_type = 'api_live') >= api_limit
            THEN
                RAISE EXCEPTION 'Batas API realtime akun Anda telah tercapai (% API).', api_limit
                    USING ERRCODE = '23514', CONSTRAINT = 'quota_live_api_count';
            END IF;

            IF words_limit > 0 AND regexp_count(
                NEW.content,
                '[^[:space:]' || chr(28) || chr(29) || chr(30) || chr(31) || chr(133) || chr(160)
                || chr(5760) || chr(8192) || '-' || chr(8202) || chr(8232) || chr(8233)
                || chr(8239) || chr(8287) || chr(12288) || ']+'
            ) > words_limit
            THEN
                RAISE EXCEPTION 'Konten melebihi batas kata per materi (% kata).', words_limit
                    USING ERRCODE = '23514', CONSTRAINT = 'quota_material_words';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_enforce_material_quota
            BEFORE INSERT OR UPDATE OF owner_id, source_type, content ON public.materials
            FOR EACH ROW EXECUTE FUNCTION public.enforce_material_quota();

        CREATE FUNCTION public.enforce_relay_room_quota()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE
            room_limit int;
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.owner_id = NEW.owner_id THEN
                RETURN NEW;
            END IF;
            IF EXISTS (SELECT 1 FROM public.users WHERE id = NEW.owner_id AND role = 'admin') THEN
                RETURN NEW;
            END IF;

            PERFORM public.lock_resource_quota(NEW.owner_id);
            SELECT COALESCE((SELECT max_relay_rooms FROM public.user_limits WHERE user_id = NEW.owner_id), 7)
            INTO room_limit;
            IF room_limit > 0 AND
                (SELECT COUNT(*) FROM public.relay_rooms WHERE owner_id = NEW.owner_id) >= room_limit
            THEN
                RAISE EXCEPTION 'Batas perangkat Relay Nyata akun Anda telah tercapai (% ruangan).', room_limit
                    USING ERRCODE = '23514', CONSTRAINT = 'quota_relay_room_count';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_enforce_relay_room_quota
            BEFORE INSERT OR UPDATE OF owner_id ON public.relay_rooms
            FOR EACH ROW EXECUTE FUNCTION public.enforce_relay_room_quota();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER trg_enforce_relay_room_quota ON public.relay_rooms;
        DROP TRIGGER trg_enforce_material_quota ON public.materials;
        DROP TRIGGER trg_lock_user_limit_change ON public.user_limits;
        DROP FUNCTION public.enforce_relay_room_quota();
        DROP FUNCTION public.enforce_material_quota();
        DROP FUNCTION public.lock_user_limit_change();
        DROP FUNCTION public.lock_resource_quota(bigint);

        DROP POLICY p_user_limits_admin ON public.user_limits;
        DROP POLICY p_user_limits_owner_read ON public.user_limits;
        CREATE POLICY p_user_limits_all ON public.user_limits FOR ALL
            USING (user_id = public.app_current_user_id() OR public.app_is_admin_or_system())
            WITH CHECK (user_id = public.app_current_user_id() OR public.app_is_admin_or_system());
        """
    )
