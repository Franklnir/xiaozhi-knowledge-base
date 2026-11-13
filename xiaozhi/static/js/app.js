/* ============================================================
   Xiaozhi Indonesia — Shared JS Module
   escapeHtml · fetch helpers · polling · toast · modal
   ============================================================ */

(function () {
  "use strict";

  /* ── escapeHtml ─────────────────────────────────────────── */
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  /* ── CSRF token helper ──────────────────────────────────── */
  function getCsrfToken() {
    const el = document.querySelector('input[name="csrf_token"]');
    return el ? el.value : "";
  }

  /* ── Fetch helpers ──────────────────────────────────────── */
  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-CSRF-Token": getCsrfToken(),
      },
      body: JSON.stringify(body || {}),
    });
    if (res.status === 401 || res.redirected) {
      if (window.App && App.toast) {
        App.toast.error("Sesi login Anda telah berakhir. Mengarahkan ke halaman login...");
      }
      setTimeout(() => {
        window.location.href = '/login?message=Sesi+login+telah+berakhir.+Silakan+masuk+kembali.';
      }, 1200);
      throw new Error("Sesi login telah berakhir.");
    }
    const ct = res.headers.get("content-type") || "";
    if (!res.ok) {
      if (ct.includes("application/json")) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || errJson.message || `HTTP ${res.status}`);
      }
      if (res.status >= 500) {
        throw new Error("Server sedang dalam pemeliharaan atau sedang sibuk. Silakan coba sesaat lagi.");
      }
      throw new Error(`Permintaan gagal (HTTP ${res.status}).`);
    }
    if (!ct.includes("application/json")) {
      return {};
    }
    return res.json().catch(() => ({}));
  }

  async function postForm(url, formData) {
    if (formData instanceof FormData === false) {
      formData = new FormData();
      for (const [k, v] of Object.entries(arguments[1] || {})) {
        formData.set(k, v);
      }
    }
    if (!formData.has("csrf_token")) {
      formData.set("csrf_token", getCsrfToken());
    }
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Accept": "application/json",
      },
      body: formData,
    });
    if (res.status === 401 || res.redirected) {
      if (window.App && App.toast) {
        App.toast.error("Sesi login Anda telah berakhir. Mengarahkan ke halaman login...");
      }
      setTimeout(() => {
        window.location.href = '/login?message=Sesi+login+telah+berakhir.+Silakan+masuk+kembali.';
      }, 1200);
      throw new Error("Sesi login telah berakhir.");
    }
    const ct = res.headers.get("content-type") || "";
    if (!res.ok) {
      if (ct.includes("application/json")) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || errJson.message || `HTTP ${res.status}`);
      }
      if (res.status >= 500) {
        throw new Error("Server sedang dalam pemeliharaan atau sedang sibuk. Silakan coba sesaat lagi.");
      }
      throw new Error(`Permintaan gagal (HTTP ${res.status}).`);
    }
    if (!ct.includes("application/json")) {
      return {};
    }
    return res.json().catch(() => ({}));
  }

  async function fetchJson(url) {
    const res = await fetch(url, {
      headers: {
        "Accept": "application/json",
      },
    });
    if (res.status === 401 || res.redirected) {
      if (window.App && App.toast) {
        App.toast.error("Sesi login Anda telah berakhir. Mengarahkan ke halaman login...");
      }
      setTimeout(() => {
        window.location.href = '/login?message=Sesi+login+telah+berakhir.+Silakan+masuk+kembali.';
      }, 1200);
      throw new Error("Sesi login telah berakhir.");
    }
    const ct = res.headers.get("content-type") || "";
    if (!res.ok) {
      if (ct.includes("application/json")) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || errJson.message || `HTTP ${res.status}`);
      }
      if (res.status >= 500) {
        throw new Error("Server sedang dalam pemeliharaan atau sedang sibuk. Silakan coba sesaat lagi.");
      }
      throw new Error(`Permintaan gagal (HTTP ${res.status}).`);
    }
    if (!ct.includes("application/json")) {
      return {};
    }
    return res.json().catch(() => ({}));
  }

  /* ── Polling Manager ────────────────────────────────────── */
  const _pollers = {};

  function startPolling(key, fn, intervalMs) {
    stopPolling(key);
    fn();
    _pollers[key] = setInterval(fn, intervalMs);
  }

  function stopPolling(key) {
    if (_pollers[key]) {
      clearInterval(_pollers[key]);
      delete _pollers[key];
    }
  }

  function stopAllPolling() {
    Object.keys(_pollers).forEach(stopPolling);
  }

  /* ── Modern Toast Notification System ─────────────────────── */
  let _toastContainer = null;
  const _MAX_VISIBLE_TOASTS = 3;
  const _recentToastTimes = new Map();

  const _toastIcons = {
    success: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    error: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
    warning: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    info: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    close: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
  };

  function _stripLeadingEmoji(text) {
    if (!text || typeof text !== "string") return "";
    let s = text.replace(/^[\s\u{1F300}-\u{1FAD6}\u{2600}-\u{27BF}\u{1F900}-\u{1F9FF}\u{1F600}-\u{1F64F}\u{1F680}-\u{1F6FF}\u2713\u2715\u26A0\u2705\u274C\u2139\u{1F389}\u{1F4E1}\u26A1\u{1F50C}\u{1F512}\u{1F513}\u25B6\u23F9\u{1F504}\u{1F4E4}\u{1F9F9}\u2B05\u2794\u2022\u2013\u2014\-:\s]+/u, "").trim() || text;
    if (s.includes("<!DOCTYPE") || s.includes("is not valid JSON") || s.includes("Unexpected token '<'")) {
      s = "Respon server tidak valid atau sedang dalam pemeliharaan. Silakan coba sesaat lagi.";
    }
    return s;
  }

  function _ensureToastContainer() {
    if (_toastContainer && document.body.contains(_toastContainer)) return _toastContainer;
    _toastContainer = document.createElement("div");
    _toastContainer.id = "app-toast-container";
    _toastContainer.className = "app-toast-container";
    _toastContainer.setAttribute("aria-live", "polite");
    _toastContainer.setAttribute("aria-atomic", "true");
    document.body.appendChild(_toastContainer);
    return _toastContainer;
  }

  function toast(message, type, durationMs) {
    type = type || "info";
    durationMs = durationMs || (type === "error" ? 5000 : 2600);
    const container = _ensureToastContainer();
    const cleanMsg = _stripLeadingEmoji(message);
    if (!cleanMsg) return;

    // 1. Cegah banjir notifikasi dengan pesan sama persis dalam 2 detik
    const now = Date.now();
    const lastTime = _recentToastTimes.get(cleanMsg) || 0;
    if (now - lastTime < 2000) {
      return;
    }
    _recentToastTimes.set(cleanMsg, now);
    if (_recentToastTimes.size > 50) {
      for (const [k, v] of _recentToastTimes.entries()) {
        if (now - v > 10000) _recentToastTimes.delete(k);
      }
    }

    // 2. Batasi tumpukan toast maksimal 3 buah (Singkirkan toast terlama secara otomatis)
    while (container.children.length >= _MAX_VISIBLE_TOASTS) {
      const oldest = container.children[0];
      if (oldest) oldest.remove();
    }

    const el = document.createElement("div");
    el.className = "app-toast-item app-toast-" + type;
    el.setAttribute("role", "status");

    const iconWrap = document.createElement("div");
    iconWrap.className = "app-toast-icon-wrap";
    iconWrap.innerHTML = _toastIcons[type] || _toastIcons.info;

    const content = document.createElement("div");
    content.className = "app-toast-content";
    content.textContent = cleanMsg;

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "app-toast-close";
    closeBtn.setAttribute("aria-label", "Tutup");
    closeBtn.innerHTML = _toastIcons.close;

    el.appendChild(iconWrap);
    el.appendChild(content);
    el.appendChild(closeBtn);

    if (durationMs > 0) {
      const progress = document.createElement("div");
      progress.className = "app-toast-progress";
      progress.setAttribute("aria-hidden", "true");
      el.style.setProperty("--toast-duration", durationMs + "ms");
      el.appendChild(progress);
    }

    let dismissed = false;
    function dismiss() {
      if (dismissed) return;
      dismissed = true;
      el.classList.add("is-dismissing");
      setTimeout(function () {
        if (el.parentNode) el.remove();
      }, 260);
    }

    closeBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      dismiss();
    });
    el.addEventListener("click", dismiss);

    container.appendChild(el);
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        el.classList.add("is-visible");
      });
    });

    if (durationMs > 0) {
      setTimeout(dismiss, durationMs);
    }
  }

  toast.success = function (msg, dur) { toast(msg, "success", dur); };
  toast.error = function (msg, dur) { toast(msg, "error", dur || 5000); };
  toast.warning = function (msg, dur) { toast(msg, "warning", dur || 4000); };
  toast.info = function (msg, dur) { toast(msg, "info", dur); };

  /* ── Modal Component ────────────────────────────────────── */
  function openModal(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add("is-open");
    el.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    var focusTarget = el.querySelector("[autofocus], input, button, [tabindex]");
    if (focusTarget) focusTarget.focus();
  }

  function closeModal(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("is-open");
    el.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  }

  function initModals() {
    document.addEventListener("click", function (e) {
      var closer = e.target.closest("[data-modal-close]");
      if (closer) {
        var modal = closer.closest(".app-modal");
        if (modal) {
          modal.classList.remove("is-open");
          modal.setAttribute("aria-hidden", "true");
          document.body.style.overflow = "";
        }
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        var openModals = document.querySelectorAll(".app-modal.is-open");
        openModals.forEach(function (m) {
          m.classList.remove("is-open");
          m.setAttribute("aria-hidden", "true");
        });
        document.body.style.overflow = "";
      }
    });
  }

  /* ── Expose globally ────────────────────────────────────── */
  window.App = {
    escapeHtml: escapeHtml,
    getCsrfToken: getCsrfToken,
    postJson: postJson,
    postForm: postForm,
    fetchJson: fetchJson,
    startPolling: startPolling,
    stopPolling: stopPolling,
    stopAllPolling: stopAllPolling,
    toast: toast,
    openModal: openModal,
    closeModal: closeModal,
  };

  /* ── Auto-init on DOMContentLoaded ──────────────────────── */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initModals);
  } else {
    initModals();
  }

})();
