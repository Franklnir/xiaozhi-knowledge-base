#!/usr/bin/env python3
"""
EduSmart Xiaozhi - Antarmuka Otomatisasi CLI (Command Line Interface)
Aplikasi otomasi untuk:
1. Mengambil data dari website publik (Web Scraping via requests & BeautifulSoup).
2. Mengekstrak dan memvalidasi informasi (Metadata, Heading, Tabel, List, Kuis Flashcards).
3. Pembersihan data otomatis (Noise filter portal berita Indonesia & Wikipedia cleaner).
4. Menyimpan dan mengorganisasi data ke database lokal (SQLite).
5. Pencarian informasi cepat (Keyword search di Knowledge Base).
6. Backup database lokal terkompresi dengan retensi file.
7. Pembuatan laporan rekapitulasi materi otomatis (Markdown / JSON).
"""

import argparse
import gzip
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Pastikan root project berada di sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Handle Windows terminal CP1252 encoding for emojis and Unicode characters
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Default environment untuk eksekusi LOCAL (SQLite)
# Jika user tidak menyetel backend lain, default selalu SQLite lokal tanpa butuh PostgreSQL
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("SQLITE_DB_PATH", str(BASE_DIR / "data" / "xiaozhi.db"))


def print_banner():
    banner = """
========================================================================
       EduSmart Xiaozhi - CLI Otomasi Data & Web Scraping
       Mode: LOCAL (SQLite Database) | Python 3.11
========================================================================
"""
    print(banner.strip())


def get_local_store():
    """Menginisialisasi store database lokal (SQLite) secara mandiri."""
    from xiaozhi.database.factory import create_store
    return create_store()


# ── 1. COMMAND: SCRAPE ────────────────────────────────────────────────────────

def cmd_scrape(args):
    """Mengambil data dari website, membersihkan, dan mengekstrak informasi."""
    from xiaozhi.services.scraper_service import scrape_url, create_material_from_url

    url = args.url.strip()
    print(f"\n[*] Mengambil dan memproses data dari: {url}")
    print("[*] Menggunakan engine: requests + BeautifulSoup (dengan SSRF Protection & Noise Cleaner)...")

    res = scrape_url(url, max_length=args.max_chars)
    if not res.get("success"):
        print(f"\n[!] GAGAL: {res.get('error', 'Terjadi kesalahan saat scraping.')}")
        sys.exit(1)

    title = res.get("title", "Dokumen Web")
    content_type = res.get("content_type", "Artikel Web")
    source_type = res.get("source_type", "web_article")
    word_count = res.get("word_count", 0)
    authors = ", ".join(res.get("authors", [])) or "-"
    pub_date = res.get("pub_date", "-")
    doi = res.get("doi", "-")
    quiz = res.get("quiz", [])

    print("\n" + "=" * 60)
    print(" [HASIL EKSTRAKSI & PEMBERSIHAN INFORMASI]")
    print("=" * 60)
    print(f"Judul         : {title}")
    print(f"Tipe Konten   : {content_type} ({source_type})")
    print(f"Penulis       : {authors}")
    print(f"Waktu/Tahun   : {pub_date}")
    if doi != "-":
        print(f"DOI           : {doi}")
    print(f"Jumlah Kata   : {word_count} kata")
    print(f"Ukuran Bersih : {res.get('size_bytes', 0)} bytes")

    # Tampilkan preview konten
    content = res.get("content", "")
    preview_lines = [l for l in content.splitlines() if l.strip()][:12]
    print("\n--- PREVIEW KONTEN BERSIH ---")
    print("\n".join(preview_lines))
    if len(content.splitlines()) > 12:
        print("\n[... teks lengkap tersedia di ekspor / database ...]")

    # Tampilkan kuis pemahaman jika ada
    if quiz:
        print("\n--- KUIS PEMAHAMAN & FLASHCARDS OTOMATIS ---")
        for idx, q in enumerate(quiz, 1):
            print(f"{idx}. Q: {q.get('question')}")
            print(f"   A: {q.get('answer')}")

    # Opsi: Simpan ke database lokal
    if args.save:
        store = get_local_store()
        category = args.category or content_type
        # Default owner_id = 1 (akun pengguna utama lokal)
        try:
            store.add_material(
                owner_id=args.user_id,
                title=title,
                category=category,
                content=content,
                keywords=res.get("keywords", f"web, {res.get('hostname', '')}"),
                api_url=url,
            )
            print(f"\n[+] SUKSES: Data berhasil disimpan ke database lokal (Kategori: '{category}').")
        except Exception as exc:
            print(f"\n[!] Catatan Simpan Database: {exc}")

    # Opsi: Ekspor ke file teks/Markdown/JSON
    if args.export:
        export_path = Path(args.export)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        if export_path.suffix.lower() == ".json":
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        else:
            with open(export_path, "w", encoding="utf-8") as f:
                f.write(content)
        print(f"[+] SUKSES: Dokumen hasil ekstraksi diekspor ke: {export_path}")


# ── 2. COMMAND: SEARCH ────────────────────────────────────────────────────────

def cmd_search(args):
    """Pencarian informasi materi yang tersimpan di database lokal."""
    store = get_local_store()
    query = args.query.strip().lower()
    print(f"\n[*] Mencari materi di database lokal dengan kata kunci: '{query}'...")

    results = []
    try:
        # Cari via search_materials
        raw_res = store.search_materials(args.user_id, query, limit=args.limit)
        results = raw_res
    except Exception:
        # Fallback pencarian manual di SQLite
        try:
            all_mats = store.list_materials(args.user_id)
            for m in all_mats:
                t = str(m.get("title", "")).lower()
                c = str(m.get("content", "")).lower()
                k = str(m.get("keywords", "")).lower()
                if query in t or query in c or query in k:
                    results.append(m)
                if len(results) >= args.limit:
                    break
        except Exception as e:
            print(f"[!] Gagal membaca database: {e}")
            return

    if not results:
        print(f"[-] Tidak ditemukan materi yang cocok dengan '{query}'.")
        return

    print(f"\n[+] Ditemukan {len(results)} materi:")
    print("-" * 75)
    print(f"{'ID':<6} | {'KATEGORI':<18} | {'JUDUL MATERI'}")
    print("-" * 75)
    for it in results:
        mid = it.get("id", "-")
        cat = it.get("category", "Umum")[:18]
        tit = it.get("title", "Tanpa Judul")[:45]
        print(f"{mid:<6} | {cat:<18} | {tit}")
    print("-" * 75)
    print("Gunakan 'python cli.py view --id <ID>' untuk membaca isi materi lengkap.")


# ── 3. COMMAND: LIST ──────────────────────────────────────────────────────────

def cmd_list(args):
    """Menampilkan daftar seluruh materi yang terorganisasi di database lokal."""
    store = get_local_store()
    print(f"\n[*] Mengambil daftar materi dari database lokal (User ID: {args.user_id})...")

    try:
        mats = store.list_materials(args.user_id)
    except Exception as e:
        print(f"[!] Gagal mengambil materi: {e}")
        return

    if args.category:
        mats = [m for m in mats if str(m.get("category", "")).lower() == args.category.strip().lower()]

    if not mats:
        print("[-] Belum ada materi yang tersimpan di database lokal.")
        print("[i] Tips: Gunakan 'python cli.py scrape --url <URL> --save' untuk mengumpulkan materi baru.")
        return

    mats = mats[:args.limit]
    print(f"\n[+] Total materi ditemukan: {len(mats)}")
    print("-" * 80)
    print(f"{'ID':<6} | {'KATEGORI':<20} | {'TANGGAL':<12} | {'JUDUL'}")
    print("-" * 80)
    for m in mats:
        mid = m.get("id", "-")
        cat = m.get("category", "Umum")[:20]
        dt = str(m.get("created_at", "-"))[:10]
        tit = m.get("title", "-")[:36]
        print(f"{mid:<6} | {cat:<20} | {dt:<12} | {tit}")
    print("-" * 80)


# ── 4. COMMAND: VIEW ──────────────────────────────────────────────────────────

def cmd_view(args):
    """Melihat isi lengkap dokumen/materi dari database berdasarkan ID."""
    store = get_local_store()
    mat_id = args.id

    try:
        mat = store.get_material(args.user_id, mat_id)
    except Exception as e:
        print(f"[!] Gagal mengambil materi: {e}")
        return

    if not mat:
        print(f"[!] Materi dengan ID {mat_id} tidak ditemukan.")
        return

    print("\n" + "=" * 70)
    print(f" ID MATERI  : {mat.get('id')}")
    print(f" JUDUL      : {mat.get('title')}")
    print(f" KATEGORI   : {mat.get('category')}")
    print(f" SUMBER     : {mat.get('api_url') or mat.get('source_url') or '-'}")
    print(f" WAKTU BUAT : {mat.get('created_at', '-')}")
    print("=" * 70)
    print("\n" + mat.get("content", "(Konten kosong)"))
    print("\n" + "=" * 70)


# ── 5. COMMAND: BACKUP ────────────────────────────────────────────────────────

def cmd_backup(args):
    """Membuat salinan cadangan (backup) database lokal dengan kompresi dan rotasi file."""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(os.getenv("SQLITE_DB_PATH", str(BASE_DIR / "data" / "xiaozhi.db")))
    if not db_path.exists():
        # Cek root fallback
        root_db = BASE_DIR / "xiaozhi.db"
        if root_db.exists():
            db_path = root_db
        else:
            print(f"[!] File database SQLite tidak ditemukan di: {db_path}")
            return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = output_dir / f"xiaozhi_sqlite_{timestamp}.db"
    backup_gz_filename = output_dir / f"xiaozhi_sqlite_{timestamp}.db.gz"

    print(f"\n[*] Memulai proses backup database lokal...")
    print(f"[*] Sumber DB : {db_path} (Ukuran: {db_path.stat().st_size:,} bytes)")

    try:
        # Salin file SQLite secara aman
        shutil.copy2(db_path, backup_filename)

        # Kompres menggunakan Gzip untuk menghemat ruang disk
        with open(backup_filename, "rb") as f_in:
            with gzip.open(backup_gz_filename, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Hapus file mentah, simpan versi terkompresi .db.gz
        backup_filename.unlink()

        gz_size = backup_gz_filename.stat().st_size
        print(f"[+] SUKSES: Backup berhasil dibuat!")
        print(f"    Lokasi Arsip : {backup_gz_filename}")
        print(f"    Ukuran Arsip : {gz_size:,} bytes")

        # Pembersihan file backup usang berdasarkan masa retensi (retention_days)
        cutoff = datetime.now() - timedelta(days=args.retention_days)
        deleted_count = 0
        for p in output_dir.glob("xiaozhi_sqlite_*.db.gz"):
            if p.is_file():
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
                if mtime < cutoff:
                    p.unlink()
                    deleted_count += 1
        if deleted_count > 0:
            print(f"[*] Rotasi: {deleted_count} file backup usang (> {args.retention_days} hari) dibersihkan.")

    except Exception as exc:
        print(f"[!] Gagal membuat backup: {exc}")


# ── 6. COMMAND: REPORT ────────────────────────────────────────────────────────

def cmd_report(args):
    """Membuat laporan rekapitulasi data otomatis dalam format Markdown / JSON."""
    store = get_local_store()
    print(f"\n[*] Menyusun laporan rekapitulasi materi otomatis...")

    try:
        mats = store.list_materials(args.user_id)
    except Exception as e:
        print(f"[!] Gagal membaca data untuk laporan: {e}")
        return

    total_materi = len(mats)
    categories: Dict[str, int] = {}
    total_words = 0
    sources: List[str] = []

    for m in mats:
        cat = m.get("category") or "Umum"
        categories[cat] = categories.get(cat, 0) + 1
        content = m.get("content") or ""
        total_words += len(content.split())
        url = m.get("api_url") or m.get("source_url")
        if url and url not in sources:
            sources.append(url)

    now_str = datetime.now().strftime("%d %B %Y %H:%M:%S")

    # Format Markdown
    md_lines = [
        "# 📄 Laporan Otomatisasi Pengumpulan & Pengorganisasian Data",
        f"**Institusi / Proyek:** EduSmart Xiaozhi Indonesia",
        f"**Waktu Pembuatan:** {now_str}",
        f"**Database Mode:** Local SQLite (`data/xiaozhi.db`)",
        f"**Total Data Materi Terkumpul:** {total_materi} dokumen",
        f"**Total Akumulasi Kata:** {total_words:,} kata",
        "",
        "---",
        "",
        "## 1. Distribusi Pengelompokan Kategori Materi",
        "| No | Kategori Dokumen | Jumlah Materi | Persentase |",
        "|:---:|:---|:---:|:---:|",
    ]

    for idx, (cat, count) in enumerate(categories.items(), 1):
        pct = (count / total_materi * 100) if total_materi > 0 else 0
        md_lines.append(f"| {idx} | **{cat}** | {count} | {pct:.1f}% |")

    md_lines.extend([
        "",
        "---",
        "",
        "## 2. Inventaris Dokumen & Materi yang Tersimpan",
        "| ID | Judul Dokumen | Kategori | Sumber Tautan | Waktu Masuk |",
        "|:---:|:---|:---|:---|:---:|",
    ])

    for m in mats:
        mid = m.get("id", "-")
        tit = (m.get("title") or "-").replace("|", "\\|")
        cat = m.get("category") or "-"
        url = m.get("api_url") or m.get("source_url") or "-"
        if len(url) > 35:
            url_display = f"[{url[:32]}...]({url})"
        elif url != "-":
            url_display = f"[{url}]({url})"
        else:
            url_display = "-"
        dt = str(m.get("created_at") or "-")[:10]
        md_lines.append(f"| {mid} | {tit} | {cat} | {url_display} | {dt} |")

    md_lines.extend([
        "",
        "---",
        "",
        "## 3. Catatan Audit Otomatisasi Sistem",
        "- **Teknologi Ekstraksi:** Python Requests, BeautifulSoup4, SSRF Security Filter.",
        "- **Pembersihan Konten:** Deteksi otomatis dan penghapusan teks noise berita Indonesia & Wikipedia referensi.",
        "- **Status Integritas:** Seluruh data tersimpan aman dan terindeks dalam format Markdown terstruktur.",
    ])

    report_content = "\n".join(md_lines)
    output_file = Path(args.output)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"\n[+] SUKSES: Laporan rekapitulasi data berhasil diekspor ke:")
    print(f"    -> {output_file.resolve()}")
    print(f"    (Total: {total_materi} materi terangkum dalam format Markdown tabel)")


# ── 7. COMMAND: INFO ──────────────────────────────────────────────────────────

def cmd_info(args):
    """Menampilkan status konfigurasi dan lingkungan lokal vs server."""
    from xiaozhi.services.scraper_service import HAS_BS4, HAS_REQUESTS, HAS_PYMUPDF, HAS_PYPDF

    db_path = Path(os.getenv("SQLITE_DB_PATH", str(BASE_DIR / "data" / "xiaozhi.db")))
    db_size = f"{db_path.stat().st_size:,} bytes" if db_path.exists() else "Belum dibuat"

    print("\n" + "=" * 65)
    print("          INFORMASI STATUS SISTEM (LOCAL vs SERVER)")
    print("=" * 65)
    print(f" Lingkungan Aktif     : LOCAL (Development / Pengujian Mandiri)")
    print(f" Database Driver      : SQLite 3 (Standar tanpa butuh PostgreSQL)")
    print(f" Lokasi Database      : {db_path}")
    print(f" Ukuran Database      : {db_size}")
    print("-" * 65)
    print(f" Engine Scraping (requests)       : {'[READY]' if HAS_REQUESTS else '[NOT INSTALLED]'}")
    print(f" Parser HTML (BeautifulSoup4)     : {'[READY]' if HAS_BS4 else '[NOT INSTALLED]'}")
    print(f" Parser Dokumen PDF (PyMuPDF)     : {'[READY]' if HAS_PYMUPDF else ('[READY - pypdf]' if HAS_PYPDF else '[NOT INSTALLED]')}")
    print("-" * 65)
    print(" Perbedaan dengan Mode SERVER (VPS):")
    print("  * Local  : Menggunakan SQLite (zero-config), jalan langsung di laptop.")
    print("  * Server : Menggunakan PostgreSQL 16 di Docker + Nginx SSL HTTPS.")
    print("=" * 65)


# ── 8. COMMAND: SERVE ─────────────────────────────────────────────────────────

def cmd_serve(args):
    """Menjalankan web server lokal untuk demonstrasi visual dashboard."""
    import uvicorn
    print(f"\n[*] Menjalankan Web Server EduSmart Xiaozhi versi LOCAL...")
    print(f"[*] Buka browser di: http://127.0.0.1:{args.port}")
    print(f"[*] Tekan Ctrl+C untuk menghentikan server.\n")

    # Pastikan cookie secure false saat dijalankan di local HTTP
    os.environ["COOKIE_SECURE"] = "false"
    os.environ["ENVIRONMENT"] = "development"

    uvicorn.run("xiaozhi.main:app", host="127.0.0.1", port=args.port, reload=args.reload)


# ── MAIN PARSER ───────────────────────────────────────────────────────────────

def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="EduSmart Xiaozhi - CLI Otomasi Data, Web Scraping, Pembersihan, dan Pelaporan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Perintah yang tersedia")

    # Subcommand: scrape
    p_scrape = subparsers.add_parser("scrape", help="Ambil dan bersihkan data dari URL website")
    p_scrape.add_argument("--url", required=True, help="URL website publik yang akan di-scrape")
    p_scrape.add_argument("--save", action="store_true", help="Simpan hasil ekstraksi langsung ke database lokal")
    p_scrape.add_argument("--category", default="", help="Kategori dokumen (default: otomatis)")
    p_scrape.add_argument("--export", default="", help="Simpan teks hasil ekstraksi ke file (misal: materi.md)")
    p_scrape.add_argument("--max-chars", type=int, default=15000, help="Batas maksimal karakter (default: 15000)")
    p_scrape.add_argument("--user-id", type=int, default=1, help="ID pengguna lokal pemilik materi (default: 1)")

    # Subcommand: search
    p_search = subparsers.add_parser("search", help="Cari materi di database lokal berdasarkan kata kunci")
    p_search.add_argument("--query", required=True, help="Kata kunci pencarian")
    p_search.add_argument("--limit", type=int, default=10, help="Jumlah maksimal hasil (default: 10)")
    p_search.add_argument("--user-id", type=int, default=1, help="ID pengguna lokal (default: 1)")

    # Subcommand: list
    p_list = subparsers.add_parser("list", help="Tampilkan daftar semua materi tersimpan di database lokal")
    p_list.add_argument("--category", default="", help="Filter berdasarkan kategori tertentu")
    p_list.add_argument("--limit", type=int, default=25, help="Jumlah maksimal materi (default: 25)")
    p_list.add_argument("--user-id", type=int, default=1, help="ID pengguna lokal (default: 1)")

    # Subcommand: view
    p_view = subparsers.add_parser("view", help="Lihat teks lengkap materi dari database lokal berdasarkan ID")
    p_view.add_argument("--id", type=int, required=True, help="ID materi di database")
    p_view.add_argument("--user-id", type=int, default=1, help="ID pengguna lokal (default: 1)")

    # Subcommand: backup
    p_backup = subparsers.add_parser("backup", help="Buat backup database lokal terkompresi dengan rotasi")
    p_backup.add_argument("--output-dir", default="backups", help="Direktori penyimpanan backup (default: backups)")
    p_backup.add_argument("--retention-days", type=int, default=7, help="Masa retensi file backup dalam hari (default: 7)")

    # Subcommand: report
    p_report = subparsers.add_parser("report", help="Ekspor laporan rekapitulasi data otomatis (Markdown/JSON)")
    p_report.add_argument("--output", default="laporan_materi_edusmart.md", help="File tujuan laporan (default: laporan_materi_edusmart.md)")
    p_report.add_argument("--user-id", type=int, default=1, help="ID pengguna lokal (default: 1)")

    # Subcommand: info
    subparsers.add_parser("info", help="Tampilkan status lingkungan lokal vs server dan dependensi")

    # Subcommand: serve
    p_serve = subparsers.add_parser("serve", help="Jalankan web server dashboard lokal (FastAPI + Uvicorn)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port server lokal (default: 8000)")
    p_serve.add_argument("--reload", action="store_true", help="Aktifkan auto-reload saat kode diubah")

    args = parser.parse_args()

    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "scrape": cmd_scrape,
        "search": cmd_search,
        "list": cmd_list,
        "view": cmd_view,
        "backup": cmd_backup,
        "report": cmd_report,
        "info": cmd_info,
        "serve": cmd_serve,
    }

    handler = handlers.get(args.subcommand)
    if handler:
        handler(args)


if __name__ == "__main__":
    main()
