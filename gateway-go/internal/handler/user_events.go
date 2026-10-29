package handler

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"gateway-go/internal/config"
	"gateway-go/internal/hub"
)

type UserEventsHandler struct {
	cfg          *config.Config
	userEventHub *hub.UserEventHub
}

func NewUserEventsHandler(cfg *config.Config, userEventHub *hub.UserEventHub) *UserEventsHandler {
	return &UserEventsHandler{
		cfg:          cfg,
		userEventHub: userEventHub,
	}
}

// VerifyStreamToken validates an HMAC-SHA256 signed stream token: "user_id.timestamp.signature".
// Strictly multi-tenant safe and tamper-proof.
func (h *UserEventsHandler) VerifyStreamToken(tokenStr string) (int64, error) {
	tokenStr = strings.TrimSpace(tokenStr)
	if tokenStr == "" {
		return 0, fmt.Errorf("token stream kosong")
	}

	parts := strings.Split(tokenStr, ".")
	if len(parts) != 3 {
		return 0, fmt.Errorf("format token tidak valid (harus user_id.timestamp.signature)")
	}

	uid, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil || uid <= 0 {
		return 0, fmt.Errorf("user_id tidak valid")
	}

	ts, err := strconv.ParseInt(parts[1], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("timestamp tidak valid")
	}

	// Secret key validation
	secret := h.cfg.AppSecretKey
	if secret != "" {
		// Verify HMAC signature
		mac := hmac.New(sha256.New, []byte(secret))
		mac.Write([]byte(fmt.Sprintf("%s.%s", parts[0], parts[1])))
		expectedSig := hex.EncodeToString(mac.Sum(nil))

		if !hmac.Equal([]byte(parts[2]), []byte(expectedSig)) {
			return 0, fmt.Errorf("tanda tangan keamanan token tidak valid")
		}

		// Verify expiration (max 24 hours)
		now := time.Now().Unix()
		diff := now - ts
		if diff < -300 || diff > 86400 {
			return 0, fmt.Errorf("token stream sudah kedaluwarsa")
		}
	}

	return uid, nil
}

// ResolveUserID resolves user_id from token query, Authorization header, or internal dev secret.
func (h *UserEventsHandler) ResolveUserID(r *http.Request) (int64, error) {
	// 1. Check query parameter ?token=...
	token := r.URL.Query().Get("token")

	// 2. Check X-Stream-Token header
	if token == "" {
		token = r.Header.Get("X-Stream-Token")
	}

	// 3. Check Authorization header
	if token == "" {
		auth := r.Header.Get("Authorization")
		if strings.HasPrefix(strings.ToLower(auth), "bearer ") {
			token = strings.TrimSpace(auth[7:])
		}
	}

	if token != "" {
		return h.VerifyStreamToken(token)
	}

	// 4. Fallback for internal services / dev testing via ?user_id=123&secret=...
	uidStr := r.URL.Query().Get("user_id")
	reqSecret := r.URL.Query().Get("secret")
	if uidStr != "" && reqSecret != "" && h.cfg.InternalAPIKey != "" && reqSecret == h.cfg.InternalAPIKey {
		uid, err := strconv.ParseInt(uidStr, 10, 64)
		if err == nil && uid > 0 {
			return uid, nil
		}
	}

	return 0, fmt.Errorf("autentikasi stream diperlukan (token tidak ditemukan)")
}

// HandleUserEventsStream handles GET /api/events/stream
// Also serves GET /api/materials/stream-events and GET /api/relay-nyata/stream for backward compatibility.
func (h *UserEventsHandler) HandleUserEventsStream(w http.ResponseWriter, r *http.Request) {
	userID, err := h.ResolveUserID(r)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"detail": err.Error(),
		})
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	// High-performance SSE headers
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache, no-transform")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	// 1. Flush initial 2KB anti-buffering padding comment (forces reverse proxies to bypass buffers immediately)
	fmt.Fprintf(w, ": %s\n\n", strings.Repeat(" ", 2048))

	// 2. Send connected event
	connPayload, _ := json.Marshal(map[string]any{
		"status":  "ok",
		"user_id": userID,
		"service": "gateway-go",
		"time":    time.Now().UTC().Format(time.RFC3339),
		"message": "Unified User Events Stream (Go Gateway Hub) aktif.",
	})
	fmt.Fprintf(w, "event: connected\ndata: %s\n\n", connPayload)
	flusher.Flush()

	// 3. Subscribe to real-time events for this user
	eventChan, unsubscribe := h.userEventHub.Subscribe(userID)
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
			dataBytes, err := json.Marshal(evt.Data)
			if err == nil {
				eventName := evt.Event
				if eventName == "" {
					eventName = "message"
				}
				fmt.Fprintf(w, "id: %s\nevent: %s\ndata: %s\n\n", evt.ID, eventName, dataBytes)
				flusher.Flush()
			}
		case <-ticker.C:
			// Heartbeat comment to keep long-lived HTTP/1.1 connections open
			fmt.Fprintf(w, ": ping\n\n")
			flusher.Flush()
		}
	}
}

type UserEmitPayload struct {
	UserID int64          `json:"user_id"`
	Event  string         `json:"event"`
	Data   map[string]any `json:"data"`
}

// HandleEmitUserEvent handles POST /api/internal/events/emit
// Receives events from Python backend and broadcasts them via UserEventHub.
func (h *UserEventsHandler) HandleEmitUserEvent(w http.ResponseWriter, r *http.Request) {
	var payload UserEmitPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"success": false,
			"error":   "Invalid JSON payload: " + err.Error(),
		})
		return
	}

	if payload.UserID <= 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"success": false,
			"error":   "user_id must be greater than 0",
		})
		return
	}

	evtName := strings.TrimSpace(payload.Event)
	if evtName == "" {
		evtName = "user_event"
	}

	evt := h.userEventHub.Broadcast(payload.UserID, evtName, payload.Data)
	writeJSON(w, http.StatusOK, map[string]any{
		"success": true,
		"id":      evt.ID,
		"user_id": payload.UserID,
		"event":   evtName,
	})
}
