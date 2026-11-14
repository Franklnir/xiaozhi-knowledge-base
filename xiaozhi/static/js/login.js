const authMode = document.getElementById("authMode");
const panels = {
    login: document.getElementById("loginPanel"),
    register: document.getElementById("registerPanel"),
};
function setMode(mode) {
    Object.entries(panels).forEach(([name, panel]) => {
        panel.classList.toggle("hidden", name !== mode);
    });
    document.querySelector(".login-shortcuts")?.classList.toggle("hidden", mode !== "login");
    document.querySelector(".auth-layout")?.classList.toggle("has-login-shortcuts", mode === "login");
    const url = new URL(window.location.href);
    url.searchParams.set("mode", mode);
    window.history.replaceState({}, "", url);
}

authMode.addEventListener("change", () => setMode(authMode.value));

document.addEventListener("DOMContentLoaded", function() {
    const loginForm = document.getElementById("loginPanel");
    const mcpCard = document.getElementById("loginMcpGatingCard");
    const mcpTokenInput = document.getElementById("login_mcp_token");
    const connectBtn = document.getElementById("loginConnectMcpBtn");
    const notice = document.getElementById("loginMcpStatusNotice");
    const noticeText = document.getElementById("loginMcpStatusText");
    const noticeIcon = document.getElementById("loginMcpStatusIcon");
    const progressBar = document.getElementById("loginMcpProgressBar");
    let csrf = document.querySelector('input[name="csrf_token"]') ? document.querySelector('input[name="csrf_token"]').value : "";

    let pollTimer = null;

    async function handleAjaxLogin(username, password, submitBtn) {
        if (!username || !password) return;
        submitBtn.disabled = true;
        const origText = submitBtn.textContent;
        submitBtn.textContent = "Memverifikasi...";

        try {
            const resp = await fetch("/api/auth/web-login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username: username,
                    password: password,
                    csrf_token: csrf
                })
            });
            const data = await resp.json().catch(() => ({}));

            if (data.success) {
                if (data.csrf_token) csrf = data.csrf_token;
                if (data.mcp_required) {
                    // MCP is required and not connected! Show gating card!
                    if (loginForm) loginForm.classList.add("hidden");
                    document.getElementById("authMode")?.parentElement?.classList.add("hidden");

                    mcpCard.classList.remove("hidden");
                    if (data.token_preview) mcpTokenInput.placeholder = data.token_preview;
                    mcpTokenInput.focus();
                } else {
                    window.location.href = data.redirect_url || "/dashboard";
                }
            } else {
                const requestId = resp.headers.get("X-Request-ID");
                const message = data.message || data.detail?.message ||
                    (typeof data.detail === "string" ? data.detail : "Login gagal. Silakan coba lagi.");
                alert(requestId ? message + "\nID request " + requestId : message);
                submitBtn.disabled = false;
                submitBtn.textContent = origText;
            }
        } catch (_) {
            alert("Login belum berhasil. Periksa koneksi lalu coba lagi.");
            submitBtn.disabled = false;
            submitBtn.textContent = origText;
        }
    }

    if (loginForm) {
        loginForm.addEventListener("submit", function(e) {
            e.preventDefault();
            const u = document.getElementById("login_username").value.trim();
            const p = document.getElementById("login_password").value;
            const btn = loginForm.querySelector('button[type="submit"]');
            handleAjaxLogin(u, p, btn);
        });
    }

    if (connectBtn) {
        connectBtn.addEventListener("click", async function() {
            const token = mcpTokenInput.value.trim();
            if (!token || (!token.startsWith("wss://") && !token.startsWith("ws://"))) {
                alert("Endpoint MCP wajib diawali wss:// atau ws://");
                mcpTokenInput.focus();
                return;
            }

            connectBtn.disabled = true;
            connectBtn.textContent = "Menghubungkan ke XiaoZhi...";
            notice.classList.remove("hidden");
            noticeIcon.textContent = "⏳";
            noticeText.textContent = "Menyimpan endpoint MCP...";
            progressBar.style.width = "40%";

            try {
                const fd = new FormData();
                fd.append("csrf_token", csrf);
                fd.append("mcp_token", token);

                const sResp = await fetch("/save_mcp", {
                    method: "POST",
                    body: fd,
                    credentials: "same-origin"
                });
                const sData = await sResp.json();

                if (sData.success) {
                    noticeText.textContent = "Endpoint tersimpan. Menghubungkan ke bridge WebSocket...";
                    progressBar.style.width = "70%";

                    let attempts = 0;
                    if (pollTimer) clearInterval(pollTimer);

                    pollTimer = setInterval(async function() {
                        attempts++;
                        try {
                            const stResp = await fetch("/api/mcp/status", { credentials: "same-origin" });
                            const stData = await stResp.json();

                            if (stData.success && stData.status) {
                                const s = stData.status;
                                if (s.connected) {
                                    clearInterval(pollTimer);
                                    noticeIcon.innerHTML = '<svg class="app-icon app-icon-sm" viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
                                    noticeText.textContent = "MCP Berhasil Terhubung! Mengalihkan ke Dashboard...";
                                    progressBar.style.width = "100%";
                                    progressBar.className = "h-full bg-emerald-600 transition-all duration-300";

                                    setTimeout(function() {
                                        window.location.href = "/dashboard";
                                    }, 1200);
                                    return;
                                } else {
                                    noticeText.textContent = s.statusText || ("Menunggu respon XiaoZhi (" + attempts + "s)...");
                                }
                            }
                        } catch (_) {}

                        if (attempts >= 25) {
                            clearInterval(pollTimer);
                            connectBtn.disabled = false;
                            connectBtn.textContent = "Coba Hubungkan Ulang";
                            noticeIcon.innerHTML = '<svg class="app-icon app-icon-sm" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';
                            noticeText.textContent = "Belum terhubung. Pastikan URL WebSocket MCP benar.";
                        }
                    }, 1500);

                } else {
                    connectBtn.disabled = false;
                    connectBtn.textContent = "Hubungkan MCP & Masuk";
                    noticeIcon.innerHTML = '<svg class="app-icon app-icon-sm" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
                    noticeText.textContent = sData.detail || "Gagal menyimpan endpoint.";
                }
            } catch (err) {
                connectBtn.disabled = false;
                connectBtn.textContent = "Hubungkan MCP & Masuk";
                noticeIcon.innerHTML = '<svg class="app-icon app-icon-sm" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
                noticeText.textContent = "Terjadi kesalahan: " + err.message;
            }
        });
    }
});
