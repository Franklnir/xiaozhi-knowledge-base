import contextvars
from typing import Optional

# ContextVar for multi-user MCP - each bridge task gets its own context
mcp_active_owner_ctx: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("mcp_active_owner", default=None)
mcp_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_request_id", default="")
