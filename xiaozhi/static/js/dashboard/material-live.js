import {flashElement, liveApiMetaText, previewText} from "./material-display.js";

const liveMaterialRows = () => Array.from(document.querySelectorAll('#materialList [data-live-api="true"]'));
const liveMaterialState = new Map();
let checkingLiveMaterials = false;

async function refreshVisibleLiveMaterials() {
    if (checkingLiveMaterials || document.hidden) return;
    checkingLiveMaterials = true;
    try {
        const rows = liveMaterialRows();
        const visibleIds = new Set(rows.map((row) => row.dataset.materialId));
        for (const id of liveMaterialState.keys()) {
            if (!visibleIds.has(id)) liveMaterialState.delete(id);
        }
        for (const row of rows) {
            if (row.isConnected) await refreshSavedLiveMaterial(row);
        }
    } finally {
        checkingLiveMaterials = false;
    }
}

function applyLiveApiUpdate(payload) {
    const materialId = String(payload.material_id || payload.id || "");
    if (!/^\d+$/.test(materialId)) return;

    const row = document.querySelector(`#materialList tr[data-material-id="${materialId}"]`);
    if (!row) return;
    const previous = liveMaterialState.get(materialId);
    const state = {
        hash: payload.content_hash || "",
        preview: payload.content ? previewText(payload.content) : previous?.preview,
        label: payload.changed === false && previous ? previous.label : liveApiMetaText(payload),
    };
    liveMaterialState.set(materialId, state);

    const contentEl = document.getElementById(`materialContent${materialId}`);
    const statusEl = document.getElementById(`liveApiRowStatus${materialId}`);

    if (contentEl && state.preview !== undefined) {
        contentEl.textContent = state.preview;
    }
    if (statusEl) {
        statusEl.textContent = state.label;
        statusEl.className = "font-semibold text-emerald-700 dark:text-emerald-400";
    }
    if (previous?.hash && previous.hash !== state.hash) {
        flashElement(row);
        if (contentEl) flashElement(contentEl);
    }
}

// Single on-demand refresh helper (for initial load or fallback)
async function refreshSavedLiveMaterial(row) {
    if (document.hidden) return;
    const materialId = row.dataset.materialId;
    if (!materialId) return;
    try {
        const previousHash = liveMaterialState.get(materialId)?.hash || "";
        const query = previousHash ? `?last_hash=${encodeURIComponent(previousHash)}` : "";
        const response = await fetch(`/api/materials/live-api/${encodeURIComponent(materialId)}${query}`, {
            headers: { "Accept": "application/json" },
            credentials: "same-origin",
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || "Gagal membaca live API.");
        if (row.isConnected) {
            if (!payload.changed) delete payload.content;
            payload.material_id = materialId;
            applyLiveApiUpdate(payload);
        }
    } catch (error) {
        const statusEl = document.getElementById(`liveApiRowStatus${materialId}`);
        if (row.isConnected && statusEl) {
            statusEl.textContent = error.message || "Live API gagal dibaca.";
            statusEl.className = "font-semibold text-red-700";
        }
    }
}

function connectMaterialEvents(sseToken) {
    if (!window.EventSource) {
        console.warn("EventSource tidak didukung browser ini. Menggunakan fallback polling.");
        return;
    }

    const pill = document.getElementById("materialSseStatusPill");
    const pillText = document.getElementById("materialSseStatusText");

    let es = null;

    function markOnline() {
        if (pill) {
            pill.classList.remove("is-offline", "is-connecting");
            pill.classList.add("is-online");
            pill.title = "Monitoring real-time database materi aktif (SSE)";
        }
        if (pillText) pillText.textContent = "SSE Realtime Aktif";
    }

    function markOffline(label) {
        if (pill) {
            pill.classList.remove("is-online");
            pill.classList.add("is-offline");
            pill.title = "Koneksi realtime terputus, mencoba menghubungkan kembali...";
        }
        if (pillText) pillText.textContent = label || "Menghubungkan...";
    }

    function connect() {
        if (es) {
            try { es.close(); } catch(e) {}
        }

        const sseUrl = sseToken ? ("/api/events/stream?token=" + encodeURIComponent(sseToken)) : "/api/materials/stream-events?view=dashboard";
        es = new EventSource(sseUrl);

        es.onopen = markOnline;
        es.addEventListener("open", markOnline);
        es.addEventListener("connected", markOnline);

        es.addEventListener("live_api_update", (evt) => {
            markOnline();
            try {
                const data = JSON.parse(evt.data);
                applyLiveApiUpdate(data);
            } catch(e) {
                console.debug("Error parsing live_api_update:", e);
            }
        });

        ["material_created", "material_updated", "material_deleted"].forEach((eventName) => {
            es.addEventListener(eventName, () => window.dispatchEvent(new Event("material-list-changed")));
        });

        es.onerror = () => {
            if (es.readyState === EventSource.CONNECTING) {
                markOffline("Menghubungkan...");
            } else if (es.readyState === EventSource.CLOSED) {
                markOffline("Menghubungkan...");
                setTimeout(connect, 2000);
            }
        };
    }

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            if (!es || es.readyState === EventSource.CLOSED) {
                connect();
            }
        }
    });

    connect();
}

export function initMaterialLive(sseToken) {
    window.addEventListener("material-list-rendered", refreshVisibleLiveMaterials);
    // Poll only on-page Live API rows. SSE invalidates the list when materials change.
    window.setInterval(refreshVisibleLiveMaterials, 15000);
    refreshVisibleLiveMaterials();
    connectMaterialEvents(sseToken);
}
