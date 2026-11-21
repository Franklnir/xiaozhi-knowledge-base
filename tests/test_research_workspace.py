import re
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from xiaozhi.config import SESSION_COOKIE, session_serializer
from xiaozhi.database.research_store import ResearchStore
from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.dependencies import make_csrf_token
from xiaozhi.services import research_workspace, scraper_service
from xiaozhi.services.research_audit import ResearchAudit, audited_call, is_source_access
from xiaozhi.services.research_retries import retry_plan, retry_research


def sample_research(query, mode):
    audited_call("cari_wikipedia", "https://example.com/search?token=private", lambda: None)
    try:
        audited_call("cari_youtube", "https://example.com/video", Mock(side_effect=TimeoutError))
    except TimeoutError:
        pass
    return {"sources": {
        "web": [{"title": "Panduan", "url": "https://example.com/guide", "excerpt": "Kapasitas baterai."}],
        "academic": [{"title": "Studi", "url": "https://example.com/study", "pdf_url": "https://example.com/study.pdf",
                      "abstract": "Uji kapasitas.", "authors": ["Peneliti A"], "year": "2025", "doi": "10.1000/test"}],
    }}


def test_audit_measures_unfinished_access_and_recognizes_older_entries(monkeypatch):
    from xiaozhi.services import research_audit

    timeline = iter([10, 10.1, 10.3, 10.4, 10.5])
    monkeypatch.setattr(research_audit, "time", SimpleNamespace(monotonic=lambda: next(timeline)))
    audit = ResearchAudit()
    audit.begin("cari_wikipedia", "https://example.com/?token=hidden")
    extraction = audit.begin("ekstraksi_sumber", "https://example.com/study")
    audit.end(extraction, "failed")
    access, outcome = audit.close()

    assert (access["status"], access["duration_ms"], access["started_after_ms"]) == ("timeout", 400, 100)
    assert access["target"] == "https://example.com/"
    assert is_source_access(access) and not is_source_access(outcome)
    assert is_source_access({"operation": "cari_wikipedia", "started_after_ms": 0})
    assert not is_source_access({"operation": "ekstraksi_sumber", "started_after_ms": 0})
    assert not is_source_access({"operation": "academic", "status": "empty"})


def test_numbering_is_stable_with_concurrent_creates_and_scoped_history(tmp_path, monkeypatch):
    store = SQLiteStore(str(tmp_path / "penelitian.sqlite"))
    user = store.create_user("penelitian", "password-demo-123")
    other = store.create_user("other", "password-demo-456")
    workspace = ResearchStore(store, user["id"], 1)
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", sample_research)

    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(lambda _: workspace.create_or_find("Panel surya")["number"], range(4)))
    assert numbers == [1, 1, 1, 1]
    second = workspace.create_or_find("ESP32")
    assert second["number"] == 2
    first = research_workspace.run_research(workspace, second, request_id="req-1")
    again = research_workspace.run_research(workspace, workspace.detail(2), "baterai", request_id="req-2")
    assert first["new_source_numbers"] == [1, 2]
    assert again["new_source_numbers"] == []
    assert [source["number"] for source in again["citations"]] == [1, 2]
    assert workspace.detail()["runs"][0]["request_id"] == "req-2"
    assert workspace.detail(2)["runs"][0]["status"] == "partial"
    assert [event["status"] for event in again["attempts"]] == ["success", "timeout"]
    assert again["attempts"][0]["target"] == "https://example.com/search"
    assert ResearchStore(store, user["id"], 2).detail(2) is None
    assert ResearchStore(store, other["id"], 1).detail(2) is None
    assert workspace.manage(1, "delete")
    assert workspace.create_or_find("Penelitian baru")["number"] == 3
    assert workspace.manage(2, "archive")
    assert workspace.list_topics()[0] == 1
    assert workspace.manage(2, "restore")
    assert workspace.list_topics()[0] == 2


def test_web_request_id_pdf_followup_and_citation_links(tmp_path, monkeypatch):
    from xiaozhi import dependencies
    from xiaozhi.main import app

    store = SQLiteStore(str(tmp_path / "penelitian-web.sqlite"))
    user = store.create_user("penelitianweb", "password-demo-123")
    monkeypatch.setattr(dependencies, "_store", store)
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", sample_research)
    monkeypatch.setattr(scraper_service, "_scrape_url", lambda url, limit: {
        "success": True,
        "content": "Metadata\n### Halaman 4\n\nHasil pengukuran tersedia.",
        "source_type": "pdf_document",
    })
    csrf = make_csrf_token(user)
    client = TestClient(app)
    assert client.get("/penelitian", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 303
    old_link = client.get("/deep-riset/1?slot=2&session=1", follow_redirects=False)
    assert old_link.status_code == 307
    assert old_link.headers["Location"] == "/penelitian/1?slot=2&session=1"
    client.cookies.set(SESSION_COOKIE, session_serializer.dumps({"id": user["id"], "username": user["username"]}))

    index = client.get("/penelitian")
    assert index.status_code == 200
    assert "<h1" in index.text and "Penelitian Saya" in index.text and "Deep Riset" not in index.text

    first = client.post("/penelitian", data={"title": "Panel surya", "slot": 2, "csrf_token": csrf}, follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["Location"] == "/penelitian/1?slot=2"
    assert client.get("/penelitian/1?slot=1").status_code == 404

    pdf = client.post("/penelitian/1", data={
        "slot": 2, "action": "source", "source_number": 2, "csrf_token": csrf,
    }, follow_redirects=False)
    assert pdf.status_code == 303
    assert pdf.headers["X-Request-ID"] != first.headers["X-Request-ID"]
    detail = client.get(first.headers["Location"])
    assert detail.status_code == 200
    assert "Penelitian #1 — Panel surya" in detail.text
    assert 'href="#sumber-2"' in detail.text and 'id="sumber-2"' in detail.text
    assert "study.pdf#page=4" in detail.text
    assert "Habis waktu" in detail.text
    assert "Log akses sumber" in detail.text and "Hasil pemrosesan" in detail.text
    assert pdf.headers["X-Request-ID"] in detail.text
    assert first.headers["X-Request-ID"] in detail.text


def test_voice_can_resume_and_recall_research_without_repeating_a_search(tmp_path, monkeypatch):
    from xiaozhi.mcp.context import mcp_active_owner_ctx, mcp_active_slot_ctx, mcp_request_id_ctx
    from xiaozhi.mcp.tools import register_tools

    store = SQLiteStore(str(tmp_path / "penelitian-mcp.sqlite"))
    user = store.create_user("penelitiansuara", "password-demo-123")
    tools = {}
    server = Mock()

    def register():
        def decorate(fn):
            tools[fn.__name__] = fn
            return fn
        return decorate

    server.tool.side_effect = register
    search = Mock(side_effect=lambda query, mode: {
        **sample_research(query, mode), "source_status": {"academic": "empty"},
    })
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", search)
    register_tools(server, store, Mock())
    account_token = mcp_active_owner_ctx.set(user["id"])
    slot_token = mcp_active_slot_ctx.set(2)
    connection_token = mcp_request_id_ctx.set("same-bridge")
    try:
        start_research = tools["autonomous_deep_research"]
        assert start_research("Penelitian")["message"] == "Topik apa yang ingin ditelusuri?"
        for command in ("XiaoZhi, tolong riset Wi‑Fi!", "Reset wifi", "Wi-Fi",
                        "Ganti jaringan Wi-Fi perangkat", "Buka konfigurasi Wi-Fi"):
            unclear = start_research(command)
            assert not unclear["success"] and unclear["needs_clarification"]
            assert re.fullmatch(r"req-[a-f0-9]{32}", unclear["request_id"])
            assert "informasi tentang Wi-Fi" in unclear["message"]
        search.assert_not_called()
        assert ResearchStore(store, user["id"], 2).list_topics()[0] == 0

        first = start_research("Panel Surya")
        second = tools["deep_research_workspace"]("continue", 1, "baterai")
        read = tools["deep_research_workspace"]()
        assert first["research_number"] == second["research_number"] == read["research_number"] == 1
        assert read["session_number"] == 2
        assert read["research_request_id"] == second["request_id"]
        assert len({first["request_id"], second["request_id"], read["request_id"]}) == 3
        assert re.fullmatch(r"req-[a-f0-9]{32}", read["request_id"])
        voice = tools["deep_research_workspace"]
        history = voice("history", research_number=1, day="hari ini")
        assert history["total_sessions"] == 2
        assert [item["session_number"] for item in history["sessions"]] == [2, 1]
        assert voice("history", day="kemarin")["sessions"] == []
        assert not voice("history", day="tanggal tidak valid")["success"]
        first_audit = voice("audit", record_request_id=first["request_id"], access_status="errors")
        assert first_audit["research_request_id"] == first["request_id"]
        assert first_audit["session_number"] == 1
        assert first_audit["counts_by_status"] == {"success": 1, "timeout": 1}
        assert [attempt["status"] for attempt in first_audit["attempts"]] == ["timeout"]
        assert first_audit["outcomes"] == []
        full_audit = voice("audit", record_request_id=first["request_id"])
        assert [outcome["operation"] for outcome in full_audit["outcomes"]] == ["academic"]
        assert full_audit["total_matching"] == 2
        assert "/penelitian/1?slot=2&session=1#sesi-1" == first_audit["session_url"]
        source = voice("sources", source_number=2, session_number=1, topic_query="Panel")
        assert source["source"]["pdf_url"] == "https://example.com/study.pdf"
        assert source["source"]["coverage"] == "Abstrak"
        assert source["source"]["metadata"] == {"authors": ["Peneliti A"], "year": "2025", "doi": "10.1000/test"}
        assert not source["current_source_version"]
        comparison = voice("compare", research_number=1, session_number=2, compare_session=1)
        assert comparison["baseline_request_id"] == first["request_id"]
        assert comparison["new_in_session"] == comparison["changes"] == []
        evidence = voice("evidence", research_number=1, session_number=1, source_numbers=[1])
        assert evidence["success"] and evidence["session_number"] == 1
        assert not voice("evidence", research_number=1, source_numbers=[42])["success"]
        assert not voice("read", session_number=2, record_request_id=first["request_id"])["success"]

        other_slot = mcp_active_slot_ctx.set(1)
        try:
            assert not voice("audit", record_request_id=first["request_id"])["success"]
        finally:
            mcp_active_slot_ctx.reset(other_slot)
        assert search.call_count == 2
        assert "baterai" in search.call_args.args[0]
        wifi_topic = start_research("Penelitian tentang perbedaan Wi-Fi 6 dan Wi-Fi 7")
        assert wifi_topic["success"] and wifi_topic["research_number"] == 2
        assert search.call_count == 3
    finally:
        mcp_request_id_ctx.reset(connection_token)
        mcp_active_slot_ctx.reset(slot_token)
        mcp_active_owner_ctx.reset(account_token)


def retry_fixture(question, mode):
    for operation, endpoint, status in [
        ("cari_wikipedia", "https://id.wikipedia.org/w/api.php", 200),
        ("cari_wikipedia", "https://en.wikipedia.org/w/api.php", 503),
        ("cari_arxiv", "https://export.arxiv.org/api/query", 200),
        ("cari_crossref", "https://api.crossref.org/works", 429),
    ]:
        audited_call(operation, endpoint, lambda: SimpleNamespace(status_code=status))
    try:
        audited_call("cari_youtube_ytdlp", "https://www.youtube.com/results", Mock(side_effect=TimeoutError))
    except TimeoutError:
        pass
    return {"sources": {"web": [{"title": "Panduan", "url": "https://example.com/panduan", "excerpt": "Sumber lama."}]}}


def test_retry_runs_only_failed_providers_and_is_idempotent(tmp_path, monkeypatch):
    from xiaozhi.services import youtube_research_service

    store = SQLiteStore(str(tmp_path / "retry.sqlite"))
    user = store.create_user("retry", "password-demo-123")
    workspace = ResearchStore(store, user["id"], 1)
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", retry_fixture)
    arxiv = Mock()
    wiki = Mock(return_value=[{"title": "Panduan kedua", "url": "https://example.com/b", "snippet": "Bukti tambahan."}])
    paper = Mock(return_value=[{"title": "Paper", "url": "https://example.com/paper", "abstract": "Abstrak."}])
    video = Mock(return_value=[])
    monkeypatch.setattr(scraper_service, "search_web_sources", wiki)
    monkeypatch.setattr(scraper_service, "search_arxiv_papers", arxiv)
    monkeypatch.setattr(scraper_service, "search_crossref_papers", paper)
    monkeypatch.setattr(youtube_research_service, "find_research_videos", video)
    topic = workspace.create_or_find("Panel surya")
    research_workspace.run_research(workspace, topic, "Kapasitas baterai", request_id="req-original")
    original = workspace.get_run(1, 1)
    assert {item[0] for item in retry_plan(original)} == {"cari_wikipedia", "cari_crossref", "cari_youtube_ytdlp"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: retry_research(workspace, workspace.detail(1), original, f"req-retry-{i}"), range(2)))
    assert results == [2, 2]
    wiki.assert_called_once_with(original["question"], 3, timeout=4, provider="en")
    paper.assert_called_once_with(original["question"], 2)
    video.assert_called_once_with(original["question"], provider="ytdlp")
    arxiv.assert_not_called()
    saved = workspace.detail(1)
    assert saved["runs"][0]["retry_of"] == 1
    assert saved["runs"][0]["request_id"].startswith("req-retry-")
    assert saved["runs"][1]["retry_session"] == 2
    assert saved["runs"][1]["result"] == original["result"]
    assert {source["number"] for source in saved["sources"]} == {1, 2, 3}


def test_web_retry_and_feedback_are_scoped_to_session(tmp_path, monkeypatch):
    from xiaozhi import dependencies
    from xiaozhi.main import app

    store = SQLiteStore(str(tmp_path / "retry-web.sqlite"))
    user = store.create_user("researchweb", "password-demo-123")
    peer = store.create_user("peer", "password-demo-456")
    monkeypatch.setattr(dependencies, "_store", store)
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", retry_fixture)
    monkeypatch.setattr(scraper_service, "search_web_sources", Mock(return_value=[]))
    monkeypatch.setattr(scraper_service, "search_crossref_papers", Mock(return_value=[]))
    from xiaozhi.services import youtube_research_service
    monkeypatch.setattr(youtube_research_service, "find_research_videos", Mock(return_value=[]))

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_serializer.dumps({"id": user["id"], "username": user["username"]}))
    csrf = make_csrf_token(user)
    original = client.post("/penelitian", data={"title": "Penelitian panel", "csrf_token": csrf}, follow_redirects=False)
    assert "Coba Ulang Akses yang Gagal" in client.get("/penelitian/1").text
    retry = client.post("/penelitian/1", data={"action": "retry", "session_number": 1, "csrf_token": csrf}, follow_redirects=False)
    assert retry.status_code == 303 and "session=2#sesi-2" in retry.headers["Location"]
    assert retry.headers["X-Request-ID"] != original.headers["X-Request-ID"]
    page = client.get(retry.headers["Location"])
    assert "Percobaan ulang dari" in page.text
    assert retry.headers["X-Request-ID"] in page.text

    data = {"action": "feedback", "session_number": 2, "rating": "needs_work", "feedback_note": "Coba PDF lain."}
    assert client.post("/penelitian/1", data={**data, "csrf_token": "bad"}).status_code == 403
    assert client.post("/penelitian/1", data={**data, "csrf_token": csrf, "slot": 2}).status_code == 404
    response = client.post("/penelitian/1", data={**data, "csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert "Masukan untuk sesi penelitian sudah disimpan." in client.get(response.headers["Location"]).text
    session = ResearchStore(store, user["id"], 1).get_run(1, 2)
    assert session["result"]["feedback"]["note"] == "Coba PDF lain."
    assert session["result"]["feedback"]["request_id"] == response.headers["X-Request-ID"]
    assert session["result"]["attempts"]

    client.cookies.set(SESSION_COOKIE, session_serializer.dumps({"id": peer["id"], "username": peer["username"]}))
    assert client.post("/penelitian/1", data={**data, "csrf_token": make_csrf_token(peer)}).status_code == 404


def test_feedback_updates_notes_and_keeps_original_attempts(tmp_path, monkeypatch):
    store = SQLiteStore(str(tmp_path / "feedback.sqlite"))
    user = store.create_user("feedback", "password-demo-123")
    workspace = ResearchStore(store, user["id"], 1)
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", retry_fixture)
    topic = workspace.create_or_find("Topik")
    research_workspace.run_research(workspace, topic, request_id="req-feedback")
    initial = workspace.get_run(1, 1)["result"]
    workspace.save_feedback(1, 1, "needs_work", "Belum lengkap.")
    workspace.save_feedback(1, 1, "helpful", "Sudah jelas.")
    saved = workspace.get_run(1, 1)["result"]
    assert saved["feedback"]["rating"] == "helpful"
    assert saved["feedback"]["note"] == "Sudah jelas."
    assert saved["attempts"] == initial["attempts"]
    assert saved["citations"] == initial["citations"]


def test_retry_pdf_preserves_citation_and_page_number(tmp_path, monkeypatch):
    store = SQLiteStore(str(tmp_path / "retry-pdf.sqlite"))
    user = store.create_user("pdfretry", "password-demo-123")
    workspace = ResearchStore(store, user["id"], 1)
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", sample_research)
    topic = workspace.create_or_find("Penelitian PDF")
    research_workspace.run_research(workspace, topic, request_id="req-search")

    def failed_pdf(url, limit):
        audited_call("unduh_pdf", url, lambda: SimpleNamespace(status_code=503))
        return {"success": False, "error": "HTTP 503"}

    monkeypatch.setattr(scraper_service, "_scrape_url", failed_pdf)
    research_workspace.run_research(workspace, workspace.detail(1), request_id="req-pdf", source_number=2)
    parent = workspace.get_run(1, 2)
    assert retry_plan(parent) == [("unduh_pdf", "https://example.com/study.pdf")]

    recovered = Mock(return_value={"success": True, "source_type": "pdf_document", "content": "### Halaman 8\nTabel hasil."})
    monkeypatch.setattr(scraper_service, "_scrape_url", recovered)
    retry_research(workspace, workspace.detail(1), parent, "req-retry")
    recovered.assert_called_once_with("https://example.com/study.pdf", 4000, use_jina=False)
    citation = workspace.get_run(1, 3)["result"]["citations"][0]
    assert citation["number"] == 2 and citation["pages"] == ["8"]
    assert citation["coverage"] == "Cuplikan teks PDF"


def test_research_history_comparison_and_pagination_use_saved_snapshots(tmp_path, monkeypatch):
    store = SQLiteStore(str(tmp_path / "voice-history.sqlite"))
    user = store.create_user("history", "password-demo-123")
    workspace = ResearchStore(store, user["id"], 1)
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", sample_research)
    topic = workspace.create_or_find("Topik berkelanjutan")
    research_workspace.run_research(workspace, topic, request_id="req-earlier")
    monkeypatch.setattr(scraper_service, "_scrape_url", lambda url, length: {
        "success": True, "source_type": "pdf_document", "content": "### Halaman 9\nData yang diperbarui.",
    })
    research_workspace.run_research(workspace, workspace.detail(1), request_id="req-pdf", source_number=2)
    for i in range(3, 22):
        session, _ = workspace.start(1, f"req-{i}", "Lanjutan")
        workspace.finish(1, session, {}, [], False)
    topic = workspace.detail(1)
    assert topic["runs"][-1]["session_number"] == 2
    compare = research_workspace.research_history_reply(workspace, topic, "compare", session_number=2, compare_session=1)
    assert compare["changes"][0]["number"] == 2
    assert compare["not_in_session"] == [1]
    old = research_workspace.research_history_reply(workspace, topic, "sources", session_number=1, source_number=2)
    current = research_workspace.research_history_reply(workspace, topic, "sources", source_number=2)
    assert old["source"]["coverage"] == "Abstrak"
    assert current["source"]["pages"] == ["9"]
    history = research_workspace.research_history_reply(workspace, topic, "history", page=3)
    assert [run["session_number"] for run in history["sessions"]] == [5, 4, 3, 2, 1]
    assert history["total_sessions"] == 21 and history["next_page"] is None


def test_cited_evidence_and_feedback_are_visible_and_guide_next_session(tmp_path, monkeypatch):
    from xiaozhi import dependencies
    from xiaozhi.main import app

    store = SQLiteStore(str(tmp_path / "evidence.sqlite"))
    user = store.create_user("evidence", "password-demo-123")
    monkeypatch.setattr(dependencies, "_store", store)
    engine = Mock(return_value={"sources": {
        "web": [{"title": "Uji rumah", "url": "https://example.com/rumah",
                 "excerpt": "Kapasitas baterai rumah sebesar 10 kWh. Ukur konsumsi listrik harian."}],
        "academic": [{"title": "Uji laboratorium", "url": "https://example.org/lab",
                      "abstract": "Kapasitas baterai rumah sebesar 12 kWh. Ukur konsumsi listrik harian."}],
        "youtube": [{"title": "Video tanpa subtitle", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}],
    }})
    monkeypatch.setattr(scraper_service, "conduct_autonomous_research", engine)
    workspace = ResearchStore(store, user["id"], 1)
    first = research_workspace.run_research(workspace, workspace.create_or_find("Panel surya"), request_id="req-first")
    analysis = first["evidence_analysis"]
    assert [item["source_number"] for item in analysis["agreement"][0]["citations"]] == [1, 2]
    assert [item["source_number"] for item in analysis["differences"][0]["citations"]] == [1, 2]
    assert analysis["metadata_only"] == [3]
    assert research_workspace.research_continuation(workspace.detail(1))["origin"] == "open_question"
    workspace.save_feedback(1, 1, "needs_work", "Data biaya Indonesia")

    # Explicit questions take priority, and do not mark unrelated feedback as answered.
    explicit = research_workspace.run_research(workspace, workspace.detail(1), "Efisiensi inverter")
    assert explicit["continuation"]["origin"] == "question"
    assert "Efisiensi inverter" in engine.call_args.args[0]

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_serializer.dumps({"id": user["id"], "username": user["username"]}))
    initial = client.get("/penelitian/1")
    assert initial.status_code == 200
    assert "Angka yang perlu dibandingkan" in initial.text and "Pertanyaan terbuka" in initial.text
    assert 'href="#sumber-1"' in initial.text and "Data biaya Indonesia" in initial.text

    engine.return_value = {"sources": {"web": [{
        "title": "Laporan Indonesia", "url": "https://example.net/biaya",
        "excerpt": "Data biaya Indonesia berasal dari catatan pemasangan panel surya rumah.",
    }]}}
    response = client.post("/penelitian/1", data={
        "action": "continue", "csrf_token": make_csrf_token(user),
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "Data biaya Indonesia" in engine.call_args.args[0]
    saved = workspace.get_run(1, 3)["result"]["continuation"]
    assert saved["from_session"] == 1 and saved["matching_sources"] == [4]
    assert "Mengikuti masukan Sesi 1" in response.text
    assert "Cuplikan baru atau diperbarui yang relevan" in response.text
    assert workspace.get_run(1, 1)["result"]["feedback"]["applied_session"] == 3
    assert research_workspace.research_continuation(workspace.detail(1))["origin"] == "open_question"

    for _ in range(20):
        sequence = workspace.detail(1)["last_session"] + 1
        session, _ = workspace.start(1, f"req-{sequence}", "Sesi berikutnya")
        workspace.finish(1, session, {}, [], False)
    workspace.save_feedback(1, 1, "needs_work", "Biaya perawatan di Indonesia")
    older_feedback = research_workspace.research_continuation(workspace.detail(1))
    assert older_feedback["from_session"] == 1
    assert older_feedback["focus"] == "Biaya perawatan di Indonesia"
