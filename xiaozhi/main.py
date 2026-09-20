import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from xiaozhi.config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALLOWED_ORIGINS,
    ALLOWED_ORIGIN_REGEX,
    IS_PRODUCTION,
    RESTORED_USER_PASSWORD,
    RESTORED_USER_USERNAME,
    logger,
)
from xiaozhi.core.api_docs import setup_api_docs
from xiaozhi.core.monitor import record_request, get_system_health, add_alert
from xiaozhi.core.task_queue import init_task_queue
from xiaozhi.database.factory import create_store
from xiaozhi.dependencies import init_dependencies
from xiaozhi.services.mcp_service import set_store_ref

# Templates
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR) if TEMPLATES_DIR.exists() else "templates")

# Configure Jinja2 environment to safely serialize datetimes/complex objects in {{ ...|tojson }}
def _jinja_json_serializer(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)

templates.env.policies["json.dumps_kwargs"] = {"default": _jinja_json_serializer}
try:
    from jinja2.utils import htmlsafe_json_dumps
    from markupsafe import Markup

    def _safe_tojson_filter(val, *args, **kwargs):
        kwargs.setdefault("default", _jinja_json_serializer)
        return Markup(htmlsafe_json_dumps(val, dumps=json.dumps, **kwargs))

    templates.env.filters["tojson"] = _safe_tojson_filter
except Exception:
    pass

# Store - auto-detect backend (PostgreSQL for VPS, HF for Spaces, SQLite for fallback)
store = create_store()

# Initialize dependencies
init_dependencies(templates, store)

# Set store reference for MCP service
set_store_ref(store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Admin initialization / startup verification
    if ADMIN_PASSWORD:
        store.ensure_admin_user(ADMIN_USERNAME, ADMIN_PASSWORD)
        logger.info("Admin account '%s' verified from environment variable.", ADMIN_USERNAME)
    elif store.has_admin_user():
        logger.info("Admin account verified in database.")
    else:
        # Fresh installation: No admin exists, and no ADMIN_PASSWORD set in environment
        import secrets
        generated_pw = secrets.token_urlsafe(16)
        store.ensure_admin_user(ADMIN_USERNAME, generated_pw)
        logger.warning("=" * 76)
        logger.warning("[SECURITY NOTICE] Fresh install: No admin found & ADMIN_PASSWORD not in .env")
        logger.warning("An initial admin account has been created with a generated secure password:")
        logger.warning("  Username : %s", ADMIN_USERNAME)
        logger.warning("  Password : %s", generated_pw)
        logger.warning("Please copy this password immediately or set ADMIN_PASSWORD in your .env file!")
        logger.warning("=" * 76)

    # Only restore test account if explicitly configured in environment
    if RESTORED_USER_USERNAME and RESTORED_USER_PASSWORD:
        store.restore_regular_user_account(RESTORED_USER_USERNAME, RESTORED_USER_PASSWORD)

    # Initialize task queue
    await init_task_queue(max_workers=3)
    logger.info("Task queue initialized")

    # Start reminder checker
    try:
        from xiaozhi.services.reminder_service import check_reminders_periodically
        asyncio.create_task(check_reminders_periodically(store))
        logger.info("Reminder checker started")
    except Exception:
        logger.warning("Reminder checker not available")

    # Start PostgreSQL LISTEN/NOTIFY multi-worker sync listener
    if hasattr(store, "dsn"):
        try:
            from xiaozhi.services.community_chat_service import start_pg_chat_listener
            asyncio.create_task(start_pg_chat_listener(store))
            logger.info("PostgreSQL multi-worker chat sync listener started")
        except Exception as exc:
            logger.warning("Failed to start PostgreSQL chat sync listener: %s", exc)

    # Start MCP background task
    try:
        from xiaozhi.mcp.server import init_mcp_server
        from xiaozhi.mcp.bridge import start_mcp_background

        # Try to get youtube search function
        youtube_search_fn = None
        try:
            from xiaozhi.routers.youtube import youtube_search
            youtube_search_fn = youtube_search
        except Exception:
            pass

        # Initialize MCP server
        mcp_server = init_mcp_server(store, youtube_search_fn)

        if mcp_server:
            await start_mcp_background(store)
    except ImportError:
        logger.warning("MCP/WebSocket packages not available. UI runs without XiaoZhi bridge.")

    # Record startup
    add_alert("info", "Application started successfully", "system")

    yield

    # Shutdown
    logger.info("Shutting down Xiaozhi Indonesia...")
    add_alert("info", "Application shutting down", "system")


app = FastAPI(
    title="Xiaozhi Indonesia",
    description="Platform knowledge base Xiaozhi Indonesia dengan integrasi MCP. "
                "Supports multi-user MCP connections, smart home control, "
                "YouTube audio streaming, and REST API for mobile clients.",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,  # Custom docs
    redoc_url=None,
)

# Static files
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Setup API documentation
setup_api_docs(app)

# Middleware - W3C Standard Compliant CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Device-Token", "X-EDUSMART-API-KEY", "Accept", "Origin"],
)


@app.middleware("http")
async def monitor_requests(request: Request, call_next):
    """Monitor request timing and errors with guaranteed unique Request ID."""
    import uuid
    start = time.time()
    req_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:10]}"
    request.state.request_id = req_id

    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000

    # Record metric
    endpoint = request.url.path
    method = request.method
    status = response.status_code
    record_request(endpoint, method, status, duration_ms)

    # Log slow requests
    if duration_ms > 5000:
        logger.warning(f"[{req_id}] Slow request: {method} {endpoint} took {duration_ms:.0f}ms")

    # Add performance headers
    response.headers["X-Response-Time"] = f"{duration_ms:.0f}ms"
    response.headers["X-Request-ID"] = req_id

    return response


# Include routers
from xiaozhi.routers import (
    admin,
    auth,
    chat,
    dashboard,
    devices,
    google_auth,
    mcp_endpoints,
    relay_nyata,
    search,
    smarthome,
    youtube,
    marketplace_ui,
    admin_firmware_ui,
)
from xiaozhi.routers import (
    api_v1_auth,
    api_v1_chat,
    api_v1_materials,
    api_v1_mcp,
    api_v1_smarthome,
    api_v1_marketplace,
    api_v1_webhooks,
)

app.include_router(auth.router)
app.include_router(google_auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(smarthome.router)
app.include_router(relay_nyata.router)
app.include_router(mcp_endpoints.router)
app.include_router(search.router)
app.include_router(youtube.router)
app.include_router(devices.router)
app.include_router(marketplace_ui.router)
app.include_router(admin_firmware_ui.router)

# API v1 routers (for mobile/API clients & webhooks)
app.include_router(api_v1_auth.router)
app.include_router(api_v1_materials.router)
app.include_router(api_v1_smarthome.router)
app.include_router(api_v1_mcp.router)
app.include_router(api_v1_chat.router)
app.include_router(api_v1_marketplace.router)
app.include_router(api_v1_webhooks.router)


# Mount MCP SSE endpoint - will be mounted during startup
_mcp_app = None

@app.on_event("startup")
async def mount_mcp_endpoint():
    global _mcp_app
    try:
        from xiaozhi.mcp.server import mcp_server
        if mcp_server:
            _mcp_app = mcp_server.sse_app()
            app.mount("/mcp", _mcp_app)
            logger.info("MCP SSE endpoint mounted at /mcp")
    except (ImportError, Exception) as e:
        logger.warning("MCP package not available. /mcp endpoint disabled: %s", e)


# SEO Endpoints
from fastapi.responses import PlainTextResponse, Response


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /dokumentasi\n"
        "Allow: /login\n"
        "Allow: /register\n"
        "Disallow: /admin\n"
        "Disallow: /dashboard\n"
        "Disallow: /api/\n"
        "Disallow: /riwayat-chat\n"
        "Disallow: /simulasi-smarthome-virtual\n"
        "Disallow: /relay-nyata\n\n"
        "Sitemap: https://xiaozhiscig.biz.id/sitemap.xml\n"
    )
    return PlainTextResponse(content, media_type="text/plain")


@app.get("/api/v1/nginx-diag", response_class=PlainTextResponse, include_in_schema=False)
async def nginx_diag():
    diag_file = Path("/app/data/nginx_diag.txt")
    if diag_file.exists():
        return PlainTextResponse(diag_file.read_text(encoding="utf-8", errors="ignore"))
    return PlainTextResponse("Diagnostic file /app/data/nginx_diag.txt not found yet.")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    from datetime import date
    today = date.today().isoformat()
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>https://xiaozhiscig.biz.id/</loc>
        <lastmod>{today}</lastmod>
        <changefreq>daily</changefreq>
        <priority>1.0</priority>
    </url>
    <url>
        <loc>https://xiaozhiscig.biz.id/dokumentasi</loc>
        <lastmod>{today}</lastmod>
        <changefreq>weekly</changefreq>
        <priority>0.9</priority>
    </url>
    <url>
        <loc>https://xiaozhiscig.biz.id/login</loc>
        <lastmod>{today}</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.6</priority>
    </url>
    <url>
        <loc>https://xiaozhiscig.biz.id/register</loc>
        <lastmod>{today}</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.6</priority>
    </url>
</urlset>"""
    return Response(content=xml_content, media_type="application/xml")


@app.get("/health/db", tags=["Health"])
async def health_check_db():
    """Check database connection latency and backend health."""
    start = time.perf_counter()
    backend = os.getenv("DB_BACKEND", "auto")
    try:
        if hasattr(store, "ping"):
            store.ping()
        elif hasattr(store, "_get_conn"):
            with store._get_conn() as conn:
                if hasattr(conn, "cursor"):
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                else:
                    conn.execute("SELECT 1")
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "healthy",
            "backend": backend,
            "latency_ms": latency_ms,
            "store_class": store.__class__.__name__,
        }
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        return {
            "status": "unhealthy",
            "backend": backend,
            "error": str(exc),
            "store_class": store.__class__.__name__,
        }


@app.get("/health/storage", tags=["Health"])
async def health_check_storage():
    """Check S3 Object Storage connectivity, bucket status, and latency."""
    from xiaozhi.marketplace.storage import storage_service
    from xiaozhi.config import S3_ENDPOINT, S3_REGION, S3_BUCKET_PUBLIC, S3_BUCKET_PRIVATE
    
    start = time.perf_counter()
    if not storage_service.has_s3:
        return {
            "status": "warning",
            "has_s3": False,
            "mode": "local_fallback",
            "message": "S3 credentials not configured in environment; running on local fallback storage.",
        }
    
    try:
        s3 = storage_service._get_s3()
        if not s3:
            raise RuntimeError("Failed to initialize boto3 S3 client.")
        
        # Test listing buckets
        resp = s3.list_buckets()
        detected_buckets = [b["Name"] for b in resp.get("Buckets", [])]
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        
        public_exists = S3_BUCKET_PUBLIC in detected_buckets
        private_exists = S3_BUCKET_PRIVATE in detected_buckets
        
        return {
            "status": "healthy" if (public_exists and private_exists) else "degraded",
            "has_s3": True,
            "mode": "s3_object_storage",
            "endpoint": S3_ENDPOINT,
            "region": S3_REGION,
            "latency_ms": latency_ms,
            "bucket_public": {
                "name": S3_BUCKET_PUBLIC,
                "exists": public_exists,
            },
            "bucket_private": {
                "name": S3_BUCKET_PRIVATE,
                "exists": private_exists,
            },
            "all_detected_buckets": detected_buckets,
        }
    except Exception as exc:
        logger.error("Storage health check failed: %s", exc)
        return {
            "status": "unhealthy",
            "has_s3": True,
            "mode": "s3_object_storage",
            "endpoint": S3_ENDPOINT,
            "error": str(exc),
        }


