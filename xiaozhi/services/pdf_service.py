"""PDF upload and text extraction service."""
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("xiaozhi.pdf")

# Try to import PDF libraries
try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from pdfminer.high_level import extract_text as pdfminer_extract
    HAS_PDFMINER = True
except ImportError:
    HAS_PDFMINER = False


def extract_text_from_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    """Extract text from PDF bytes."""
    result = {
        "success": False,
        "text": "",
        "pages": 0,
        "error": None,
    }

    if not pdf_bytes:
        result["error"] = "File PDF kosong"
        return result

    # Try PyMuPDF first (faster)
    if HAS_PYMUPDF:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                pages.append(page.get_text())
            doc.close()
            result["text"] = "\n\n---\n\n".join(pages)
            result["pages"] = len(pages)
            result["success"] = True
            return result
        except Exception as e:
            logger.warning(f"PyMuPDF failed: {e}")

    # Fallback to pdfminer
    if HAS_PDFMINER:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(pdf_bytes)
                tmp_path = tmp.name
            text = pdfminer_extract(tmp_path)
            os.unlink(tmp_path)
            result["text"] = text
            result["pages"] = text.count("\f") + 1  # Form feed = page break
            result["success"] = True
            return result
        except Exception as e:
            logger.warning(f"pdfminer failed: {e}")

    result["error"] = "Tidak ada library PDF yang tersedia. Install PyMuPDF: pip install pymupdf"
    return result


def validate_pdf_upload(file_bytes: bytes, max_size_mb: int = 10) -> Dict[str, Any]:
    """Validate PDF file before processing."""
    if not file_bytes:
        return {"valid": False, "error": "File kosong"}

    if len(file_bytes) > max_size_mb * 1024 * 1024:
        return {"valid": False, "error": f"File terlalu besar. Maksimal {max_size_mb}MB"}

    # Check PDF magic bytes
    if not file_bytes[:4] == b'%PDF':
        return {"valid": False, "error": "File bukan PDF valid"}

    return {"valid": True}


def create_material_from_pdf(pdf_bytes: bytes, title: str, category: str, owner_id: int) -> Dict[str, Any]:
    """Create a material from uploaded PDF."""
    validation = validate_pdf_upload(pdf_bytes)
    if not validation["valid"]:
        return {"success": False, "error": validation["error"]}

    extraction = extract_text_from_pdf(pdf_bytes)
    if not extraction["success"]:
        return {"success": False, "error": extraction["error"]}

    content = extraction["text"]
    if len(content.strip()) < 10:
        return {"success": False, "error": "PDF tidak mengandung text yang bisa dibaca"}

    # Truncate if too long
    max_bytes = 5 * 1024 * 1024  # 5MB
    if len(content.encode("utf-8")) > max_bytes:
        content = content[:max_bytes // 4] + "\n\n[Content truncated...]"

    return {
        "success": True,
        "title": title,
        "category": category,
        "content": content,
        "pages": extraction["pages"],
        "size_bytes": len(content.encode("utf-8")),
    }
