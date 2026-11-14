import {initMaterialDialogs} from "./dashboard/material-dialogs.js";
import {initMaterialLive} from "./dashboard/material-live.js";

(() => {
    const form = document.getElementById("materialSearchForm");
    const list = document.getElementById("materialList");
    const status = document.getElementById("materialListStatus");
    const search = document.getElementById("searchInput");
    const category = document.getElementById("filterCategory");
    const clear = document.getElementById("clearSearchBtn");
    let pending;
    let refreshTimer;

    function currentParams() {
        return new URLSearchParams(window.location.search);
    }

    async function load(params, updateHistory) {
        pending?.abort();
        const controller = new AbortController();
        pending = controller;
        list.setAttribute("aria-busy", "true");
        status.textContent = "Memuat materi…";
        try {
            const response = await fetch(`/api/materials/list?${params}`, {signal: controller.signal});
            if (!response.ok) throw new Error(errorMessage(response));
            const html = await response.text();
            if (pending !== controller) return;
            list.innerHTML = html;
            const state = list.firstElementChild.dataset;
            document.getElementById("badgeRowCount").textContent = `${state.total} item`;
            for (const [name, value] of Object.entries(JSON.parse(state.stats))) {
                const stat = document.querySelector(`[data-material-stat="${name}"]`);
                if (stat) stat.textContent = value;
            }
            params.set("page", state.page);
            params.delete("message");
            if (updateHistory) history.pushState(null, "", `/dashboard?${params}`);
            status.textContent = "";
            window.dispatchEvent(new Event("material-list-rendered"));
        } catch (error) {
            if (pending === controller && error.name !== "AbortError") {
                status.textContent = error.message;
            }
        } finally {
            if (pending === controller) {
                pending = null;
                list.removeAttribute("aria-busy");
            }
        }
    }

    function searchMaterials() {
        clear.style.display = search.value ? "block" : "none";
        load(new URLSearchParams(new FormData(form)), true);
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        searchMaterials();
    });
    category.addEventListener("change", searchMaterials);
    clear.addEventListener("click", () => {
        search.value = "";
        search.focus();
        searchMaterials();
    });
    search.addEventListener("input", () => {
        clear.style.display = search.value ? "block" : "none";
    });
    list.addEventListener("click", (event) => {
        const link = event.target.closest("a[data-page]");
        if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        load(new URL(link.href).searchParams, true);
    });
    window.addEventListener("popstate", () => {
        const params = currentParams();
        search.value = params.get("q") || "";
        category.value = params.get("category") || "";
        clear.style.display = search.value ? "block" : "none";
        load(params, false);
    });
    window.addEventListener("material-list-changed", () => {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => {
            if (pending) {
                window.dispatchEvent(new Event("material-list-changed"));
                return;
            }
            load(currentParams(), false);
        }, 300);
    });

    function errorMessage(response) {
        const requestId = response.headers.get("X-Request-ID");
        const message = response.status === 401 ? "Sesi berakhir. Silakan masuk kembali."
            : response.status === 404 ? "Materi tidak ditemukan." : "Materi gagal dimuat. Silakan coba lagi.";
        return requestId ? `${message} ID request ${requestId}` : message;
    }

    async function fetchMaterialDetail(button) {
        button.disabled = true;
        status.textContent = "Memuat isi materi…";
        try {
            const response = await fetch(`/api/materials/detail/${encodeURIComponent(button.dataset.materialId)}`);
            if (!response.ok) throw new Error(errorMessage(response));
            return await response.json();
        } finally {
            button.disabled = false;
            status.textContent = "";
        }
    }

    initMaterialDialogs(fetchMaterialDetail);
    initMaterialLive(document.querySelector(".dashboard-container").dataset.materialStreamToken);
})();
