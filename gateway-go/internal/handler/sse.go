package handler

import (
	"encoding/json"
	"fmt"
	"net/http"
	"runtime"
	"strings"
	"time"

	"gateway-go/internal/config"
	"gateway-go/internal/hub"
)

type SSEHandler struct {
	cfg          *config.Config
	sseHub       *hub.SSEHub
	userEventHub *hub.UserEventHub
	deviceHub    *hub.DeviceHub
	startTime    time.Time
}

func NewSSEHandler(cfg *config.Config, sseHub *hub.SSEHub, userEventHub *hub.UserEventHub, deviceHub *hub.DeviceHub) *SSEHandler {
	return &SSEHandler{
		cfg:          cfg,
		sseHub:       sseHub,
		userEventHub: userEventHub,
		deviceHub:    deviceHub,
		startTime:    time.Now(),
	}
}

// HandleLogsStream handles GET /admin/api/logs/stream
func (h *SSEHandler) HandleLogsStream(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache, no-transform")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	// 1. Send recent backlog (last 25 logs)
	recent := h.sseHub.GetRecent(25)
	for _, evt := range recent {
		data, err := json.Marshal(evt)
		if err == nil {
			fmt.Fprintf(w, "id: %s\nevent: log\ndata: %s\n\n", evt.ID, data)
		}
	}

	// 2. Send connected event
	connMsg, _ := json.Marshal(map[string]any{
		"status":  "ok",
		"message": "Live Log Stream (Go Hub) aktif.",
	})
	fmt.Fprintf(w, "event: connected\ndata: %s\n\n", connMsg)
	flusher.Flush()

	// 3. Subscribe to real-time events
	eventChan, unsubscribe := h.sseHub.Subscribe()
	defer unsubscribe()

	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case evt, ok := <-eventChan:
			if !ok {
				return
			}
			data, err := json.Marshal(evt)
			if err == nil {
				fmt.Fprintf(w, "id: %s\nevent: log\ndata: %s\n\n", evt.ID, data)
				flusher.Flush()
			}
		case <-ticker.C:
			// Heartbeat comment
			fmt.Fprintf(w, ": ping\n\n")
			flusher.Flush()
		}
	}
}

type EmitPayload struct {
	Category string         `json:"category"`
	Message  string         `json:"message"`
	Metadata map[string]any `json:"metadata"`
}

// HandleInternalEmit handles POST /api/internal/sse/emit
func (h *SSEHandler) HandleInternalEmit(w http.ResponseWriter, r *http.Request) {
	var payload EmitPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"success": false,
			"error":   "Invalid JSON payload: " + err.Error(),
		})
		return
	}

	cat := strings.TrimSpace(payload.Category)
	if cat == "" {
		cat = "system"
	}
	msg := strings.TrimSpace(payload.Message)
	if msg == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"success": false,
			"error":   "Message cannot be empty",
		})
		return
	}

	evt := h.sseHub.Broadcast(cat, msg, payload.Metadata)
	writeJSON(w, http.StatusOK, map[string]any{
		"success": true,
		"id":      evt.ID,
	})
}

// HandleSystemStatus handles GET /api/internal/status
func (h *SSEHandler) HandleSystemStatus(w http.ResponseWriter, r *http.Request) {
	uptime := time.Since(h.startTime).Round(time.Second).String()
	var m runtime.MemStats
	runtime.ReadMemStats(&m)

	userSubs := 0
	activeUsers := 0
	if h.userEventHub != nil {
		userSubs = h.userEventHub.TotalSubscribers()
		activeUsers = h.userEventHub.ActiveUserCount()
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":               "ok",
		"service":              "xiaozhi-gateway-go",
		"uptime":               uptime,
		"active_devices":       h.deviceHub.Count(),
		"sse_subscribers":      h.sseHub.SubscriberCount(),
		"user_sse_subscribers": userSubs,
		"active_sse_users":     activeUsers,
		"goroutines":           runtime.NumGoroutine(),
		"memory_alloc_mb":      float64(m.Alloc) / 1024 / 1024,
	})
}

// HandleDeviceStats handles GET /api/internal/device/stats
func (h *SSEHandler) HandleDeviceStats(w http.ResponseWriter, r *http.Request) {
	devices := h.deviceHub.List()
	writeJSON(w, http.StatusOK, map[string]any{
		"success": true,
		"count":   len(devices),
		"devices": devices,
	})
}
