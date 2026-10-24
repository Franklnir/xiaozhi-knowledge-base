"""URL scraping and document extraction service with SSRF protection.
Supports:
1. Standard Web HTML articles with structured headings, tables, and lists.
2. Indonesian news portal noise cleaner (Baca Juga, Simak Video, dsb.).
3. Modern JavaScript/SPA fallback via Jina AI Reader (r.jina.ai).
4. Direct PDF online document extraction via PyMuPDF / pypdf.
5. Educational YouTube video subtitle/transcript extraction via YouTubeTranscriptApi.
"""
import io
import ipaddress
import json
import logging
import re
import socket
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger("xiaozhi.scraper")

# HTTP Clients
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

# HTML Parser
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# PDF Document Parsers (PyMuPDF / fitz, pypdf)
HAS_PYMUPDF = False
try:
    import pymupdf as fitz  # PyMuPDF v1.23+
    HAS_PYMUPDF = True
except ImportError:
    try:
        import fitz  # Legacy import
        HAS_PYMUPDF = True
    except ImportError:
        HAS_PYMUPDF = False

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

# YouTube Transcript Extractor
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    HAS_YOUTUBE_TRANSCRIPT = True
except ImportError:
    HAS_YOUTUBE_TRANSCRIPT = False


# ── 1. Security: SSRF Protection ──────────────────────────────────────────────

def validate_scrape_url(url: str) -> Dict[str, Any]:
    """
    Validate URL before scraping with comprehensive SSRF protection.
    Blocks private IP addresses, loopback, link-local, and reserved ranges.
    """
    if not url or not isinstance(url, str):
        return {"valid": False, "error": "URL kosong atau tidak valid"}

    url_clean = url.strip()
    try:
        parsed = urllib.parse.urlparse(url_clean)
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
            pass

        return {"valid": True, "url": url_clean, "hostname": hostname}
    except Exception as e:
        return {"valid": False, "error": f"URL tidak valid: {str(e)[:80]}"}


# ── 2. Indonesian News Portal Noise Cleaner ───────────────────────────────────

def clean_indonesian_news_noise(text: str) -> str:
    """
    Pembersih teks pengganggu khas portal berita online Indonesia
    (Detik, Kompas, Tribun, Liputan6, CNN Indonesia, Kumparan).
    Menghapus sisipan:
    - 'Baca juga: ...'
    - 'Simak video: ...'
    - 'Halaman selanjutnya ...'
    - 'Pilihan editor: ...'
    - 'Download aplikasi ...'
    - 'Ikuti berita terkini di Google News ...'
    - Baris copyright / sponsor
    """
    if not text:
        return ""

    noise_patterns = [
        r"(?i)^\s*(?:baca\s+juga|simak\s+video|tonton\s+juga|lihat\s+foto|artikel\s+terkait|berita\s+terkait|video\s+terkait)\s*:?.*$",
        r"(?i)^\s*(?:halaman\s+selanjutnya|pilihan\s+editor|editor\s*:\s*[\w\s,\.]+|penulis\s*:\s*[\w\s,\.]+|kontributor\s*:\s*[\w\s,\.]+)\b.*$",
        r"(?i)^\s*(?:copyright\s*©?|hak\s+cipta\s+dilindungi|all\s+rights\s+reserved)\b.*$",
        r"(?i)ikuti\s+berita\s+terkini\s+dan\s+mendalam\s+di\s+google\s+news.*",
        r"(?i)download\s+aplikasi\s+.*?\s+sekarang.*",
        r"(?i)dapatkan\s+update\s+berita\s+pilihan\s+setiap\s+hari\s+dari\s+.*",
        r"(?i)^\s*(?:ad|advertisement|ads|iklan|sponsor|advertorial)\s*$",
        r"(?i)gabung\s+kompas\.com\s+plus.*",
    ]
    compiled_noises = [re.compile(p) for p in noise_patterns]

    cleaned_lines = []
    for line in text.splitlines():
        line_strip = line.strip()
        if not line_strip:
            cleaned_lines.append("")
            continue

        is_noise = False
        for pat in compiled_noises:
            if pat.search(line_strip):
                # If matched the whole line or majority of it, drop it
                sub_res = pat.sub("", line_strip).strip()
                if len(sub_res) < 10:
                    is_noise = True
                    break
        if not is_noise:
            cleaned_lines.append(line)

    res = "\n".join(cleaned_lines)
    return re.sub(r"\n{3,}", "\n\n", res).strip()


# ── 3. HTML Table & Structured Extraction ─────────────────────────────────────

def format_html_table_to_markdown(table_el) -> str:
    """Konversi elemen tabel HTML menjadi format tabel Markdown terstruktur."""
    rows = table_el.find_all("tr")
    if not rows:
        return ""
    table_lines = []
    for tr in rows:
        cols = [td.get_text(separator=" ", strip=True).replace("\n", " ").strip() for td in tr.find_all(["th", "td"])]
        if cols and any(cols):
            table_lines.append("| " + " | ".join(cols) + " |")
    if not table_lines:
        return ""
    if len(table_lines) > 1:
        first_row_cols = max(1, len(table_lines[0].split("|")) - 2)
        divider = "| " + " | ".join(["---"] * first_row_cols) + " |"
        table_lines.insert(1, divider)
    return "\n" + "\n".join(table_lines) + "\n"


def extract_structured_text_bs4(soup: Any, max_chars: int = 25000) -> str:
    """
    Ekstraksi konten artikel web dengan mempertahankan struktur elemen:
    - Judul & Subjudul (H1-H4 sebagai Markdown Heading ###)
    - Daftar poin & nomor (UL/OL sebagai bullet points •)
    - Tabel data (TABLE sebagai Markdown Table)
    - Paragraf & kutipan
    """
    unwanted_tags = [
        "script", "style", "nav", "footer", "header", "aside", "noscript",
        "iframe", "form", "svg", "button", "input", "select", "textarea"
    ]
    for tag in soup(unwanted_tags):
        tag.decompose()

    ad_pattern = re.compile(r"(\bads?\b|\badvertisement\b|cookie-banner|popup-banner)", re.I)
    for el in soup.find_all(attrs={"class": ad_pattern}):
        if el.name not in ("body", "main", "article", "html"):
            el.decompose()

    main_el = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find(class_=re.compile(r"(article-content|post-content|entry-content|story-body|main-content|content-area)", re.I))
        or soup.find(id=re.compile(r"(content|main|article)", re.I))
        or soup.body
        or soup
    )

    blocks = []
    seen_tables = set()
    seen_lists = set()

    for el in main_el.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol", "table", "blockquote"]):
        tag = el.name.lower()
        if tag in ("h1", "h2", "h3", "h4"):
            text = el.get_text(strip=True)
            if text and len(text) > 2:
                blocks.append(f"\n### {text}\n")
        elif tag == "table":
            if id(el) in seen_tables:
                continue
            seen_tables.add(id(el))
            tbl_md = format_html_table_to_markdown(el)
            if tbl_md:
                blocks.append(tbl_md)
        elif tag in ("ul", "ol"):
            if id(el) in seen_lists:
                continue
            seen_lists.add(id(el))
            items = []
            for li in el.find_all("li", recursive=False):
                lit = li.get_text(strip=True)
                if lit:
                    items.append(f"• {lit}")
            if items:
                blocks.append("\n" + "\n".join(items) + "\n")
        elif tag in ("p", "blockquote"):
            if el.find_parent(["table", "ul", "ol"]):
                continue
            pt = el.get_text(strip=True)
            if pt and len(pt) > 15:
                blocks.append(pt)

    if blocks:
        text = "\n\n".join(blocks)
    else:
        text = main_el.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        text = "\n".join(lines)

    text = clean_indonesian_news_noise(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def _extract_page_data_bs4(html: str, max_length: int) -> Dict[str, Any]:
    """Extract clean title, description, keywords, and body text using BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")

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

    # 3. Extract Main Content with structured elements (headings, tables, lists)
    text = extract_structured_text_bs4(soup, max_chars=max_length)

    is_truncated = False
    if len(text) >= max_length:
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
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""
    if not title:
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
        title = h1_match.group(1).strip() if h1_match else ""

    cleaned = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    cleaned = re.sub(r"<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = clean_indonesian_news_noise(cleaned)

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


# ── 4. Fallback: Jina AI Reader (r.jina.ai) ───────────────────────────────────

def _parse_jina_markdown_response(raw_text: str, original_url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    """Parse output dari r.jina.ai menjadi format standar scraper."""
    if not raw_text or len(raw_text.strip()) < 40:
        return None

    title = ""
    content_lines = []
    lines = raw_text.splitlines()

    in_content = False
    for line in lines:
        if not in_content:
            if line.startswith("Title:"):
                title = line[6:].strip()
                continue
            if line.startswith("Markdown Content:"):
                in_content = True
                continue
            # Some Jina outputs don't have Markdown Content marker
            if line.startswith("# ") or (line and not line.startswith("URL Source:") and not line.startswith("Published Time:")):
                in_content = True
        if in_content:
            content_lines.append(line)

    body = "\n".join(content_lines).strip()
    if not body:
        body = raw_text.strip()

    body = clean_indonesian_news_noise(body)

    if not title:
        parsed = urllib.parse.urlparse(original_url)
        title = f"Artikel: {parsed.hostname or 'Web'}"

    is_truncated = False
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n[...Konten dipotong untuk batas memori...]"
        is_truncated = True

    word_count = len(body.split())
    if word_count < 15:
        return None

    return {
        "success": True,
        "url": original_url,
        "hostname": urllib.parse.urlparse(original_url).hostname or "",
        "title": title[:160],
        "description": f"Artikel diekstrak via Reader Engine ({word_count} kata)",
        "keywords": f"web, reader, {urllib.parse.urlparse(original_url).hostname or ''}",
        "content": body,
        "word_count": word_count,
        "size_bytes": len(body.encode("utf-8")),
        "is_truncated": is_truncated,
        "source_engine": "jina_reader",
    }


def scrape_via_jina_reader(url: str, max_chars: int = 25000) -> Optional[Dict[str, Any]]:
    """
    Fallback reader menggunakan Jina AI Reader (https://r.jina.ai/<url>).
    Sangat ampuh untuk website modern berbasis JavaScript (React, Vue, SPA, Next.js),
    portal paywall ringan, Medium, Substack, dan Twitter/X.
    """
    safe_jina_url = f"https://r.jina.ai/{url}"
    headers = {
        "User-Agent": "XiaoZhi-AI-Scraper/2.0",
        "Accept": "text/markdown, text/plain",
        "X-Return-Format": "markdown",
        "X-Timeout": "12",
    }
    try:
        if HAS_REQUESTS:
            resp = requests.get(safe_jina_url, headers=headers, timeout=12)
            if resp.status_code == 200 and resp.text:
                return _parse_jina_markdown_response(resp.text, url, max_chars)
        elif HAS_HTTPX:
            with httpx.Client(timeout=12.0) as client:
                resp = client.get(safe_jina_url, headers=headers)
                if resp.status_code == 200 and resp.text:
                    return _parse_jina_markdown_response(resp.text, url, max_chars)
    except Exception as e:
        logger.debug("Jina Reader fallback failed for %s: %s", url, e)
    return None


async def scrape_via_jina_reader_async(url: str, max_chars: int = 25000) -> Optional[Dict[str, Any]]:
    """Versi asinkron dari Jina AI Reader fallback."""
    safe_jina_url = f"https://r.jina.ai/{url}"
    headers = {
        "User-Agent": "XiaoZhi-AI-Scraper/2.0",
        "Accept": "text/markdown, text/plain",
        "X-Return-Format": "markdown",
        "X-Timeout": "12",
    }
    try:
        if HAS_HTTPX:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(safe_jina_url, headers=headers)
                if resp.status_code == 200 and resp.text:
                    return _parse_jina_markdown_response(resp.text, url, max_chars)
        else:
            return scrape_via_jina_reader(url, max_chars)
    except Exception as e:
        logger.debug("Async Jina Reader fallback failed for %s: %s", url, e)
    return None


# ── 5. Online PDF Document Extractor ──────────────────────────────────────────

def extract_pdf_content(safe_url: str, max_chars: int = 25000) -> Dict[str, Any]:
    """
    Ekstraksi dokumen PDF online secara streaming dengan proteksi memori (max 15MB).
    Mendukung PyMuPDF (fitz) atau pypdf.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        if not HAS_REQUESTS:
            return {"success": False, "error": "Library requests tidak tersedia untuk mengunduh PDF."}

        resp = requests.get(safe_url, headers=headers, stream=True, timeout=15)
        resp.raise_for_status()

        # Check content length if available
        cl = resp.headers.get("content-length")
        if cl and int(cl) > 15 * 1024 * 1024:
            return {"success": False, "error": "Ukuran dokumen PDF melebihi batas maksimal 15 MB."}

        pdf_bytes = io.BytesIO()
        total_downloaded = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                total_downloaded += len(chunk)
                if total_downloaded > 15 * 1024 * 1024:
                    return {"success": False, "error": "Ukuran dokumen PDF melebihi batas maksimal 15 MB."}
                pdf_bytes.write(chunk)

        pdf_bytes.seek(0)
        filename = safe_url.rstrip("/").split("/")[-1].split("?")[0]
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        decoded_filename = urllib.parse.unquote(filename)
        title = f"Dokumen PDF: {decoded_filename}"

        pages_text = []
        num_pages = 0

        if HAS_PYMUPDF:
            doc = fitz.open(stream=pdf_bytes.getvalue(), filetype="pdf")
            num_pages = len(doc)
            for pno in range(min(num_pages, 60)):
                page = doc[pno]
                txt = page.get_text().strip()
                if txt:
                    pages_text.append(f"### Halaman {pno + 1}\n\n{txt}")
            doc.close()
        elif HAS_PYPDF:
            reader = pypdf.PdfReader(pdf_bytes)
            num_pages = len(reader.pages)
            for pno in range(min(num_pages, 60)):
                page = reader.pages[pno]
                txt = (page.extract_text() or "").strip()
                if txt:
                    pages_text.append(f"### Halaman {pno + 1}\n\n{txt}")
        else:
            return {"success": False, "error": "Library pembaca PDF (PyMuPDF / pypdf) belum terpasang di sistem."}

        if not pages_text:
            return {"success": False, "error": "Dokumen PDF ini tidak memiliki teks yang dapat dibaca (mungkin gambar hasil scan tanpa OCR)."}

        full_content = f"📄 **Dokumen PDF**: {decoded_filename}\n📊 **Jumlah Halaman**: {num_pages}\n🔗 **URL**: {safe_url}\n\n" + "\n\n".join(pages_text)

        is_truncated = False
        if len(full_content) > max_chars:
            full_content = full_content[:max_chars] + "\n\n[...Konten PDF dipotong untuk batas memori...]"
            is_truncated = True

        word_count = len(full_content.split())
        return {
            "success": True,
            "url": safe_url,
            "hostname": urllib.parse.urlparse(safe_url).hostname or "",
            "title": title[:160],
            "description": f"Dokumen PDF {num_pages} halaman ({word_count} kata)",
            "keywords": f"pdf, dokumen, {decoded_filename}",
            "content": full_content,
            "word_count": word_count,
            "size_bytes": len(full_content.encode("utf-8")),
            "is_truncated": is_truncated,
            "source_type": "pdf_document",
        }
    except Exception as e:
        logger.exception("Error extracting PDF from %s", safe_url)
        return {"success": False, "error": f"Gagal mengekstrak dokumen PDF: {str(e)[:120]}"}


# ── 6. YouTube Video Transcript Extractor ─────────────────────────────────────

def extract_youtube_video_id(url: str) -> Optional[str]:
    """Ekstrak 11-karakter video ID dari berbagai format URL YouTube."""
    if not url:
        return None
    patterns = [
        r"(?:v=|\/youtu\.be\/|\/shorts\/|\/live\/|\/embed\/)([0-9A-Za-z_-]{11})",
        r"^([0-9A-Za-z_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def extract_youtube_transcript(video_id: str, original_url: str, max_chars: int = 25000) -> Dict[str, Any]:
    """
    Ekstrak transkrip percakapan/subtitle video YouTube edukasi dan judul resmi via oEmbed.
    Mendukung bahasa Indonesia (id), Inggris (en), dan subtitle otomatis (auto-generated).
    """
    video_title = f"YouTube Video: {video_id}"
    channel_name = ""
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("title"):
                video_title = data["title"].strip()
            if data.get("author_name"):
                channel_name = data["author_name"].strip()
    except Exception:
        pass

    if not HAS_YOUTUBE_TRANSCRIPT:
        return {
            "success": False,
            "error": "Library youtube_transcript_api belum terpasang di sistem server.",
        }

    try:
        transcript_data = None
        # 1. Dukungan API v0.6+ (get_transcript)
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            try:
                transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=["id", "en"])
            except Exception:
                try:
                    if hasattr(YouTubeTranscriptApi, "list_transcripts"):
                        t_list = YouTubeTranscriptApi.list_transcripts(video_id)
                        for lang_code in ["id", "en"]:
                            try:
                                transcript_data = t_list.find_transcript([lang_code]).fetch()
                                break
                            except Exception:
                                pass
                        if not transcript_data:
                            for t in t_list:
                                transcript_data = t.fetch()
                                break
                except Exception:
                    pass

        # 2. Dukungan API v1.2+ (fetch / list pada instance atau class)
        if not transcript_data and (hasattr(YouTubeTranscriptApi, "fetch") or hasattr(YouTubeTranscriptApi, "list") or isinstance(YouTubeTranscriptApi, type)):
            try:
                api = YouTubeTranscriptApi() if isinstance(YouTubeTranscriptApi, type) else YouTubeTranscriptApi
                if hasattr(api, "fetch"):
                    try:
                        res = api.fetch(video_id, languages=["id", "en"])
                        transcript_data = res.to_raw_data() if hasattr(res, "to_raw_data") else list(res)
                    except Exception:
                        pass
                if not transcript_data and hasattr(api, "list"):
                    try:
                        t_list = api.list(video_id)
                        for t in t_list:
                            f = t.fetch()
                            transcript_data = f.to_raw_data() if hasattr(f, "to_raw_data") else list(f)
                            break
                    except Exception:
                        pass
            except Exception:
                pass

        if not transcript_data:
            return {
                "success": False,
                "error": f"Transkrip/subtitle tidak tersedia untuk video YouTube ini ({video_id}). Pastikan video memiliki subtitle atau CC aktif.",
            }

        # Format transkrip dengan penanda waktu (timestamp) tiap 30 detik
        transcript_blocks = []
        current_chunk = []
        last_time = -1

        for item in transcript_data:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip().replace("\n", " ")
                start_sec = int(item.get("start", 0))
            else:
                text = str(getattr(item, "text", "")).strip().replace("\n", " ")
                start_sec = int(getattr(item, "start", 0))
            mins, secs = divmod(start_sec, 60)
            time_str = f"[{mins:02d}:{secs:02d}]"

            if last_time < 0 or (start_sec - last_time) >= 30:
                if current_chunk:
                    transcript_blocks.append(" ".join(current_chunk))
                    current_chunk = []
                current_chunk.append(f"{time_str} {text}")
                last_time = start_sec
            else:
                current_chunk.append(text)

        if current_chunk:
            transcript_blocks.append(" ".join(current_chunk))

        body_text = "\n\n".join(transcript_blocks)

        header_info = f"🎬 **Judul Video**: {video_title}\n"
        if channel_name:
            header_info += f"👤 **Channel**: {channel_name}\n"
        header_info += f"🔗 **Tautan**: https://www.youtube.com/watch?v={video_id}\n\n### 📝 Transkrip Lengkap Pembicaraan:\n\n"

        full_content = header_info + body_text

        is_truncated = False
        if len(full_content) > max_chars:
            full_content = full_content[:max_chars] + "\n\n[...Transkrip dipotong untuk batas memori...]"
            is_truncated = True

        word_count = len(full_content.split())
        return {
            "success": True,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "hostname": "youtube.com",
            "title": f"Transkrip: {video_title}"[:160],
            "description": f"Transkrip video YouTube dari channel {channel_name or 'YouTube'} ({word_count} kata)",
            "keywords": f"youtube, transkrip, subtitle, video, {channel_name}",
            "content": full_content,
            "word_count": word_count,
            "size_bytes": len(full_content.encode("utf-8")),
            "is_truncated": is_truncated,
            "source_type": "youtube_transcript",
        }
    except Exception as exc:
        logger.exception("Error extracting YouTube transcript for %s", video_id)
        return {"success": False, "error": f"Gagal mengekstrak transkrip YouTube: {str(exc)[:120]}"}


# ── 7. Main Unified Scraping Engines (Sync & Async) ───────────────────────────

def scrape_url(url: str, max_length: int = 25000) -> Dict[str, Any]:
    """
    Synchronously scrape content from a URL with SSRF protection,
    supporting Web HTML, Indonesian News cleaner, YouTube Transcripts,
    PDF documents, and Jina AI Reader fallback.
    """
    validation = validate_scrape_url(url)
    if not validation["valid"]:
        return {"success": False, "error": validation["error"]}

    safe_url = validation["url"]
    parsed = urllib.parse.urlparse(safe_url)
    host = (parsed.hostname or "").lower()

    # 1. YouTube Video URL Routing
    if "youtube.com" in host or "youtu.be" in host:
        vid = extract_youtube_video_id(safe_url)
        if vid:
            return extract_youtube_transcript(vid, safe_url, max_length)

    # 2. PDF Document URL Routing
    if parsed.path.lower().endswith(".pdf"):
        return extract_pdf_content(safe_url, max_length)

    if not HAS_REQUESTS:
        return {"success": False, "error": "Library requests tidak tersedia di sistem"}

    # 3. Standard HTML Scraping
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        response = requests.get(safe_url, headers=headers, timeout=12, allow_redirects=True)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "application/pdf" in content_type:
            return extract_pdf_content(safe_url, max_length)

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
        word_count = len(content.split()) if content else 0

        # Fallback to Jina Reader if HTML body is suspiciously empty / SPA (React/Vue/Cloudflare)
        if word_count < 30:
            jina_res = scrape_via_jina_reader(safe_url, max_length)
            if jina_res and jina_res.get("success") and jina_res.get("word_count", 0) > word_count:
                return jina_res

        if not content:
            return {"success": False, "error": "Tidak ada teks artikel yang berhasil diekstrak dari halaman ini"}

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
            "source_type": "web_article",
        }

    except requests.exceptions.Timeout:
        # Try Jina reader on timeout
        jina_res = scrape_via_jina_reader(safe_url, max_length)
        if jina_res and jina_res.get("success"):
            return jina_res
        return {"success": False, "error": "Batas waktu habis (Timeout): Website tujuan terlalu lambat merespons."}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Gagal terhubung ke website. Periksa apakah URL aktif dan domain valid."}
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "Unknown"
        # Try Jina Reader on 403 / 401 / Cloudflare challenge
        if status in (401, 403, 503):
            jina_res = scrape_via_jina_reader(safe_url, max_length)
            if jina_res and jina_res.get("success"):
                return jina_res
        return {"success": False, "error": f"Website mengembalikan status HTTP {status}"}
    except Exception as e:
        logger.exception("Error scraping URL %s", safe_url)
        return {"success": False, "error": f"Gagal mengekstrak web: {str(e)[:120]}"}


async def scrape_url_async(url: str, max_length: int = 25000) -> Dict[str, Any]:
    """
    Asynchronously scrape content from a URL using httpx with SSRF protection,
    supporting Web HTML, Indonesian News cleaner, YouTube Transcripts,
    PDF documents, and Jina AI Reader fallback.
    """
    validation = validate_scrape_url(url)
    if not validation["valid"]:
        return {"success": False, "error": validation["error"]}

    safe_url = validation["url"]
    parsed = urllib.parse.urlparse(safe_url)
    host = (parsed.hostname or "").lower()

    # 1. YouTube Video URL Routing
    if "youtube.com" in host or "youtu.be" in host:
        vid = extract_youtube_video_id(safe_url)
        if vid:
            return extract_youtube_transcript(vid, safe_url, max_length)

    # 2. PDF Document URL Routing
    if parsed.path.lower().endswith(".pdf"):
        return extract_pdf_content(safe_url, max_length)

    if not HAS_HTTPX:
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
            if "application/pdf" in content_type:
                return extract_pdf_content(safe_url, max_length)

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
            word_count = len(content.split()) if content else 0

            # Fallback to Jina Reader if HTML body is suspiciously empty / SPA (React/Vue/Cloudflare)
            if word_count < 30:
                jina_res = await scrape_via_jina_reader_async(safe_url, max_length)
                if jina_res and jina_res.get("success") and jina_res.get("word_count", 0) > word_count:
                    return jina_res

            if not content:
                return {"success": False, "error": "Tidak ada teks yang dapat diekstrak dari halaman web ini."}

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
                "source_type": "web_article",
            }
    except httpx.TimeoutException:
        jina_res = await scrape_via_jina_reader_async(safe_url, max_length)
        if jina_res and jina_res.get("success"):
            return jina_res
        return {"success": False, "error": "Timeout: Website terlalu lambat merespons"}
    except httpx.ConnectError:
        return {"success": False, "error": "Gagal terhubung ke website. Periksa apakah link aktif."}
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403, 503):
            jina_res = await scrape_via_jina_reader_async(safe_url, max_length)
            if jina_res and jina_res.get("success"):
                return jina_res
        return {"success": False, "error": f"HTTP status error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("Error async scraping URL %s", safe_url)
        return {"success": False, "error": f"Gagal scrape web: {str(e)[:120]}"}


# ── 8. Material Formatter for Knowledge Base ─────────────────────────────────

def create_material_from_url(url: str, title: str = "", category: str = "Web Scraping") -> Dict[str, Any]:
    """Create a structured knowledge material from scraped URL, PDF, or YouTube content."""
    result = scrape_url(url)
    if not result["success"]:
        return result

    raw_content = result.get("content", "").strip()
    word_count = result.get("word_count", 0)
    if word_count < 15 or len(raw_content) < 80:
        return {
            "success": False,
            "error": "Konten halaman web terlalu pendek atau terblokir (kurang dari 15 kata). Materi tidak disimpan demi menjaga kualitas Knowledge Base.",
        }

    page_title = title.strip() if title else result.get("title", "")
    if not page_title:
        page_title = f"Web: {result.get('hostname', 'Artikel')}"

    # Auto-adjust category if user left default
    source_type = result.get("source_type", "web_article")
    final_category = category
    if category in ("", "Web Scraping"):
        if source_type == "youtube_transcript":
            final_category = "Video Edukasi"
        elif source_type == "pdf_document":
            final_category = "Dokumen Modul"
        else:
            final_category = "Artikel Web"

    content = f"🌐 **Sumber**: {url}\n"
    if result.get("description"):
        content += f"📌 **Ringkasan**: {result['description']}\n\n"
    else:
        content += "\n"
    content += result["content"]

    keywords = result.get("keywords", "")
    if not keywords:
        keywords = f"web, {result.get('hostname', '')}, {final_category.lower()}"

    return {
        "success": True,
        "title": page_title[:160],
        "category": final_category[:80],
        "content": content,
        "keywords": keywords[:300],
        "source_url": url,
        "size_bytes": result.get("size_bytes", len(content.encode("utf-8"))),
        "word_count": word_count,
        "source_type": source_type,
    }
