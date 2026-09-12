import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from xiaozhi.config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
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
templates = Jinja2Templates(directory="templates")

# Store - auto-detect backend (HF for Spaces, SQLite for VPS)
store = create_store()

# Initialize dependencies
init_dependencies(templates, store)

# Set store reference for MCP service
set_store_ref(store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Xiaozhi Indonesia...")
    store.ensure_admin_user(ADMIN_USERNAME, ADMIN_PASSWORD)
    store.restore_regular_user_account(RESTORED_USER_USERNAME, RESTORED_USER_PASSWORD)

    # Initialize task queue
    await init_task_queue(max_workers=3)
    logger.info("Task queue initialized")

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

# Setup API documentation
setup_api_docs(app)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Device-Token", "X-EDUSMART-API-KEY"],
)


@app.middleware("http")
async def monitor_requests(request: Request, call_next):
    """Monitor request timing and errors."""
    start = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000

    # Record metric
    endpoint = request.url.path
    method = request.method
    status = response.status_code
    record_request(endpoint, method, status, duration_ms)

    # Log slow requests
    if duration_ms > 5000:
        logger.warning(f"Slow request: {method} {endpoint} took {duration_ms:.0f}ms")

    # Add performance headers
    response.headers["X-Response-Time"] = f"{duration_ms:.0f}ms"
    response.headers["X-Request-ID"] = request.headers.get("X-Request-ID", "")

    return response


# Include routers
from xiaozhi.routers import (
    admin,
    auth,
    chat,
    dashboard,
    devices,
    mcp_endpoints,
    relay_nyata,
    search,
    smarthome,
    youtube,
)
from xiaozhi.routers import api_v1_auth, api_v1_materials, api_v1_smarthome

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(smarthome.router)
app.include_router(relay_nyata.router)
app.include_router(mcp_endpoints.router)
app.include_router(search.router)
app.include_router(youtube.router)
app.include_router(devices.router)

# API v1 routers (for mobile/API clients)
app.include_router(api_v1_auth.router)
app.include_router(api_v1_materials.router)
app.include_router(api_v1_smarthome.router)


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
