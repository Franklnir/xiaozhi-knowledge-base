"""URL scraping service for extracting web content with SSRF protection."""
import ipaddress
import logging
import re
import socket
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
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


def validate_scrape_url(url: str) -> Dict[str, Any]:
    """
    Validate URL before scraping with comprehensive SSRF protection.
    Blocks private IP addresses, loopback, link-local, and reserved ranges.
    """
    if not url or not isinstance(url, str):
        return {"valid": False, "error": "URL kosong atau tidak valid"}

    url_clean = url.strip()
    try:
        parsed = urlparse(url_clean)
        if parsed.scheme not in ("http", "https"):
            return {"valid": False, "error": "Protokol URL harus HTTP atau HTTPS"}
        
        hostname = parsed.hostname
        if not hostname:
            return {"valid": False, "error": "Hostname URL tidak valid"}

        # Block literal local hostnames
        if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return {"valid": False, "error": "Akses ke host lokal diblokir demi keamanan (SSRF Protection)"}

        # Resolve hostname to IP to block private/internal cloud networks
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for family, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                ip = ipaddress.ip_address(ip_str)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip_str.startswith("169.254.")  # Cloud instance metadata service
                ):
                    return {
                        "valid": False,
                        "error": f"Akses ke alamat IP jaringan privat/lokal ({ip_str}) diblokir demi keamanan",
                    }
        except socket.gaierror:
            return {"valid": False, "error": f"Domain '{hostname}' tidak dapat ditemukan atau diakses"}
        except ValueError:
            pass  # Not a standard IP parsing issue

        return {"valid": True, "url": url_clean, "hostname": hostname}
    except Exception as e:
        return {"valid": False, "error": f"URL tidak valid: {str(e)[:80]}"}


def _extract_page_data_bs4(html: str, max_length: int) -> Dict[str, Any]:
    """Extract clean title, description, keywords, and body text using BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted tags
    unwanted_tags = [
        "script", "style", "nav", "footer", "header", "aside", "noscript",
        "iframe", "form", "svg", "button", "input", "select", "textarea"
    ]
    for tag in soup(unwanted_tags):
        tag.decompose()

    # Also remove common advertisement and cookie banners by class/id (use word boundaries to avoid matching words like lead, ready, reading)
    ad_pattern = re.compile(r"(\bads?\b|\badvertisement\b|cookie-banner|popup-banner)", re.I)
    for el in soup.find_all(attrs={"class": ad_pattern}):
        if el.name not in ("body", "main", "article", "html"):
            el.decompose()

    # 1. Extract Title
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.h1:
        title = soup.h1.get_text().strip()

    # 2. Extract Description & Keywords
    description = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        description = og_desc["content"].strip()
    else:
        meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"].strip()

    keywords = ""
    meta_kw = soup.find("meta", attrs={"name": re.compile(r"^keywords$", re.I)})
    if meta_kw and meta_kw.get("content"):
        keywords = meta_kw["content"].strip()

    # 3. Extract Main Content
    main_el = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find(class_=re.compile(r"(article-content|post-content|entry-content|story-body|main-content)", re.I))
        or soup.find(id=re.compile(r"(content|main|article)", re.I))
        or soup.body
        or soup
    )

    # Get structured paragraphs or text blocks
    paragraphs = []
    for p in main_el.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"]):
        t = p.get_text(strip=True)
        if t and len(t) > 15:
            # Prefix headers with markdown formatting for better AI readability
            if p.name in ("h1", "h2", "h3"):
                paragraphs.append(f"\n### {t}")
            elif p.name == "li":
                paragraphs.append(f"• {t}")
            else:
                paragraphs.append(t)

    if paragraphs:
        text = "\n\n".join(paragraphs)
    else:
        text = main_el.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        text = "\n".join(lines)

    # Clean redundant whitespace
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    is_truncated = False
    if len(text) > max_length:
        text = text[:max_length] + "\n\n[...Konten dipotong untuk batas memori...]"
        is_truncated = True

    return {
        "title": title,
        "description": description,
        "keywords": keywords,
        "content": text,
        "is_truncated": is_truncated,
    }


def _extract_page_data_regex(html: str, max_length: int) -> Dict[str, Any]:
    """Fallback text extractor if BeautifulSoup is not installed."""
    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""
    if not title:
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
        title = h1_match.group(1).strip() if h1_match else ""

    # Remove script, style, comments
    cleaned = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    cleaned = re.sub(r"<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    is_truncated = False
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "\n\n[...Konten dipotong...]"
        is_truncated = True

    return {
        "title": title,
        "description": "",
        "keywords": "",
        "content": cleaned,
        "is_truncated": is_truncated,
    }


def scrape_url(url: str, max_length: int = 25000) -> Dict[str, Any]:
    """
    Synchronously scrape content from a URL with SSRF protection.
    Returns cleaned title, description, keywords, main content, and metadata.
    """
    if not HAS_REQUESTS:
        return {"success": False, "error": "Library requests tidak tersedia di sistem"}

    validation = validate_scrape_url(url)
    if not validation["valid"]:
        return {"success": False, "error": validation["error"]}

    safe_url = validation["url"]
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        response = requests.get(safe_url, headers=headers, timeout=12, allow_redirects=True)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type and "text/plain" not in content_type:
            return {
                "success": False,
                "error": f"Tipe konten '{content_type}' bukan halaman web HTML yang dapat di-scrape",
            }

        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip().strip('"').strip("'")

        html = response.content.decode(charset, errors="replace")

        if HAS_BS4:
            extracted = _extract_page_data_bs4(html, max_length)
        else:
            extracted = _extract_page_data_regex(html, max_length)

        title = extracted["title"] or validation.get("hostname", "Webpage")
        content = extracted["content"]

        if not content:
            return {"success": False, "error": "Tidak ada teks artikel yang berhasil diekstrak dari halaman ini"}

        word_count = len(content.split())

        return {
            "success": True,
            "url": safe_url,
            "hostname": validation.get("hostname", ""),
            "title": title[:160],
            "description": extracted.get("description", "")[:300],
            "keywords": extracted.get("keywords", "")[:300],
            "content": content,
            "word_count": word_count,
            "size_bytes": len(content.encode("utf-8")),
            "is_truncated": extracted.get("is_truncated", False),
        }

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Batas waktu habis (Timeout): Website tujuan terlalu lambat merespons."}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Gagal terhubung ke website. Periksa apakah URL aktif dan domain valid."}
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "Unknown"
        return {"success": False, "error": f"Website mengembalikan status HTTP {status}"}
    except Exception as e:
        logger.exception("Error scraping URL %s", safe_url)
        return {"success": False, "error": f"Gagal mengekstrak web: {str(e)[:120]}"}


async def scrape_url_async(url: str, max_length: int = 25000) -> Dict[str, Any]:
    """
    Asynchronously scrape content from a URL using httpx with SSRF protection.
    """
    validation = validate_scrape_url(url)
    if not validation["valid"]:
        return {"success": False, "error": validation["error"]}

    safe_url = validation["url"]
    if not HAS_HTTPX:
        # Fallback to sync
        return scrape_url(safe_url, max_length)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(safe_url, headers=headers)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type and "text/plain" not in content_type:
                return {
                    "success": False,
                    "error": f"Tipe konten '{content_type}' bukan halaman web HTML yang dapat di-scrape",
                }

            html = resp.text

            if HAS_BS4:
                extracted = _extract_page_data_bs4(html, max_length)
            else:
                extracted = _extract_page_data_regex(html, max_length)

            title = extracted["title"] or validation.get("hostname", "Webpage")
            content = extracted["content"]

            if not content:
                return {"success": False, "error": "Tidak ada teks yang dapat diekstrak dari halaman web ini."}

            word_count = len(content.split())

            return {
                "success": True,
                "url": safe_url,
                "hostname": validation.get("hostname", ""),
                "title": title[:160],
                "description": extracted.get("description", "")[:300],
                "keywords": extracted.get("keywords", "")[:300],
                "content": content,
                "word_count": word_count,
                "size_bytes": len(content.encode("utf-8")),
                "is_truncated": extracted.get("is_truncated", False),
            }
    except httpx.TimeoutException:
        return {"success": False, "error": "Timeout: Website terlalu lambat merespons"}
    except httpx.ConnectError:
        return {"success": False, "error": "Gagal terhubung ke website. Periksa apakah link aktif."}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HTTP status error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("Error async scraping URL %s", safe_url)
        return {"success": False, "error": f"Gagal scrape web: {str(e)[:120]}"}


def create_material_from_url(url: str, title: str = "", category: str = "Web Scraping") -> Dict[str, Any]:
    """Create a structured knowledge material from scraped URL content."""
    result = scrape_url(url)
    if not result["success"]:
        return result

    raw_content = result.get("content", "").strip()
    word_count = result.get("word_count", 0)
    if word_count < 15 or len(raw_content) < 80:
        return {
            "success": False,
            "error": "Konten halaman web terlalu pendek atau terblokir (kurang dari 15 kata). Materi tidak disimpan demi menjaga kualitas Knowledge Base."
        }

    page_title = title.strip() if title else result.get("title", "")
    if not page_title:
        page_title = f"Web: {result.get('hostname', 'Artikel')}"

    content = f"🌐 **Sumber**: {url}\n"
    if result.get("description"):
        content += f"📌 **Ringkasan**: {result['description']}\n\n"
    else:
        content += "\n"
    content += result["content"]

    keywords = result.get("keywords", "")
    if not keywords:
        keywords = f"web, scraping, {result.get('hostname', '')}"

    return {
        "success": True,
        "title": page_title[:160],
        "category": category[:80] if category else "Web Scraping",
        "content": content,
        "keywords": keywords[:300],
        "source_url": url,
        "size_bytes": result["size_bytes"],
        "word_count": result.get("word_count", 0),
    }
