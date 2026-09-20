import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

import psycopg
from psycopg import sql

from contextlib import contextmanager
import os

logger = logging.getLogger("xiaozhi.marketplace.repository")


class MarketplaceRepository:
    """
    Database repository for Firmware Marketplace.
    Uses the application's existing Psycopg 3 connection pool or creates a pool from DATABASE_URL.
    """
    def __init__(self, store):
        self.store = store
        self._custom_pool = None
        if not hasattr(self.store, "_get_conn"):
            db_url = os.getenv("DATABASE_URL")
            if db_url:
                try:
                    from psycopg_pool import ConnectionPool
                    self._custom_pool = ConnectionPool(conninfo=db_url, min_size=1, max_size=5, open=True)
                except Exception as e:
                    logger.warning("Failed to open custom pool for marketplace: %s", e)

    def is_db_ready(self) -> bool:
        return hasattr(self.store, "_get_conn") or self._custom_pool is not None

    @contextmanager
    def _get_conn(self):
        if hasattr(self.store, "_get_conn"):
            with self.store._get_conn() as conn:
                yield conn
        elif self._custom_pool:
            with self._custom_pool.connection() as conn:
                yield conn
        else:
            raise RuntimeError("Database PostgreSQL belum terhubung. Pastikan DATABASE_URL diset di environment.")

    def set_rls_context(self, cur, user_id: Optional[int], user_role: Optional[str] = "user"):
        """Sets transaction-local configuration for PostgreSQL Row-Level Security."""
        uid_str = str(user_id) if user_id is not None else ""
        role_str = str(user_role) if user_role else "user"
        cur.execute("SELECT set_config('app.user_id', %s, true);", (uid_str,))
        cur.execute("SELECT set_config('app.user_role', %s, true);", (role_str,))

    # ── PRODUCTS ─────────────────────────────────────────────────────────────
    def get_published_products(self, limit: int = 20, cursor_time: Optional[str] = None, cursor_id: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.is_db_ready():
            return []
        query = """
            SELECT 
                p.id, p.title, p.slug, p.short_description, p.price_amount, p.currency,
                p.is_admin_product, p.sales_count, p.published_at, p.seller_id,
                u.username AS seller_username,
                img.storage_key AS primary_image_key
            FROM firmware_products p
            JOIN users u ON u.id = p.seller_id
            LEFT JOIN firmware_product_images img ON img.product_id = p.id AND img.is_primary = TRUE
            WHERE p.status = 'PUBLISHED'
        """
        params = []
        if search:
            query += " AND (p.title ILIKE %s OR p.short_description ILIKE %s)"
            s_param = f"%{search.strip()}%"
            params.extend([s_param, s_param])

        if cursor_time and cursor_id:
            query += " AND (p.published_at, p.id) < (%s, %s)"
            params.extend([cursor_time, cursor_id])

        query += " ORDER BY p.published_at DESC, p.id DESC LIMIT %s;"
        params.append(min(max(1, limit), 50))

        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("get_published_products query failed: %s", exc)
            return []

    def get_product_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_db_ready():
            return None
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT p.*, u.username AS seller_username, u.role AS seller_role
                        FROM firmware_products p
                        JOIN users u ON u.id = p.seller_id
                        WHERE p.id = %s;
                    """, (product_id,))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as exc:
            logger.warning("get_product_by_id query failed: %s", exc)
            return None

    def get_product_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        if not self.is_db_ready():
            return None
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT p.*, u.username AS seller_username, u.role AS seller_role
                        FROM firmware_products p
                        JOIN users u ON u.id = p.seller_id
                        WHERE p.slug = %s;
                    """, (slug,))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as exc:
            logger.warning("get_product_by_slug query failed: %s", exc)
            return None

    def get_product_images(self, product_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM firmware_product_images
                    WHERE product_id = %s
                    ORDER BY sort_order ASC;
                """, (product_id,))
                return [dict(r) for r in cur.fetchall()]

    def get_product_links(self, product_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM firmware_product_links
                    WHERE product_id = %s
                    ORDER BY sort_order ASC;
                """, (product_id,))
                return [dict(r) for r in cur.fetchall()]

    def get_latest_version(self, product_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT v.*, 
                           a.id AS asset_id, a.original_filename, a.file_size, a.sha256, a.upload_status, a.storage_key,
                           s.id AS stl_asset_id, s.original_filename AS stl_original_filename, s.file_size AS stl_file_size, s.sha256 AS stl_sha256, s.upload_status AS stl_upload_status, s.storage_key AS stl_storage_key
                    FROM firmware_product_versions v
                    LEFT JOIN firmware_assets a ON a.product_version_id = v.id
                    LEFT JOIN product_stl_assets s ON s.product_version_id = v.id
                    WHERE v.product_id = %s
                    ORDER BY v.created_at DESC
                    LIMIT 1;
                """, (product_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_seller_products(self, seller_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.is_db_ready():
            return []
        query = """
            SELECT p.*, 
                   img.storage_key AS primary_image_key,
                   appr.reason AS reject_reason
            FROM firmware_products p
            LEFT JOIN firmware_product_images img ON img.product_id = p.id AND img.is_primary = TRUE
            LEFT JOIN LATERAL (
                SELECT reason FROM product_approvals 
                WHERE product_id = p.id AND status = 'REJECTED' 
                ORDER BY reviewed_at DESC LIMIT 1
            ) appr ON TRUE
            WHERE p.seller_id = %s
        """
        params = [seller_id]
        if status:
            query += " AND p.status = %s"
            params.append(status)
        query += " ORDER BY p.updated_at DESC NULLS LAST, p.created_at DESC;"

        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("get_seller_products failed: %s", exc)
            return []

    def get_all_products_admin(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.is_db_ready():
            return []
        query = """
            SELECT p.*, 
                   u.username AS seller_username,
                   COALESCE(u.google_email, u.username) AS seller_email,
                   img.storage_key AS primary_image_key
            FROM firmware_products p
            JOIN users u ON u.id = p.seller_id
            LEFT JOIN firmware_product_images img ON img.product_id = p.id AND img.is_primary = TRUE
        """
        params = []
        if status and status != "all":
            query += " WHERE p.status = %s"
            params.append(status)
        query += " ORDER BY p.created_at DESC;"

        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("get_all_products_admin failed: %s", exc)
            return []

    def create_product(
        self,
        seller_id: int,
        title: str,
        slug: str,
        short_description: str,
        full_description: str,
        price_amount: int,
        is_admin: bool = False,
        version_label: str = "1.0.0",
    ) -> Dict[str, Any]:
        initial_status = "PUBLISHED" if is_admin else "DRAFT"
        published_at = datetime.now(timezone.utc) if is_admin else None

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, seller_id, "admin" if is_admin else "user")
                
                # 1. Insert product
                cur.execute("""
                    INSERT INTO firmware_products (
                        seller_id, title, slug, short_description, full_description,
                        price_amount, currency, status, is_admin_product, published_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'IDR', %s, %s, %s, CURRENT_TIMESTAMP)
                    RETURNING *;
                """, (seller_id, title, slug, short_description, full_description, price_amount, initial_status, is_admin, published_at))
                product = dict(cur.fetchone())

                # 2. Insert initial version
                version_status = "PUBLISHED" if is_admin else "DRAFT"
                cur.execute("""
                    INSERT INTO firmware_product_versions (
                        product_id, version_label, release_notes, status, published_at, created_by
                    ) VALUES (%s, %s, 'Initial Release', %s, %s, %s)
                    RETURNING *;
                """, (product["id"], version_label, version_status, published_at, seller_id))
                version = dict(cur.fetchone())

                conn.commit()
                product["version_id"] = str(version["id"])
                return product

    def update_product_details(
        self,
        product_id: str,
        seller_id: int,
        title: str,
        short_description: str,
        full_description: str,
        price_amount: int,
        expected_version: int,
    ) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, seller_id)
                cur.execute("""
                    UPDATE firmware_products
                    SET title = %s,
                        short_description = %s,
                        full_description = %s,
                        price_amount = %s,
                        version = version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND seller_id = %s AND version = %s
                    RETURNING *;
                """, (title, short_description, full_description, price_amount, product_id, seller_id, expected_version))
                row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None

    def delete_or_archive_product(self, product_id: str, actor_id: int, is_admin: bool = False) -> Dict[str, Any]:
        """
        Deletes or archives a product:
        - If product has orders or was published: Soft-delete/Archive (status = 'ARCHIVED') to maintain financial integrity.
        - If product is draft/rejected without orders: Hard delete.
        - Returns {'action': 'deleted' | 'archived'}.
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if is_admin:
                    cur.execute("SELECT * FROM firmware_products WHERE id = %s FOR UPDATE;", (product_id,))
                else:
                    cur.execute("SELECT * FROM firmware_products WHERE id = %s AND seller_id = %s FOR UPDATE;", (product_id, actor_id))
                prod = cur.fetchone()
                if not prod:
                    raise ValueError("Produk tidak ditemukan atau Anda tidak memiliki izin.")

                cur.execute("SELECT COUNT(*) AS c FROM orders WHERE product_id = %s;", (product_id,))
                orders_count = cur.fetchone()["c"]

                if orders_count > 0 or prod["status"] == "PUBLISHED":
                    cur.execute("""
                        UPDATE firmware_products 
                        SET status = 'ARCHIVED', updated_at = CURRENT_TIMESTAMP 
                        WHERE id = %s;
                    """, (product_id,))
                    cur.execute("""
                        UPDATE firmware_product_versions 
                        SET status = 'RETIRED' 
                        WHERE product_id = %s;
                    """, (product_id,))
                    conn.commit()
                    return {"action": "archived", "product_id": product_id}
                else:
                    cur.execute("DELETE FROM firmware_products WHERE id = %s;", (product_id,))
                    conn.commit()
                    return {"action": "deleted", "product_id": product_id}

    def admin_get_all_products(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.*, u.username AS seller_username, u.role AS seller_role,
                           img.storage_key AS primary_image_key
                    FROM firmware_products p
                    JOIN users u ON u.id = p.seller_id
                    LEFT JOIN firmware_product_images img ON img.product_id = p.id AND img.is_primary = TRUE
                    ORDER BY p.created_at DESC
                    LIMIT %s;
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]


    def set_product_images(self, product_id: str, images: List[Dict[str, Any]]) -> None:
        """Replace product images (max 3)."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM firmware_product_images WHERE product_id = %s;", (product_id,))
                for idx, img in enumerate(images[:3]):
                    is_primary = (idx == 0)
                    cur.execute("""
                        INSERT INTO firmware_product_images (
                            product_id, sort_order, storage_key, mime_type, file_size, width, height, sha256, is_primary
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """, (product_id, idx, img["storage_key"], img.get("mime_type", "image/webp"),
                          img["file_size"], img.get("width"), img.get("height"), img["sha256"], is_primary))
                conn.commit()

    def set_product_links(self, product_id: str, links: List[Dict[str, str]]) -> None:
        """Replace product documentation links."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM firmware_product_links WHERE product_id = %s;", (product_id,))
                for idx, link in enumerate(links):
                    label = link.get("label", "Dokumentasi").strip()
                    url = link.get("url", "").strip()
                    if url:
                        cur.execute("""
                            INSERT INTO firmware_product_links (product_id, label, url, sort_order)
                            VALUES (%s, %s, %s, %s);
                        """, (product_id, label, url, idx))
                conn.commit()

    def set_firmware_asset(
        self,
        product_version_id: str,
        original_filename: str,
        storage_bucket: str,
        storage_key: str,
        file_size: int,
        sha256: str,
    ) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO firmware_assets (
                        product_version_id, original_filename, storage_bucket, storage_key, file_size, sha256, upload_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'READY')
                    ON CONFLICT (product_version_id) DO UPDATE SET
                        original_filename = EXCLUDED.original_filename,
                        storage_bucket = EXCLUDED.storage_bucket,
                        storage_key = EXCLUDED.storage_key,
                        file_size = EXCLUDED.file_size,
                        sha256 = EXCLUDED.sha256,
                        upload_status = 'READY'
                    RETURNING *;
                """, (product_version_id, original_filename, storage_bucket, storage_key, file_size, sha256))
                row = cur.fetchone()
                conn.commit()
                return dict(row)

    def set_stl_asset(
        self,
        product_version_id: str,
        original_filename: str,
        storage_bucket: str,
        storage_key: str,
        file_size: int,
        sha256: str,
    ) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO product_stl_assets (
                        product_version_id, original_filename, storage_bucket, storage_key, file_size, sha256, upload_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'READY')
                    ON CONFLICT (product_version_id) DO UPDATE SET
                        original_filename = EXCLUDED.original_filename,
                        storage_bucket = EXCLUDED.storage_bucket,
                        storage_key = EXCLUDED.storage_key,
                        file_size = EXCLUDED.file_size,
                        sha256 = EXCLUDED.sha256,
                        upload_status = 'READY'
                    RETURNING *;
                """, (product_version_id, original_filename, storage_bucket, storage_key, file_size, sha256))
                row = cur.fetchone()
                conn.commit()
                return dict(row)

    def submit_product_for_approval(self, product_id: str, seller_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Lock product row
                cur.execute("""
                    SELECT * FROM firmware_products 
                    WHERE id = %s AND seller_id = %s FOR UPDATE;
                """, (product_id, seller_id))
                prod = cur.fetchone()
                if not prod:
                    return None
                if prod["status"] not in ("DRAFT", "REJECTED"):
                    raise ValueError(f"Produk dalam status '{prod['status']}' tidak dapat diajukan.")

                # 2. Check prerequisites
                cur.execute("SELECT COUNT(*) AS c FROM firmware_product_images WHERE product_id = %s;", (product_id,))
                if cur.fetchone()["c"] < 1:
                    raise ValueError("Produk wajib memiliki minimal 1 foto produk.")

                # Check latest version & assets (must have either .bin or .stl ready)
                cur.execute("""
                    SELECT v.id AS version_id, 
                           a.sha256 AS bin_sha256, a.upload_status AS bin_status,
                           s.sha256 AS stl_sha256, s.upload_status AS stl_status
                    FROM firmware_product_versions v
                    LEFT JOIN firmware_assets a ON a.product_version_id = v.id
                    LEFT JOIN product_stl_assets s ON s.product_version_id = v.id
                    WHERE v.product_id = %s
                    ORDER BY v.created_at DESC LIMIT 1;
                """, (product_id,))
                ver_asset = cur.fetchone()
                has_valid_bin = ver_asset and ver_asset["bin_status"] == "READY" and ver_asset["bin_sha256"]
                has_valid_stl = ver_asset and ver_asset["stl_status"] == "READY" and ver_asset["stl_sha256"]
                if not (has_valid_bin or has_valid_stl):
                    raise ValueError("Produk wajib memiliki minimal salah satu file (Binary Firmware .bin atau Model 3D .stl) yang sudah ter-upload.")

                # 3. Create approval record
                cur.execute("""
                    INSERT INTO product_approvals (
                        product_id, product_version_id, submitted_by, status
                    ) VALUES (%s, %s, %s, 'PENDING_REVIEW')
                    RETURNING *;
                """, (product_id, ver_asset["version_id"], seller_id))
                approval = cur.fetchone()

                # 4. Update product & version status
                cur.execute("""
                    UPDATE firmware_products 
                    SET status = 'PENDING_REVIEW', updated_at = CURRENT_TIMESTAMP 
                    WHERE id = %s;
                """, (product_id,))
                cur.execute("""
                    UPDATE firmware_product_versions 
                    SET status = 'PENDING_REVIEW' 
                    WHERE id = %s;
                """, (ver_asset["version_id"],))

                conn.commit()
                return dict(approval)

    # ── ADMIN APPROVALS ──────────────────────────────────────────────────────
    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        appr.id AS approval_id, appr.status, appr.submitted_at,
                        p.id AS product_id, p.title, p.price_amount, p.currency, p.short_description, p.full_description,
                        v.id AS version_id, v.version_label,
                        a.original_filename, a.file_size, a.sha256,
                        u.id AS seller_id, u.username AS seller_username
                    FROM product_approvals appr
                    JOIN firmware_products p ON p.id = appr.product_id
                    JOIN firmware_product_versions v ON v.id = appr.product_version_id
                    LEFT JOIN firmware_assets a ON a.product_version_id = v.id
                    JOIN users u ON u.id = appr.submitted_by
                    WHERE appr.status = 'PENDING_REVIEW'
                    ORDER BY appr.submitted_at ASC;
                """)
                return [dict(r) for r in cur.fetchall()]

    def admin_approve_product(self, approval_id: str, admin_user_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM product_approvals WHERE id = %s FOR UPDATE;
                """, (approval_id,))
                appr = cur.fetchone()
                if not appr or appr["status"] != "PENDING_REVIEW":
                    return False

                # Update approval
                cur.execute("""
                    UPDATE product_approvals
                    SET status = 'APPROVED', reviewed_by = %s, reviewed_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """, (admin_user_id, approval_id))

                # Publish product & version
                cur.execute("""
                    UPDATE firmware_products
                    SET status = 'PUBLISHED', published_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """, (appr["product_id"],))

                cur.execute("""
                    UPDATE firmware_product_versions
                    SET status = 'PUBLISHED', published_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """, (appr["product_version_id"],))

                # Log audit
                cur.execute("""
                    INSERT INTO audit_logs (
                        actor_user_id, actor_role, action, entity_type, entity_id, reason
                    ) VALUES (%s, 'admin', 'PRODUCT_APPROVED', 'product', %s, 'Product approved by admin');
                """, (admin_user_id, appr["product_id"]))

                conn.commit()
                return True

    def admin_reject_product(self, approval_id: str, admin_user_id: int, reason: str) -> bool:
        if not reason or len(reason.strip()) < 10:
            raise ValueError("Alasan penolakan wajib diisi (minimal 10 karakter).")

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM product_approvals WHERE id = %s FOR UPDATE;
                """, (approval_id,))
                appr = cur.fetchone()
                if not appr or appr["status"] != "PENDING_REVIEW":
                    return False

                # Update approval
                cur.execute("""
                    UPDATE product_approvals
                    SET status = 'REJECTED', reviewed_by = %s, reviewed_at = CURRENT_TIMESTAMP, reason = %s
                    WHERE id = %s;
                """, (admin_user_id, reason.strip(), approval_id))

                # Return product to REJECTED
                cur.execute("""
                    UPDATE firmware_products
                    SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """, (appr["product_id"],))

                # Log audit
                cur.execute("""
                    INSERT INTO audit_logs (
                        actor_user_id, actor_role, action, entity_type, entity_id, reason
                    ) VALUES (%s, 'admin', 'PRODUCT_REJECTED', 'product', %s, %s);
                """, (admin_user_id, appr["product_id"], reason.strip()))

                conn.commit()
                return True

    # ── ORDERS & CHECKOUT ────────────────────────────────────────────────────
    def create_order(
        self,
        buyer_id: int,
        seller_id: int,
        product_id: str,
        version_id: str,
        order_number: str,
        subtotal_amount: int,
        platform_fee_amount: int,
        buyer_total_amount: int,
        seller_net_amount: int,
        product_title_snapshot: str,
        seller_name_snapshot: str,
        version_label_snapshot: str,
        firmware_sha256_snapshot: Optional[str] = None,
        stl_sha256_snapshot: Optional[str] = None,
        stl_filename_snapshot: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO orders (
                        buyer_id, seller_id, product_id, product_version_id, order_number,
                        currency, subtotal_amount, platform_fee_amount, buyer_total_amount, seller_net_amount,
                        product_title_snapshot, seller_name_snapshot, version_label_snapshot,
                        price_snapshot, firmware_sha256_snapshot, stl_sha256_snapshot, stl_filename_snapshot, status
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        'IDR', %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, 'PENDING_PAYMENT'
                    ) RETURNING *;
                """, (
                    buyer_id, seller_id, product_id, version_id, order_number,
                    subtotal_amount, platform_fee_amount, buyer_total_amount, seller_net_amount,
                    product_title_snapshot, seller_name_snapshot, version_label_snapshot,
                    subtotal_amount, firmware_sha256_snapshot or "", stl_sha256_snapshot, stl_filename_snapshot
                ))
                order = dict(cur.fetchone())
                conn.commit()
                return order

    def get_order_by_number(self, order_number: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM orders WHERE order_number = %s;", (order_number,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM orders WHERE id = %s;", (order_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def check_existing_entitlement(self, buyer_id: int, product_id: str) -> bool:
        """Returns True if buyer already owns an active entitlement for this product."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM purchase_entitlements
                    WHERE buyer_id = %s AND product_id = %s AND status = 'ACTIVE'
                    LIMIT 1;
                """, (buyer_id, product_id))
                return bool(cur.fetchone())

    # ── WEBHOOK & FINANCIAL SETTLEMENT ───────────────────────────────────────
    def finalize_order_payment(
        self,
        order_number: str,
        provider: str,
        provider_event_id: str,
        provider_reference: str,
        payload: Dict[str, Any],
    ) -> bool:
        """
        Atomic, idempotent finalization of a paid order:
        1. Checks payment_webhook_events for duplicate event.
        2. Row-locks orders table FOR UPDATE.
        3. Updates order status to PAID.
        4. Creates active purchase_entitlement.
        5. Performs transparent ledger accounting (credited to seller net, platform fee to admin).
        6. Increments sales_count.
        """
        import hashlib
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Idempotency check on webhook event
                cur.execute("""
                    INSERT INTO payment_webhook_events (
                        provider, provider_event_id, provider_reference, event_type, payload_hash, payload, processing_status
                    ) VALUES (%s, %s, %s, 'PAYMENT_SUCCESS', %s, %s, 'PROCESSING')
                    ON CONFLICT (provider, provider_event_id) DO NOTHING
                    RETURNING id;
                """, (provider, provider_event_id, provider_reference, payload_hash, json.dumps(payload)))
                evt = cur.fetchone()
                if not evt:
                    # Duplicate webhook event, safe acknowledge
                    logger.info("Ignoring duplicate webhook event %s:%s", provider, provider_event_id)
                    return True

                webhook_event_id = evt["id"]

                # 2. Lock order
                cur.execute("""
                    SELECT * FROM orders WHERE order_number = %s FOR UPDATE;
                """, (order_number,))
                order = cur.fetchone()
                if not order:
                    cur.execute("UPDATE payment_webhook_events SET processing_status = 'ORDER_NOT_FOUND' WHERE id = %s;", (webhook_event_id,))
                    conn.commit()
                    return False

                if order["status"] == "PAID":
                    # Already paid earlier
                    cur.execute("UPDATE payment_webhook_events SET processing_status = 'ALREADY_PAID' WHERE id = %s;", (webhook_event_id,))
                    conn.commit()
                    return True

                # 3. Update order
                cur.execute("""
                    UPDATE orders 
                    SET status = 'PAID', paid_at = CURRENT_TIMESTAMP 
                    WHERE id = %s;
                """, (order["id"],))

                # 4. Create Entitlement
                cur.execute("""
                    INSERT INTO purchase_entitlements (
                        buyer_id, order_id, product_id, product_version_id, status
                    ) VALUES (%s, %s, %s, %s, 'ACTIVE')
                    ON CONFLICT (order_id) DO NOTHING;
                """, (order["buyer_id"], order["id"], order["product_id"], order["product_version_id"]))

                # 5. Financial accounting & Wallet Ledger
                seller_id = order["seller_id"]
                subtotal = order["subtotal_amount"]
                platform_fee = order["platform_fee_amount"]
                seller_net = order["seller_net_amount"]

                # Ensure seller wallet exists
                cur.execute("""
                    INSERT INTO wallet_accounts (user_id, currency, available_balance, pending_balance)
                    VALUES (%s, 'IDR', 0, 0)
                    ON CONFLICT (user_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    RETURNING id;
                """, (seller_id,))
                seller_wallet_id = cur.fetchone()["id"]

                # Seller Ledger entry: SALE (+subtotal)
                cur.execute("""
                    INSERT INTO wallet_ledger (
                        wallet_account_id, entry_type, direction, amount, currency,
                        reference_type, reference_id, idempotency_key, description
                    ) VALUES (
                        %s, 'SALE', 'CREDIT', %s, 'IDR',
                        'ORDER', %s, %s, %s
                    );
                """, (
                    seller_wallet_id, subtotal, order["id"],
                    f"sale_{order['id']}", f"Penjualan firmware #{order_number}"
                ))

                # Seller Ledger entry: PLATFORM_FEE (-platform_fee) if fee > 0
                if platform_fee > 0:
                    cur.execute("""
                        INSERT INTO wallet_ledger (
                            wallet_account_id, entry_type, direction, amount, currency,
                            reference_type, reference_id, idempotency_key, description
                        ) VALUES (
                            %s, 'PLATFORM_FEE', 'DEBIT', %s, 'IDR',
                            'ORDER', %s, %s, %s
                        );
                    """, (
                        seller_wallet_id, platform_fee, order["id"],
                        f"fee_{order['id']}", f"Biaya admin platform pesanan #{order_number}"
                    ))

                # Update seller available balance cache (+seller_net)
                cur.execute("""
                    UPDATE wallet_accounts
                    SET available_balance = available_balance + %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """, (seller_net, seller_wallet_id))

                # Credit Platform Admin Wallet with PLATFORM_FEE
                if platform_fee > 0:
                    # Find primary admin
                    cur.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1;")
                    admin_user = cur.fetchone()
                    if admin_user:
                        admin_id = admin_user["id"]
                        cur.execute("""
                            INSERT INTO wallet_accounts (user_id, currency, available_balance, pending_balance)
                            VALUES (%s, 'IDR', 0, 0)
                            ON CONFLICT (user_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                            RETURNING id;
                        """, (admin_id,))
                        admin_wallet_id = cur.fetchone()["id"]

                        cur.execute("""
                            INSERT INTO wallet_ledger (
                                wallet_account_id, entry_type, direction, amount, currency,
                                reference_type, reference_id, idempotency_key, description
                            ) VALUES (
                                %s, 'ADMIN_COMMISSION', 'CREDIT', %s, 'IDR',
                                'ORDER', %s, %s, %s
                            );
                        """, (
                            admin_wallet_id, platform_fee, order["id"],
                            f"admin_fee_{order['id']}", f"Komisi admin dari pesanan #{order_number}"
                        ))
                        cur.execute("""
                            UPDATE wallet_accounts
                            SET available_balance = available_balance + %s, updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s;
                        """, (platform_fee, admin_wallet_id))

                # 6. Increment product sales_count
                cur.execute("""
                    UPDATE firmware_products
                    SET sales_count = sales_count + 1
                    WHERE id = %s;
                """, (order["product_id"],))

                # Mark webhook event processed
                cur.execute("""
                    UPDATE payment_webhook_events
                    SET processing_status = 'SUCCESS', processed_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """, (webhook_event_id,))

                # Audit Log
                cur.execute("""
                    INSERT INTO audit_logs (
                        actor_user_id, actor_role, action, entity_type, entity_id, reason
                    ) VALUES (%s, 'system', 'PAYMENT_CONFIRMED', 'order', %s, 'Payment verified via webhook');
                """, (order["buyer_id"], order["id"]))

                conn.commit()
                return True

    # ── PURCHASES & DOWNLOADS ────────────────────────────────────────────────
    def get_buyer_purchases(self, buyer_id: int) -> List[Dict[str, Any]]:
        if not self.is_db_ready():
            return []
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            e.id AS purchase_id, e.status AS entitlement_status, e.entitled_at,
                            o.order_number, o.paid_at, o.subtotal_amount, o.currency,
                            o.product_title_snapshot, o.seller_name_snapshot, o.version_label_snapshot, o.firmware_sha256_snapshot,
                            o.stl_sha256_snapshot, o.stl_filename_snapshot,
                            p.id AS product_id, p.slug,
                            img.storage_key AS primary_image_key,
                            a.original_filename, a.file_size, a.storage_key,
                            s.original_filename AS stl_original_filename, s.file_size AS stl_file_size, s.storage_key AS stl_storage_key, s.sha256 AS stl_sha256
                        FROM purchase_entitlements e
                        JOIN orders o ON o.id = e.order_id
                        JOIN firmware_products p ON p.id = e.product_id
                        JOIN firmware_product_versions v ON v.id = e.product_version_id
                        LEFT JOIN firmware_assets a ON a.product_version_id = v.id
                        LEFT JOIN product_stl_assets s ON s.product_version_id = v.id
                        LEFT JOIN firmware_product_images img ON img.product_id = p.id AND img.is_primary = TRUE
                        WHERE e.buyer_id = %s AND e.status = 'ACTIVE' AND o.status = 'PAID'
                        ORDER BY e.entitled_at DESC;
                    """, (buyer_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("get_buyer_purchases failed: %s", exc)
            return []

    def get_entitlement_for_download(self, purchase_id: str, buyer_id: int, asset_type: str = "bin") -> Optional[Dict[str, Any]]:
        if not self.is_db_ready():
            return None
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    if str(asset_type).lower() == "stl":
                        cur.execute("""
                            SELECT 
                                e.id AS purchase_id, e.buyer_id, e.status AS entitlement_status,
                                o.status AS order_status,
                                s.storage_bucket, s.storage_key, s.original_filename, s.sha256, s.file_size,
                                'stl' AS asset_type
                            FROM purchase_entitlements e
                            JOIN orders o ON o.id = e.order_id
                            JOIN product_stl_assets s ON s.product_version_id = e.product_version_id
                            WHERE e.id = %s AND e.buyer_id = %s AND e.status = 'ACTIVE' AND o.status = 'PAID';
                        """, (purchase_id, buyer_id))
                    else:
                        cur.execute("""
                            SELECT 
                                e.id AS purchase_id, e.buyer_id, e.status AS entitlement_status,
                                o.status AS order_status,
                                a.storage_bucket, a.storage_key, a.original_filename, a.sha256, a.file_size,
                                'bin' AS asset_type
                            FROM purchase_entitlements e
                            JOIN orders o ON o.id = e.order_id
                            JOIN firmware_assets a ON a.product_version_id = e.product_version_id
                            WHERE e.id = %s AND e.buyer_id = %s AND e.status = 'ACTIVE' AND o.status = 'PAID';
                        """, (purchase_id, buyer_id))
                    row = cur.fetchone()
                    return dict(row) if row else None
        except Exception as exc:
            logger.warning("get_entitlement_for_download failed: %s", exc)
            return None

    # ── WALLET & WITHDRAWALS ─────────────────────────────────────────────────
    def get_wallet(self, user_id: int) -> Dict[str, Any]:
        default_wallet = {"id": "0", "user_id": user_id, "available_balance": 0, "pending_balance": 0, "currency": "IDR"}
        if not self.is_db_ready():
            return default_wallet
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO wallet_accounts (user_id, currency, available_balance, pending_balance)
                        VALUES (%s, 'IDR', 0, 0)
                        ON CONFLICT (user_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                        RETURNING *;
                    """, (user_id,))
                    wallet = dict(cur.fetchone())
                    conn.commit()
                    return wallet
        except Exception as exc:
            logger.warning("get_wallet failed: %s", exc)
            return default_wallet

    def get_wallet_ledger(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.is_db_ready():
            return []
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT l.* 
                        FROM wallet_ledger l
                        JOIN wallet_accounts w ON w.id = l.wallet_account_id
                        WHERE w.user_id = %s
                        ORDER BY l.created_at DESC
                        LIMIT %s;
                    """, (user_id, limit))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("get_wallet_ledger failed: %s", exc)
            return []

    def request_withdrawal(
        self,
        user_id: int,
        amount: int,
        fee: int,
        net_amount: int,
        destination_bank: str,
        destination_account_name: str,
        destination_account_encrypted: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Lock wallet row
                cur.execute("""
                    SELECT * FROM wallet_accounts WHERE user_id = %s FOR UPDATE;
                """, (user_id,))
                wallet = cur.fetchone()
                if not wallet or wallet["available_balance"] < amount:
                    raise ValueError(f"Saldo tidak mencukupi untuk melakukan penarikan sebesar Rp {amount:,}.")

                # 2. Insert withdrawal request
                cur.execute("""
                    INSERT INTO withdrawals (
                        user_id, wallet_account_id, amount, fee, net_amount,
                        destination_bank, destination_account_name, destination_account_encrypted,
                        status, idempotency_key
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        'REQUESTED', %s
                    ) RETURNING *;
                """, (
                    user_id, wallet["id"], amount, fee, net_amount,
                    destination_bank, destination_account_name, destination_account_encrypted,
                    idempotency_key
                ))
                withdrawal = dict(cur.fetchone())

                # 3. Insert debit to ledger
                cur.execute("""
                    INSERT INTO wallet_ledger (
                        wallet_account_id, entry_type, direction, amount, currency,
                        reference_type, reference_id, idempotency_key, description
                    ) VALUES (
                        %s, 'WITHDRAWAL', 'DEBIT', %s, 'IDR',
                        'WITHDRAWAL', %s, %s, %s
                    );
                """, (
                    wallet["id"], amount, withdrawal["id"],
                    f"wd_{withdrawal['id']}", f"Penarikan dana ke {destination_bank} ({destination_account_name})"
                ))

                # 4. Deduct available balance
                cur.execute("""
                    UPDATE wallet_accounts
                    SET available_balance = available_balance - %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                """, (amount, wallet["id"]))

                conn.commit()
                return withdrawal

    def get_seller_withdrawals(self, user_id: int) -> List[Dict[str, Any]]:
        if not self.is_db_ready():
            return []
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT * FROM withdrawals
                        WHERE user_id = %s
                        ORDER BY created_at DESC;
                    """, (user_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("get_seller_withdrawals failed: %s", exc)
            return []

    def get_seller_sales_summary(self, seller_id: int) -> Dict[str, Any]:
        default_summary = {"total_orders": 0, "gross_sales": 0, "total_fees": 0, "net_sales": 0}
        if not self.is_db_ready():
            return default_summary
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            COALESCE(COUNT(*), 0) AS total_orders,
                            COALESCE(SUM(subtotal_amount), 0) AS gross_sales,
                            COALESCE(SUM(platform_fee_amount), 0) AS total_fees,
                            COALESCE(SUM(seller_net_amount), 0) AS net_sales
                        FROM orders
                        WHERE seller_id = %s AND status = 'PAID';
                    """, (seller_id,))
                    return dict(cur.fetchone())
        except Exception as exc:
            logger.warning("get_seller_sales_summary failed: %s", exc)
            return default_summary

    def get_seller_orders_list(self, seller_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.is_db_ready():
            return []
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            o.*, u.username AS buyer_username
                        FROM orders o
                        JOIN users u ON u.id = o.buyer_id
                        WHERE o.seller_id = %s AND o.status = 'PAID'
                        ORDER BY o.paid_at DESC
                        LIMIT %s;
                    """, (seller_id, limit))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("get_seller_orders_list failed: %s", exc)
            return []

    # ── ADMIN PLATFORM FINANCE ───────────────────────────────────────────────
    def get_admin_platform_finance(self) -> Dict[str, Any]:
        default_finance = {"total_paid_orders": 0, "gross_volume": 0, "total_admin_fees": 0, "total_seller_disbursed": 0, "admin_available_balance": 0}
        if not self.is_db_ready():
            return default_finance
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    # Overall marketplace stats
                    cur.execute("""
                        SELECT 
                            COALESCE(COUNT(*), 0) AS total_paid_orders,
                            COALESCE(SUM(subtotal_amount), 0) AS gross_volume,
                            COALESCE(SUM(platform_fee_amount), 0) AS total_admin_fees,
                            COALESCE(SUM(seller_net_amount), 0) AS total_seller_disbursed
                        FROM orders
                        WHERE status = 'PAID';
                    """)
                    summary = dict(cur.fetchone())

                    # Admin wallet balance
                    cur.execute("""
                        SELECT w.available_balance 
                        FROM wallet_accounts w
                        JOIN users u ON u.id = w.user_id
                        WHERE u.role = 'admin'
                        ORDER BY u.id ASC LIMIT 1;
                    """)
                    w_row = cur.fetchone()
                    summary["admin_available_balance"] = w_row["available_balance"] if w_row else 0

                    # Recent platform transactions
                    cur.execute("""
                        SELECT 
                            o.id, o.order_number, o.product_title_snapshot, o.subtotal_amount,
                            o.platform_fee_amount, o.seller_net_amount, o.paid_at,
                            sb.username AS buyer_username,
                            ss.username AS seller_username
                        FROM orders o
                        JOIN users sb ON sb.id = o.buyer_id
                        JOIN users ss ON ss.id = o.seller_id
                        WHERE o.status = 'PAID'
                        ORDER BY o.paid_at DESC
                        LIMIT 50;
                    """)
                    summary["transactions"] = [dict(r) for r in cur.fetchall()]
                    return summary
        except Exception as exc:
            logger.warning("get_admin_platform_finance failed: %s", exc)
            return default_finance

    def get_admin_orders(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        if not self.is_db_ready():
            return [], 0
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    where_clauses = []
                    params = []

                    if status and status.upper() not in ("ALL", ""):
                        where_clauses.append("o.status = %s")
                        params.append(status.upper())

                    if search and search.strip():
                        s = f"%{search.strip().lower()}%"
                        where_clauses.append("""(
                            LOWER(o.order_number) LIKE %s
                            OR LOWER(o.product_title_snapshot) LIKE %s
                            OR LOWER(sb.username) LIKE %s
                            OR LOWER(ss.username) LIKE %s
                        )""")
                        params.extend([s, s, s, s])

                    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

                    # Count total matching
                    cur.execute(f"""
                        SELECT COUNT(*)
                        FROM orders o
                        JOIN users sb ON sb.id = o.buyer_id
                        JOIN users ss ON ss.id = o.seller_id
                        {where_sql};
                    """, tuple(params))
                    total_count = cur.fetchone()["count"]

                    # Fetch rows
                    cur.execute(f"""
                        SELECT 
                            o.*,
                            sb.username AS buyer_username,
                            COALESCE(sb.google_email, '') AS buyer_email,
                            ss.username AS seller_username,
                            COALESCE(ss.google_email, '') AS seller_email,
                            pt.provider AS payment_provider,
                            pt.status AS payment_status,
                            pt.provider_transaction_id
                        FROM orders o
                        JOIN users sb ON sb.id = o.buyer_id
                        JOIN users ss ON ss.id = o.seller_id
                        LEFT JOIN payment_transactions pt ON pt.order_id = o.id
                        {where_sql}
                        ORDER BY o.created_at DESC
                        LIMIT %s OFFSET %s;
                    """, tuple(params + [limit, offset]))

                    orders = [dict(r) for r in cur.fetchall()]
                    return orders, total_count
        except Exception as exc:
            logger.warning("get_admin_orders failed: %s", exc)
            return [], 0

    def get_admin_order_detail(self, order_id_or_number: str) -> Optional[Dict[str, Any]]:
        if not self.is_db_ready():
            return None
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            o.*,
                            sb.username AS buyer_username,
                            sb.role AS buyer_role,
                            COALESCE(sb.google_email, '') AS buyer_email,
                            ss.username AS seller_username,
                            ss.role AS seller_role,
                            COALESCE(ss.google_email, '') AS seller_email,
                            p.slug AS product_slug,
                            v.version_label,
                            a.original_filename,
                            a.file_size,
                            a.sha256 AS binary_sha256,
                            sa.original_filename AS stl_original_filename,
                            sa.file_size AS stl_file_size,
                            sa.sha256 AS stl_sha256,
                            pt.provider AS payment_provider,
                            pt.provider_transaction_id,
                            pt.status AS payment_status,
                            pt.checkout_url
                        FROM orders o
                        JOIN users sb ON sb.id = o.buyer_id
                        JOIN users ss ON ss.id = o.seller_id
                        LEFT JOIN firmware_products p ON p.id = o.product_id
                        LEFT JOIN firmware_product_versions v ON v.id = o.product_version_id
                        LEFT JOIN firmware_assets a ON a.product_version_id = o.product_version_id
                        LEFT JOIN product_stl_assets sa ON sa.product_version_id = o.product_version_id
                        LEFT JOIN payment_transactions pt ON pt.order_id = o.id
                        WHERE o.id::text = %s OR o.order_number = %s
                        LIMIT 1;
                    """, (str(order_id_or_number), str(order_id_or_number)))
                    row = cur.fetchone()
                    if not row:
                        return None
                    order = dict(row)

                    # Also fetch webhook and ledger events for audit
                    cur.execute("""
                        SELECT l.entry_type, l.direction, l.amount, l.description, l.created_at, u.username
                        FROM wallet_ledger l
                        JOIN wallet_accounts w ON w.id = l.wallet_account_id
                        JOIN users u ON u.id = w.user_id
                        WHERE l.reference_id = %s
                        ORDER BY l.created_at ASC;
                    """, (order["id"],))
                    order["ledger_entries"] = [dict(r) for r in cur.fetchall()]

                    return order
        except Exception as exc:
            logger.warning("get_admin_order_detail failed: %s", exc)
            return None

    def get_admin_withdrawals(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.is_db_ready():
            return []
        try:
            from xiaozhi.marketplace.security import decrypt_sensitive_data
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    where_clause = ""
                    params = []
                    if status and status.upper() not in ("ALL", ""):
                        where_clause = "WHERE w.status = %s"
                        params.append(status.upper())

                    cur.execute(f"""
                        SELECT 
                            w.*,
                            u.username AS seller_username,
                            COALESCE(u.google_email, '') AS seller_email,
                            wa.available_balance AS seller_available_balance
                        FROM withdrawals w
                        JOIN users u ON u.id = w.user_id
                        JOIN wallet_accounts wa ON wa.id = w.wallet_account_id
                        {where_clause}
                        ORDER BY 
                            CASE WHEN w.status = 'REQUESTED' THEN 0
                                 WHEN w.status = 'PROCESSING' THEN 1
                                 ELSE 2 END,
                            w.created_at DESC
                        LIMIT %s;
                    """, tuple(params + [limit]))

                    withdrawals = []
                    for r in cur.fetchall():
                        item = dict(r)
                        # Decrypt account number safely for admin
                        enc_num = item.get("destination_account_encrypted") or ""
                        item["destination_account_number"] = decrypt_sensitive_data(enc_num) if enc_num else ""
                        withdrawals.append(item)
                    return withdrawals
        except Exception as exc:
            logger.warning("get_admin_withdrawals failed: %s", exc)
            return []

    def admin_process_withdrawal(
        self,
        withdrawal_id: str,
        action: str,
        admin_user_id: int,
        admin_notes: Optional[str] = None,
    ) -> bool:
        """
        Processes a seller withdrawal request:
        - approve: marks status = 'PAID', logs processed_at and admin reference.
        - reject: marks status = 'REJECTED', performs financial ledger reversal (credits amount back to seller).
        """
        if not self.is_db_ready():
            return False
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # Lock row
                cur.execute("""
                    SELECT * FROM withdrawals WHERE id = %s FOR UPDATE;
                """, (withdrawal_id,))
                wd = cur.fetchone()
                if not wd:
                    raise ValueError("Permintaan penarikan tidak ditemukan.")
                if wd["status"] not in ("REQUESTED", "PROCESSING"):
                    raise ValueError(f"Permintaan penarikan ini sudah diproses sebelumnya dengan status: {wd['status']}.")

                notes = (admin_notes or "").strip()

                if action == "approve":
                    cur.execute("""
                        UPDATE withdrawals
                        SET status = 'PAID', processed_at = CURRENT_TIMESTAMP, admin_notes = %s
                        WHERE id = %s;
                    """, (notes or "Disetujui oleh admin", withdrawal_id))

                    # Audit Log
                    cur.execute("""
                        INSERT INTO audit_logs (
                            actor_user_id, actor_role, action, entity_type, entity_id, reason
                        ) VALUES (%s, 'admin', 'WITHDRAWAL_PAID', 'withdrawal', %s, %s);
                    """, (admin_user_id, withdrawal_id, notes or "Transfer penarikan selesai"))

                    conn.commit()
                    return True

                elif action == "reject":
                    if not notes:
                        raise ValueError("Alasan penolakan penarikan dana wajib dicantumkan.")

                    cur.execute("""
                        UPDATE withdrawals
                        SET status = 'REJECTED', processed_at = CURRENT_TIMESTAMP, admin_notes = %s
                        WHERE id = %s;
                    """, (notes, withdrawal_id))

                    # FINANCIAL INTEGRITY: Refund balance back to seller available_balance
                    amount = wd["amount"]
                    wallet_id = wd["wallet_account_id"]

                    cur.execute("""
                        UPDATE wallet_accounts
                        SET available_balance = available_balance + %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s;
                    """, (amount, wallet_id))

                    # Double-entry ledger: WITHDRAWAL_REVERSAL
                    cur.execute("""
                        INSERT INTO wallet_ledger (
                            wallet_account_id, entry_type, direction, amount, currency,
                            reference_type, reference_id, idempotency_key, description
                        ) VALUES (
                            %s, 'WITHDRAWAL_REVERSAL', 'CREDIT', %s, 'IDR',
                            'WITHDRAWAL', %s, %s, %s
                        );
                    """, (
                        wallet_id, amount, withdrawal_id,
                        f"wd_rev_{withdrawal_id}", f"Pengembalian saldo penarikan ditolak: {notes}"
                    ))

                    # Audit Log
                    cur.execute("""
                        INSERT INTO audit_logs (
                            actor_user_id, actor_role, action, entity_type, entity_id, reason
                        ) VALUES (%s, 'admin', 'WITHDRAWAL_REJECTED', 'withdrawal', %s, %s);
                    """, (admin_user_id, withdrawal_id, notes))

                    conn.commit()
                    return True
                else:
                    raise ValueError(f"Aksi tidak valid: {action}")

    # ── CHAT ─────────────────────────────────────────────────────────────────
    def get_or_create_conversation(self, product_id: str, seller_id: int, buyer_id: int) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO chat_conversations (product_id, seller_id, buyer_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (product_id, seller_id, buyer_id) DO UPDATE 
                    SET last_message_at = CURRENT_TIMESTAMP
                    RETURNING *;
                """, (product_id, seller_id, buyer_id))
                conv = dict(cur.fetchone())
                conn.commit()
                return conv

    def get_messages(self, conversation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.*, u.username AS sender_username
                    FROM chat_messages m
                    JOIN users u ON u.id = m.sender_id
                    WHERE m.conversation_id = %s
                    ORDER BY m.created_at ASC
                    LIMIT %s;
                """, (conversation_id, limit))
                return [dict(r) for r in cur.fetchall()]

    def send_message(self, conversation_id: str, sender_id: int, body: str) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO chat_messages (conversation_id, sender_id, body)
                    VALUES (%s, %s, %s)
                    RETURNING *;
                """, (conversation_id, sender_id, body.strip()))
                msg = dict(cur.fetchone())
                cur.execute("""
                    UPDATE chat_conversations 
                    SET last_message_at = CURRENT_TIMESTAMP 
                    WHERE id = %s;
                """, (conversation_id,))
                conn.commit()
                return msg
