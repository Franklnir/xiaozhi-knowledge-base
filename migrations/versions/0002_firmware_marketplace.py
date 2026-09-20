"""0002_firmware_marketplace

Revision ID: 0002_firmware_marketplace
Revises: 0001_initial_schema
Create Date: 2026-09-20 10:10:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_firmware_marketplace'
down_revision: Union[str, None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. PostgreSQL RLS Helper Functions (transaction-local)
    op.execute("""
        CREATE OR REPLACE FUNCTION app_current_user_id()
        RETURNS BIGINT
        LANGUAGE sql
        STABLE
        AS $$
            SELECT NULLIF(current_setting('app.user_id', true), '')::bigint;
        $$;

        CREATE OR REPLACE FUNCTION app_current_user_role()
        RETURNS VARCHAR
        LANGUAGE sql
        STABLE
        AS $$
            SELECT NULLIF(current_setting('app.user_role', true), '')::varchar;
        $$;
    """)

    # 2. Firmware Products
    op.execute("""
        CREATE TABLE IF NOT EXISTS firmware_products (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            seller_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(160) NOT NULL,
            slug VARCHAR(180) UNIQUE NOT NULL,
            short_description VARCHAR(500) NOT NULL,
            full_description TEXT NOT NULL,
            price_amount BIGINT NOT NULL CHECK (price_amount >= 0),
            currency CHAR(3) NOT NULL DEFAULT 'IDR',
            status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
            visibility VARCHAR(20) NOT NULL DEFAULT 'PUBLIC',
            is_admin_product BOOLEAN NOT NULL DEFAULT FALSE,
            version INTEGER NOT NULL DEFAULT 1,
            sales_count INTEGER NOT NULL DEFAULT 0,
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ,
            CONSTRAINT chk_product_status CHECK (status IN ('DRAFT', 'PENDING_REVIEW', 'PUBLISHED', 'REJECTED', 'ARCHIVED'))
        );
        CREATE INDEX IF NOT EXISTS idx_products_seller_status ON firmware_products(seller_id, status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_products_marketplace_published ON firmware_products(published_at DESC, id DESC) WHERE status = 'PUBLISHED';
        CREATE INDEX IF NOT EXISTS idx_products_slug ON firmware_products(slug);
    """)

    # 3. Product Images (max 3 images, max 500KB = 512000 bytes)
    op.execute("""
        CREATE TABLE IF NOT EXISTS firmware_product_images (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES firmware_products(id) ON DELETE CASCADE,
            sort_order SMALLINT NOT NULL CHECK (sort_order BETWEEN 0 AND 2),
            storage_key TEXT NOT NULL,
            mime_type VARCHAR(100) NOT NULL,
            file_size BIGINT NOT NULL CHECK (file_size <= 512000),
            width INTEGER,
            height INTEGER,
            sha256 CHAR(64) NOT NULL,
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_product_images_product ON firmware_product_images(product_id, sort_order);
    """)

    # 4. Product Documentation Links
    op.execute("""
        CREATE TABLE IF NOT EXISTS firmware_product_links (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES firmware_products(id) ON DELETE CASCADE,
            label VARCHAR(100) NOT NULL,
            url TEXT NOT NULL,
            sort_order SMALLINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_product_links_product ON firmware_product_links(product_id, sort_order);
    """)

    # 5. Product Versions
    op.execute("""
        CREATE TABLE IF NOT EXISTS firmware_product_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES firmware_products(id) ON DELETE CASCADE,
            version_label VARCHAR(50) NOT NULL,
            release_notes TEXT,
            status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            CONSTRAINT chk_version_status CHECK (status IN ('DRAFT', 'PENDING_REVIEW', 'PUBLISHED', 'RETIRED'))
        );
        CREATE INDEX IF NOT EXISTS idx_product_versions_product ON firmware_product_versions(product_id, created_at DESC);
    """)

    # 6. Firmware Binary Assets (.bin only, stored privately)
    op.execute("""
        CREATE TABLE IF NOT EXISTS firmware_assets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_version_id UUID UNIQUE NOT NULL REFERENCES firmware_product_versions(id) ON DELETE CASCADE,
            original_filename VARCHAR(255) NOT NULL,
            storage_bucket VARCHAR(100) NOT NULL,
            storage_key TEXT UNIQUE NOT NULL,
            mime_type VARCHAR(100) NOT NULL DEFAULT 'application/octet-stream',
            file_size BIGINT NOT NULL CHECK (file_size > 0),
            sha256 CHAR(64) NOT NULL,
            upload_status VARCHAR(30) NOT NULL DEFAULT 'READY',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_firmware_assets_version ON firmware_assets(product_version_id);
    """)

    # 7. Product Approvals
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_approvals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES firmware_products(id) ON DELETE CASCADE,
            product_version_id UUID NOT NULL REFERENCES firmware_product_versions(id) ON DELETE CASCADE,
            submitted_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reviewed_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW',
            reason TEXT,
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_approval_status CHECK (status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED'))
        );
        CREATE INDEX IF NOT EXISTS idx_approvals_status ON product_approvals(status, submitted_at DESC);
        CREATE INDEX IF NOT EXISTS idx_approvals_product ON product_approvals(product_id);
    """)

    # 8. Orders & Snapshots (Full transparency: subtotal, platform_fee, seller_net)
    op.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            buyer_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            seller_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id UUID NOT NULL REFERENCES firmware_products(id) ON DELETE CASCADE,
            product_version_id UUID NOT NULL REFERENCES firmware_product_versions(id) ON DELETE CASCADE,
            order_number VARCHAR(40) UNIQUE NOT NULL,
            currency CHAR(3) NOT NULL DEFAULT 'IDR',
            subtotal_amount BIGINT NOT NULL CHECK (subtotal_amount >= 0),
            platform_fee_amount BIGINT NOT NULL DEFAULT 0 CHECK (platform_fee_amount >= 0),
            payment_fee_amount BIGINT NOT NULL DEFAULT 0 CHECK (payment_fee_amount >= 0),
            buyer_total_amount BIGINT NOT NULL CHECK (buyer_total_amount >= 0),
            seller_net_amount BIGINT NOT NULL CHECK (seller_net_amount >= 0),
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING_PAYMENT',
            product_title_snapshot VARCHAR(160) NOT NULL,
            seller_name_snapshot VARCHAR(100) NOT NULL,
            version_label_snapshot VARCHAR(50) NOT NULL,
            price_snapshot BIGINT NOT NULL,
            firmware_sha256_snapshot CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            paid_at TIMESTAMPTZ,
            expired_at TIMESTAMPTZ,
            CONSTRAINT chk_order_status CHECK (status IN ('PENDING_PAYMENT', 'PAID', 'EXPIRED', 'CANCELLED', 'REFUNDED'))
        );
        CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders(buyer_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_orders_seller_paid ON orders(seller_id, paid_at DESC) WHERE status = 'PAID';
        CREATE INDEX IF NOT EXISTS idx_orders_number ON orders(order_number);
    """)

    # 9. Purchase Entitlements
    op.execute("""
        CREATE TABLE IF NOT EXISTS purchase_entitlements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            buyer_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            order_id UUID UNIQUE NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id UUID NOT NULL REFERENCES firmware_products(id) ON DELETE CASCADE,
            product_version_id UUID NOT NULL REFERENCES firmware_product_versions(id) ON DELETE CASCADE,
            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
            entitled_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_entitlement_status CHECK (status IN ('ACTIVE', 'REVOKED', 'REFUNDED'))
        );
        CREATE INDEX IF NOT EXISTS idx_entitlements_buyer_active ON purchase_entitlements(buyer_id, created_at DESC) WHERE status = 'ACTIVE';
        CREATE INDEX IF NOT EXISTS idx_entitlements_product_buyer ON purchase_entitlements(product_id, buyer_id);
    """)

    # 10. Wallet Accounts
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id BIGINT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            currency CHAR(3) NOT NULL DEFAULT 'IDR',
            available_balance BIGINT NOT NULL DEFAULT 0 CHECK (available_balance >= 0),
            pending_balance BIGINT NOT NULL DEFAULT 0 CHECK (pending_balance >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_wallet_accounts_user ON wallet_accounts(user_id);
    """)

    # 11. Wallet Ledger (Append-only)
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_ledger (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet_account_id UUID NOT NULL REFERENCES wallet_accounts(id) ON DELETE CASCADE,
            entry_type VARCHAR(30) NOT NULL,
            direction VARCHAR(10) NOT NULL,
            amount BIGINT NOT NULL CHECK (amount > 0),
            currency CHAR(3) NOT NULL DEFAULT 'IDR',
            reference_type VARCHAR(50) NOT NULL,
            reference_id UUID,
            idempotency_key VARCHAR(100) UNIQUE,
            description TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_ledger_direction CHECK (direction IN ('CREDIT', 'DEBIT')),
            CONSTRAINT chk_ledger_type CHECK (entry_type IN ('SALE', 'PLATFORM_FEE', 'ADMIN_COMMISSION', 'WITHDRAWAL', 'WITHDRAWAL_REVERSAL', 'REFUND', 'ADJUSTMENT'))
        );
        CREATE INDEX IF NOT EXISTS idx_wallet_ledger_wallet ON wallet_ledger(wallet_account_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_wallet_ledger_ref ON wallet_ledger(reference_type, reference_id);
    """)

    # 12. Withdrawals
    op.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            wallet_account_id UUID NOT NULL REFERENCES wallet_accounts(id) ON DELETE CASCADE,
            amount BIGINT NOT NULL CHECK (amount > 0),
            fee BIGINT NOT NULL DEFAULT 0 CHECK (fee >= 0),
            net_amount BIGINT NOT NULL CHECK (net_amount > 0),
            destination_bank VARCHAR(50) NOT NULL,
            destination_account_name VARCHAR(100) NOT NULL,
            destination_account_encrypted TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'REQUESTED',
            idempotency_key VARCHAR(100) UNIQUE,
            admin_notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMPTZ,
            CONSTRAINT chk_withdrawal_status CHECK (status IN ('REQUESTED', 'PROCESSING', 'PAID', 'FAILED', 'REJECTED'))
        );
        CREATE INDEX IF NOT EXISTS idx_withdrawals_user ON withdrawals(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status, created_at DESC);
    """)

    # 13. Payment Webhook Events & Transactions
    op.execute("""
        CREATE TABLE IF NOT EXISTS payment_webhook_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider VARCHAR(50) NOT NULL,
            provider_event_id VARCHAR(100) NOT NULL,
            provider_reference VARCHAR(100),
            event_type VARCHAR(50) NOT NULL,
            payload_hash CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMPTZ,
            processing_status VARCHAR(30) NOT NULL DEFAULT 'RECEIVED',
            error_message TEXT,
            CONSTRAINT uq_payment_webhook_provider_event UNIQUE(provider, provider_event_id)
        );

        CREATE TABLE IF NOT EXISTS payment_transactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            provider VARCHAR(50) NOT NULL,
            provider_transaction_id VARCHAR(100),
            checkout_url TEXT,
            amount BIGINT NOT NULL,
            currency CHAR(3) NOT NULL DEFAULT 'IDR',
            status VARCHAR(30) NOT NULL DEFAULT 'INITIATED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_payment_provider_tx UNIQUE(provider, provider_transaction_id)
        );
    """)

    # 14. Chat Conversations & Messages
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES firmware_products(id) ON DELETE CASCADE,
            seller_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            buyer_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_message_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_chat_conv UNIQUE(product_id, seller_id, buyer_id)
        );
        CREATE INDEX IF NOT EXISTS idx_chat_conv_users ON chat_conversations(buyer_id, seller_id);

        CREATE TABLE IF NOT EXISTS chat_messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_chat_messages_conv ON chat_messages(conversation_id, created_at DESC);
    """)

    # 15. Audit Logs
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            request_id VARCHAR(64),
            actor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            actor_role VARCHAR(30),
            action VARCHAR(50) NOT NULL,
            entity_type VARCHAR(50) NOT NULL,
            entity_id UUID,
            before_state JSONB,
            after_state JSONB,
            reason TEXT,
            ip_metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor_user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS audit_logs CASCADE;
        DROP TABLE IF EXISTS chat_messages CASCADE;
        DROP TABLE IF EXISTS chat_conversations CASCADE;
        DROP TABLE IF EXISTS payment_transactions CASCADE;
        DROP TABLE IF EXISTS payment_webhook_events CASCADE;
        DROP TABLE IF EXISTS withdrawals CASCADE;
        DROP TABLE IF EXISTS wallet_ledger CASCADE;
        DROP TABLE IF EXISTS wallet_accounts CASCADE;
        DROP TABLE IF EXISTS purchase_entitlements CASCADE;
        DROP TABLE IF EXISTS orders CASCADE;
        DROP TABLE IF EXISTS product_approvals CASCADE;
        DROP TABLE IF EXISTS firmware_assets CASCADE;
        DROP TABLE IF EXISTS firmware_product_versions CASCADE;
        DROP TABLE IF EXISTS firmware_product_links CASCADE;
        DROP TABLE IF EXISTS firmware_product_images CASCADE;
        DROP TABLE IF EXISTS firmware_products CASCADE;
        DROP FUNCTION IF EXISTS app_current_user_role() CASCADE;
        DROP FUNCTION IF EXISTS app_current_user_id() CASCADE;
    """)
