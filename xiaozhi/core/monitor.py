"""System monitoring and alerting."""
import logging
import os
import time
from collections import deque
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xiaozhi.monitor")

# Metrics storage
_metrics: Dict[str, deque] = {}
_alerts: deque = deque(maxlen=100)
_lock = Lock()


def record_metric(name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
    """Record a metric value."""
    with _lock:
        if name not in _metrics:
            _metrics[name] = deque(maxlen=1000)
        _metrics[name].append({
            "value": value,
            "timestamp": time.time(),
            "tags": tags or {},
        })


def get_metrics(name: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Get recent metric values."""
    with _lock:
        return list(_metrics.get(name, []))[-limit:]


def get_metric_summary(name: str) -> Dict[str, Any]:
    """Get summary statistics for a metric."""
    with _lock:
        values = [m["value"] for m in _metrics.get(name, [])]
        if not values:
            return {"count": 0, "min": 0, "max": 0, "avg": 0, "sum": 0}
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "sum": sum(values),
        }


def add_alert(level: str, message: str, source: str = "system") -> None:
    """Add an alert."""
    alert = {
        "level": level,
        "message": message,
        "source": source,
        "timestamp": time.time(),
        "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with _lock:
        _alerts.append(alert)
    logger.warning(f"[{level.upper()}] {source}: {message}")


def get_alerts(limit: int = 50, level: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get recent alerts."""
    with _lock:
        alerts = list(_alerts)
    if level:
        alerts = [a for a in alerts if a["level"] == level]
    return alerts[-limit:]


def get_system_health() -> Dict[str, Any]:
    """Get overall system health status."""
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        health = {
            "status": "healthy",
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_mb": memory.used // (1024 * 1024),
            "memory_total_mb": memory.total // (1024 * 1024),
            "disk_percent": disk.percent,
            "disk_used_gb": disk.used // (1024 * 1024 * 1024),
            "disk_total_gb": disk.total // (1024 * 1024 * 1024),
        }
        if cpu_percent > 90 or memory.percent > 90:
            health["status"] = "warning"
            add_alert("warning", f"High resource usage: CPU={cpu_percent}%, MEM={memory.percent}%")
        return health
    except ImportError:
        return {"status": "unknown", "message": "psutil not installed"}


def get_uptime() -> float:
    """Get process uptime in seconds."""
    try:
        import psutil
        return time.time() - psutil.Process().create_time()
    except ImportError:
        return 0


# Predefined metrics
def record_request(endpoint: str, method: str, status_code: int, duration_ms: float) -> None:
    """Record an HTTP request metric."""
    record_metric("http_requests", 1, {"endpoint": endpoint, "method": method, "status": str(status_code)})
    record_metric("http_duration", duration_ms, {"endpoint": endpoint})


def record_mcp_event(event_type: str, user_id: int, success: bool) -> None:
    """Record an MCP event."""
    record_metric("mcp_events", 1, {"type": event_type, "user": str(user_id), "success": str(success)})
    if not success:
        add_alert("warning", f"MCP {event_type} failed for user {user_id}", "mcp")


def record_error(error_type: str, message: str, source: str = "system") -> None:
    """Record an error."""
    record_metric("errors", 1, {"type": error_type})
    add_alert("error", message, source)
