export function previewText(value, maxLength = 160) {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength - 3).trim()}...`;
}

function formatLiveTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "-";
    return date.toLocaleTimeString("id-ID", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
    });
}

function formatSizeLabel(payload) {
    if (payload.content_size_label) return payload.content_size_label;
    const sizeBytes = Number(payload.content_size_bytes || 0);
    return `${(sizeBytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function liveApiMetaText(payload) {
    const total = payload.live_total_items ?? payload.total_items ?? 0;
    const updatedAt = payload.live_updated_at || payload.updated_at;
    return `Update ${formatLiveTime(updatedAt)} | Size ${formatSizeLabel(payload)} | ${total} record`;
}

export function flashElement(element) {
    if (!element) return;
    element.classList.remove("app-live-flash");
    void element.offsetWidth;
    element.classList.add("app-live-flash");
}
