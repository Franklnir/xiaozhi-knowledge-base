from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from xiaozhi.core.research_evidence import analyze_research_evidence
from xiaozhi.database.research_store import ResearchStore
from xiaozhi.dependencies import get_store, render, require_user, validate_csrf
from xiaozhi.services.research_audit import is_source_access
from xiaozhi.services.research_retries import retry_plan, retry_research
from xiaozhi.services.research_workspace import research_continuation, run_research


router = APIRouter()
pages = APIRouter(prefix="/penelitian")


def _workspace(request, slot):
    user = require_user(request)
    store = get_store()
    workspace = ResearchStore(store, user["id"], slot) if ResearchStore.supported(store) else None
    return user, workspace


def _page(request, user, slot, workspace, *, topic=None, page=1, session=None, error=""):
    if topic:
        for run in topic["runs"]:
            run["can_retry"] = bool(retry_plan(run)) and not run["retry_session"]
            run["access_attempts"] = accesses = []
            run["processing_outcomes"] = outcomes = []
            for entry in run["result"].get("attempts", []):
                (accesses if is_source_access(entry) else outcomes).append(entry)
            if "evidence_analysis" not in run["result"] and run["result"].get("citations"):
                run["result"]["evidence_analysis"] = analyze_research_evidence(run["result"]["citations"], run["question"])
    response = render(request, "research.html", {
        "user": user, "active_page": "research", "slot": slot, "research_page": page,
        "topic": topic, "supported": bool(workspace), "research_error": error,
        "topic_list": workspace.list_topics(page) if workspace and topic is None else None,
        "open_session": session,
        "feedback_saved": request.query_params.get("notice") == "feedback",
        "topic_evidence": analyze_research_evidence(topic["sources"], topic["title"]) if topic else None,
        "next_focus": research_continuation(topic) if topic else None,
    }, status_code=400 if error else 200)
    response.headers["Cache-Control"] = "no-store"
    return response


@pages.get("")
def list_research(request: Request, slot: int = Query(1, ge=1, le=3), page: int = Query(1, ge=1)):
    user, workspace = _workspace(request, slot)
    return _page(request, user, slot, workspace, page=page)


@pages.get("/{number}")
def view_research(
    request: Request, number: int, slot: int = Query(1, ge=1, le=3), page: int = Query(1, ge=1),
    session: int | None = Query(None, ge=1),
):
    user, workspace = _workspace(request, slot)
    topic = workspace.detail(number, page) if workspace else None
    if not topic:
        raise HTTPException(404, "Penelitian tidak ditemukan pada slot ini.")
    if session is not None:
        if session > topic["last_session"]:
            raise HTTPException(404, "Sesi penelitian tidak ditemukan.")
        session_page = (topic["last_session"] - session) // 20 + 1
        if session_page != page:
            page = session_page
            topic = workspace.detail(number, page)
    return _page(request, user, slot, workspace, topic=topic, page=page, session=session)


@pages.post("")
def create_research(
    request: Request, title: str = Form(max_length=160), question: str = Form("", max_length=300),
    slot: int = Form(1, ge=1, le=3), csrf_token: str = Form(),
):
    user, workspace = _workspace(request, slot)
    validate_csrf(request, csrf_token, user)
    if not workspace:
        raise HTTPException(503, "Penyimpanan Penelitian Saya belum tersedia.")
    try:
        topic = workspace.create_or_find(title)
        if topic.get("last_session", 0) == 0 or question.strip():
            run_research(workspace, topic, question, request_id=request.state.request_id)
    except ValueError as exc:
        return _page(request, user, slot, workspace, error=str(exc))
    return RedirectResponse(f"/penelitian/{topic['number']}?slot={slot}", status_code=303)


@pages.post("/{number}")
def update_research(
    request: Request, number: int, action: str = Form(),
    question: str = Form("", max_length=300), title: str = Form("", max_length=160),
    source_number: int | None = Form(None, ge=1), slot: int = Form(1, ge=1, le=3), csrf_token: str = Form(),
    session_number: int | None = Form(None, ge=1), rating: str = Form("", max_length=16),
    feedback_note: str = Form("", max_length=600),
):
    user, workspace = _workspace(request, slot)
    validate_csrf(request, csrf_token, user)
    topic = workspace.detail(number) if workspace else None
    if not topic:
        raise HTTPException(404, "Penelitian tidak ditemukan pada slot ini.")
    suffix = ""
    try:
        if action in {"continue", "source"}:
            if action == "source" and source_number is None:
                raise ValueError("Pilih sumber yang ingin dibaca.")
            run_research(workspace, topic, question, request_id=request.state.request_id,
                         source_number=source_number if action == "source" else None)
        elif action in {"retry", "feedback"}:
            if session_number is None:
                raise ValueError("Pilih sesi penelitian terlebih dahulu.")
            if action == "retry":
                parent = workspace.get_run(number, session_number)
                child = retry_research(workspace, topic, parent, request.state.request_id)
                suffix = f"&session={child}#sesi-{child}"
            else:
                workspace.save_feedback(number, session_number, rating, feedback_note, request_id=request.state.request_id)
                suffix = f"&session={session_number}&notice=feedback#sesi-{session_number}"
        elif not workspace.manage(number, action, title):
            raise HTTPException(404, "Penelitian tidak ditemukan.")
    except ValueError as exc:
        return _page(request, user, slot, workspace, topic=topic, error=str(exc))
    target = "/penelitian" if action == "delete" else f"/penelitian/{number}"
    return RedirectResponse(f"{target}?slot={slot}{suffix}", status_code=303)


@router.api_route("/deep-riset", methods=["GET", "POST"], include_in_schema=False)
@router.api_route("/deep-riset/{number}", methods=["GET", "POST"], include_in_schema=False)
def redirect_research_bookmark(request: Request, number: int | None = None):
    target = "/penelitian" if number is None else f"/penelitian/{number}"
    if request.url.query:
        target += f"?{request.url.query}"
    return RedirectResponse(target, status_code=307)


router.include_router(pages)
