"""API documentation configuration."""
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.responses import HTMLResponse


def setup_api_docs(app: FastAPI) -> None:
    """Setup OpenAPI/Swagger documentation."""

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - API Documentation",
            swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.min.js",
            swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.min.css",
            swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
        )

    @app.get("/redoc", include_in_schema=False)
    async def redoc_html():
        return get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - ReDoc",
            redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js",
        )

    @app.get("/api/docs", include_in_schema=False)
    async def api_docs_redirect():
        return HTMLResponse(
            content="""
            <html><head><meta http-equiv="refresh" content="0;url=/docs"></head>
            <body><p>Redirecting to <a href="/docs">API Documentation</a></p></body></html>
            """,
            status_code=302,
            headers={"Location": "/docs"},
        )


# OpenAPI tags for documentation
API_TAGS = [
    {"name": "auth", "description": "Authentication endpoints (login, register, logout)"},
    {"name": "api-v1-auth", "description": "JWT authentication for mobile/API clients"},
    {"name": "api-v1-materials", "description": "Material CRUD operations"},
    {"name": "api-v1-smarthome", "description": "Smart home control"},
    {"name": "mcp", "description": "MCP endpoint management"},
    {"name": "admin", "description": "Admin panel endpoints"},
    {"name": "search", "description": "Search endpoints"},
    {"name": "devices", "description": "Device management"},
    {"name": "youtube", "description": "YouTube audio streaming"},
]
