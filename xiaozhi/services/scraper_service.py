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
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

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
    from bs4 import BeautifulSoup, NavigableString, Tag
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    NavigableString = Any  # type: ignore
    Tag = Any  # type: ignore

# PDF Document Parsers (PyMuPDF / fitz, pypdf)
HAS_PYMUPDF = False
try:
    import pymupdf as fitz  # type: ignore # PyMuPDF v1.23+
    HAS_PYMUPDF = True
except ImportError:
    try:
        import fitz  # type: ignore # Legacy import
        HAS_PYMUPDF = True
    except ImportError:
        HAS_PYMUPDF = False

try:
    import pypdf  # type: ignore
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

# YouTube Transcript Extractor
try:
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
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

    # 1. Hapus pola inline 'baca juga', 'simak video', 'halaman selanjutnya'
    text = re.sub(r"(?i)\b(?:baca\s+juga|simak\s+video(?:\s+\w+)?|tonton\s+juga|lihat\s+foto|artikel\s+terkait)\s*:\s*[^.\n]+[.]?", "", text)
    text = re.sub(r"(?i)\bhalaman\s+selanjutnya\s*:?\s*[^.\n]*[.]?", "", text)
    text = re.sub(r"(?i)\bdownload\s+aplikasi\s+[^.\n]+sekarang[^.\n]*[.]?", "", text)
    text = re.sub(r"(?i)\bikuti\s+berita\s+terkini\s+dan\s+mendalam\s+di\s+google\s+news[^.\n]*[.]?", "", text)

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

def clean_inline_spaces(s: str) -> str:
    """Bersihkan spasi berlebih dalam baris teks."""
    return re.sub(r"[ \t]+", " ", s).strip()


def clean_wikipedia_dom(soup: Any, base_url: str = "") -> List[str]:
    """
    Format dan bersihkan elemen DOM Wikipedia untuk pembacaan suara dan teks yang bersih:
    - Menghapus penanda kutipan referensi angka [1], [2], [rujukan?], [citation needed]
    - Menghapus navigasi Wikipedia, tombol sunting bab, daftar isi (TOC), dan footer referensi
    - Mengekstrak Infobox menjadi ringkasan spesifikasi fakta terstruktur
    """
    if soup is None:
        return []

    # 1. Hapus referensi [1], [2], [citation needed] dan tombol sunting
    for sup in soup.find_all("sup", class_=re.compile(r"(reference|noprint)", re.I)):
        sup.decompose()
    for span in soup.find_all("span", class_=re.compile(r"(mw-editsection|citation)", re.I)):
        span.decompose()
    for el in soup.find_all(["div", "table"], class_=re.compile(r"(hatnote|navbox|vertical-navbox|metadata|side-box|reflist|printfooter)", re.I)):
        el.decompose()
    for el in soup.find_all(id=re.compile(r"(toc|mw-navigation|catlinks)", re.I)):
        el.decompose()

    # 2. Ekstrak data Infobox menjadi daftar fakta bersih
    infobox_summary: List[str] = []
    infobox = soup.find("table", class_=re.compile(r"\binfobox\b", re.I))
    if infobox:
        for tr in infobox.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                label = th.get_text(separator=" ", strip=True).replace("\n", " ")
                val = td.get_text(separator=" ", strip=True).replace("\n", " ")
            elif len(tr.find_all("td")) >= 2:
                all_td = tr.find_all("td")
                label = all_td[0].get_text(separator=" ", strip=True).replace("\n", " ")
                val = all_td[1].get_text(separator=" ", strip=True).replace("\n", " ")
            else:
                continue
            label = re.sub(r"\[(?:\d+|catatan \d+|rujukan\?|butuh rujukan|citation needed)\]", "", label).strip()
            val = re.sub(r"\[(?:\d+|catatan \d+|rujukan\?|butuh rujukan|citation needed)\]", "", val).strip()
            if label and val and len(label) < 60 and len(val) < 250:
                infobox_summary.append(f"• **{label}**: {val}")
        infobox.decompose()

    return infobox_summary


def generate_quiz_from_content(content: str, title: str = "", num_questions: int = 3) -> List[Dict[str, str]]:
    """
    Ekstrak 3-5 pertanyaan kuis pemahaman faktual dan kunci jawaban ringkas (flashcards)
    secara otomatis dari teks materi yang di-scrape.
    """
    if not content or len(content.strip()) < 80:
        return []

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    paragraphs = [
        l for l in lines
        if len(l) > 35
        and not l.startswith(("#", ">", "•", "|", "```", "---", "1.", "2.", "3.", "4.", "5."))
        and not any(noise in l.lower() for noise in ["referensi", "doi:", "sumber url", "penulis:"])
    ]
    headings = [
        re.sub(r"^#+\s*", "", l).strip()
        for l in lines
        if l.startswith(("#", "###", "##")) and not any(k in l.lower() for k in ["informasi dokumen", "referensi", "tautan", "tipe konten", "kuis pemahaman"])
    ]

    doc_title = title or (headings[0] if headings else "Topik Ini")
    doc_title = re.sub(r"^(?:jurnal ilmiah|artikel web|dokumen pdf|transkrip):\s*", "", doc_title, flags=re.I).strip()

    quiz: List[Dict[str, str]] = []

    # Q1: Definisi / Konsep Inti
    if paragraphs:
        first_p = paragraphs[0]
        sents = [s.strip() for s in re.split(r"[.!?]\s+", first_p) if len(s.strip()) > 20]
        ans1 = (sents[0] if sents else first_p[:200]).rstrip(".") + "."
        quiz.append({
            "question": f"Apa konsep atau fungsi utama yang dibahas mengenai {doc_title}?",
            "answer": ans1[:280]
        })

    # Q2: Karakteristik / Cara Kerja / Metodologi
    mid_idx = len(paragraphs) // 2
    if len(paragraphs) > 1 and mid_idx < len(paragraphs):
        mid_p = paragraphs[mid_idx]
        sents = [s.strip() for s in re.split(r"[.!?]\s+", mid_p) if len(s.strip()) > 20]
        ans2 = (sents[0] if sents else mid_p[:200]).rstrip(".") + "."
        focus_topic = headings[1] if len(headings) > 1 else doc_title
        quiz.append({
            "question": f"Bagaimana prinsip kerja, metode, atau karakteristik penting terkait {focus_topic}?",
            "answer": ans2[:280]
        })

    # Q3: Keunggulan / Manfaat / Temuan Kunci
    if len(paragraphs) > 2:
        last_p = paragraphs[-1]
        sents = [s.strip() for s in re.split(r"[.!?]\s+", last_p) if len(s.strip()) > 20]
        ans3 = (sents[0] if sents else last_p[:200]).rstrip(".") + "."
        quiz.append({
            "question": f"Apa temuan penting, hasil pengujian, atau keunggulan dari pembahasan {doc_title}?",
            "answer": ans3[:280]
        })

    return quiz[:num_questions]


def format_quiz_as_markdown(quiz_items: List[Dict[str, str]]) -> str:
    """Format daftar kuis menjadi blok Markdown Flashcards terstruktur."""
    if not quiz_items:
        return ""
    lines = ["\n---\n", "### 🧠 Kuis Pemahaman & Flashcards\n"]
    for idx, item in enumerate(quiz_items, 1):
        lines.append(f"{idx}. ❓ **Pertanyaan**: {item['question']}")
        lines.append(f"   - 💡 **Kunci Jawaban**: {item['answer']}\n")
    return "\n".join(lines)


def format_inline_markdown(element: Any, base_url: str = "", collected_links: Optional[list] = None) -> str:
    """
    Format elemen teks inline dengan mempertahankan link Markdown [teks](url),
    blok kode inline `code`, bold **teks**, dan italic *teks*.
    Menyaring tautan sampah (javascript, share sosial, mailto, telp).
    """
    if element is None:
        return ""
    if isinstance(element, NavigableString):
        return str(element)

    out = []
    for child in getattr(element, "children", []):
        if isinstance(child, NavigableString):
            out.append(str(child))
        elif isinstance(child, Tag):
            tag_name = child.name.lower()
            if tag_name in ("script", "style", "noscript", "svg", "button", "iframe", "input", "form"):
                continue

            child_text = format_inline_markdown(child, base_url, collected_links)
            stripped_text = child_text.strip()

            if tag_name == "a":
                href = child.get("href", "").strip()
                if href and stripped_text and not href.startswith(("javascript:", "#", "mailto:", "tel:")):
                    # Filter social media share intents and tracking noise
                    is_social_share = any(
                        noise in href.lower()
                        for noise in (
                            "facebook.com/sharer",
                            "twitter.com/intent",
                            "x.com/intent",
                            "api.whatsapp.com/send",
                            "t.me/share",
                            "linkedin.com/sharing",
                            "pinterest.com/pin",
                        )
                    )
                    if not is_social_share:
                        if base_url:
                            href = urllib.parse.urljoin(base_url, href)
                        out.append(f"[{stripped_text}]({href})")
                        if collected_links is not None and len(collected_links) < 25:
                            if not any(l["url"] == href for l in collected_links) and len(stripped_text) > 2:
                                collected_links.append({"text": stripped_text[:80], "url": href})
                        continue
                out.append(child_text)
            elif tag_name == "code":
                if "\n" not in stripped_text and stripped_text:
                    out.append(f"`{stripped_text}`")
                else:
                    out.append(child_text)
            elif tag_name in ("strong", "b"):
                if stripped_text:
                    out.append(f"**{stripped_text}**")
            elif tag_name in ("em", "i"):
                if stripped_text:
                    out.append(f"*{stripped_text}*")
            else:
                out.append(child_text)

    return "".join(out)


def format_html_table_to_markdown(table_el: Any, base_url: str = "", collected_links: Optional[list] = None) -> str:
    """Konversi elemen tabel HTML menjadi format tabel Markdown terstruktur dengan tautan inline."""
    rows = table_el.find_all("tr")
    if not rows:
        return ""
    table_lines = []
    for tr in rows:
        cols = []
        for td in tr.find_all(["th", "td"]):
            cell_text = format_inline_markdown(td, base_url, collected_links).replace("\n", " ").strip()
            cell_text = cell_text.replace("|", "\\|")
            cols.append(cell_text)
        if cols and any(cols):
            table_lines.append("| " + " | ".join(cols) + " |")
    if not table_lines:
        return ""
    if len(table_lines) > 1:
        first_row_cols = max(1, len(table_lines[0].split("|")) - 2)
        divider = "| " + " | ".join(["---"] * first_row_cols) + " |"
        table_lines.insert(1, divider)
    return "\n" + "\n".join(table_lines) + "\n"


def extract_academic_and_article_metadata(soup: Any, url: str = "") -> Dict[str, Any]:
    """
    Ekstrak metadata komprehensif untuk jurnal ilmiah, paper riset, dan artikel:
    - Google Scholar / Highwire Press (citation_title, citation_author, citation_doi, citation_abstract, dsb.)
    - Dublin Core (DC.title, DC.creator, DC.date, DC.identifier, DC.description, dsb.)
    - Schema.org JSON-LD (ScholarlyArticle, Article, TechArticle, NewsArticle)
    - Repositori arXiv (Title, Authors, Abstract, Subjects)
    - Container abstrak in-page (class/id abstract, abstrak, paper-abstract)
    - Penulis byline, waktu rilis, DOI, dan kategori konten otomatis.
    """
    meta: Dict[str, Any] = {
        "title": "",
        "authors": [],
        "pub_date": "",
        "journal": "",
        "doi": "",
        "abstract": "",
        "keywords": [],
        "content_type": "Artikel Web",
        "has_code": False,
    }

    # 1. Schema.org / JSON-LD parsing
    for script_tag in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            raw_json = script_tag.string or script_tag.get_text()
            if not raw_json:
                continue
            data = json.loads(raw_json)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Handle graph structure
                graph = item.get("@graph")
                sub_items = graph if isinstance(graph, list) else [item]
                for si in sub_items:
                    stype = str(si.get("@type", "")).lower()
                    if any(t in stype for t in ["scholarly", "article", "paper", "report", "news", "tech"]):
                        if "scholarly" in stype or "paper" in stype:
                            meta["content_type"] = "Jurnal Ilmiah & Riset"
                        elif "tech" in stype:
                            meta["content_type"] = "Dokumentasi Teknis"
                        elif "news" in stype:
                            meta["content_type"] = "Artikel Berita"

                        if not meta["title"] and si.get("headline"):
                            meta["title"] = str(si["headline"]).strip()
                        elif not meta["title"] and si.get("name"):
                            meta["title"] = str(si["name"]).strip()

                        if not meta["abstract"] and si.get("abstract"):
                            meta["abstract"] = str(si["abstract"]).strip()
                        elif not meta["abstract"] and si.get("description"):
                            meta["abstract"] = str(si["description"]).strip()

                        if not meta["pub_date"] and (si.get("datePublished") or si.get("dateCreated")):
                            meta["pub_date"] = str(si.get("datePublished") or si.get("dateCreated"))[:10]

                        authors = si.get("author")
                        if authors:
                            if isinstance(authors, list):
                                for a in authors:
                                    aname = a.get("name") if isinstance(a, dict) else str(a)
                                    if aname and aname not in meta["authors"]:
                                        meta["authors"].append(aname.strip())
                            elif isinstance(authors, dict) and authors.get("name"):
                                if authors["name"] not in meta["authors"]:
                                    meta["authors"].append(authors["name"].strip())
                            elif isinstance(authors, str) and authors not in meta["authors"]:
                                meta["authors"].append(authors.strip())

                        pub = si.get("publisher")
                        if pub and not meta["journal"]:
                            pname = pub.get("name") if isinstance(pub, dict) else str(pub)
                            if pname:
                                meta["journal"] = pname.strip()
        except Exception:
            pass

    # 2. Academic Citation Meta Tags (Google Scholar, Dublin Core, Highwire Press)
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or "").lower().strip()
        val = (tag.get("content") or "").strip()
        if not val:
            continue

        if name in ("citation_title", "dc.title") and not meta["title"]:
            meta["title"] = val
        elif name in ("citation_author", "dc.creator"):
            if val not in meta["authors"]:
                meta["authors"].append(val)
        elif name in ("citation_publication_date", "citation_date", "dc.date") and not meta["pub_date"]:
            meta["pub_date"] = val[:10]
        elif name in ("citation_journal_title", "citation_conference_title", "dc.publisher") and not meta["journal"]:
            meta["journal"] = val
        elif name in ("citation_doi", "dc.identifier") and ("10." in val) and not meta["doi"]:
            meta["doi"] = val
        elif name in ("citation_abstract", "dc.description") and not meta["abstract"]:
            meta["abstract"] = val
        elif name in ("citation_keywords", "dc.subject"):
            meta["keywords"].extend([k.strip() for k in re.split(r"[,;]", val) if k.strip()])

    # Academic recognition based on DOI, journal tags, or known academic domains
    url_lower = (url or "").lower()
    is_academic_site = any(
        d in url_lower
        for d in (
            "arxiv.org",
            "sciencedirect.com",
            "ieee.org",
            "springer.com",
            "nature.com",
            "acm.org",
            "researchgate.net",
            "ncbi.nlm.nih.gov",
            "biorxiv.org",
            "medrxiv.org",
            "iopscience.iop.org",
            "wiley.com",
            "journal.",
            "ejournal.",
        )
    )
    if meta["journal"] or meta["doi"] or is_academic_site:
        meta["content_type"] = "Jurnal Ilmiah & Riset"

    # 3. arXiv specific extraction
    if "arxiv.org" in url_lower:
        meta["content_type"] = "Jurnal Ilmiah (arXiv)"
        arxiv_title = soup.find("h1", class_="title")
        if arxiv_title:
            meta["title"] = arxiv_title.get_text().replace("Title:", "").strip()
        arxiv_authors = soup.find("div", class_="authors")
        if arxiv_authors and not meta["authors"]:
            meta["authors"] = [a.get_text().strip() for a in arxiv_authors.find_all("a")]
        arxiv_abstract = soup.find("blockquote", class_=re.compile(r"abstract", re.I))
        if arxiv_abstract:
            meta["abstract"] = arxiv_abstract.get_text().replace("Abstract:", "").strip()

    # 4. In-page Abstract container search if not in meta
    if not meta["abstract"]:
        abs_el = (
            soup.find(class_=re.compile(r"(\babstract\b|article-abstract|paper-abstract|abstrak|abstract-content)", re.I))
            or soup.find(id=re.compile(r"(\babstract\b|abstrak)", re.I))
        )
        if abs_el:
            clean_abs = abs_el.get_text(separator=" ", strip=True)
            clean_abs = re.sub(r"^(?:abstract|abstrak)\s*:?\s*", "", clean_abs, flags=re.I).strip()
            if len(clean_abs) > 40:
                meta["abstract"] = clean_abs

    # 5. In-page DOI search if not found
    if not meta["doi"]:
        doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", str(soup))
        if doi_match:
            meta["doi"] = doi_match.group(0).rstrip(".")

    # 6. Title Fallbacks (og:title, title tag, h1)
    if not meta["title"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            meta["title"] = og_title["content"].strip()
        elif soup.title and soup.title.string:
            meta["title"] = soup.title.string.strip()
        elif soup.h1:
            meta["title"] = soup.h1.get_text().strip()

    # 7. Date Fallbacks
    if not meta["pub_date"]:
        pub_meta = soup.find("meta", property="article:published_time") or soup.find("meta", attrs={"name": re.compile(r"date", re.I)})
        if pub_meta and pub_meta.get("content"):
            meta["pub_date"] = pub_meta["content"].strip()[:10]
        else:
            time_tag = soup.find("time")
            if time_tag:
                dt = time_tag.get("datetime") or time_tag.get_text().strip()
                if dt:
                    meta["pub_date"] = dt[:10]

    # 8. Author Fallbacks
    if not meta["authors"]:
        author_meta = soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)}) or soup.find("meta", property="article:author")
        if author_meta and author_meta.get("content"):
            meta["authors"] = [author_meta["content"].strip()]
        else:
            byline = soup.find(class_=re.compile(r"(byline|author-name|entry-author)", re.I))
            if byline:
                btxt = byline.get_text(strip=True)
                btxt = re.sub(r"^(?:by|oleh|penulis|editor)\s*:?\s*", "", btxt, flags=re.I).strip()
                if btxt and len(btxt) < 60:
                    meta["authors"] = [btxt]

    # Clean title whitespaces
    if meta["title"]:
        meta["title"] = re.sub(r"\s+", " ", meta["title"]).strip()

    return meta


def extract_structured_text_bs4(
    soup: Any,
    max_chars: int = 25000,
    base_url: str = "",
    meta: Optional[Dict[str, Any]] = None,
    collected_links: Optional[list] = None,
) -> str:
    """
    Ekstraksi konten artikel web dengan mempertahankan struktur elemen:
    - Judul & Subjudul (H1-H4 sebagai Markdown Heading ###)
    - Tautan inline presisi [teks](url) tanpa membuang anchor text
    - Daftar poin & nomor (UL/OL sebagai bullet points • atau nomor urut)
    - Tabel data (TABLE sebagai Markdown Table)
    - Blok kode sintaks terformat (PRE/CODE dengan bahasa pendukung)
    - Kotak informasi, kutipan, dan alert (BLOCKQUOTE / info-box / alert)
    - Paragraf bebas noise (menyaring sisipan berita Indonesia)
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
        or soup.find(class_=re.compile(r"(article-content|post-content|entry-content|story-body|main-content|content-area|paper-body)", re.I))
        or soup.find(id=re.compile(r"(content|main|article|paper)", re.I))
        or soup.body
        or soup
    )

    is_wikipedia = "wikipedia.org" in (base_url or "").lower()
    blocks = []
    seen_tables = set()
    seen_lists = set()
    seen_pres = set()
    seen_callouts = set()

    # Pre-extract Wikipedia Infobox facts if present
    if is_wikipedia:
        infobox_specs = clean_wikipedia_dom(soup, base_url)
        if infobox_specs:
            blocks.append("### ℹ️ Ringkasan Fakta & Spesifikasi (Infobox Wikipedia)\n" + "\n".join(infobox_specs[:12]))

    for el in main_el.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol", "table", "pre", "blockquote", "div"]):
        tag = el.name.lower()
        if tag in ("h1", "h2", "h3", "h4"):
            text = format_inline_markdown(el, base_url, collected_links).strip()
            if is_wikipedia:
                text = re.sub(r"\[(?:\d+|catatan \d+|rujukan\?|butuh rujukan|citation needed)\]", "", text).strip()
                text = re.sub(r"\[(?:sunting|edit)\]", "", text).strip()
                # Stop parsing at Wikipedia footer sections
                if text.lower() in ("referensi", "pranala luar", "lihat pula", "catatan kaki", "daftar pustaka", "references", "external links", "see also"):
                    break
            if not text or len(text) < 2:
                continue
            # Skip if heading duplicates page title or is an abstract header
            if meta and meta.get("title") and text.lower() == meta["title"].lower():
                continue
            if re.match(r"^(?:title|abstract|abstrak)\s*:?$", text, re.I):
                continue
            blocks.append(f"\n### {text}\n")

        elif tag == "table":
            if id(el) in seen_tables or el.find_parent("table"):
                continue
            seen_tables.add(id(el))
            tbl_md = format_html_table_to_markdown(el, base_url, collected_links)
            if tbl_md:
                blocks.append(tbl_md)

        elif tag in ("ul", "ol"):
            if id(el) in seen_lists or el.find_parent(["ul", "ol", "table"]):
                continue
            seen_lists.add(id(el))
            items = []
            for idx, li in enumerate(el.find_all("li", recursive=False), 1):
                lit = format_inline_markdown(li, base_url, collected_links).strip()
                if is_wikipedia:
                    lit = re.sub(r"\[(?:\d+|catatan \d+|rujukan\?|butuh rujukan|citation needed)\]", "", lit)
                    lit = re.sub(r"\[(?:sunting|edit)\]", "", lit).strip()
                if lit:
                    if tag == "ol":
                        items.append(f"{idx}. {lit}")
                    else:
                        items.append(f"• {lit}")
            if items:
                blocks.append("\n" + "\n".join(items) + "\n")

        elif tag == "pre":
            if id(el) in seen_pres or el.find_parent("pre"):
                continue
            seen_pres.add(id(el))
            if meta is not None:
                meta["has_code"] = True
            code_el = el.find("code")
            code_raw = (code_el or el).get_text()
            cls = (code_el or el).get("class", [])
            lang = ""
            for c in cls:
                if c.startswith(("language-", "lang-")):
                    lang = c.split("-", 1)[-1]
                    break
            blocks.append(f"\n```{lang}\n{code_raw.strip()}\n```\n")

        elif tag == "blockquote" or (tag == "div" and any(c in (el.get("class") or []) for c in ("callout", "info-box", "alert", "highlight", "note", "notice", "quote"))):
            if id(el) in seen_callouts or el.find_parent(["blockquote", "table", "pre"]):
                continue
            seen_callouts.add(id(el))
            # In arXiv, abstract is inside blockquote.abstract which is already extracted in metadata
            if "arxiv.org" in (base_url or "").lower() and "abstract" in (el.get("class") or []):
                continue
            btext = format_inline_markdown(el, base_url, collected_links).strip()
            if btext and len(btext) > 15:
                classes = el.get("class") or []
                if any(c in classes for c in ("alert", "warning", "danger")):
                    icon = "⚠️ **Perhatian**:"
                elif any(c in classes for c in ("quote", "blockquote")):
                    icon = "💬 **Kutipan**:"
                else:
                    icon = "💡 **Informasi**:"
                lines = btext.splitlines()
                quoted = "\n".join([f"> {icon} {line}" if i == 0 else f"> {line}" for i, line in enumerate(lines) if line.strip()])
                blocks.append(f"\n{quoted}\n")

        elif tag == "p":
            if el.find_parent(["table", "ul", "ol", "blockquote", "pre"]):
                continue
            parent_classes = el.parent.get("class") or [] if el.parent else []
            if any(c in parent_classes for c in ("callout", "info-box", "alert", "highlight", "note", "notice", "quote")):
                continue
            # Skip author byline if already recorded in metadata
            p_classes = el.get("class") or []
            if any(c in p_classes for c in ("byline", "author", "author-name")):
                continue
            pt = format_inline_markdown(el, base_url, collected_links).strip()
            if is_wikipedia:
                pt = re.sub(r"\[(?:\d+|catatan \d+|rujukan\?|butuh rujukan|citation needed)\]", "", pt)
                pt = re.sub(r"\[(?:sunting|edit)\]", "", pt).strip()
            if pt and len(pt) > 10:
                blocks.append(pt)

    if blocks:
        text = "\n\n".join(blocks)
    else:
        text = main_el.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        text = "\n".join(lines)

    text = clean_indonesian_news_noise(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if meta is not None and meta.get("has_code") and meta.get("content_type") == "Artikel Web":
        meta["content_type"] = "Dokumentasi Teknis & Panduan"

    return text[:max_chars]


def _extract_page_data_bs4(html: str, max_length: int, url: str = "") -> Dict[str, Any]:
    """
    Ekstrak data halaman web komprehensif menggunakan BeautifulSoup:
    - Metadata akademik (Google Scholar, Dublin Core, JSON-LD, arXiv, DOI, Abstrak).
    - Konten terstruktur (Markdown Headings, Tables, Lists, Code Blocks, Callouts).
    - Format Wikipedia bersih bebas rujukan [1][2] dengan Infobox fakta.
    - Kuis pemahaman & flashcards 3 pertanyaan otomatis.
    - Tautan inline presisi [teks](url) dan daftar referensi terkait di bagian akhir.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. Extract Academic & Article Metadata
    meta = extract_academic_and_article_metadata(soup, url)

    # 2. Extract Description & Keywords
    description = meta.get("abstract", "")
    if not description:
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            description = og_desc["content"].strip()
        else:
            meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
            if meta_desc and meta_desc.get("content"):
                description = meta_desc["content"].strip()

    keywords = ", ".join(meta.get("keywords", []))
    if not keywords:
        meta_kw = soup.find("meta", attrs={"name": re.compile(r"^keywords$", re.I)})
        if meta_kw and meta_kw.get("content"):
            keywords = meta_kw["content"].strip()

    # 3. Extract Main Body Content with structured markdown
    collected_links: list = []
    body_text = extract_structured_text_bs4(
        soup,
        max_chars=max_length,
        base_url=url,
        meta=meta,
        collected_links=collected_links,
    )

    body_word_count = len(body_text.split()) if body_text else 0
    if meta.get("abstract"):
        body_word_count += len(meta["abstract"].split())

    # If body content is too short / empty (e.g. error/captcha page with < 8 words and no abstract)
    if body_word_count < 8 and not meta.get("abstract"):
        return {
            "title": meta["title"] or "Dokumen Web",
            "description": description,
            "keywords": keywords,
            "content": body_text,
            "word_count": body_word_count,
            "is_truncated": False,
            "source_type": "web_article",
            "content_type": meta["content_type"],
            "authors": meta.get("authors", []),
            "journal": meta.get("journal", ""),
            "pub_date": meta.get("pub_date", ""),
            "doi": meta.get("doi", ""),
            "abstract": meta.get("abstract", ""),
            "collected_links": [],
            "quiz": [],
        }

    # 4. Construct Executive Document Structure
    title = meta["title"] or "Dokumen Web"

    header_lines = [f"# {title}\n"]
    header_lines.append(f"> 📚 **Tipe Konten**: {meta['content_type']}")
    if meta.get("authors"):
        header_lines.append(f"> 👤 **Penulis / Kontributor**: {', '.join(meta['authors'])}")
    if meta.get("journal"):
        header_lines.append(f"> 🏛️ **Penerbit / Jurnal**: {meta['journal']}")
    if meta.get("pub_date"):
        header_lines.append(f"> 🗓️ **Waktu Publikasi**: {meta['pub_date']}")
    if url:
        header_lines.append(f"> 🌐 **Sumber URL**: {url}")
    if meta.get("doi"):
        doi_val = meta["doi"]
        doi_url = doi_val if doi_val.startswith("http") else f"https://doi.org/{doi_val}"
        header_lines.append(f"> 🆔 **DOI**: [{doi_val}]({doi_url})")

    doc_parts = ["\n".join(header_lines)]
    doc_parts.append("\n---\n")

    # Abstract Section (if academic or executive summary present)
    if meta.get("abstract"):
        doc_parts.append("### 📑 Abstrak / Ringkasan Eksekutif\n")
        abs_lines = [f"> {line}" for line in meta["abstract"].splitlines() if line.strip()]
        doc_parts.append("\n".join(abs_lines))
        doc_parts.append("\n\n---\n")

    # Main Body Section
    doc_parts.append("### 📖 Isi Dokumen / Artikel\n")
    doc_parts.append(body_text if body_text else "(Tidak ada teks paragraf tambahan)")

    # Key References Section
    if collected_links:
        doc_parts.append("\n\n---\n")
        doc_parts.append("### 🔗 Referensi & Tautan Penting Terkait\n")
        ref_lines = [f"• [{link['text']}]({link['url']})" for link in collected_links[:15]]
        doc_parts.append("\n".join(ref_lines))

    # 5. Generate Comprehension Quiz / Flashcards
    quiz_items = generate_quiz_from_content(body_text, title=title, num_questions=3)
    if quiz_items:
        quiz_md = format_quiz_as_markdown(quiz_items)
        doc_parts.append(quiz_md)

    full_document = "\n\n".join(doc_parts).strip()
    full_document = re.sub(r"\n{3,}", "\n\n", full_document)

    is_truncated = False
    if len(full_document) >= max_length:
        full_document = full_document[:max_length] + "\n\n[...Konten dipotong untuk batas memori...]"
        is_truncated = True

    return {
        "title": title,
        "description": description,
        "keywords": keywords,
        "content": full_document,
        "word_count": body_word_count,
        "is_truncated": is_truncated,
        "source_type": "web_article",
        "content_type": meta["content_type"],
        "authors": meta.get("authors", []),
        "journal": meta.get("journal", ""),
        "pub_date": meta.get("pub_date", ""),
        "doi": meta.get("doi", ""),
        "abstract": meta.get("abstract", ""),
        "collected_links": collected_links,
        "quiz": quiz_items,
    }


def _extract_page_data_regex(html: str, max_length: int, url: str = "") -> Dict[str, Any]:
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
        "source_type": "web_article",
        "content_type": "Artikel Web",
        "authors": [],
        "journal": "",
        "pub_date": "",
        "doi": "",
        "abstract": "",
        "collected_links": [],
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
            extracted = _extract_page_data_bs4(html, max_length, safe_url)
        else:
            extracted = _extract_page_data_regex(html, max_length, safe_url)

        title = extracted["title"] or validation.get("hostname", "Webpage")
        content = extracted["content"]
        word_count = extracted.get("word_count", len(content.split()) if content else 0)

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
            "source_type": extracted.get("source_type", "web_article"),
            "content_type": extracted.get("content_type", "Artikel Web"),
            "authors": extracted.get("authors", []),
            "journal": extracted.get("journal", ""),
            "pub_date": extracted.get("pub_date", ""),
            "doi": extracted.get("doi", ""),
            "abstract": extracted.get("abstract", ""),
            "collected_links": extracted.get("collected_links", []),
            "quiz": extracted.get("quiz", []),
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
                extracted = _extract_page_data_bs4(html, max_length, safe_url)
            else:
                extracted = _extract_page_data_regex(html, max_length, safe_url)

            title = extracted["title"] or validation.get("hostname", "Webpage")
            content = extracted["content"]
            word_count = extracted.get("word_count", len(content.split()) if content else 0)

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
                "source_type": extracted.get("source_type", "web_article"),
                "content_type": extracted.get("content_type", "Artikel Web"),
                "authors": extracted.get("authors", []),
                "journal": extracted.get("journal", ""),
                "pub_date": extracted.get("pub_date", ""),
                "doi": extracted.get("doi", ""),
                "abstract": extracted.get("abstract", ""),
                "collected_links": extracted.get("collected_links", []),
                "quiz": extracted.get("quiz", []),
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
    content_type = result.get("content_type", "")
    final_category = category
    if category in ("", "Web Scraping"):
        if source_type == "youtube_transcript":
            final_category = "Video Edukasi"
        elif source_type == "pdf_document":
            final_category = "Dokumen Modul"
        elif "Jurnal" in content_type or "Riset" in content_type:
            final_category = "Jurnal Ilmiah"
        elif "Dokumentasi" in content_type or "Teknis" in content_type:
            final_category = "Dokumentasi Teknis"
        elif "Berita" in content_type:
            final_category = "Artikel Berita"
        else:
            final_category = "Artikel Web"

    if "🌐 **Sumber URL**" in result["content"] or "🌐 **Sumber**" in result["content"]:
        content = result["content"]
    else:
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
        "content_type": content_type or final_category,
        "authors": result.get("authors", []),
        "journal": result.get("journal", ""),
        "pub_date": result.get("pub_date", ""),
        "doi": result.get("doi", ""),
        "abstract": result.get("abstract", ""),
        "quiz": result.get("quiz", []),
    }


# ── 9. Direct Academic Paper APIs & Autonomous Deep Research ─────────────────

def search_arxiv_papers(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Cari jurnal ilmiah gratis di arXiv API berdasarkan kata kunci.
    Mengembalikan daftar paper dengan judul, penulis, tahun, abstrak, dan link PDF open-access.
    """
    clean_q = re.sub(r"[^\w\s-]", " ", query).strip()
    if not clean_q:
        return []

    url = f"http://export.arxiv.org/api/query?search_query=all:{urllib.parse.quote_plus(clean_q)}&start=0&max_results={max_results}"
    results: List[Dict[str, Any]] = []

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "XiaoZhi-Academic/1.0 (academic-research@xiaozhiscig.biz.id)"}
        )
        with urllib.request.urlopen(req, timeout=7) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        atom_ns = "{http://www.w3.org/2005/Atom}"
        arxiv_ns = "{http://arxiv.org/schemas/atom}"

        for entry in root.findall(f"{atom_ns}entry"):
            title_el = entry.find(f"{atom_ns}title")
            title = re.sub(r"\s+", " ", title_el.text or "").strip() if title_el is not None else ""
            if not title:
                continue

            summary_el = entry.find(f"{atom_ns}summary")
            abstract = re.sub(r"\s+", " ", summary_el.text or "").strip() if summary_el is not None else ""

            pub_el = entry.find(f"{atom_ns}published")
            year = pub_el.text[:4] if pub_el is not None and pub_el.text else ""

            id_el = entry.find(f"{atom_ns}id")
            entry_id = (id_el.text or "").strip() if id_el is not None else ""

            authors = []
            for a in entry.findall(f"{atom_ns}author"):
                name_el = a.find(f"{atom_ns}name")
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            pdf_url = ""
            for link in entry.findall(f"{atom_ns}link"):
                if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                    pdf_url = link.get("href", "")
                    break
            if not pdf_url and entry_id:
                pdf_url = entry_id.replace("/abs/", "/pdf/") + ".pdf"

            doi_el = entry.find(f"{arxiv_ns}doi")
            doi = (doi_el.text or "").strip() if doi_el is not None else ""

            results.append({
                "title": title,
                "authors": authors,
                "year": year,
                "citations": "Tersedia di arXiv (Open Access)",
                "citation_count": 0,
                "abstract": abstract,
                "doi": doi,
                "pdf_url": pdf_url,
                "url": entry_id or pdf_url,
                "source": "arXiv",
            })
    except Exception as e:
        logger.warning("Error searching arXiv for '%s': %s", query, e)

    return results


def search_semantic_scholar_papers(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Cari paper ilmiah di Semantic Scholar Graph API.
    Menyajikan citation count, abstrak komprehensif, dan link PDF open-access jika ada.
    """
    clean_q = re.sub(r"[^\w\s-]", " ", query).strip()
    if not clean_q:
        return []

    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/search?"
        f"query={urllib.parse.quote_plus(clean_q)}&limit={max_results}"
        f"&fields=title,authors,year,citationCount,abstract,openAccessPdf,url,externalIds"
    )
    results: List[Dict[str, Any]] = []

    try:
        headers = {
            "User-Agent": "XiaoZhi-Scholar/1.0 (academic-research@xiaozhiscig.biz.id)",
            "Accept": "application/json",
        }
        if HAS_REQUESTS:
            resp = requests.get(url, headers=headers, timeout=6)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
            else:
                data = []
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as response:
                data = json.loads(response.read().decode("utf-8")).get("data", [])

        for item in data:
            title = (item.get("title") or "").strip()
            if not title:
                continue

            authors = [a.get("name", "").strip() for a in item.get("authors", []) if a.get("name")]
            year = str(item.get("year") or "")
            citations = item.get("citationCount") or 0
            abstract = (item.get("abstract") or "").strip()
            oa_pdf = item.get("openAccessPdf") or {}
            pdf_url = oa_pdf.get("url") or ""
            paper_url = item.get("url") or ""
            ext_ids = item.get("externalIds") or {}
            doi = ext_ids.get("DOI") or ""

            results.append({
                "title": title,
                "authors": authors,
                "year": year,
                "citations": f"{citations} Sitasi",
                "citation_count": citations,
                "abstract": abstract,
                "doi": doi,
                "pdf_url": pdf_url,
                "url": paper_url or (f"https://doi.org/{doi}" if doi else ""),
                "source": "Semantic Scholar",
            })
    except Exception as e:
        logger.warning("Semantic Scholar search notice for '%s': %s", query, e)

    return results


def search_crossref_papers(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Cari jurnal ilmiah terindeks di CrossRef REST API (metadata resmi jutaan penerbit dunia).
    Gratis tanpa API key, tahan banting dengan citation count resmi.
    """
    clean_q = re.sub(r"[^\w\s-]", " ", query).strip()
    if not clean_q:
        return []

    url = f"https://api.crossref.org/works?query={urllib.parse.quote_plus(clean_q)}&rows={max_results}"
    results: List[Dict[str, Any]] = []

    try:
        headers = {
            "User-Agent": "XiaoZhiScraper/1.0 (mailto:admin@xiaozhiscig.biz.id)",
            "Accept": "application/json",
        }
        if HAS_REQUESTS:
            resp = requests.get(url, headers=headers, timeout=6)
            if resp.status_code == 200:
                items = resp.json().get("message", {}).get("items", [])
            else:
                items = []
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as response:
                items = json.loads(response.read().decode("utf-8")).get("message", {}).get("items", [])

        for it in items:
            titles = it.get("title", [])
            title = titles[0].strip() if titles else ""
            if not title:
                continue

            authors = []
            for a in it.get("author", []):
                name = f"{a.get('given', '')} {a.get('family', '')}".strip()
                if name:
                    authors.append(name)

            issued = it.get("issued", {}).get("date-parts", [[]])
            year = str(issued[0][0]) if issued and issued[0] else ""

            citations = it.get("is-referenced-by-count", 0)
            doi = it.get("DOI", "")
            doi_url = it.get("URL", f"https://doi.org/{doi}" if doi else "")

            # Open Access link extraction
            pdf_url = ""
            for l in it.get("link", []):
                if "application/pdf" in l.get("content-type", "").lower():
                    pdf_url = l.get("URL", "")
                    break

            abstract = it.get("abstract", "")
            if abstract:
                abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
                abstract = re.sub(r"\s+", " ", abstract)

            results.append({
                "title": title,
                "authors": authors,
                "year": year,
                "citations": f"{citations} Sitasi",
                "citation_count": citations,
                "abstract": abstract,
                "doi": doi,
                "pdf_url": pdf_url,
                "url": doi_url,
                "source": "CrossRef / Jurnal Resmi",
            })
    except Exception as e:
        logger.warning("CrossRef search notice for '%s': %s", query, e)

    return results


def search_academic_papers_unified(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Integrasi gabungan arXiv, Semantic Scholar, dan CrossRef:
    - Melakukan pencarian paralel/bertingkat ke 3 database jurnal bereputasi tinggi.
    - Menghapus duplikasi judul.
    - Mengurutkan berdasarkan relevansi dan jumlah sitasi.
    - Menyusun format Markdown presentasi yang siap dibaca user atau XiaoZhi.
    """
    papers: List[Dict[str, Any]] = []
    seen_titles = set()

    # 1. Search arXiv
    arxiv_list = search_arxiv_papers(query, max_results=max_results)
    for p in arxiv_list:
        norm = re.sub(r"[^a-z0-9]", "", p["title"].lower())
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            papers.append(p)

    # 2. Search Semantic Scholar
    ss_list = search_semantic_scholar_papers(query, max_results=max_results)
    for p in ss_list:
        norm = re.sub(r"[^a-z0-9]", "", p["title"].lower())
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            papers.append(p)

    # 3. Search CrossRef (tahan banting dan kaya sitasi)
    if len(papers) < max_results:
        cr_list = search_crossref_papers(query, max_results=max_results)
        for p in cr_list:
            norm = re.sub(r"[^a-z0-9]", "", p["title"].lower())
            if norm and norm not in seen_titles:
                seen_titles.add(norm)
                papers.append(p)

    # Urutkan berdasarkan ada tidaknya PDF open-access & sitasi
    papers.sort(key=lambda x: (bool(x.get("pdf_url")), x.get("citation_count", 0)), reverse=True)
    selected_papers = papers[:max_results]

    if not selected_papers:
        return {
            "success": False,
            "query": query,
            "total_found": 0,
            "papers": [],
            "message": f"Tidak ditemukan paper atau jurnal akademis untuk kata kunci '{query}'.",
        }

    # Format Markdown rapi untuk konsumsi user & AI
    md_lines = [f"## 📚 Hasil Pencarian Jurnal & Paper Ilmiah: '{query}'\n"]
    for idx, p in enumerate(selected_papers, 1):
        md_lines.append(f"### {idx}. {p['title']}")
        if p.get("authors"):
            md_lines.append(f"- 👤 **Penulis**: {', '.join(p['authors'][:4])}")
        md_lines.append(f"- 🗓️ **Tahun**: {p.get('year') or 'N/A'} | 🏛️ **Database**: {p.get('source')} | 📊 **Sitasi**: {p.get('citations')}")
        if p.get("pdf_url"):
            md_lines.append(f"- 📥 **Akses PDF**: [Unduh / Buka PDF Full-Text]({p['pdf_url']})")
        if p.get("url"):
            md_lines.append(f"- 🔗 **Tautan Paper**: [{p.get('doi') or p['url']}]({p['url']})")
        if p.get("abstract"):
            clean_abs = p["abstract"][:400] + ("..." if len(p["abstract"]) > 400 else "")
            md_lines.append(f"- 📑 **Intisari Abstrak**:\n  > {clean_abs}")
        md_lines.append("")

    return {
        "success": True,
        "query": query,
        "total_found": len(selected_papers),
        "papers": selected_papers,
        "summary_markdown": "\n".join(md_lines).strip(),
    }


def search_web_sources(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """
    Cari sumber referensi ensiklopedia (Wikipedia OpenSearch) dan berita/tutorial terpercaya.
    """
    clean_q = re.sub(r"[^\w\s-]", " ", query).strip()
    sources: List[Dict[str, str]] = []
    seen_urls = set()

    # 1. Wikipedia OpenSearch API (Bahasa Indonesia & Inggris)
    for lang in ("id", "en"):
        try:
            wiki_api = f"https://{lang}.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote_plus(clean_q)}&limit={max_results}&namespace=0&format=json"
            headers = {"User-Agent": "XiaoZhi-Wiki/1.0 (admin@xiaozhiscig.biz.id)"}
            if HAS_REQUESTS:
                resp = requests.get(wiki_api, headers=headers, timeout=5)
                data = resp.json() if resp.status_code == 200 else []
            else:
                req = urllib.request.Request(wiki_api, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))

            if len(data) >= 4:
                titles = data[1]
                snippets = data[2]
                urls = data[3]
                for t, s, u in zip(titles, snippets, urls):
                    if u not in seen_urls:
                        seen_urls.add(u)
                        sources.append({
                            "title": t,
                            "url": u,
                            "snippet": s or f"Artikel ensiklopedia Wikipedia {lang.upper()}",
                            "source": f"Wikipedia ({lang.upper()})",
                        })
        except Exception as e:
            logger.debug("Wikipedia OpenSearch notice (%s): %s", lang, e)

    # 2. Google News RSS untuk topik terkini jika belum cukup
    if len(sources) < max_results:
        try:
            rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(clean_q)}&hl=id&gl=ID&ceid=ID:id"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            if HAS_REQUESTS:
                resp = requests.get(rss_url, headers=headers, timeout=5)
                xml_content = resp.text if resp.status_code == 200 else ""
            else:
                req = urllib.request.Request(rss_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as r:
                    xml_content = r.read().decode("utf-8", errors="ignore")

            if xml_content:
                items = re.findall(r"<item>(.*?)</item>", xml_content, re.DOTALL)
                for item in items[:max_results]:
                    t = re.search(r"<title>(.*?)</title>", item)
                    l = re.search(r"<link>(.*?)</link>", item)
                    d = re.search(r"<description>(.*?)</description>", item)
                    if t and l:
                        u = l.group(1).strip()
                        if u not in seen_urls:
                            seen_urls.add(u)
                            desc = re.sub(r"<[^>]+>", " ", d.group(1) if d else "").strip()
                            sources.append({
                                "title": t.group(1).strip(),
                                "url": u,
                                "snippet": desc[:250],
                                "source": "Google News Terverifikasi",
                            })
        except Exception as e:
            logger.debug("News RSS notice: %s", e)

    return sources[:max_results]


def conduct_autonomous_research(topic: str, mode: str = "auto") -> Dict[str, Any]:
    """
    Autonomous Deep Research Engine:
    Secara mandiri mencari, mengekstraksi, dan menyusun briefing materi komprehensif
    lengkap dengan kuis pemahaman tanpa mengharuskan pengguna menyalin tautan link manual.

    PENTING (ADAPTIF & AMAN):
    Sesuai instruksi pengguna, hasil TIDAK LANGSUNG disimpan ke database.
    XiaoZhi akan terlebih dahulu mempresentasikan intisari temuan, lalu menanyakan
    konfirmasi persis kepada pengguna:
    "Apakah Anda ingin saya menyimpan materi ini ke Knowledge Base Anda? Dan ada hal lain yang ingin Anda tanyakan atau pelajari?"

    Args:
        topic: Topik atau pertanyaan yang ingin diteliti secara mandiri.
        mode: 'academic' (arXiv/SemanticScholar/CrossRef), 'web' (Tutorial/Wikipedia), atau 'auto' (cerdas adaptif).
    """
    clean_topic = topic.strip()
    if not clean_topic:
        return {"success": False, "error": "Topik penelitian tidak boleh kosong."}

    # Klasifikasi otomatis apakah pengguna menargetkan paper/jurnal ilmiah atau tutorial praktis
    is_academic = False
    academic_keywords = [
        "paper", "jurnal", "riset", "penelitian", "scholar", "arxiv",
        "algoritma", "deep learning", "transformer", "neural", "arsitektur",
        "akurasi", "dataset", "scientific", "study", "metodologi",
    ]
    if mode == "academic" or (mode == "auto" and any(k in clean_topic.lower() for k in academic_keywords)):
        is_academic = True

    # 1. Jalur Penelitian Akademis (arXiv / Semantic Scholar / CrossRef)
    if is_academic:
        res = search_academic_papers_unified(clean_topic, max_results=3)
        if res.get("success") and res.get("papers"):
            top_paper = res["papers"][0]
            title = top_paper["title"]
            authors_str = ", ".join(top_paper.get("authors", [])) or "Tim Peneliti"
            year = top_paper.get("year", "N/A")
            source_platform = top_paper.get("source", "Repositori Ilmiah")
            citations = top_paper.get("citations", "N/A")
            abstract = top_paper.get("abstract") or "Abstrak lengkap dapat diakses pada dokumen publikasi resmi."
            pdf_link = top_paper.get("pdf_url") or ""
            paper_url = top_paper.get("url") or pdf_link

            # Sintesis Executive Briefing
            brief_parts = [
                f"# 📑 Kajian Paper Ilmiah: {title}\n",
                f"> 🏛️ **Sumber / Platform**: {source_platform} | 🗓️ **Tahun**: {year}",
                f"> 👤 **Peneliti**: {authors_str}",
                f"> 📊 **Dampak & Sitasi**: {citations}",
            ]
            if pdf_link:
                brief_parts.append(f"> 📥 **Akses Full-Text PDF**: [{pdf_link}]({pdf_link})")
            if paper_url:
                brief_parts.append(f"> 🌐 **URL Dokumen**: {paper_url}")
            brief_parts.append("\n---\n")
            brief_parts.append("### 🔬 Intisari & Abstrak Riset\n")
            brief_parts.append(f"> {abstract}\n")

            # Hasilkan Kuis Pemahaman Otomatis (3 Soal)
            quiz_items = generate_quiz_from_content(abstract, title=title, num_questions=3)
            if quiz_items:
                brief_parts.append(format_quiz_as_markdown(quiz_items))

            full_briefing = "\n".join(brief_parts)

            materi_payload = {
                "title": f"Paper: {title[:120]}",
                "category": "Jurnal Ilmiah",
                "content": full_briefing,
                "url": paper_url,
                "keywords": f"paper, riset, {source_platform.lower()}, {clean_topic[:60]}",
            }

            return {
                "success": True,
                "mode": "academic",
                "topic": clean_topic,
                "primary_source": {
                    "title": title,
                    "authors": authors_str,
                    "year": year,
                    "source": source_platform,
                    "pdf_url": pdf_link,
                    "url": paper_url,
                },
                "summary": full_briefing,
                "quiz": quiz_items,
                "saved_to_db": False,
                "needs_user_confirmation": True,
                "confirmation_prompt": (
                    "Apakah Anda ingin saya menyimpan materi ini ke Knowledge Base Anda? "
                    "Dan ada hal lain yang ingin Anda tanyakan atau pelajari?"
                ),
                "materi_siap_simpan": materi_payload,
                "instruksi_ai": (
                    "PANDUAN MENJAWAB PENGGUNA:\n"
                    "1. Jelaskan intisari paper di atas dengan ramah, padat, dan terstruktur (judul, temuan utama, dan link PDF jika ada).\n"
                    "2. JANGAN LANGSUNG MENYIMPAN KE DATABASE! Wajib tanyakan konfirmasi ini di akhir jawaban:\n"
                    "   'Apakah Anda ingin saya menyimpan materi ini ke Knowledge Base Anda? Dan ada hal lain yang ingin Anda tanyakan atau pelajari?'\n"
                    "3. Jika pengguna menyetujui, panggil tool 'confirm_and_save_research_material'."
                ),
            }

    # 2. Jalur Riset Web & Ensiklopedia / Dokumentasi Teknis
    web_sources = search_web_sources(clean_topic, max_results=3)
    if web_sources:
        best_source = web_sources[0]
        scrape_res = scrape_url(best_source["url"], max_length=15000)
        if scrape_res.get("success"):
            full_content = scrape_res["content"]
            title = scrape_res.get("title") or best_source["title"]
            quiz_items = scrape_res.get("quiz") or generate_quiz_from_content(full_content, title=title, num_questions=3)

            materi_payload = {
                "title": title[:140],
                "category": "Dokumentasi & Riset Mandiri",
                "content": full_content,
                "url": best_source["url"],
                "keywords": f"riset, autonomous, {best_source.get('source', '').lower()}, {clean_topic[:60]}",
            }

            return {
                "success": True,
                "mode": "web",
                "topic": clean_topic,
                "primary_source": {
                    "title": title,
                    "url": best_source["url"],
                    "source": best_source.get("source", "Web"),
                },
                "summary": full_content[:3000] + ("\n\n[...Konten selengkapnya siap disimpan...]" if len(full_content) > 3000 else ""),
                "quiz": quiz_items,
                "saved_to_db": False,
                "needs_user_confirmation": True,
                "confirmation_prompt": (
                    "Apakah Anda ingin saya menyimpan materi ini ke Knowledge Base Anda? "
                    "Dan ada hal lain yang ingin Anda tanyakan atau pelajari?"
                ),
                "materi_siap_simpan": materi_payload,
                "instruksi_ai": (
                    "PANDUAN MENJAWAB PENGGUNA:\n"
                    "1. Jelaskan langkah-langkah / konsep inti dari materi yang kamu pelajari di atas secara ringkas dan praktis.\n"
                    "2. JANGAN LANGSUNG MENYIMPAN KE DATABASE! Wajib tanyakan konfirmasi ini di akhir jawaban:\n"
                    "   'Apakah Anda ingin saya menyimpan materi ini ke Knowledge Base Anda? Dan ada hal lain yang ingin Anda tanyakan atau pelajari?'\n"
                    "3. Jika pengguna menyetujui, panggil tool 'confirm_and_save_research_material'."
                ),
            }

    # 3. Fallback jika tidak menemukan sumber spesifik: coba CrossRef umum
    fallback_academic = search_academic_papers_unified(clean_topic, max_results=2)
    if fallback_academic.get("success") and fallback_academic.get("papers"):
        return conduct_autonomous_research(clean_topic, mode="academic")

    return {
        "success": False,
        "error": f"Tidak dapat menemukan materi terverifikasi secara otomatis untuk topik '{clean_topic}'. Pengguna dapat memberikan link URL spesifik jika ada.",
    }

