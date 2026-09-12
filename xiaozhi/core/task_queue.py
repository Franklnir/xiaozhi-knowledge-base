"""Background task queue for async operations."""
import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Callable, Coroutine, Dict, Optional

logger = logging.getLogger("xiaozhi.task_queue")

# Task queue
_task_queue: asyncio.Queue = None
_tasks: Dict[str, Dict[str, Any]] = {}
_running = False


async def init_task_queue(max_workers: int = 3):
    """Initialize the task queue."""
    global _task_queue, _running
    _task_queue = asyncio.Queue()
    _running = True
    for i in range(max_workers):
        asyncio.create_task(_worker(f"worker-{i}"))
    logger.info(f"Task queue initialized with {max_workers} workers")


async def _worker(name: str):
    """Worker that processes tasks from the queue."""
    while _running:
        try:
            task_id, func, args, kwargs = await _task_queue.get()
            _tasks[task_id]["status"] = "running"
            _tasks[task_id]["started_at"] = time.time()
            try:
                result = await func(*args, **kwargs)
                _tasks[task_id]["status"] = "completed"
                _tasks[task_id]["result"] = result
            except Exception as e:
                _tasks[task_id]["status"] = "failed"
                _tasks[task_id]["error"] = str(e)
                logger.exception(f"Task {task_id} failed: {e}")
            finally:
                _tasks[task_id]["completed_at"] = time.time()
                _task_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception(f"Worker {name} error")


async def submit_task(func: Callable[..., Coroutine], *args, **kwargs) -> str:
    """Submit an async task to the queue. Returns task ID."""
    task_id = uuid.uuid4().hex[:12]
    _tasks[task_id] = {
        "id": task_id,
        "status": "pending",
        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "result": None,
        "error": None,
    }
    await _task_queue.put((task_id, func, args, kwargs))
    return task_id


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Get status of a task."""
    return _tasks.get(task_id)


def list_tasks(limit: int = 50) -> list:
    """List recent tasks."""
    tasks = sorted(_tasks.values(), key=lambda t: t.get("created_at", 0), reverse=True)
    return tasks[:limit]


def get_queue_stats() -> Dict[str, Any]:
    """Get queue statistics."""
    statuses = {}
    for task in _tasks.values():
        status = task.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "queue_size": _task_queue.qsize() if _task_queue else 0,
        "total_tasks": len(_tasks),
        "by_status": statuses,
    }
