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
        "X-CSRF-Token": getCsrfToken(),
      },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
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
    const res = await fetch(url, { method: "POST", body: formData });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
  }

  async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
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

  /* ── Toast Notification ─────────────────────────────────── */
  let _toastContainer = null;

  function _ensureToastContainer() {
    if (_toastContainer) return _toastContainer;
    _toastContainer = document.createElement("div");
    _toastContainer.id = "app-toast-container";
    _toastContainer.setAttribute("aria-live", "polite");
    _toastContainer.setAttribute("aria-atomic", "true");
    Object.assign(_toastContainer.style, {
      position: "fixed",
      top: "16px",
      right: "16px",
      zIndex: "10000",
      display: "flex",
      flexDirection: "column",
      gap: "8px",
      maxWidth: "380px",
      pointerEvents: "none",
    });
    document.body.appendChild(_toastContainer);
    return _toastContainer;
  }

  function _toastStyle(type) {
    const base = {
      padding: "12px 16px",
      borderRadius: "var(--app-radius, 8px)",
      fontSize: "14px",
      fontWeight: "500",
      lineHeight: "1.4",
      boxShadow: "var(--app-shadow, 0 4px 12px rgba(0,0,0,.15))",
      pointerEvents: "auto",
      transform: "translateX(120%)",
      transition: "transform .25s ease, opacity .25s ease",
      opacity: "0",
      cursor: "pointer",
    };
    const colors = {
      success: { background: "#059669", color: "#fff" },
      error: { background: "#dc2626", color: "#fff" },
      warning: { background: "#d97706", color: "#fff" },
      info: { background: "var(--app-sky, #0284c7)", color: "#fff" },
    };
    return { ...base, ...(colors[type] || colors.info) };
  }

  function toast(message, type, durationMs) {
    type = type || "info";
    durationMs = durationMs || 3500;
    const container = _ensureToastContainer();
    const el = document.createElement("div");
    el.textContent = message;
    Object.assign(el.style, _toastStyle(type));
    el.setAttribute("role", "status");
    el.addEventListener("click", function () {
      el.style.opacity = "0";
      el.style.transform = "translateX(120%)";
      setTimeout(function () {
        el.remove();
      }, 300);
    });
    container.appendChild(el);
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        el.style.transform = "translateX(0)";
        el.style.opacity = "1";
      });
    });
    setTimeout(function () {
      el.style.opacity = "0";
      el.style.transform = "translateX(120%)";
      setTimeout(function () {
        el.remove();
      }, 300);
    }, durationMs);
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
