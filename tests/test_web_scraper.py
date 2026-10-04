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


if __name__ == "__main__":
    unittest.main()
