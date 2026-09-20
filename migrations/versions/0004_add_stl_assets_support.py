"""0004_add_stl_assets_support

Revision ID: 0004_add_stl_assets_support
Revises: 0003_enable_native_rls
Create Date: 2026-09-20 17:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004_add_stl_assets_support'
down_revision: Union[str, None] = '0003_enable_native_rls'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create table for 3D model STL assets
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_stl_assets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_version_id UUID UNIQUE NOT NULL REFERENCES firmware_product_versions(id) ON DELETE CASCADE,
            original_filename VARCHAR(255) NOT NULL,
            storage_bucket VARCHAR(100) NOT NULL,
            storage_key TEXT UNIQUE NOT NULL,
            mime_type VARCHAR(100) NOT NULL DEFAULT 'model/stl',
            file_size BIGINT NOT NULL CHECK (file_size > 0),
            sha256 CHAR(64) NOT NULL,
            upload_status VARCHAR(30) NOT NULL DEFAULT 'READY',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_product_stl_assets_version ON product_stl_assets(product_version_id);
    """)

    # 2. Add STL snapshot columns to orders table for immutable order receipts
    op.execute("""
        ALTER TABLE orders ADD COLUMN IF NOT EXISTS stl_sha256_snapshot CHAR(64);
        ALTER TABLE orders ADD COLUMN IF NOT EXISTS stl_filename_snapshot VARCHAR(255);
    """)

    # 3. Enable Native Row Level Security (RLS) on product_stl_assets
    op.execute("""
        ALTER TABLE product_stl_assets ENABLE ROW LEVEL SECURITY;
        ALTER TABLE product_stl_assets FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS p_product_stl_assets_all ON product_stl_assets;
        CREATE POLICY p_product_stl_assets_all ON product_stl_assets
        FOR ALL USING (
            EXISTS (
                SELECT 1 FROM firmware_product_versions v
                WHERE v.id = product_stl_assets.product_version_id
                  AND (v.status = 'PUBLISHED' OR v.created_by = app_current_user_id() OR app_is_admin_or_system())
            )
        ) WITH CHECK (
            app_is_admin_or_system()
            OR EXISTS (
                SELECT 1 FROM firmware_product_versions v
                WHERE v.id = product_stl_assets.product_version_id
                  AND v.created_by = app_current_user_id()
            )
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP POLICY IF EXISTS p_product_stl_assets_all ON product_stl_assets;
        ALTER TABLE IF EXISTS product_stl_assets NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE IF EXISTS product_stl_assets DISABLE ROW LEVEL SECURITY;
        DROP TABLE IF EXISTS product_stl_assets;
        ALTER TABLE orders DROP COLUMN IF EXISTS stl_sha256_snapshot;
        ALTER TABLE orders DROP COLUMN IF EXISTS stl_filename_snapshot;
    """)
