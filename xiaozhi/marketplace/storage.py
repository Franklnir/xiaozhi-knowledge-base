import io
import os
import uuid
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from PIL import Image

from xiaozhi.config import (
    S3_ENDPOINT,
    S3_REGION,
    S3_BUCKET_PUBLIC,
    S3_BUCKET_PRIVATE,
    S3_ACCESS_KEY,
    S3_SECRET_KEY,
    S3_USE_SSL,
)

BASE_DIR = Path(__file__).resolve().parents[2]

from xiaozhi.marketplace.security import (
    MAX_IMAGE_SIZE_BYTES,
    MAX_FIRMWARE_SIZE_BYTES,
    calculate_sha256_stream,
    generate_download_token,
)

# Prevent decompression bombs
Image.MAX_IMAGE_PIXELS = 10_000_000

# Local storage fallback directories
LOCAL_STORAGE_DIR = BASE_DIR / "data" / "marketplace_storage"
LOCAL_PUBLIC_IMAGES_DIR = LOCAL_STORAGE_DIR / "public" / "images"
LOCAL_PRIVATE_FIRMWARE_DIR = LOCAL_STORAGE_DIR / "private" / "firmwares"
LOCAL_PRIVATE_STL_DIR = LOCAL_STORAGE_DIR / "private" / "stl_files"


class StorageService:
    """
    Storage Service supporting S3-compatible Object Storage (NepalCloud / AWS / MinIO)
    with seamless zero-dependency Local Filesystem Fallback.
    """
    def __init__(self):
        self.has_s3 = bool(S3_ENDPOINT and S3_ACCESS_KEY and S3_SECRET_KEY)
        self._s3_client = None
        
        # Ensure local directories exist
        LOCAL_PUBLIC_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_PRIVATE_FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_PRIVATE_STL_DIR.mkdir(parents=True, exist_ok=True)

    def _get_s3(self):
        if not self.has_s3:
            return None
        if self._s3_client is None:
            try:
                import boto3
                from botocore.client import Config
                self._s3_client = boto3.client(
                    "s3",
                    endpoint_url=S3_ENDPOINT,
                    region_name=S3_REGION,
                    aws_access_key_id=S3_ACCESS_KEY,
                    aws_secret_access_key=S3_SECRET_KEY,
                    use_ssl=S3_USE_SSL,
                    config=Config(signature_version="s3", s3={"addressing_style": "path"}),
                )
            except Exception:
                self._s3_client = None
        return self._s3_client

    def process_and_validate_image(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        """
        Validates, strips EXIF, and converts image to optimized WebP format (<= 500 KB).
        """
        if len(file_bytes) == 0:
            raise ValueError("File gambar tidak boleh kosong.")
        if len(file_bytes) > 10 * 1024 * 1024:  # Initial raw upload guard 10MB
            raise ValueError("Ukuran file gambar mentah terlalu besar. Maksimal 10 MB per foto.")

        try:
            with Image.open(io.BytesIO(file_bytes)) as img:
                # Security: verify format
                if img.format not in ("JPEG", "PNG", "WEBP", "JPG"):
                    raise ValueError(f"Format gambar {img.format} tidak didukung. Gunakan PNG, JPEG, atau WebP.")
                
                # Resize if excessively large to save memory
                max_dim = 1400
                if img.width > max_dim or img.height > max_dim:
                    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

                # Convert to RGB (or RGBA if transparency)
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    converted = img.convert("RGBA")
                else:
                    converted = img.convert("RGB")

                # Initial compression pass
                quality = 82
                out_buffer = io.BytesIO()
                converted.save(out_buffer, format="WEBP", quality=quality, method=4)
                processed_bytes = out_buffer.getvalue()

                # Adaptive multi-pass compression: reduce quality and dimension if > 500 KB
                while len(processed_bytes) > MAX_IMAGE_SIZE_BYTES and quality > 35:
                    quality -= 15
                    out_buffer = io.BytesIO()
                    converted.save(out_buffer, format="WEBP", quality=quality, method=5)
                    processed_bytes = out_buffer.getvalue()

                # If still over 500 KB after quality drop, downscale dimensions
                if len(processed_bytes) > MAX_IMAGE_SIZE_BYTES:
                    converted.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                    out_buffer = io.BytesIO()
                    converted.save(out_buffer, format="WEBP", quality=60, method=5)
                    processed_bytes = out_buffer.getvalue()

                if len(processed_bytes) > MAX_IMAGE_SIZE_BYTES:
                    converted.thumbnail((800, 800), Image.Resampling.LANCZOS)
                    out_buffer = io.BytesIO()
                    converted.save(out_buffer, format="WEBP", quality=50, method=5)
                    processed_bytes = out_buffer.getvalue()

                width, height = converted.size
        except Exception as e:
            if "decompression bomb" in str(e).lower():
                raise ValueError("Gambar terdeteksi berbahaya (decompression bomb).")
            raise ValueError(f"Gagal memproses gambar: {str(e)}")

        if len(processed_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise ValueError(f"Ukuran gambar hasil kompresi ({len(processed_bytes)} bytes) melebihi batas 500 KB (512000 bytes).")

        import hashlib
        sha256_hash = hashlib.sha256(processed_bytes).hexdigest()

        return {
            "bytes": processed_bytes,
            "mime_type": "image/webp",
            "width": width,
            "height": height,
            "file_size": len(processed_bytes),
            "sha256": sha256_hash,
        }

    def validate_firmware(self, filename: str, file_obj) -> Dict[str, Any]:
        """
        Validates .bin extension, computes SHA-256 using memory-efficient chunked streaming.
        """
        clean_name = Path(filename).name.strip()
        if not clean_name.lower().endswith(".bin"):
            raise ValueError("Hanya file firmware berekstensi .bin yang diperbolehkan.")
        
        sha256_hash, total_bytes = calculate_sha256_stream(file_obj)
        if total_bytes == 0:
            raise ValueError("File firmware .bin tidak boleh kosong (0 bytes).")
        if total_bytes > MAX_FIRMWARE_SIZE_BYTES:
            raise ValueError(f"Ukuran firmware ({total_bytes / (1024*1024):.1f} MB) melebihi batas sistem ({MAX_FIRMWARE_SIZE_BYTES / (1024*1024):.0f} MB).")

        return {
            "original_filename": clean_name,
            "file_size": total_bytes,
            "sha256": sha256_hash,
        }

    def validate_stl(self, filename: str, file_obj) -> Dict[str, Any]:
        """
        Validates .stl extension, computes SHA-256 using memory-efficient chunked streaming.
        """
        clean_name = Path(filename).name.strip()
        if not clean_name.lower().endswith(".stl"):
            raise ValueError("Hanya file model 3D berekstensi .stl yang diperbolehkan.")
        
        sha256_hash, total_bytes = calculate_sha256_stream(file_obj)
        if total_bytes == 0:
            raise ValueError("File model 3D .stl tidak boleh kosong (0 bytes).")
        if total_bytes > MAX_FIRMWARE_SIZE_BYTES:
            raise ValueError(f"Ukuran model 3D ({total_bytes / (1024*1024):.1f} MB) melebihi batas sistem ({MAX_FIRMWARE_SIZE_BYTES / (1024*1024):.0f} MB).")

        return {
            "original_filename": clean_name,
            "file_size": total_bytes,
            "sha256": sha256_hash,
        }

    def save_product_image(self, product_id: str, image_bytes: bytes) -> str:
        """Saves image and returns storage_key."""
        img_id = uuid.uuid4().hex
        storage_key = f"product-images/{product_id}/{img_id}.webp"
        
        s3 = self._get_s3()
        if s3:
            s3.put_object(
                Bucket=S3_BUCKET_PUBLIC,
                Key=storage_key,
                Body=image_bytes,
                ContentType="image/webp",
            )
        else:
            # Local storage
            local_path = LOCAL_PUBLIC_IMAGES_DIR / product_id
            local_path.mkdir(parents=True, exist_ok=True)
            target_file = local_path / f"{img_id}.webp"
            with open(target_file, "wb") as f:
                f.write(image_bytes)

        return storage_key

    def save_firmware(self, seller_id: int, product_id: str, version_id: str, file_obj) -> Tuple[str, str]:
        """
        Saves firmware binary to private storage using chunked stream.
        Returns (storage_bucket, storage_key).
        """
        random_id = uuid.uuid4().hex
        storage_key = f"firmwares/{seller_id}/{product_id}/{version_id}/{random_id}.bin"
        bucket_name = S3_BUCKET_PRIVATE if self.has_s3 else "local-private"

        s3 = self._get_s3()
        if s3:
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            s3.upload_fileobj(
                file_obj,
                Bucket=bucket_name,
                Key=storage_key,
                ExtraArgs={"ContentType": "application/octet-stream"},
            )
        else:
            # Local private storage
            target_file = LOCAL_PRIVATE_FIRMWARE_DIR / f"{random_id}.bin"
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            with open(target_file, "wb") as dest:
                while True:
                    chunk = file_obj.read(65536)
                    if not chunk:
                        break
                    dest.write(chunk)

        return bucket_name, storage_key

    def save_stl(self, seller_id: int, product_id: str, version_id: str, file_obj) -> Tuple[str, str]:
        """
        Saves 3D model STL to private storage using chunked stream.
        Returns (storage_bucket, storage_key).
        """
        random_id = uuid.uuid4().hex
        storage_key = f"stl_models/{seller_id}/{product_id}/{version_id}/{random_id}.stl"
        bucket_name = S3_BUCKET_PRIVATE if self.has_s3 else "local-private"

        s3 = self._get_s3()
        if s3:
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            s3.upload_fileobj(
                file_obj,
                Bucket=bucket_name,
                Key=storage_key,
                ExtraArgs={"ContentType": "model/stl"},
            )
        else:
            # Local private storage
            target_file = LOCAL_PRIVATE_STL_DIR / f"{random_id}.stl"
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            with open(target_file, "wb") as dest:
                while True:
                    chunk = file_obj.read(65536)
                    if not chunk:
                        break
                    dest.write(chunk)

        return bucket_name, storage_key

    def get_image_url(self, storage_key: str) -> str:
        """Returns public or local-served URL for a product image."""
        if not storage_key:
            return "/static/img/default-firmware.png"
        
        s3 = self._get_s3()
        if s3:
            try:
                return s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": S3_BUCKET_PUBLIC, "Key": storage_key},
                    ExpiresIn=86400,
                )
            except Exception:
                endpoint = S3_ENDPOINT.rstrip("/")
                return f"{endpoint}/{S3_BUCKET_PUBLIC}/{storage_key}"
        
        # Local served route
        return f"/api/v1/marketplace/images/{storage_key}"


    def get_firmware_download_url(self, purchase_id: str, storage_key: str, filename: str, expires_in_seconds: int = 600) -> str:
        """
        Generates a short-lived signed download URL (5-10 minutes).
        """
        s3 = self._get_s3()
        if s3:
            return s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": S3_BUCKET_PRIVATE,
                    "Key": storage_key,
                    "ResponseContentDisposition": f'attachment; filename="{filename}"',
                },
                ExpiresIn=expires_in_seconds,
            )
        
        # Local secured download token
        token = generate_download_token(purchase_id, storage_key, expires_in_seconds)
        return f"/api/v1/marketplace/storage/download/{token}?filename={filename}"

    def get_stl_download_url(self, purchase_id: str, storage_key: str, filename: str, expires_in_seconds: int = 600) -> str:
        """
        Generates a short-lived signed download URL (10 minutes) for 3D model STL.
        """
        s3 = self._get_s3()
        if s3:
            return s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": S3_BUCKET_PRIVATE,
                    "Key": storage_key,
                    "ResponseContentDisposition": f'attachment; filename="{filename}"',
                },
                ExpiresIn=expires_in_seconds,
            )
        
        # Local secured download token
        token = generate_download_token(purchase_id, storage_key, expires_in_seconds)
        return f"/api/v1/marketplace/storage/download/{token}?filename={filename}"

    def get_local_firmware_path(self, storage_key: str) -> Optional[Path]:
        """Returns Path to local private file (.bin or .stl) after security sanitization."""
        filename = Path(storage_key).name
        # Check firmware dir
        target = LOCAL_PRIVATE_FIRMWARE_DIR / filename
        if target.exists() and target.is_file():
            return target
        # Check STL dir
        target_stl = LOCAL_PRIVATE_STL_DIR / filename
        if target_stl.exists() and target_stl.is_file():
            return target_stl
        return None

    def get_local_stl_path(self, storage_key: str) -> Optional[Path]:
        """Returns Path to local private STL file after security sanitization."""
        filename = Path(storage_key).name
        target = LOCAL_PRIVATE_STL_DIR / filename
        if target.exists() and target.is_file():
            return target
        return None

    def get_local_image_path(self, storage_key: str) -> Optional[Path]:
        """Returns Path to local public image file."""
        # Key format: product-images/{product_id}/{img_id}.webp
        parts = storage_key.split("/")
        if len(parts) >= 3 and parts[0] == "product-images":
            target = LOCAL_PUBLIC_IMAGES_DIR / parts[1] / parts[2]
            if target.exists() and target.is_file():
                return target
        return None


# Global storage service instance
storage_service = StorageService()
