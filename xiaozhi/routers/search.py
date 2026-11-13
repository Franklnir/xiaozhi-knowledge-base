from fastapi import APIRouter, Query, Request
from xiaozhi.database.models import SearchResponse
from xiaozhi.dependencies import get_store, require_user


def format_material_for_xiaozhi(item: dict, keyword: str = "") -> dict:
    """Format material for search results."""
    return {
        "id": item.get("id"),
        "title": item.get("title", ""),
        "category": item.get("category", ""),
        "keywords": item.get("keywords", ""),
        "content": item.get("content", "")[:500],
    }

router = APIRouter()


@router.get("/search_course_materials", response_model=SearchResponse)
async def search_course_materials(
    request: Request,
    search_keyword: str = Query("", max_length=200),
):
    user = require_user(request)
    store = get_store()
    data = store.search_materials(user["id"], search_keyword, limit=10)
    if not data:
        return SearchResponse(
            search_keyword=search_keyword,
            message="Data atau materi tersebut tidak ditemukan di sistem Xiaozhi Indonesia.",
            results=[],
        )
    formatted_results = [format_material_for_xiaozhi(item, search_keyword) for item in data]
    return SearchResponse(
        search_keyword=search_keyword,
        message="Data berhasil ditemukan.",
        results=formatted_results,
    )
