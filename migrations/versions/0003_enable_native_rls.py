"""0003_enable_native_rls

Revision ID: 0003_enable_native_rls
Revises: 0002_firmware_marketplace
Create Date: 2026-09-20 11:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_enable_native_rls'
down_revision: Union[str, None] = '0002_firmware_marketplace'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Helper function for role check (admin or system background worker)
    op.execute("""
        CREATE OR REPLACE FUNCTION app_is_admin_or_system()
        RETURNS BOOLEAN
        LANGUAGE sql
        STABLE
        AS $$
            SELECT COALESCE(current_setting('app.user_role', true) IN ('admin', 'system'), false);
        $$;
    """)

    # 2. Firmware Products
    op.execute("""
        ALTER TABLE firmware_products ENABLE ROW LEVEL SECURITY;
        ALTER TABLE firmware_products FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_firmware_products_select ON firmware_products;
        CREATE POLICY p_firmware_products_select ON firmware_products
        FOR SELECT USING (
            (status = 'PUBLISHED' AND visibility = 'PUBLIC')
            OR seller_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_firmware_products_insert ON firmware_products;
        CREATE POLICY p_firmware_products_insert ON firmware_products
        FOR INSERT WITH CHECK (
            seller_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_firmware_products_update ON firmware_products;
        CREATE POLICY p_firmware_products_update ON firmware_products
        FOR UPDATE USING (
            seller_id = app_current_user_id()
            OR app_is_admin_or_system()
        ) WITH CHECK (
            seller_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_firmware_products_delete ON firmware_products;
        CREATE POLICY p_firmware_products_delete ON firmware_products
        FOR DELETE USING (
            seller_id = app_current_user_id()
            OR app_is_admin_or_system()
        );
    """)

    # 3. Product Images
    op.execute("""
        ALTER TABLE firmware_product_images ENABLE ROW LEVEL SECURITY;
        ALTER TABLE firmware_product_images FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_firmware_images_select ON firmware_product_images;
        CREATE POLICY p_firmware_images_select ON firmware_product_images
        FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM firmware_products p
                WHERE p.id = firmware_product_images.product_id
                  AND ((p.status = 'PUBLISHED' AND p.visibility = 'PUBLIC')
                       OR p.seller_id = app_current_user_id()
                       OR app_is_admin_or_system())
            )
        );

        DROP POLICY IF EXISTS p_firmware_images_modify ON firmware_product_images;
        CREATE POLICY p_firmware_images_modify ON firmware_product_images
        FOR ALL USING (
            EXISTS (
                SELECT 1 FROM firmware_products p
                WHERE p.id = firmware_product_images.product_id
                  AND (p.seller_id = app_current_user_id() OR app_is_admin_or_system())
            )
        ) WITH CHECK (
            EXISTS (
                SELECT 1 FROM firmware_products p
                WHERE p.id = firmware_product_images.product_id
                  AND (p.seller_id = app_current_user_id() OR app_is_admin_or_system())
            )
        );
    """)

    # 4. Product Links
    op.execute("""
        ALTER TABLE firmware_product_links ENABLE ROW LEVEL SECURITY;
        ALTER TABLE firmware_product_links FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_firmware_links_select ON firmware_product_links;
        CREATE POLICY p_firmware_links_select ON firmware_product_links
        FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM firmware_products p
                WHERE p.id = firmware_product_links.product_id
                  AND ((p.status = 'PUBLISHED' AND p.visibility = 'PUBLIC')
                       OR p.seller_id = app_current_user_id()
                       OR app_is_admin_or_system())
            )
        );

        DROP POLICY IF EXISTS p_firmware_links_modify ON firmware_product_links;
        CREATE POLICY p_firmware_links_modify ON firmware_product_links
        FOR ALL USING (
            EXISTS (
                SELECT 1 FROM firmware_products p
                WHERE p.id = firmware_product_links.product_id
                  AND (p.seller_id = app_current_user_id() OR app_is_admin_or_system())
            )
        ) WITH CHECK (
            EXISTS (
                SELECT 1 FROM firmware_products p
                WHERE p.id = firmware_product_links.product_id
                  AND (p.seller_id = app_current_user_id() OR app_is_admin_or_system())
            )
        );
    """)

    # 5. Product Versions & Firmware Assets
    op.execute("""
        ALTER TABLE firmware_product_versions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE firmware_product_versions FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_firmware_versions_all ON firmware_product_versions;
        CREATE POLICY p_firmware_versions_all ON firmware_product_versions
        FOR ALL USING (
            status = 'PUBLISHED'
            OR created_by = app_current_user_id()
            OR app_is_admin_or_system()
        ) WITH CHECK (
            created_by = app_current_user_id()
            OR app_is_admin_or_system()
        );

        ALTER TABLE firmware_assets ENABLE ROW LEVEL SECURITY;
        ALTER TABLE firmware_assets FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_firmware_assets_all ON firmware_assets;
        CREATE POLICY p_firmware_assets_all ON firmware_assets
        FOR ALL USING (
            EXISTS (
                SELECT 1 FROM firmware_product_versions v
                WHERE v.id = firmware_assets.product_version_id
                  AND (v.status = 'PUBLISHED' OR v.created_by = app_current_user_id() OR app_is_admin_or_system())
            )
        ) WITH CHECK (
            app_is_admin_or_system()
            OR EXISTS (
                SELECT 1 FROM firmware_product_versions v
                WHERE v.id = firmware_assets.product_version_id
                  AND v.created_by = app_current_user_id()
            )
        );
    """)

    # 6. Orders
    op.execute("""
        ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
        ALTER TABLE orders FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_orders_select ON orders;
        CREATE POLICY p_orders_select ON orders
        FOR SELECT USING (
            buyer_id = app_current_user_id()
            OR seller_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_orders_insert ON orders;
        CREATE POLICY p_orders_insert ON orders
        FOR INSERT WITH CHECK (
            buyer_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_orders_update ON orders;
        CREATE POLICY p_orders_update ON orders
        FOR UPDATE USING (
            app_is_admin_or_system()
        );
    """)

    # 7. Purchase Entitlements
    op.execute("""
        ALTER TABLE purchase_entitlements ENABLE ROW LEVEL SECURITY;
        ALTER TABLE purchase_entitlements FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_entitlements_select ON purchase_entitlements;
        CREATE POLICY p_entitlements_select ON purchase_entitlements
        FOR SELECT USING (
            buyer_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_entitlements_write ON purchase_entitlements;
        CREATE POLICY p_entitlements_write ON purchase_entitlements
        FOR ALL USING (
            app_is_admin_or_system()
        ) WITH CHECK (
            app_is_admin_or_system()
        );
    """)

    # 8. Wallet Accounts & Ledger
    op.execute("""
        ALTER TABLE wallet_accounts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE wallet_accounts FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_wallet_accounts_select ON wallet_accounts;
        CREATE POLICY p_wallet_accounts_select ON wallet_accounts
        FOR SELECT USING (
            user_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_wallet_accounts_modify ON wallet_accounts;
        CREATE POLICY p_wallet_accounts_modify ON wallet_accounts
        FOR ALL USING (
            app_is_admin_or_system()
        ) WITH CHECK (
            app_is_admin_or_system()
        );

        ALTER TABLE wallet_ledger ENABLE ROW LEVEL SECURITY;
        ALTER TABLE wallet_ledger FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_wallet_ledger_select ON wallet_ledger;
        CREATE POLICY p_wallet_ledger_select ON wallet_ledger
        FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM wallet_accounts w
                WHERE w.id = wallet_ledger.wallet_account_id
                  AND (w.user_id = app_current_user_id() OR app_is_admin_or_system())
            )
        );

        DROP POLICY IF EXISTS p_wallet_ledger_insert ON wallet_ledger;
        CREATE POLICY p_wallet_ledger_insert ON wallet_ledger
        FOR INSERT WITH CHECK (
            app_is_admin_or_system()
        );
    """)

    # 9. Withdrawals
    op.execute("""
        ALTER TABLE withdrawals ENABLE ROW LEVEL SECURITY;
        ALTER TABLE withdrawals FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_withdrawals_select ON withdrawals;
        CREATE POLICY p_withdrawals_select ON withdrawals
        FOR SELECT USING (
            user_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_withdrawals_insert ON withdrawals;
        CREATE POLICY p_withdrawals_insert ON withdrawals
        FOR INSERT WITH CHECK (
            user_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_withdrawals_update ON withdrawals;
        CREATE POLICY p_withdrawals_update ON withdrawals
        FOR UPDATE USING (
            app_is_admin_or_system()
        ) WITH CHECK (
            app_is_admin_or_system()
        );
    """)

    # 10. Product Approvals
    op.execute("""
        ALTER TABLE product_approvals ENABLE ROW LEVEL SECURITY;
        ALTER TABLE product_approvals FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_approvals_select ON product_approvals;
        CREATE POLICY p_approvals_select ON product_approvals
        FOR SELECT USING (
            submitted_by = app_current_user_id()
            OR app_is_admin_or_system()
        );

        DROP POLICY IF EXISTS p_approvals_modify ON product_approvals;
        CREATE POLICY p_approvals_modify ON product_approvals
        FOR ALL USING (
            submitted_by = app_current_user_id()
            OR app_is_admin_or_system()
        ) WITH CHECK (
            submitted_by = app_current_user_id()
            OR app_is_admin_or_system()
        );
    """)

    # 11. Chat Conversations & Messages
    op.execute("""
        ALTER TABLE chat_conversations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE chat_conversations FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_chat_conv_all ON chat_conversations;
        CREATE POLICY p_chat_conv_all ON chat_conversations
        FOR ALL USING (
            buyer_id = app_current_user_id()
            OR seller_id = app_current_user_id()
            OR app_is_admin_or_system()
        ) WITH CHECK (
            buyer_id = app_current_user_id()
            OR seller_id = app_current_user_id()
            OR app_is_admin_or_system()
        );

        ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE chat_messages FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_chat_msg_select ON chat_messages;
        CREATE POLICY p_chat_msg_select ON chat_messages
        FOR SELECT USING (
            sender_id = app_current_user_id()
            OR app_is_admin_or_system()
            OR EXISTS (
                SELECT 1 FROM chat_conversations c
                WHERE c.id = chat_messages.conversation_id
                  AND (c.buyer_id = app_current_user_id() OR c.seller_id = app_current_user_id())
            )
        );

        DROP POLICY IF EXISTS p_chat_msg_insert ON chat_messages;
        CREATE POLICY p_chat_msg_insert ON chat_messages
        FOR INSERT WITH CHECK (
            sender_id = app_current_user_id()
            OR app_is_admin_or_system()
        );
    """)


def downgrade() -> None:
    tables = [
        "firmware_products",
        "firmware_product_images",
        "firmware_product_links",
        "firmware_product_versions",
        "firmware_assets",
        "orders",
        "purchase_entitlements",
        "wallet_accounts",
        "wallet_ledger",
        "withdrawals",
        "product_approvals",
        "chat_conversations",
        "chat_messages",
    ]
    for t in tables:
        op.execute(f"ALTER TABLE IF EXISTS {t} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE IF EXISTS {t} DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP FUNCTION IF EXISTS app_is_admin_or_system() CASCADE;")
