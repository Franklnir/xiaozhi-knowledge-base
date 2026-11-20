"""Capture the access attempts for one research request."""

import time
from contextvars import ContextVar
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


current_audit = ContextVar("current_research_audit", default=None)
opened_research_urls = ContextVar("opened_research_urls", default=frozenset())


def public_url(url):
    """Keep identifiers like YouTube v while excluding private URL parameters."""
    try:
        parsed = urlsplit(str(url or "").strip())
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        if any(char.isspace() or char in "\\<>\"" for char in parsed.netloc):
            return ""
        sensitive = ("token", "key", "secret", "password", "signature", "auth", "credential", "session")
        params = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
                  if not key.lower().startswith(("utm_", "x-amz-", "x-goog-"))
                  and key.lower() not in {"fbclid", "gclid"}
                  and not any(word in key.lower() for word in sensitive)]
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, urlencode(params), ""))
    except ValueError:
        return ""


class ResearchAudit:
    def __init__(self):
        self._lock = Lock()
        self._entries = []
        self._closed = False
        self._started_at = time.monotonic()

    def begin(self, operation, target):
        with self._lock:
            if self._closed:
                raise TimeoutError("Waktu permintaan riset sudah selesai.")
            retry_target = public_url(target)
            parsed = urlsplit(retry_target)
            safe_target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) if parsed.netloc else ""
            entry = {"operation": operation, "target": safe_target}
            if operation in {"ekstraksi_sumber", "buka_halaman", "unduh_pdf", "jina_reader", "cari_subtitle", "baca_subtitle"}:
                entry["retry_target"] = retry_target
            self._entries.append((time.monotonic(), entry))
            return entry

    def end(self, entry, status, detail=""):
        with self._lock:
            if not self._closed:
                started = next(started for started, candidate in self._entries if candidate is entry)
                entry.update(status=status, detail=detail, duration_ms=round((time.monotonic() - started) * 1000))

    def close(self):
        with self._lock:
            self._closed = True
            return [
                {"status": "timeout", "detail": "Belum selesai saat hasil riset ditutup.", **entry,
                 "started_after_ms": round((started - self._started_at) * 1000)}
                for started, entry in self._entries
            ]


def audited_call(operation, target, call, *args, classify=None, **kwargs):
    audit = current_audit.get()
    if audit is None:
        return call(*args, **kwargs)
    entry = audit.begin(operation, target)
    try:
        result = call(*args, **kwargs)
    except Exception as error:
        code = getattr(error, "code", None)
        timed_out = isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()
        audit.end(entry, "timeout" if timed_out else "failed", f"HTTP {code}" if isinstance(code, int) else type(error).__name__)
        raise
    if classify is None:
        code = getattr(result, "status_code", None)
        if not isinstance(code, int):
            code = getattr(result, "status", None)
        status, detail = ("failed" if code >= 400 else "success", f"HTTP {code}") if isinstance(code, int) else ("success", "")
    else:
        status, detail = classify(result)
    audit.end(entry, status, detail)
    return result
