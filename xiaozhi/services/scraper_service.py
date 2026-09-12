"""URL scraping service for extracting web content."""
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger("xiaozhi.scraper")

# Try to import scraping libraries
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


def validate_scrape_url(url: str) -> Dict[str, Any]:
    """Validate URL before scraping."""
    if not url:
        return {"valid": False, "error": "URL kosong"}

    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"valid": False, "error": "URL harus http atau https"}
        if not parsed.netloc:
            return {"valid": False, "error": "URL tidak valid"}
        # Block local/private IPs
        if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
            return {"valid": False, "error": "URL lokal tidak diizinkan"}
        return {"valid": True}
    except Exception:
        return {"valid": False, "error": "URL tidak valid"}


def scrape_url(url: str, max_length: int = 100000) -> Dict[str, Any]:
    """Scrape content from URL."""
    if not HAS_REQUESTS:
        return {"success": False, "error": "Library requests tidak tersedia"}

    validation = validate_scrape_url(url)
    if not validation["valid"]:
        return {"success": False, "error": validation["error"]}

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; XiaozhiBot/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()

        html = response.content.decode(charset, errors="replace")

        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")

            # Remove script and style elements
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            text = soup.get_text(separator="\n", strip=True)
        else:
            # Simple HTML stripping
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()

        # Clean up text
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        text = "\n".join(lines)

        if len(text) > max_length:
            text = text[:max_length] + "\n\n[Content truncated...]"

        return {
            "success": True,
            "url": url,
            "title": title,
            "content": text,
            "content_type": content_type,
            "size_bytes": len(text.encode("utf-8")),
        }

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Timeout - website terlalu lama merespon"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Tidak bisa terhubung ke website"}
    except requests.exceptions.HTTPError as e:
        return {"success": False, "error": f"HTTP error: {e.response.status_code}"}
    except Exception as e:
        return {"success": False, "error": f"Error: {str(e)[:100]}"}


def create_material_from_url(url: str, title: str, category: str) -> Dict[str, Any]:
    """Create a material from scraped URL content."""
    result = scrape_url(url)
    if not result["success"]:
        return result

    content = f"Sumber: {url}\n\n{result['content']}"
    if not title:
        title = result.get("title", "")[:160] or "Scraped Content"

    return {
        "success": True,
        "title": title,
        "category": category,
        "content": content,
        "source_url": url,
        "size_bytes": result["size_bytes"],
    }
