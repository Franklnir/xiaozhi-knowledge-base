import re
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from xiaozhi.marketplace.repository import MarketplaceRepository
from xiaozhi.marketplace.storage import storage_service


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text or "firmware"


class ProductService:
    def __init__(self, repo: MarketplaceRepository):
        self.repo = repo

    def create_product(
        self,
        seller: Dict[str, Any],
        title: str,
        short_description: str,
        full_description: str,
        price_amount: int,
        images_data: List[bytes],
        firmware_bytes: Optional[bytes] = None,
        firmware_filename: Optional[str] = None,
        stl_bytes: Optional[bytes] = None,
        stl_filename: Optional[str] = None,
        links: Optional[List[Dict[str, str]]] = None,
        version_label: str = "1.0.0",
    ) -> Dict[str, Any]:
        seller_id = int(seller["id"])
        is_admin = (str(seller.get("role", "")).lower() == "admin")

        title = title.strip()
        if len(title) < 3 or len(title) > 160:
            raise ValueError("Judul produk harus antara 3 hingga 160 karakter.")
        if len(short_description.strip()) > 500:
            raise ValueError("Deskripsi singkat maksimal 500 karakter.")
        if price_amount < 0:
            raise ValueError("Harga tidak boleh negatif.")
        if not images_data or len(images_data) == 0:
            raise ValueError("Wajib mengunggah minimal 1 foto produk (maksimal 3 foto).")
        if len(images_data) > 3:
            raise ValueError("Maksimal 3 foto produk diperbolehkan.")

        has_fw = bool(firmware_bytes and len(firmware_bytes) > 0 and firmware_filename)
        has_stl = bool(stl_bytes and len(stl_bytes) > 0 and stl_filename)
        if not has_fw and not has_stl:
            raise ValueError("Wajib mengunggah minimal salah satu file: Binary Firmware (.bin) atau File 3D Model (.stl).")

        import io

        # 1. Validate firmware if present
        fw_meta = None
        firmware_io = None
        if has_fw:
            firmware_io = io.BytesIO(firmware_bytes)
            fw_meta = storage_service.validate_firmware(firmware_filename, firmware_io)

        # 2. Validate STL if present
        stl_meta = None
        stl_io = None
        if has_stl:
            stl_io = io.BytesIO(stl_bytes)
            stl_meta = storage_service.validate_stl(stl_filename, stl_io)

        # 3. Process and validate images
        processed_images = []
        for idx, img_b in enumerate(images_data[:3]):
            p_img = storage_service.process_and_validate_image(img_b, f"img_{idx}.webp")
            processed_images.append(p_img)

        # 4. Create slug
        base_slug = slugify(title)
        slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

        # 5. Insert product & initial version
        product = self.repo.create_product(
            seller_id=seller_id,
            title=title,
            slug=slug,
            short_description=short_description,
            full_description=full_description,
            price_amount=price_amount,
            is_admin=is_admin,
            version_label=version_label,
        )
        product_id = str(product["id"])
        version_id = str(product["version_id"])

        # 6. Save images to storage and save to DB
        db_images = []
        for p_img in processed_images:
            storage_key = storage_service.save_product_image(product_id, p_img["bytes"])
            db_images.append({
                "storage_key": storage_key,
                "mime_type": p_img["mime_type"],
                "file_size": p_img["file_size"],
                "width": p_img["width"],
                "height": p_img["height"],
                "sha256": p_img["sha256"],
            })
        self.repo.set_product_images(product_id, db_images)

        # 7. Save firmware to private storage and save to DB
        if has_fw and firmware_io and fw_meta:
            firmware_io.seek(0)
            bucket, fw_key = storage_service.save_firmware(seller_id, product_id, version_id, firmware_io)
            self.repo.set_firmware_asset(
                product_version_id=version_id,
                original_filename=fw_meta["original_filename"],
                storage_bucket=bucket,
                storage_key=fw_key,
                file_size=fw_meta["file_size"],
                sha256=fw_meta["sha256"],
            )

        # 8. Save STL to private storage and save to DB
        if has_stl and stl_io and stl_meta:
            stl_io.seek(0)
            stl_bucket, stl_key = storage_service.save_stl(seller_id, product_id, version_id, stl_io)
            self.repo.set_stl_asset(
                product_version_id=version_id,
                original_filename=stl_meta["original_filename"],
                storage_bucket=stl_bucket,
                storage_key=stl_key,
                file_size=stl_meta["file_size"],
                sha256=stl_meta["sha256"],
            )

        # 9. Set links if any
        if links:
            self.repo.set_product_links(product_id, links)

        return product

    def get_marketplace_list(
        self,
        limit: int = 20,
        cursor_time: Optional[str] = None,
        cursor_id: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        products = self.repo.get_published_products(
            limit=limit,
            cursor_time=cursor_time,
            cursor_id=cursor_id,
            search=search,
        )
        for p in products:
            p["image_url"] = storage_service.get_image_url(p.get("primary_image_key", ""))
        return products

    def get_product_detail(self, product_id_or_slug: str, current_user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        # Try UUID first, then slug
        product = None
        try:
            uuid.UUID(str(product_id_or_slug))
            product = self.repo.get_product_by_id(str(product_id_or_slug))
        except (ValueError, TypeError):
            pass

        if not product:
            product = self.repo.get_product_by_slug(str(product_id_or_slug))

        if not product:
            return None

        # Fetch images
        raw_images = self.repo.get_product_images(str(product["id"]))
        images = []
        for img in raw_images:
            img["url"] = storage_service.get_image_url(img["storage_key"])
            images.append(img)
        product["images"] = images

        # Fetch links
        product["links"] = self.repo.get_product_links(str(product["id"]))

        # Fetch latest version & asset info
        latest_ver = self.repo.get_latest_version(str(product["id"]))
        product["latest_version"] = latest_ver

        # Check if current user already purchased / owns this
        product["is_owned"] = False
        product["is_seller"] = False
        if current_user_id:
            if current_user_id == product["seller_id"]:
                product["is_seller"] = True
            else:
                product["is_owned"] = self.repo.check_existing_entitlement(current_user_id, str(product["id"]))

        return product

    def submit_for_review(self, product_id: str, seller_id: int) -> Dict[str, Any]:
        return self.repo.submit_product_for_approval(product_id, seller_id)

    def update_product(
        self,
        product_id: str,
        user: Dict[str, Any],
        title: str,
        short_description: str,
        full_description: str,
        price_amount: int,
        images_data: Optional[List[bytes]] = None,
        firmware_bytes: Optional[bytes] = None,
        firmware_filename: Optional[str] = None,
        stl_bytes: Optional[bytes] = None,
        stl_filename: Optional[str] = None,
        links: Optional[List[Dict[str, str]]] = None,
        version_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id = int(user["id"])
        is_admin = (str(user.get("role", "")).lower() == "admin")

        prod = self.repo.get_product_by_id(product_id)
        if not prod:
            raise ValueError("Produk tidak ditemukan.")
        if not is_admin and prod["seller_id"] != user_id:
            raise PermissionError("Anda tidak memiliki izin mengedit produk ini.")

        title = title.strip()
        if len(title) < 3 or len(title) > 160:
            raise ValueError("Judul produk harus antara 3 hingga 160 karakter.")
        if len(short_description.strip()) > 500:
            raise ValueError("Deskripsi singkat maksimal 500 karakter.")
        if price_amount < 0:
            raise ValueError("Harga tidak boleh negatif.")

        # Update core details
        updated = self.repo.update_product_details(
            product_id=product_id,
            seller_id=prod["seller_id"],
            title=title,
            short_description=short_description,
            full_description=full_description,
            price_amount=price_amount,
            expected_version=prod["version"],
        )
        if not updated:
            raise ValueError("Konflik versi: data telah diubah sebelumnya. Muat ulang halaman.")

        # If new images uploaded
        if images_data and len(images_data) > 0:
            db_images = []
            for idx, img_b in enumerate(images_data[:3]):
                p_img = storage_service.process_and_validate_image(img_b, f"img_{idx}.webp")
                storage_key = storage_service.save_product_image(product_id, p_img["bytes"])
                db_images.append({
                    "storage_key": storage_key,
                    "mime_type": p_img["mime_type"],
                    "file_size": p_img["file_size"],
                    "width": p_img["width"],
                    "height": p_img["height"],
                    "sha256": p_img["sha256"],
                })
            self.repo.set_product_images(product_id, db_images)

        has_new_fw = bool(firmware_bytes and len(firmware_bytes) > 0 and firmware_filename)
        has_new_stl = bool(stl_bytes and len(stl_bytes) > 0 and stl_filename)

        # If new binary or STL uploaded -> create new version
        if has_new_fw or has_new_stl:
            import io
            fw_meta = None
            fw_io = None
            if has_new_fw:
                fw_io = io.BytesIO(firmware_bytes)
                fw_meta = storage_service.validate_firmware(firmware_filename, fw_io)

            stl_meta = None
            stl_io = None
            if has_new_stl:
                stl_io = io.BytesIO(stl_bytes)
                stl_meta = storage_service.validate_stl(stl_filename, stl_io)

            v_label = version_label or f"v{prod['version'] + 1}.0"
            v_status = "PUBLISHED" if is_admin else "DRAFT"

            with self.repo._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO firmware_product_versions (
                            product_id, version_label, release_notes, status, published_at, created_by
                        ) VALUES (%s, %s, 'Updated Version', %s, %s, %s)
                        RETURNING id;
                    """, (product_id, v_label, v_status, datetime.now(timezone.utc) if is_admin else None, user_id))
                    new_ver_id = str(cur.fetchone()["id"])

                    # If not admin, revert product to DRAFT so seller must resubmit review
                    if not is_admin:
                        cur.execute("UPDATE firmware_products SET status = 'DRAFT' WHERE id = %s;", (product_id,))

                    conn.commit()

            if has_new_fw and fw_io and fw_meta:
                fw_io.seek(0)
                bucket, fw_key = storage_service.save_firmware(prod["seller_id"], product_id, new_ver_id, fw_io)
                self.repo.set_firmware_asset(
                    product_version_id=new_ver_id,
                    original_filename=fw_meta["original_filename"],
                    storage_bucket=bucket,
                    storage_key=fw_key,
                    file_size=fw_meta["file_size"],
                    sha256=fw_meta["sha256"],
                )

            if has_new_stl and stl_io and stl_meta:
                stl_io.seek(0)
                stl_bucket, stl_key = storage_service.save_stl(prod["seller_id"], product_id, new_ver_id, stl_io)
                self.repo.set_stl_asset(
                    product_version_id=new_ver_id,
                    original_filename=stl_meta["original_filename"],
                    storage_bucket=stl_bucket,
                    storage_key=stl_key,
                    file_size=stl_meta["file_size"],
                    sha256=stl_meta["sha256"],
                )

        # Update links if provided
        if links is not None:
            self.repo.set_product_links(product_id, links)

        return updated

    def delete_product(self, product_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
        user_id = int(user["id"])
        is_admin = (str(user.get("role", "")).lower() == "admin")
        return self.repo.delete_or_archive_product(product_id, user_id, is_admin=is_admin)

