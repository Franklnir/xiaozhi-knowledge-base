import unittest
from unittest.mock import patch, MagicMock
from xiaozhi.services.scraper_service import (
    validate_scrape_url,
    scrape_url,
    create_material_from_url,
)


class TestWebScraper(unittest.TestCase):
    def test_ssrf_blocking_localhost_and_private_ips(self):
        # Localhost strings
        res = validate_scrape_url("http://localhost:8000/secret")
        self.assertFalse(res["valid"])
        self.assertIn("diblokir", res["error"].lower())

        res = validate_scrape_url("http://127.0.0.1:5432/db")
        self.assertFalse(res["valid"])
        self.assertIn("diblokir", res["error"].lower())

        # Cloud Metadata IP
        res = validate_scrape_url("http://169.254.169.254/latest/meta-data/")
        self.assertFalse(res["valid"])
        self.assertIn("diblokir", res["error"].lower())

        # Private RFC1918 IPs
        res = validate_scrape_url("http://192.168.1.1/admin")
        self.assertFalse(res["valid"])
        self.assertIn("diblokir", res["error"].lower())

        res = validate_scrape_url("http://10.0.0.5/")
        self.assertFalse(res["valid"])
        self.assertIn("diblokir", res["error"].lower())

    def test_valid_public_url(self):
        res = validate_scrape_url("https://example.com/edukasi/ai-indonesia")
        self.assertTrue(res["valid"])
        self.assertEqual(res["hostname"], "example.com")

    def test_scrape_html_cleaning_and_extraction(self):
        sample_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Belajar IoT dan AI dengan ESP32 - EduSmart</title>
            <meta name="description" content="Panduan komprehensif merakit voice assistant berbasis ESP32.">
            <meta name="keywords" content="esp32, iot, voice assistant, ai">
            <style>body { font-family: sans-serif; } .ad { display: block; }</style>
            <script>console.log("analytics tracking");</script>
        </head>
        <body>
            <header><nav><a href="/">Home</a> <a href="/menu">Menu</a></nav></header>
            <article class="article-content">
                <h1>Tutorial ESP32 Voice Assistant</h1>
                <p>Platform XiaoZhi adalah solusi asisten suara cerdas berbasis mikrokontroler ESP32.</p>
                <div class="ad">Iklan Banner Mengganggu</div>
                <p>Dengan integrasi FastMCP, perangkat dapat terhubung ke berbagai tools komputasi dan database.</p>
            </article>
            <footer><p>&copy; 2026 Hak Cipta Dilindungi</p></footer>
        </body>
        </html>
        """

        mock_resp = MagicMock()
        mock_resp.content = sample_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            data = scrape_url("https://example.com/tutorial-esp32")

            self.assertTrue(data["success"])
            self.assertIn("Belajar IoT dan AI dengan ESP32", data["title"])
            self.assertIn("Panduan komprehensif", data["description"])
            self.assertIn("esp32, iot", data["keywords"])
            # Ensure script, style, ads, and nav are removed
            self.assertNotIn("analytics tracking", data["content"])
            self.assertNotIn("Iklan Banner", data["content"])
            self.assertNotIn("Home", data["content"])
            # Ensure article content is retained
            self.assertIn("Platform XiaoZhi adalah solusi asisten suara", data["content"])
            self.assertIn("FastMCP", data["content"])
            self.assertGreater(data["word_count"], 10)

    def test_create_material_from_url_formatter(self):
        sample_html = """<html><head><title>Materi Kuliah AI</title></head><body>
        <p>Ini adalah ringkasan konsep jaringan syaraf tiruan dan arsitektur pembelajaran mesin modern.</p>
        <p>Model komputasi syaraf meniru cara kerja biologis otak manusia untuk menyelesaikan masalah klasifikasi dan regresi data secara mendalam.</p>
        </body></html>"""
        mock_resp = MagicMock()
        mock_resp.content = sample_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            mat = create_material_from_url("https://example.com/materi-ai", category="Kecerdasan Buatan")
            self.assertTrue(mat["success"])
            self.assertEqual(mat["title"], "Materi Kuliah AI")
            self.assertEqual(mat["category"], "Kecerdasan Buatan")
            self.assertIn("https://example.com/materi-ai", mat["content"])
            self.assertIn("jaringan syaraf tiruan", mat["content"])

    def test_reject_too_short_or_blocked_content(self):
        # Only 3 words (e.g. captcha or error page)
        sample_html = "<html><head><title>Captcha</title></head><body><p>Access denied please.</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.content = sample_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            mat = create_material_from_url("https://example.com/blocked")
            self.assertFalse(mat["success"])
            self.assertIn("terlalu pendek", mat["error"])

    def test_mcp_scraper_tools(self):
        from xiaozhi.mcp.tools import register_tools, mcp_active_owner_ctx

        registered_tools = {}

        class DummyServer:
            def tool(self):
                def decorator(fn):
                    registered_tools[fn.__name__] = fn
                    return fn
                return decorator

        server = DummyServer()
        mock_store = MagicMock()
        mock_store.add_material.return_value = 999
        mock_history = MagicMock()

        register_tools(server, mock_store, mock_history)

        self.assertIn("scrape_webpage", registered_tools)
        self.assertIn("scrape_and_save_to_knowledge", registered_tools)

        # Test scrape_webpage handler with active user context
        token = mcp_active_owner_ctx.set(101)
        try:
            with patch("xiaozhi.services.scraper_service.scrape_url") as mock_scrape:
                mock_scrape.return_value = {
                    "success": True,
                    "url": "https://example.com/berita",
                    "title": "Berita AI Terkini",
                    "content": "Kecerdasan buatan berkembang pesat di Indonesia.",
                    "word_count": 7,
                    "is_truncated": False,
                }
                res = registered_tools["scrape_webpage"]("https://example.com/berita")
                self.assertTrue(res["success"])
                self.assertEqual(res["judul"], "Berita AI Terkini")
                self.assertIn("berkembang pesat", res["isi_konten"])

            # Test scrape_and_save_to_knowledge
            with patch("xiaozhi.services.scraper_service.create_material_from_url") as mock_create:
                mock_create.return_value = {
                    "success": True,
                    "title": "Artikel Edukasi",
                    "category": "Web Scraping",
                    "content": "Isi materi edukasi...",
                    "keywords": "web, edukasi",
                    "word_count": 3,
                }
                with patch("xiaozhi.services.mcp_service.signal_mcp_reload"):
                    save_res = registered_tools["scrape_and_save_to_knowledge"]("https://example.com/edukasi")
                    self.assertTrue(save_res["success"])
                    self.assertEqual(save_res["material_id"], 999)
                    mock_store.add_material.assert_called_once()

            # Test delete_course_material when user says "salah website, hapus yang tadi"
            self.assertIn("delete_course_material", registered_tools)
            mock_store.list_materials.return_value = [
                {"id": 999, "title": "Artikel Edukasi Salah", "category": "Web Scraping", "content": "Sumber: https://example.com"}
            ]
            mock_store.delete_material.return_value = True
            with patch("xiaozhi.services.mcp_service.signal_mcp_reload"):
                del_res = registered_tools["delete_course_material"]("salah website tolong hapus materi terakhir", delete_last_scraped=True)
                self.assertTrue(del_res["success"])
                self.assertEqual(del_res["deleted_id"], 999)
                self.assertIn("Artikel Edukasi Salah", del_res["message"])
                mock_store.delete_material.assert_called_with(101, 999)
        finally:
            mcp_active_owner_ctx.reset(token)

    def test_clean_indonesian_news_noise(self):
        from xiaozhi.services.scraper_service import clean_indonesian_news_noise

        dirty_text = """
        Kementerian Pendidikan mengumumkan kurikulum baru untuk teknologi kecerdasan buatan.
        Baca juga: 5 Kampus Terbaik dengan Jurusan AI di Indonesia
        Simak video: Wawancara Eksklusif dengan Mendikbudristek
        Kurikulum ini akan diterapkan serempak pada jenjang SMA dan SMK.
        Halaman selanjutnya: Rincian Mata Pelajaran AI
        Download aplikasi Kompas.com sekarang untuk info lebih lanjut.
        Ikuti berita terkini dan mendalam di Google News.
        Pembelajaran akan berbasis proyek mikrokontroler seperti ESP32.
        """
        cleaned = clean_indonesian_news_noise(dirty_text)
        self.assertIn("Kementerian Pendidikan mengumumkan", cleaned)
        self.assertIn("Kurikulum ini akan diterapkan", cleaned)
        self.assertIn("ESP32", cleaned)
        self.assertNotIn("Baca juga:", cleaned)
        self.assertNotIn("Simak video:", cleaned)
        self.assertNotIn("Halaman selanjutnya", cleaned)
        self.assertNotIn("Download aplikasi", cleaned)
        self.assertNotIn("Google News", cleaned)

    def test_extract_youtube_video_id(self):
        from xiaozhi.services.scraper_service import extract_youtube_video_id

        self.assertEqual(extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(extract_youtube_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(extract_youtube_video_id("https://youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(extract_youtube_video_id("https://youtube.com/live/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertIsNone(extract_youtube_video_id("https://example.com/not-youtube"))

    def test_jina_markdown_response_parser(self):
        from xiaozhi.services.scraper_service import _parse_jina_markdown_response

        jina_output = """Title: Belajar Mikrokontroler ESP32
URL Source: https://medium.com/@dev/belajar-esp32
Published Time: 2026-01-15T08:00:00Z

Markdown Content:
# Belajar Mikrokontroler ESP32

ESP32 adalah mikrokontroler berkemampuan Wi-Fi dan Bluetooth berbiaya rendah dan hemat daya.
Sangat cocok untuk IoT dan voice assistant modern.
"""
        res = _parse_jina_markdown_response(jina_output, "https://medium.com/@dev/belajar-esp32", 10000)
        self.assertIsNotNone(res)
        self.assertTrue(res["success"])
        self.assertEqual(res["title"], "Belajar Mikrokontroler ESP32")
        self.assertIn("ESP32 adalah mikrokontroler", res["content"])
        self.assertEqual(res["source_engine"], "jina_reader")

    def test_youtube_transcript_mock(self):
        from xiaozhi.services.scraper_service import extract_youtube_transcript

        mock_transcript = [
            {"text": "Halo teman-teman semua", "start": 0.0, "duration": 2.5},
            {"text": "hari ini kita belajar IoT ESP32", "start": 3.0, "duration": 4.0},
            {"text": "pertama kita siapkan kabel data", "start": 35.0, "duration": 3.0},
        ]

        mock_instance = MagicMock()
        mock_fetched = MagicMock()
        mock_fetched.to_raw_data.return_value = mock_transcript
        mock_instance.fetch.return_value = mock_fetched

        with patch("xiaozhi.services.scraper_service.YouTubeTranscriptApi", return_value=mock_instance) as mock_cls:
            mock_cls.get_transcript = MagicMock(return_value=mock_transcript)
            mock_cls.fetch = mock_instance.fetch
            with patch("xiaozhi.services.scraper_service.urllib.request.urlopen") as mock_oembed:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"title": "Tutorial ESP32 Pemula", "author_name": "Kelas Robotika"}'
                mock_oembed.return_value.__enter__.return_value = mock_resp

                res = extract_youtube_transcript("dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ")
                self.assertTrue(res["success"])
                self.assertIn("Tutorial ESP32 Pemula", res["title"])
                self.assertIn("[00:00] Halo teman-teman semua", res["content"])
                self.assertIn("[00:35] pertama kita siapkan kabel data", res["content"])
                self.assertEqual(res["source_type"], "youtube_transcript")

    def test_academic_journal_scraping_and_metadata(self):
        journal_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="citation_title" content="Deep Learning for Real-Time IoT Speech Recognition">
            <meta name="citation_author" content="Dr. Budi Santoso">
            <meta name="citation_author" content="Prof. Siti Aminah">
            <meta name="citation_publication_date" content="2025/11/15">
            <meta name="citation_journal_title" content="IEEE Transactions on Neural Networks">
            <meta name="citation_doi" content="10.1109/TNNLS.2025.1234567">
            <meta name="citation_abstract" content="In this research, we propose an ultra-compact Transformer model optimized for edge devices like ESP32-S3, achieving 98% accuracy.">
        </head>
        <body>
            <article>
                <h1>Deep Learning for Real-Time IoT Speech Recognition</h1>
                <div class="abstract">
                    In this research, we propose an ultra-compact Transformer model optimized for edge devices like ESP32-S3, achieving 98% accuracy.
                </div>
                <h2>Introduction</h2>
                <p>Speech processing at the edge enables low-latency offline voice assistants without constant cloud dependencies.</p>
                <p>We evaluate memory footprints against baseline Recurrent Neural Networks.</p>
            </article>
        </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.content = journal_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            data = scrape_url("https://ieeexplore.ieee.org/document/1234567")
            self.assertTrue(data["success"])
            self.assertEqual(data["title"], "Deep Learning for Real-Time IoT Speech Recognition")
            self.assertIn("Jurnal Ilmiah", data["content_type"])
            self.assertIn("Dr. Budi Santoso", data["authors"])
            self.assertIn("Prof. Siti Aminah", data["authors"])
            self.assertEqual(data["journal"], "IEEE Transactions on Neural Networks")
            self.assertEqual(data["doi"], "10.1109/TNNLS.2025.1234567")
            self.assertIn("10.1109/TNNLS.2025.1234567", data["content"])
            self.assertIn("### 📑 Abstrak / Ringkasan Eksekutif", data["content"])
            self.assertIn("Transformer model optimized for edge devices", data["content"])

    def test_arxiv_paper_scraping(self):
        arxiv_html = """
        <html>
        <head>
            <meta name="citation_title" content="Attention Is All You Need">
            <meta name="citation_author" content="Ashish Vaswani">
            <meta name="citation_author" content="Noam Shazeer">
            <meta name="citation_date" content="2017/06/12">
            <meta name="citation_doi" content="10.48550/arXiv.1706.03762">
        </head>
        <body>
            <div id="abs">
                <h1 class="title mathjax"><span class="descriptor">Title:</span>Attention Is All You Need</h1>
                <div class="authors"><span class="descriptor">Authors:</span><a href="/search?searchtype=author">Ashish Vaswani</a>, <a href="/search?searchtype=author">Noam Shazeer</a></div>
                <blockquote class="abstract mathjax">
                    <span class="descriptor">Abstract:</span> The dominant sequence transduction models are based on complex recurrent or convolutional neural networks. We propose the Transformer, a novel network architecture based solely on attention mechanisms.
                </blockquote>
            </div>
        </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.content = arxiv_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            data = scrape_url("https://arxiv.org/abs/1706.03762")
            self.assertTrue(data["success"])
            self.assertEqual(data["title"], "Attention Is All You Need")
            self.assertIn("arXiv", data["content_type"])
            self.assertIn("Ashish Vaswani", data["authors"])
            self.assertIn("Noam Shazeer", data["authors"])
            self.assertIn("10.48550/arXiv.1706.03762", data["doi"])
            self.assertIn("We propose the Transformer", data["content"])

    def test_inline_links_and_reference_section(self):
        links_html = """
        <html><body><article>
            <h1>Panduan Hardware ESP32</h1>
            <p>Untuk memulai proyek, kunjungi <a href="/docs/getting-started">Dokumentasi Resmi</a>.</p>
            <p>Gunakan sensor mikrofon I2S yang tersedia di <a href="https://vendor.com/inmp441">Modul INMP441</a>.</p>
            <p>Bagikan artikel ini: <a href="https://facebook.com/sharer?u=abc">Share ke FB</a></p>
            <p>Untuk pertanyaan hubungi kami: <a href="mailto:support@example.com">Email CS</a></p>
        </article></body></html>
        """
        mock_resp = MagicMock()
        mock_resp.content = links_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            data = scrape_url("https://example.com/tutorial")
            self.assertTrue(data["success"])
            # Relative link resolved to absolute
            self.assertIn("[Dokumentasi Resmi](https://example.com/docs/getting-started)", data["content"])
            self.assertIn("[Modul INMP441](https://vendor.com/inmp441)", data["content"])
            # Social share and mailto filtered out from markdown link syntax
            self.assertNotIn("[Share ke FB](https://facebook.com", data["content"])
            self.assertNotIn("mailto:", data["content"])
            # Key references section appended
            self.assertIn("### 🔗 Referensi & Tautan Penting Terkait", data["content"])
            self.assertIn("• [Dokumentasi Resmi](https://example.com/docs/getting-started)", data["content"])

    def test_code_blocks_and_callout_extraction(self):
        code_html = """
        <html><body><article>
            <h1>Konfigurasi WiFi ESP32</h1>
            <div class="alert warning">
                Pastikan frekuensi WiFi adalah 2.4 GHz karena ESP32 tidak mendukung 5 GHz.
            </div>
            <p>Gunakan baris kode berikut pada Arduino IDE:</p>
            <pre><code class="language-cpp">
#include &lt;WiFi.h&gt;
void setup() {
    WiFi.begin("SSID", "PASS");
}
            </code></pre>
            <p>Periksa serial monitor dengan kecepatan <code>115200 baud</code>.</p>
        </article></body></html>
        """
        mock_resp = MagicMock()
        mock_resp.content = code_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            data = scrape_url("https://example.com/wifi-esp32")
            self.assertTrue(data["success"])
            # Code block with cpp language tag
            self.assertIn("```cpp", data["content"])
            self.assertIn("WiFi.begin(\"SSID\", \"PASS\");", data["content"])
            # Inline code formatted
            self.assertIn("`115200 baud`", data["content"])
            # Warning callout formatted
            self.assertIn("> ⚠️ **Perhatian**: Pastikan frekuensi WiFi adalah 2.4 GHz", data["content"])
            # Content type auto-classified as technical documentation
            self.assertIn("Dokumentasi Teknis", data["content_type"])

    def test_auto_category_classification_in_create_material(self):
        journal_html = """
        <html><head>
            <meta name="citation_title" content="Advanced Neural Robotics">
            <meta name="citation_doi" content="10.1234/robotics.2026.01">
        </head><body><article>
            <h1>Advanced Neural Robotics</h1>
            <p>Neural networks applied to bipedal robotic motion control in outdoor terrain.</p>
            <p>Extensive simulation demonstrates stability under perturbations.</p>
        </article></body></html>
        """
        mock_resp = MagicMock()
        mock_resp.content = journal_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            mat = create_material_from_url("https://example.com/robotics-paper")
            self.assertTrue(mat["success"])
            self.assertEqual(mat["category"], "Jurnal Ilmiah")
            self.assertEqual(mat["doi"], "10.1234/robotics.2026.01")

    def test_wikipedia_smart_formatter(self):
        wiki_html = """
        <!DOCTYPE html>
        <html>
        <head><title>ESP32 - Wikipedia bahasa Indonesia</title></head>
        <body>
            <article id="content">
                <table class="infobox">
                    <tr><th colspan="2">ESP32 Microcontroller</th></tr>
                    <tr><td>Produsen</td><td>Espressif Systems</td></tr>
                    <tr><td>Arsitektur</td><td>Xtensa dual-core 32-bit LX6</td></tr>
                    <tr><td>Frekuensi</td><td>240 MHz</td></tr>
                </table>
                <h1>ESP32</h1>
                <p>ESP32 adalah mikrokontroler berbiaya rendah dan hemat daya dengan Wi-Fi terintegrasi[1][2]. Perangkat ini dikembangkan oleh Espressif Systems[sunting].</p>
                <p>Fitur utamanya meliputi Bluetooth v4.2 BR/EDR dan BLE[citation needed] yang mendukung transmisi audio secara nirkabel.</p>
                <h2>Referensi</h2>
                <p>1. Espressif Systems Overview.</p>
                <h2>Pranala luar</h2>
                <p>Situs resmi Espressif.</p>
            </article>
        </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.content = wiki_html.encode("utf-8")
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.raise_for_status = MagicMock()

        with patch("xiaozhi.services.scraper_service.requests.get", return_value=mock_resp):
            data = scrape_url("https://id.wikipedia.org/wiki/ESP32")
            self.assertTrue(data["success"])
            # Infobox extracted into specifications
            self.assertIn("Ringkasan Fakta & Spesifikasi (Infobox Wikipedia)", data["content"])
            self.assertIn("Espressif Systems", data["content"])
            self.assertIn("Xtensa dual-core", data["content"])
            # Citations [1][2] and [sunting] and [citation needed] stripped
            self.assertNotIn("[1]", data["content"])
            self.assertNotIn("[2]", data["content"])
            self.assertNotIn("[sunting]", data["content"])
            self.assertNotIn("[citation needed]", data["content"])
            # Truncated before Referensi and Pranala luar
            self.assertNotIn("Situs resmi Espressif", data["content"])

    def test_auto_quiz_generator_flashcards(self):
        from xiaozhi.services.scraper_service import generate_quiz_from_content, format_quiz_as_markdown
        text = (
            "Sensor MAX6675 adalah konverter digital termokopel tipe-K dengan kompensasi cold-junction terintegrasi. "
            "Sensor ini menggunakan antarmuka SPI hanya-baca dengan resolusi pembacaan 12-bit dan akurasi 0.25 derajat Celsius. "
            "Tegangan operasional sensor berkisar antara 3.0V hingga 5.5V sehingga sangat cocok dipasangkan langsung ke ESP32 atau Arduino."
        )
        quiz = generate_quiz_from_content(text, title="MAX6675 ESP32", num_questions=3)
        self.assertGreaterEqual(len(quiz), 1)
        self.assertIn("question", quiz[0])
        self.assertIn("answer", quiz[0])

        quiz_md = format_quiz_as_markdown(quiz)
        self.assertIn("### 🧠 Kuis Pemahaman & Flashcards", quiz_md)
        self.assertIn("❓ **Pertanyaan**:", quiz_md)
        self.assertIn("💡 **Kunci Jawaban**:", quiz_md)

    def test_academic_papers_unified_search(self):
        from xiaozhi.services.scraper_service import search_academic_papers_unified
        mock_arxiv_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
            <entry>
                <id>http://arxiv.org/abs/2401.00001v1</id>
                <title>TinyTransformer: Edge Speech Recognition on Microcontrollers</title>
                <summary>We introduce a tiny transformer model achieving 96% accuracy on embedded microcontrollers.</summary>
                <published>2024-01-10T12:00:00Z</published>
                <author><name>Dr. John Doe</name></author>
                <link title="pdf" href="http://arxiv.org/pdf/2401.00001v1.pdf" type="application/pdf"/>
            </entry>
        </feed>
        """
        mock_arxiv_resp = MagicMock()
        mock_arxiv_resp.read.return_value = mock_arxiv_xml
        mock_arxiv_resp.__enter__.return_value = mock_arxiv_resp

        with patch("urllib.request.urlopen", return_value=mock_arxiv_resp), \
             patch("xiaozhi.services.scraper_service.search_semantic_scholar_papers", return_value=[]), \
             patch("xiaozhi.services.scraper_service.search_crossref_papers", return_value=[]):
            res = search_academic_papers_unified("edge speech recognition", max_results=3)
            self.assertTrue(res["success"])
            self.assertEqual(res["total_found"], 1)
            paper = res["papers"][0]
            self.assertEqual(paper["title"], "TinyTransformer: Edge Speech Recognition on Microcontrollers")
            self.assertEqual(paper["year"], "2024")
            self.assertIn("Dr. John Doe", paper["authors"])
            self.assertIn("http://arxiv.org/pdf/2401.00001v1.pdf", paper["pdf_url"])
            self.assertIn("Hasil Pencarian Jurnal & Paper Ilmiah", res["summary_markdown"])

    def test_autonomous_deep_research_confirmation_rule(self):
        from xiaozhi.services.scraper_service import conduct_autonomous_research
        mock_paper = {
            "success": True,
            "query": "MAX6675 ESP32",
            "total_found": 1,
            "papers": [{
                "title": "Interfacing High Temperature MAX6675 with ESP32",
                "authors": ["Eng. Smith"],
                "year": "2025",
                "source": "CrossRef",
                "citations": "15 Sitasi",
                "citation_count": 15,
                "abstract": "Design and calibration of MAX6675 thermocouple for industrial temperature monitoring with ESP32.",
                "pdf_url": "https://example.com/paper.pdf",
                "url": "https://example.com/paper",
            }],
            "summary_markdown": "summary",
        }

        with patch("xiaozhi.services.scraper_service.search_academic_papers_unified", return_value=mock_paper):
            res = conduct_autonomous_research("paper MAX6675 ESP32", mode="academic")
            self.assertTrue(res["success"])
            # Must NOT be saved directly to DB
            self.assertFalse(res["saved_to_db"])
            self.assertTrue(res["needs_user_confirmation"])
            # Must contain the exact confirmation prompt
            self.assertIn("Apakah Anda ingin saya menyimpan materi ini ke Knowledge Base Anda?", res["confirmation_prompt"])
            self.assertIn("Dan ada hal lain yang ingin Anda tanyakan atau pelajari?", res["confirmation_prompt"])
            # Must prepare payload ready for user approval
            self.assertIn("materi_siap_simpan", res)
            self.assertEqual(res["materi_siap_simpan"]["category"], "Jurnal Ilmiah")

    def test_mcp_tools_registration_and_quiz_flow(self):
        from xiaozhi.mcp.tools import register_tools
        from xiaozhi.mcp.context import mcp_active_owner_ctx

        registered = {}
        mock_mcp = MagicMock()
        def mock_tool():
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator
        mock_mcp.tool = mock_tool

        mock_store = MagicMock()
        mock_store.list_materials.return_value = [{
            "id": 42,
            "title": "ESP32 Termokopel MAX6675",
            "category": "Jurnal Ilmiah",
            "content": (
                "# ESP32 Termokopel MAX6675\n\n"
                "Panduan interfacing sensor MAX6675 dengan mikrokontroler ESP32.\n\n"
                "### 🧠 Kuis Pemahaman & Flashcards\n\n"
                "1. ❓ **Pertanyaan**: Berapa resolusi ADC internal MAX6675?\n"
                "   - 💡 **Kunci Jawaban**: Resolusi 12-bit dengan presisi 0.25 derajat Celsius.\n\n"
                "2. ❓ **Pertanyaan**: Apa protokol komunikasi yang digunakan?\n"
                "   - 💡 **Kunci Jawaban**: Protokol SPI read-only."
            ),
            "keywords": "esp32, max6675, spi",
        }]
        mock_store.add_material.return_value = 43

        history_calls = []
        def mock_record_history(owner_id, tool_name, summary, params, result):
            history_calls.append((owner_id, tool_name, summary, result))

        register_tools(mock_mcp, mock_store, mock_record_history)

        # Ensure all 4 new tools are registered
        self.assertIn("search_academic_papers", registered)
        self.assertIn("autonomous_deep_research", registered)
        self.assertIn("confirm_and_save_research_material", registered)
        self.assertIn("test_user_knowledge_quiz", registered)

        # Test context owner
        token = mcp_active_owner_ctx.set(101)
        try:
            # 1. Test quiz presentation (Question 1)
            quiz_fn = registered["test_user_knowledge_quiz"]
            q1_res = quiz_fn(topic_or_material_title="MAX6675", question_number=1, user_answer="")
            self.assertTrue(q1_res["success"])
            self.assertEqual(q1_res["mode"], "tanya_soal")
            self.assertEqual(q1_res["soal_ke"], 1)
            self.assertIn("Berapa resolusi ADC internal MAX6675?", q1_res["pertanyaan"])

            # 2. Test user answering Question 1
            eval_res = quiz_fn(topic_or_material_title="MAX6675", question_number=1, user_answer="Resolusinya 12 bit")
            self.assertTrue(eval_res["success"])
            self.assertEqual(eval_res["mode"], "evaluasi_jawaban")
            self.assertIn("12-bit", eval_res["kunci_jawaban"])
            self.assertTrue(eval_res["ada_soal_berikutnya"])
            self.assertEqual(eval_res["nomor_soal_berikutnya"], 2)

            # 3. Test confirmation save tool
            confirm_fn = registered["confirm_and_save_research_material"]
            save_res = confirm_fn(
                title="Riset MAX6675 ESP32",
                content="Langkah wiring SPI ESP32",
                url="https://example.com/max6675",
                category="Riset Mandiri",
                save_confirmed=True
            )
            self.assertTrue(save_res["success"])
            self.assertEqual(save_res["material_id"], 43)
            self.assertIn("Dan ada hal lain yang ingin Anda tanyakan atau pelajari?", save_res["tanya_pengguna_lanjutan"])
            mock_store.add_material.assert_called_once()
        finally:
            mcp_active_owner_ctx.reset(token)


if __name__ == "__main__":
    unittest.main()


