import {flashElement, liveApiMetaText} from "./material-display.js";

const addForm = document.getElementById("addMaterialForm");
let activeViewingItem = null;
let savingMaterial = false;

function syncDialogScroll() {
    document.body.style.overflow = document.querySelector(".dash-modal-backdrop.is-open") ? "hidden" : "";
}

// Word counter utility
function updateWordCounter(textarea, targetId) {
    if (!textarea) return;
    const text = textarea.value.trim();
    const words = text ? text.split(/\s+/).length : 0;
    const target = document.getElementById(targetId);
    if (target) {
        target.textContent = `${words} kata`;
    }
}

// Modal controllers
function openAddModal() {
    const modal = document.getElementById('addMaterialModal');
    if (modal) {
        modal.classList.add('is-open');
        syncLiveApiFields('add');
        document.body.style.overflow = 'hidden';
        setTimeout(() => {
            const titleInp = document.getElementById('title');
            if (titleInp) titleInp.focus();
        }, 100);
    }
}

function closeAddModal() {
    clearLiveApiTimer('add');
    const modal = document.getElementById('addMaterialModal');
    if (modal) {
        modal.classList.remove('is-open');
        syncDialogScroll();
    }
}

function openCategoryModal() {
    const modal = document.getElementById('categoryModal');
    if (modal) {
        modal.classList.add('is-open');
        document.body.style.overflow = 'hidden';
    }
}

function closeCategoryModal() {
    const modal = document.getElementById('categoryModal');
    if (modal) {
        modal.classList.remove('is-open');
        syncDialogScroll();
    }
}

async function viewMaterial(btn, fetchMaterialDetail) {
    try {
        const item = await fetchMaterialDetail(btn);
        activeViewingItem = item;

        document.getElementById('viewModalTitle').textContent = item.title || 'Tanpa Judul';
        document.getElementById('viewIdBadge').textContent = '#' + (item.id || '');

        const catBadge = document.getElementById('viewCatBadge');
        if (catBadge) {
            catBadge.textContent = item.category || 'Umum';
            const catLower = (item.category || '').toLowerCase();
            catBadge.className = 'cat-chip ' + (
                catLower.includes('tugas') ? 'cat-chip-tugas' :
                catLower.includes('pengumuman') ? 'cat-chip-pengumuman' :
                catLower.includes('materi') ? 'cat-chip-materi' :
                (item.source_type === 'api_live' || catLower.includes('api')) ? 'cat-chip-api' : 'cat-chip-default'
            );
        }

        const kwWrap = document.getElementById('viewKeywordsWrap');
        const kwText = document.getElementById('viewKeywordsText');
        if (item.keywords && item.keywords.trim()) {
            if (kwWrap) kwWrap.style.display = 'flex';
            if (kwText) kwText.textContent = item.keywords;
        } else {
            if (kwWrap) kwWrap.style.display = 'none';
        }

        const contentBox = document.getElementById('viewFullContent');
        if (contentBox) contentBox.textContent = item.content || '';

        const words = item.content ? item.content.trim().split(/\s+/).length : 0;
        const chars = item.content ? item.content.length : 0;
        const metaStats = document.getElementById('viewMetaStats');
        if (metaStats) metaStats.textContent = `${words} kata · ${chars} karakter`;

        const liveEndpoint = document.getElementById('viewLiveEndpointText');
        if (liveEndpoint) {
            if (item.source_type === 'api_live' && (item.masked_api_url || item.api_label || item.api_url)) {
                liveEndpoint.textContent = "Endpoint Terenkripsi: " + (item.masked_api_url || item.api_label || "Aktif (AES-256)");
            } else {
                liveEndpoint.textContent = '';
            }
        }

        const modal = document.getElementById('viewModal');
        if (modal) {
            modal.classList.add('is-open');
            document.body.style.overflow = 'hidden';
        }
    } catch(e) {
        document.getElementById('materialListStatus').textContent = e.message;
    }
}

function closeViewModal() {
    activeViewingItem = null;
    const modal = document.getElementById('viewModal');
    if (modal) {
        modal.classList.remove('is-open');
        syncDialogScroll();
    }
}

function copyFullContent() {
    if (!activeViewingItem || !activeViewingItem.content) return;
    navigator.clipboard.writeText(activeViewingItem.content).then(() => {
        const btnText = document.getElementById('copyBtnText');
        const btn = document.getElementById('copyContentBtn');
        if (btnText && btn) {
            const old = btnText.textContent;
            btnText.textContent = 'Tersalin!';
            btn.style.color = '#059669';
            setTimeout(() => {
                btnText.textContent = old;
                btn.style.color = 'var(--app-sky)';
            }, 2000);
        }
    }).catch(err => {
        alert('Gagal menyalin: ' + err.message);
    });
}

function confirmDeleteMaterial(id, title) {
    const form = document.getElementById('deleteForm');
    const prompt = document.getElementById('deleteTargetPrompt');
    if (form) form.action = '/delete_material/' + encodeURIComponent(id);
    if (prompt) {
        const label = document.createElement('strong');
        label.textContent = `"${title}"`;
        prompt.replaceChildren('Apakah Anda yakin ingin menghapus materi ', label, ` (ID #${id})? Tindakan ini permanen.`);
    }
    const modal = document.getElementById('deleteModal');
    if (modal) {
        modal.classList.add('is-open');
        document.body.style.overflow = 'hidden';
    }
}

function closeDeleteModal() {
    const modal = document.getElementById('deleteModal');
    if (modal) {
        modal.classList.remove('is-open');
        syncDialogScroll();
    }
}

async function editData(button, fetchMaterialDetail) {
    try {
        const item = await fetchMaterialDetail(button);
        openEditWithItem(item);
    } catch (e) {
        document.getElementById('materialListStatus').textContent = e.message;
    }
}

function openEditWithItem(item) {
    liveApiContexts.edit.lastHash = "";
    liveApiContexts.edit.lastContent = "";
    document.getElementById("edit_title").value = item.title || "";
    document.getElementById("edit_category").value = item.category || "";
    document.getElementById("edit_content").value = item.content || "";
    document.getElementById("edit_keywords").value = item.keywords || "";
    document.getElementById("edit_api_url").value = item.api_url || "";
    document.getElementById("editForm").action = "/update_material/" + encodeURIComponent(item.id);
    updateWordCounter(document.getElementById("edit_content"), "editWordCountDisplay");
    const modal = document.getElementById("editModal");
    if (modal) {
        modal.classList.add('is-open');
        syncLiveApiFields("edit");
        document.body.style.overflow = 'hidden';
    }
}

function closeModal() {
    const modal = document.getElementById("editModal");
    if (modal) {
        modal.classList.remove('is-open');
        syncDialogScroll();
    }
    clearLiveApiTimer("edit");
}

function closeProgressModal() {
    document.getElementById("materialProgressModal").classList.remove("is-open");
    syncDialogScroll();
}

// Real-Time SSE Submission Handler
async function handleAddMaterialSubmit(event) {
    event.preventDefault();
    if (savingMaterial) return;
    const form = event.target;
    const title = form.querySelector('[name="title"]').value.trim();
    const category = form.querySelector('[name="category"]').value.trim();
    const content = form.querySelector('[name="content"]').value.trim();
    const keywords = form.querySelector('[name="keywords"]').value.trim();
    let apiUrl = form.querySelector('[name="api_url"]') ? form.querySelector('[name="api_url"]').value.trim() : '';

    // Pastikan endpoint API hanya dikirim jika kategori memang khusus live API
    if (!isLiveApiCategory(category)) {
        apiUrl = '';
    }

    if (!title || !content) {
        form.submit();
        return false;
    }
    savingMaterial = true;

    const modal = document.getElementById('materialProgressModal');
    const bar = document.getElementById('matProgressBar');
    const percentText = document.getElementById('matProgPercent');
    const titleEl = document.getElementById('matProgStepTitle');
    const msgEl = document.getElementById('matProgMessage');
    let completed = false;

    closeAddModal();
    modal.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    bar.style.width = '5%';
    percentText.innerText = '5%';
    titleEl.innerText = 'Memulai Vektorisasi...';
    msgEl.innerText = 'Mengirim data materi ke pipeline RAG Xiaozhi...';

    try {
        const formData = new FormData();
        formData.append('title', title);
        formData.append('category', category);
        formData.append('content', content);
        formData.append('keywords', keywords);
        formData.append('api_url', apiUrl);
        formData.append('csrf_token', form.elements.csrf_token.value);

        const response = await fetch('/api/materials/stream-index', {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'text/event-stream'
            }
        });

        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            const requestId = response.headers.get("X-Request-ID");
            const message = typeof payload.detail === "string" ? payload.detail : "Materi gagal disimpan.";
            alert(requestId ? `${message}\nID request ${requestId}` : message);
            modal.classList.remove('is-open');
            return false;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split('\n\n');
            buffer = parts.pop();

            for (const part of parts) {
                if (!part.trim() || part.startsWith(':')) continue;

                let currentEvent = 'message';
                let dataText = '';
                for (const line of part.split('\n')) {
                    if (line.startsWith('event:')) currentEvent = line.replace('event:', '').trim();
                    else if (line.startsWith('data:')) dataText += line.replace('data:', '').trim();
                }
                if (!dataText) continue;

                try {
                    const parsed = JSON.parse(dataText);
                    if (currentEvent === 'progress') {
                        bar.style.width = parsed.percent + '%';
                        percentText.innerText = parsed.percent + '%';
                        if (parsed.step) titleEl.innerText = parsed.step;
                        if (parsed.message) msgEl.innerText = parsed.message;
                    } else if (currentEvent === 'done') {
                        completed = true;
                        bar.style.width = '100%';
                        percentText.innerText = '100%';
                        titleEl.innerText = 'Selesai!';
                        msgEl.innerText = parsed.message || 'Materi berhasil disimpan dan divektorisasi.';
                        setTimeout(() => {
                            savingMaterial = false;
                            closeProgressModal();
                            form.reset();
                            syncLiveApiFields("add");
                            window.dispatchEvent(new Event("material-list-changed"));
                            const tableWrap = document.querySelector('.app-table-wrap');
                            if (tableWrap) tableWrap.scrollTo({ top: 0, behavior: 'smooth' });
                        }, 500);
                        return true;
                    } else if (currentEvent === 'error') {
                        alert('Gagal: ' + (parsed.message || 'Terjadi kesalahan'));
                        closeProgressModal();
                        return false;
                    }
                } catch(err) {}
            }
        }
        throw new Error('Stream ended before confirmation');
    } catch (err) {
        closeProgressModal();
        window.dispatchEvent(new Event("material-list-changed"));
        alert("Koneksi terputus saat menyimpan. Periksa daftar materi sebelum mencoba lagi.");
    } finally {
        if (!completed) savingMaterial = false;
    }
    return false;
}

async function postJson(url, body, signal) {
    const response = await fetch(url, {
        method: "POST",
        headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-CSRF-Token": addForm.elements.csrf_token.value,
        },
        credentials: "same-origin",
        body: JSON.stringify(body),
        signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Request gagal.");
    return data;
}

const liveApiContexts = {
    add: {
        category: document.getElementById("category"),
        fields: document.getElementById("liveApiFields"),
        apiUrl: document.getElementById("api_url"),
        content: document.getElementById("content"),
        status: document.getElementById("liveApiStatus"),
        refresh: document.getElementById("liveApiRefreshButton"),
        lastContent: "",
        lastHash: "",
        timer: null,
        debounce: null,
    },
    edit: {
        category: document.getElementById("edit_category"),
        fields: document.getElementById("editLiveApiFields"),
        apiUrl: document.getElementById("edit_api_url"),
        content: document.getElementById("edit_content"),
        status: document.getElementById("editLiveApiStatus"),
        refresh: document.getElementById("editLiveApiRefreshButton"),
        lastContent: "",
        lastHash: "",
        timer: null,
        debounce: null,
    },
};

function isLiveApiCategory(value) {
    const val = String(value || "").trim().toLowerCase();
    return val.includes("data dari api");
}

function clearLiveApiTimer(mode) {
    const ctx = liveApiContexts[mode];
    if (!ctx) return;
    if (ctx.timer) window.clearInterval(ctx.timer);
    ctx.timer = null;
    if (ctx.debounce) window.clearTimeout(ctx.debounce);
    ctx.debounce = null;
    ctx.inflight?.abort();
    ctx.inflight = null;
    if (ctx.refresh) ctx.refresh.disabled = false;
}

function setLiveApiStatus(mode, message, isError = false) {
    const ctx = liveApiContexts[mode];
    if (!ctx || !ctx.status) return;
    ctx.status.textContent = message;
    ctx.status.className = isError
        ? "text-[11px] font-semibold text-red-700"
        : "text-[11px] font-semibold text-slate-500";
}

async function refreshLiveApiPreview(mode, showChecking = false) {
    const ctx = liveApiContexts[mode];
    if (!ctx || !ctx.apiUrl || !ctx.category) return;
    const modal = ctx.category.closest('.dash-modal-backdrop');
    const apiUrl = ctx.apiUrl.value.trim();
    if (!modal.classList.contains('is-open') || !apiUrl || !isLiveApiCategory(ctx.category.value) || ctx.inflight) return;
    const request = new AbortController();
    ctx.inflight = request;
    if (ctx.refresh) ctx.refresh.disabled = true;
    if (!ctx.lastHash || showChecking) {
        setLiveApiStatus(mode, "Mengecek perubahan API...");
    }
    try {
        const previousHash = ctx.lastHash;
        const payload = await postJson("/api/materials/live-api/preview", {
            api_url: apiUrl,
            previous_hash: previousHash,
        }, request.signal);
        if (request.signal.aborted || ctx.apiUrl.value.trim() !== apiUrl || !payload.changed) {
            return;
        }
        ctx.lastHash = payload.content_hash || "";
        if (payload.content && payload.content !== ctx.lastContent) {
            ctx.lastContent = payload.content;
            if (previousHash) {
                flashElement(ctx.fields);
                flashElement(ctx.content);
            }
        }
        if (ctx.content) ctx.content.value = payload.content || "";
        setLiveApiStatus(mode, liveApiMetaText(payload));
    } catch (error) {
        if (!request.signal.aborted) setLiveApiStatus(mode, error.message || "Gagal membaca API.", true);
    } finally {
        if (ctx.inflight === request) {
            ctx.inflight = null;
            if (ctx.refresh) ctx.refresh.disabled = false;
        }
    }
}

function scheduleLiveApiPreview(mode) {
    const ctx = liveApiContexts[mode];
    if (!ctx) return;
    if (ctx.debounce) window.clearTimeout(ctx.debounce);
    ctx.debounce = window.setTimeout(() => refreshLiveApiPreview(mode), 450);
}

function syncLiveApiFields(mode) {
    const ctx = liveApiContexts[mode];
    if (!ctx || !ctx.category) return;
    const live = isLiveApiCategory(ctx.category.value);

    if (mode === "add") {
        const scraperTool = document.getElementById("addWebScraperTool");
        if (scraperTool) {
            scraperTool.style.display = live ? "none" : "block";
        }
    }

    if (ctx.fields) {
        ctx.fields.classList.toggle("hidden", !live);
        ctx.fields.style.display = live ? "block" : "none";
    }
    if (ctx.apiUrl) {
        ctx.apiUrl.required = live;
        if (!live) {
            ctx.apiUrl.value = "";
        }
    }
    if (ctx.content) {
        ctx.content.required = !live;
        ctx.content.readOnly = live;
        ctx.content.placeholder = live
            ? "Data dari API live endpoint akan ditarik secara otomatis oleh server dan dienkripsi aman."
            : "Tuliskan materi secara lengkap, runtut, dan jelas agar mudah dipahami oleh asisten suara saat pengguna bertanya...";
        if (live) {
            if (!ctx.content.value.trim() || ctx.content.value.trim() === "Data realtime dari API akan muncul setelah endpoint terbaca.") {
                ctx.content.value = "Data realtime dari API akan muncul setelah endpoint terbaca.";
            }
        } else {
            if (ctx.content.value.trim() === "Data realtime dari API akan muncul setelah endpoint terbaca." ||
                ctx.content.value.startsWith("Data realtime dari API:")) {
                ctx.content.value = "";
            }
        }
        const wordDisplayId = mode === "add" ? "addWordCountDisplay" : "editWordCountDisplay";
        updateWordCounter(ctx.content, wordDisplayId);
    }

    if (!live) {
        clearLiveApiTimer(mode);
        ctx.lastHash = "";
        ctx.lastContent = "";
        return;
    }

    if (!ctx.category.closest('.dash-modal-backdrop').classList.contains('is-open')) return;
    if (!ctx.timer) {
        ctx.timer = window.setInterval(() => refreshLiveApiPreview(mode), 3000);
    }
    if (ctx.apiUrl && ctx.apiUrl.value.trim()) scheduleLiveApiPreview(mode);
}

Object.entries(liveApiContexts).forEach(([mode, ctx]) => {
    if (ctx.category) {
        ctx.category.addEventListener("change", () => syncLiveApiFields(mode));
    }
    if (ctx.apiUrl) {
        ctx.apiUrl.addEventListener("input", () => {
            ctx.inflight?.abort();
            ctx.inflight = null;
            if (ctx.refresh) ctx.refresh.disabled = false;
            ctx.lastHash = "";
            ctx.lastContent = "";
            scheduleLiveApiPreview(mode);
        });
    }
    if (ctx.refresh) {
        ctx.refresh.addEventListener("click", () => refreshLiveApiPreview(mode, true));
    }
});

syncLiveApiFields("add");

export function initMaterialDialogs(fetchMaterialDetail) {
    const closeDialogs = {
        addMaterialModal: closeAddModal,
        editModal: closeModal,
        viewModal: closeViewModal,
        categoryModal: closeCategoryModal,
        deleteModal: closeDeleteModal,
    };
    const actions = {
        add: openAddModal,
        categories: openCategoryModal,
        view: (button) => viewMaterial(button, fetchMaterialDetail),
        edit: (button) => editData(button, fetchMaterialDetail),
        delete: (button) => confirmDeleteMaterial(button.dataset.materialId, button.dataset.title),
        copy: copyFullContent,
        scrape: executeWebScraper,
        close: (button) => closeDialogs[button.closest(".dash-modal-backdrop").id]?.(),
        "edit-viewed": () => {
            const item = activeViewingItem;
            if (!item) return;
            closeViewModal();
            openEditWithItem(item);
        },
    };

    document.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-material-action]");
        if (button) actions[button.dataset.materialAction]?.(button);
        if (event.target.matches(".dash-modal-backdrop")) closeDialogs[event.target.id]?.();
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            const open = Array.from(document.querySelectorAll(".dash-modal-backdrop.is-open")).at(-1);
            if (open) closeDialogs[open.id]?.();
        } else if (event.key === "/" && !document.querySelector(".dash-modal-backdrop.is-open")
            && !event.target.closest("input, textarea, select, [contenteditable]")) {
            event.preventDefault();
            const search = document.getElementById("searchInput");
            search.focus();
            search.select();
        }
    });
    addForm.addEventListener("submit", handleAddMaterialSubmit);
    document.getElementById("categoryModal").addEventListener("submit", (event) => {
        const name = event.target.dataset.deleteCategory;
        if (name && !confirm(`Hapus kategori "${name}"? Materi dalam kategori ini tidak ikut terhapus.`)) {
            event.preventDefault();
        }
    });
    liveApiContexts.add.content.addEventListener("input", (event) => updateWordCounter(event.target, "addWordCountDisplay"));
    liveApiContexts.edit.content.addEventListener("input", (event) => updateWordCounter(event.target, "editWordCountDisplay"));
}

async function executeWebScraper() {
    const urlInput = document.getElementById('scrape_target_url');
    const statusHint = document.getElementById('scrapeStatusHint');
    const btn = document.getElementById('btnScrapeUrl');
    const url = (urlInput ? urlInput.value : '').trim();

    if (!url) {
        alert('Silakan masukkan link/URL website yang ingin di-scrape (contoh: https://...)');
        if (urlInput) urlInput.focus();
        return;
    }

    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        alert('URL harus diawali dengan http:// atau https://');
        if (urlInput) urlInput.focus();
        return;
    }

    btn.disabled = true;
    const oldBtnText = btn.innerHTML;
    btn.innerHTML = `<span class="mcp-spinner" style="margin:0 4px 0 0;"></span> Mengekstrak...`;
    statusHint.style.display = 'block';
    statusHint.className = 'text-[11px] mt-2 text-sky-700 dark:text-sky-400 font-semibold';
    statusHint.textContent = 'Menghubungkan ke website dan mengekstrak artikel bersih...';

    try {
        const resp = await fetch('/api/materials/scrape-url', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({ url: url })
        });
        const res = await resp.json();
        if (res.success && res.data) {
            const d = res.data;
            const titleField = document.getElementById('title');
            const contentField = document.getElementById('content');
            const kwField = document.getElementById('keywords');

            if (titleField && d.title) titleField.value = d.title;
            const catField = document.getElementById('category');
            if (catField && isLiveApiCategory(catField.value)) {
                const firstRegular = catField.querySelector('optgroup[label*="Reguler"] option') || catField.querySelector('option:not([value="data dari api"])');
                if (firstRegular) {
                    catField.value = firstRegular.value;
                    syncLiveApiFields('add');
                }
            }
            if (contentField && d.content) {
                contentField.value = `Sumber: ${d.url}\n\n${d.content}`;
                updateWordCounter(contentField, 'addWordCountDisplay');
            }
            if (kwField) {
                kwField.value = d.keywords || `web, ${d.hostname || 'artikel'}`;
            }

            statusHint.className = 'text-[11px] mt-2 text-emerald-700 dark:text-emerald-400 font-semibold';
            statusHint.textContent = `Berhasil mengekstrak ${d.word_count || 0} kata dari ${d.hostname}. Judul, konten, dan kata kunci telah terisi di form bawah.`;

            if (titleField) {
                titleField.focus();
            }
        } else {
            statusHint.className = 'text-[11px] mt-2 text-rose-700 dark:text-rose-400 font-semibold';
            statusHint.textContent = 'Gagal: ' + (res.message || 'Gagal mengekstrak isi website.');
        }
    } catch (e) {
        statusHint.className = 'text-[11px] mt-2 text-rose-700 dark:text-rose-400 font-semibold';
        statusHint.textContent = 'Gagal: Terjadi kesalahan jaringan saat scrape: ' + e.message;
    } finally {
        btn.disabled = false;
        btn.innerHTML = oldBtnText;
    }
}
