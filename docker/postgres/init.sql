-- =============================================================================
-- Xiaozhi Indonesia - PostgreSQL Production Initialization Schema
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "unaccent";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- 1. USERS
CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    session_version INT NOT NULL DEFAULT 1,
    ui_theme VARCHAR(30) NOT NULL DEFAULT 'neo',
    google_id VARCHAR(100),
    google_email VARCHAR(255),
    registered_with_google BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ,
    CONSTRAINT chk_user_role CHECK (role IN ('user', 'admin', 'operator'))
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;

-- 2. CATEGORIES
CREATE TABLE IF NOT EXISTS categories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_categories_owner_name UNIQUE (owner_id, name)
);
CREATE INDEX IF NOT EXISTS idx_categories_owner ON categories(owner_id);

-- 3. MATERIALS
CREATE TABLE IF NOT EXISTS materials (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    category VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT,
    source_type VARCHAR(50),
    source_hash VARCHAR(64),
    source_key VARCHAR(128),
    api_url_ciphertext TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ,
    search_vector tsvector
);
CREATE INDEX IF NOT EXISTS idx_materials_owner ON materials(owner_id);
CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(owner_id, category);
CREATE INDEX IF NOT EXISTS idx_materials_source ON materials(source_hash, source_key);
CREATE INDEX IF NOT EXISTS idx_materials_created ON materials(owner_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_materials_owner_source_hash ON materials (owner_id, source_hash) 
    WHERE source_hash IS NOT NULL AND source_hash != '';

-- PL/pgSQL function & trigger for search_vector (bypasses unaccent immutability limitation)
CREATE OR REPLACE FUNCTION trg_materials_search_vector_fn()
RETURNS trigger 
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_vector := 
        setweight(to_tsvector('indonesian', coalesce(unaccent(NEW.title), '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(NEW.keywords, '')), 'B') ||
        setweight(to_tsvector('indonesian', coalesce(unaccent(NEW.content), '')), 'C');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_materials_search_vector_update ON materials;
CREATE TRIGGER trg_materials_search_vector_update
    BEFORE INSERT OR UPDATE OF title, keywords, content 
    ON materials
    FOR EACH ROW
    EXECUTE FUNCTION trg_materials_search_vector_fn();

CREATE INDEX IF NOT EXISTS idx_materials_search_vector ON materials USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS idx_materials_title_trgm ON materials USING GIN (title gin_trgm_ops);

-- 4. XIAOZHI TOKENS (MCP Server Endpoints)
CREATE TABLE IF NOT EXISTS xiaozhi_tokens (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_ciphertext TEXT NOT NULL,
    token_hash VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tokens_hash ON xiaozhi_tokens(token_hash);

-- 5. CHAT HISTORY (Logging Tool MCP & Percakapan)
CREATE TABLE IF NOT EXISTS chat_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64),
    source VARCHAR(50) NOT NULL DEFAULT 'mcp_tool',
    tool_name VARCHAR(100) NOT NULL,
    user_message TEXT,
    xiaozhi_answer TEXT,
    request_payload JSONB,
    response_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_owner ON chat_history(owner_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_chat_token ON chat_history(token_hash);
CREATE INDEX IF NOT EXISTS idx_chat_tool ON chat_history(tool_name);

-- 6. RELAY ROOMS (ESP32 Smart Home Rooms)
CREATE TABLE IF NOT EXISTS relay_rooms (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nama_tempat VARCHAR(100) NOT NULL,
    api_slug VARCHAR(100) UNIQUE NOT NULL,
    api_token_ciphertext TEXT,
    api_token_hash VARCHAR(64),
    api_client_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_relay_owner ON relay_rooms(owner_id);
CREATE INDEX IF NOT EXISTS idx_relay_slug ON relay_rooms(api_slug);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_rooms_token_hash ON relay_rooms (api_token_hash) WHERE api_token_hash IS NOT NULL;

-- 7. RELAY DEVICES (Individual Relays)
CREATE TABLE IF NOT EXISTS relay_devices (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id BIGINT NOT NULL REFERENCES relay_rooms(id) ON DELETE CASCADE,
    relay_number SMALLINT NOT NULL,
    nama_relay VARCHAR(100),
    voice_command_on VARCHAR(150),
    voice_command_off VARCHAR(150),
    status VARCHAR(10) NOT NULL DEFAULT 'OFF',
    CONSTRAINT uq_room_relay_number UNIQUE (room_id, relay_number),
    CONSTRAINT chk_relay_device_status CHECK (status IN ('ON', 'OFF'))
);
CREATE INDEX IF NOT EXISTS idx_relay_device_room ON relay_devices(room_id);

-- 8. AUDIO QUEUE
CREATE TABLE IF NOT EXISTS audio_queue (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255),
    stream_url TEXT NOT NULL,
    video_url TEXT,
    duration VARCHAR(50),
    video_id VARCHAR(50),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_audio_queue_status CHECK (status IN ('pending', 'playing', 'done', 'error', 'played', 'stopped', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_audio_owner ON audio_queue(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_audio_queue_active ON audio_queue (owner_id, id ASC) WHERE status = 'pending';

-- 9. REGISTERED DEVICES
CREATE TABLE IF NOT EXISTS registered_devices (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id VARCHAR(100) NOT NULL,
    device_name VARCHAR(100),
    device_type VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_device_owner ON registered_devices(owner_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_device_id ON registered_devices(device_id);

-- 10. FEATURE SETTINGS
CREATE TABLE IF NOT EXISTS feature_settings (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    virtual_smarthome_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    youtube_music_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ
);

-- 11. USER LIMITS
CREATE TABLE IF NOT EXISTS user_limits (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    max_materials INT NOT NULL DEFAULT 3,
    max_words_per_material INT NOT NULL DEFAULT 6000,
    max_live_apis INT NOT NULL DEFAULT 2,
    max_relay_rooms INT NOT NULL DEFAULT 7,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ
);

-- 12. REMINDERS
CREATE TABLE IF NOT EXISTS reminders (
    id VARCHAR(64) PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMPTZ,
    CONSTRAINT chk_reminder_status CHECK (status IN ('pending', 'processing', 'sent', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_reminders_owner ON reminders(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_pending_scheduled ON reminders (scheduled_at ASC) WHERE status = 'pending';

-- 13. MCP USER SETTINGS
CREATE TABLE IF NOT EXISTS mcp_user_settings (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    mcp_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    blocked_at TIMESTAMPTZ,
    blocked_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ
);

-- 14. MCP TOOL TOGGLES
CREATE TABLE IF NOT EXISTS mcp_tool_toggles (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool_name VARCHAR(100) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ,
    CONSTRAINT uq_mcp_toggles_user_tool UNIQUE (user_id, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_mcp_toggles_user ON mcp_tool_toggles(user_id);

-- 15. USER PERSONA (Memory Profiling)
CREATE TABLE IF NOT EXISTS user_persona (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category VARCHAR(50) NOT NULL DEFAULT 'informasi_pribadi',
    preference_key VARCHAR(100) NOT NULL,
    preference_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_persona_owner_key UNIQUE (owner_id, preference_key)
);
CREATE INDEX IF NOT EXISTS idx_persona_owner ON user_persona(owner_id);
CREATE INDEX IF NOT EXISTS idx_persona_owner_cat ON user_persona(owner_id, category);

-- 16. COMMUNITY CHATS (Chat Room & Voice Notes)
CREATE TABLE IF NOT EXISTS community_chats (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    username VARCHAR(50) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    content TEXT,
    msg_type VARCHAR(20) NOT NULL DEFAULT 'text',
    voice_filename VARCHAR(255),
    voice_duration INT DEFAULT 0,
    reply_to_id BIGINT REFERENCES community_chats(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_date VARCHAR(10) NOT NULL DEFAULT TO_CHAR(CURRENT_TIMESTAMP, 'YYYY-MM-DD'),
    CONSTRAINT chk_community_msg_type CHECK (msg_type IN ('text', 'voice'))
);
CREATE INDEX IF NOT EXISTS idx_community_chats_pagination ON community_chats (id DESC, user_id);
CREATE INDEX IF NOT EXISTS idx_community_chats_user ON community_chats(user_id);
CREATE INDEX IF NOT EXISTS idx_community_chats_date ON community_chats(created_date);
CREATE INDEX IF NOT EXISTS idx_community_chats_reply ON community_chats(reply_to_id) WHERE reply_to_id IS NOT NULL;

-- 17. USER CHAT READ STATE
CREATE TABLE IF NOT EXISTS user_chat_read_state (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_read_message_id BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- 18. NATIVE POSTGRESQL ROW LEVEL SECURITY (RLS) POLICIES
-- =============================================================================

CREATE OR REPLACE FUNCTION app_current_user_id()
RETURNS BIGINT
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')::bigint;
$$;

CREATE OR REPLACE FUNCTION app_is_admin_or_system()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(current_setting('app.user_role', true) IN ('admin', 'system'), false);
$$;

-- Enable and force RLS on all private entities
ALTER TABLE IF EXISTS categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS categories FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_categories_all ON categories;
CREATE POLICY p_categories_all ON categories
    FOR ALL USING (owner_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (owner_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS materials ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS materials FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_materials_all ON materials;
CREATE POLICY p_materials_all ON materials
    FOR ALL USING (owner_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (owner_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS chat_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS chat_history FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_chat_history_all ON chat_history;
CREATE POLICY p_chat_history_all ON chat_history
    FOR ALL USING (owner_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (owner_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS user_persona ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS user_persona FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_user_persona_all ON user_persona;
CREATE POLICY p_user_persona_all ON user_persona
    FOR ALL USING (owner_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (owner_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS relay_rooms ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS relay_rooms FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_relay_rooms_all ON relay_rooms;
CREATE POLICY p_relay_rooms_all ON relay_rooms
    FOR ALL USING (owner_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (owner_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS registered_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS registered_devices FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_registered_devices_all ON registered_devices;
CREATE POLICY p_registered_devices_all ON registered_devices
    FOR ALL USING (owner_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (owner_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS audio_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS audio_queue FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_audio_queue_all ON audio_queue;
CREATE POLICY p_audio_queue_all ON audio_queue
    FOR ALL USING (owner_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (owner_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS reminders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_reminders_all ON reminders;
CREATE POLICY p_reminders_all ON reminders
    FOR ALL USING (owner_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (owner_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS mcp_user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS mcp_user_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_mcp_user_settings_all ON mcp_user_settings;
CREATE POLICY p_mcp_user_settings_all ON mcp_user_settings
    FOR ALL USING (user_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (user_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS mcp_tool_toggles ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS mcp_tool_toggles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_mcp_tool_toggles_all ON mcp_tool_toggles;
CREATE POLICY p_mcp_tool_toggles_all ON mcp_tool_toggles
    FOR ALL USING (user_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (user_id = app_current_user_id() OR app_is_admin_or_system());

ALTER TABLE IF EXISTS user_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS user_limits FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_user_limits_all ON user_limits;
CREATE POLICY p_user_limits_all ON user_limits
    FOR ALL USING (user_id = app_current_user_id() OR app_is_admin_or_system())
    WITH CHECK (user_id = app_current_user_id() OR app_is_admin_or_system());
