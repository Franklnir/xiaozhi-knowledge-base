"""Retry individual failed research accesses."""

import logging
import time
from concurrent.futures import wait
from contextvars import copy_context
from urllib.parse import urlsplit

from xiaozhi.services import scraper_service as scraper
from xiaozhi.services import youtube_research_service as youtube
from xiaozhi.services.research_audit import ResearchAudit, current_audit, public_url
from xiaozhi.services.research_workspace import _evidence, _finding, _read_evidence


logger = logging.getLogger(__name__)
_ACADEMIC = {
    "cari_arxiv": "search_arxiv_papers",
    "cari_semantic_scholar": "search_semantic_scholar_papers",
    "cari_crossref": "search_crossref_papers",
}
_VIDEO = {"cari_youtube_api": "api", "cari_youtube_ytdlp": "ytdlp"}
_NETWORK_READ = {"buka_halaman", "unduh_pdf", "jina_reader", "cari_subtitle", "baca_subtitle"}
_SEARCH_GROUPS = {
    "web": {"cari_google_news", "cari_wikipedia"},
    "academic": set(_ACADEMIC),
    "youtube": set(_VIDEO),
}


def retry_plan(run):
    attempts = run["result"].get("attempts", [])
    recorded = {a["operation"] for a in attempts}
    succeeded = {(a["operation"], a.get("retry_target") or a.get("target")) for a in attempts if a["status"] == "success"}
    retryable = {}
    for attempt in attempts:
        operation = attempt["operation"]
        target = attempt.get("retry_target") or attempt.get("target") or ""
        if attempt["status"] not in {"failed", "timeout"} or (operation, target) in succeeded:
            continue
        if operation in _ACADEMIC or operation in _VIDEO or operation == "cari_google_news":
            retryable[(operation, "")] = attempt
        elif operation in _SEARCH_GROUPS and not (_SEARCH_GROUPS[operation] & recorded):
            retryable[(operation, "")] = attempt
        elif operation == "cari_wikipedia" and urlsplit(target).hostname in {"id.wikipedia.org", "en.wikipedia.org"}:
            retryable[(operation, target)] = attempt
        elif operation in _NETWORK_READ and public_url(target):
            if operation in {"cari_subtitle", "baca_subtitle"}:
                if ("baca_subtitle", target) in succeeded or not scraper.extract_youtube_video_id(target):
                    continue
                operation = "baca_subtitle"
            retryable[(operation, target)] = attempt
    return list(retryable)


def _source_read(target, topic, *, jina=False):
    operation = scraper.scrape_via_jina_reader if jina else scraper.scrape_url
    data = operation(target, 4000) if jina else operation(target, 4000, use_jina=False)
    if not data or not data.get("success"):
        return {}
    original = next(
        (item for item in topic["sources"] if target in (item["url"], item["pdf_url"])), None,
    )
    source = original or {
        "url": target, "title": data.get("title") or target,
        "publisher": "", "kind": "web", "pdf_url": target if data.get("source_type") == "pdf_document" else "",
    }
    return _read_evidence(source, data)


def _retry_access(operation, target, question, topic):
    if operation == "academic":
        papers = scraper.search_academic_papers_unified(question, 2).get("papers", [])
        return _evidence({"sources": {"academic": papers}})
    if operation in _ACADEMIC:
        return _evidence({"sources": {"academic": getattr(scraper, _ACADEMIC[operation])(question, 2)}})
    if operation in _VIDEO or operation == "youtube":
        return _evidence({"sources": {"youtube": youtube.find_research_videos(question[:300], provider=_VIDEO.get(operation, ""))}})
    if operation in _SEARCH_GROUPS["web"] or operation == "web":
        provider = urlsplit(target).hostname.split(".")[0] if operation == "cari_wikipedia" else ""
        if operation == "cari_google_news":
            provider = "news"
        return _evidence({"sources": {"web": scraper.search_web_sources(question, 3, timeout=4, provider=provider)}})
    if operation == "baca_subtitle":
        original = next((item for item in topic["sources"] if item["url"] == target), {})
        source, reason = youtube.read_research_video_captions({
            "url": target, "video_id": scraper.extract_youtube_video_id(target),
            "title": original.get("title", target), "channel": original.get("publisher", ""),
        })
        return {} if reason else _evidence({"sources": {"youtube": [source]}})
    if operation == "jina_reader":
        origin = target.removeprefix("https://r.jina.ai/")
        if origin == target or not scraper.validate_scrape_url(origin)["valid"]:
            return {}
        return _source_read(origin, topic, jina=True)
    return _source_read(target, topic)


def retry_research(workspace, topic, parent, request_id):
    """Create one linked retry per session and run only its eligible failed accesses."""
    if not parent or parent["status"] == "running":
        raise ValueError("Sesi belum selesai atau tidak ditemukan.")
    plan = retry_plan(parent)
    if not plan:
        raise ValueError("Sesi ini tidak memiliki akses gagal atau habis waktu yang bisa diulang.")
    number, parent_number = topic["number"], parent["session_number"]
    question = parent["question"]
    session, created = workspace.start(number, request_id, question, retry_of=parent_number)
    if not created:
        return session

    audit = ResearchAudit()
    token = current_audit.set(audit)
    started = time.monotonic()
    pending = {}
    evidence = {}
    outcomes = []
    try:
        for operation, target in plan:
            future = scraper._research_workers.submit(copy_context().run, _retry_access, operation, target, question, topic)
            pending[future] = (operation, target)
        done, _ = wait(pending, timeout=max(0, scraper._RESEARCH_TIMEOUT_SECONDS - (time.monotonic() - started)))
        for future, (operation, target) in pending.items():
            if future not in done:
                future.cancel()
                status = "timeout"
            else:
                try:
                    found = future.result()
                    evidence.update(found)
                    status = "success" if found else "empty"
                except Exception:
                    logger.warning("Retry %s failed: %s", request_id, operation, exc_info=True)
                    status = "failed"
            outcomes.append({"kind": "outcome", "operation": operation, "target": target.split("?", 1)[0],
                             "retry_target": target, "status": status, "detail": "Hasil percobaan ulang."})
    except RuntimeError:
        logger.exception("Unable to schedule retry %s", request_id)
        for future in pending:
            future.cancel()
        outcomes = [
            {"kind": "outcome", "operation": operation, "target": target.split("?", 1)[0], "retry_target": target,
             "status": "failed", "detail": "Percobaan ulang belum bisa dijalankan."}
            for operation, target in plan
        ]
    finally:
        current_audit.reset(token)
        attempts = audit.close()

    # Individual HTTP records already give the reason for a failed call. Keep a result for each
    # operation that failed before sending a request or did not return usable evidence.
    for outcome in outcomes:
        if outcome["status"] != "success" or not attempts:
            attempts.append(outcome)
    for item in evidence.values():
        item["finding"] = _finding(item["excerpt"], question) if item.get("excerpt") else ""
    workspace.finish(number, session, evidence, attempts, bool(evidence), retry_results=outcomes)
    return session
