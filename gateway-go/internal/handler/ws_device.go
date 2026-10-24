package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/gorilla/websocket"

	"gateway-go/internal/config"
	"gateway-go/internal/hub"
	"gateway-go/internal/store"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 1024,
	CheckOrigin: func(r *http.Request) bool {
		// Permissive origin check for embedded hardware ESP32
		return true
	},
}

type WSDeviceHandler struct {
	cfg         *config.Config
	deviceStore *store.DeviceStore
	audioStore  *store.AudioStore
	sseHub      *hub.SSEHub
	devHub      *hub.DeviceHub
}

func NewWSDeviceHandler(
	cfg *config.Config,
	deviceStore *store.DeviceStore,
	audioStore *store.AudioStore,
	sseHub *hub.SSEHub,
	devHub *hub.DeviceHub,
) *WSDeviceHandler {
	return &WSDeviceHandler{
		cfg:         cfg,
		deviceStore: deviceStore,
		audioStore:  audioStore,
		sseHub:      sseHub,
		devHub:      devHub,
	}
}

// HandleDeviceAudioWS handles persistent WebSocket connection from ESP32:
// /ws/device/audio/{device_id} and /ws/device/{device_id}
func (h *WSDeviceHandler) HandleDeviceAudioWS(w http.ResponseWriter, r *http.Request) {
	deviceID := chi.URLParam(r, "device_id")
	if deviceID == "" {
		deviceID = r.URL.Query().Get("device_id")
	}
	if deviceID == "" {
		deviceID = r.URL.Query().Get("mac")
	}
	if deviceID == "" {
		http.Error(w, "device_id or MAC is required", http.StatusBadRequest)
		return
	}

	normMAC := store.NormalizeMAC(deviceID)
	cleanMAC := store.CleanHexMAC(deviceID)
	if normMAC == "" && cleanMAC == "" {
		normMAC = strings.ToUpper(strings.TrimSpace(deviceID))
		cleanMAC = strings.ToLower(normMAC)
	}

	// Extract Chip & Token
	q := r.URL.Query()
	chip := strings.TrimSpace(q.Get("chip"))
	if chip == "" {
		chip = r.Header.Get("X-Device-Chip")
	}
	if chip == "" {
		chip = r.Header.Get("Device-Chip")
	}
	if chip == "" {
		chip = r.Header.Get("X-Chip")
	}
	if chip == "" {
		chip = r.Header.Get("X-Chip-Type")
	}
	chip = strings.ToLower(strings.TrimSpace(chip))

	token := strings.TrimSpace(q.Get("token"))
	if token == "" {
		token = r.Header.Get("X-Token")
	}
	if token == "" && strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
		token = strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	}

	// Upgrade HTTP to WebSocket
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[WS-DEVICE] Failed to upgrade WebSocket for %s: %v", deviceID, err)
		return
	}
	defer conn.Close()

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()

	// Resolve Owner ID
	ownerID, _ := h.deviceStore.ResolveOwner(ctx, token, deviceID)

	// Record activity
	_ = h.deviceStore.RecordDeviceActivity(ctx, deviceID, ownerID)

	// Register device in in-memory hub
	clientIP := r.RemoteAddr
	if realIP := r.Header.Get("X-Real-IP"); realIP != "" {
		clientIP = realIP
	}

	dev := &hub.ConnectedDevice{
		MAC:         normMAC,
		CleanMAC:    cleanMAC,
		IP:          clientIP,
		Chip:        chip,
		OwnerID:     ownerID,
		ConnectedAt: time.Now(),
		LastPing:    time.Now(),
	}
	h.devHub.Register(dev)
	defer h.devHub.Unregister(normMAC)

	log.Printf("[WS-DEVICE] ESP32 Connected: MAC=%s (Owner=%d, Chip=%s, IP=%s)", normMAC, ownerID, chip, clientIP)

	// Emit SSE broadcast event for Admin Dashboard
	h.sseHub.Broadcast("device", fmt.Sprintf("ESP32 [%s] terhubung via WebSocket persistent (Owner: %d, Chip: %s)", normMAC, ownerID, chip), map[string]any{
		"mac":      normMAC,
		"owner_id": ownerID,
		"chip":     chip,
		"ip":       clientIP,
		"action":   "connected",
	})

	// Mutex for safe concurrent writes to the WebSocket connection
	var writeMu sync.Mutex
	safeWriteMessage := func(messageType int, data []byte) error {
		writeMu.Lock()
		defer writeMu.Unlock()
		_ = conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
		return conn.WriteMessage(messageType, data)
	}

	// Ping/Pong Heartbeat configuration
	_ = conn.SetReadDeadline(time.Now().Add(75 * time.Second))
	conn.SetPongHandler(func(string) error {
		h.devHub.UpdatePing(normMAC)
		_ = conn.SetReadDeadline(time.Now().Add(75 * time.Second))
		return nil
	})

	// 1. Reader Goroutine
	go func() {
		defer cancel()
		for {
			msgType, msg, err := conn.ReadMessage()
			if err != nil {
				if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
					log.Printf("[WS-DEVICE] Read error for %s: %v", normMAC, err)
				}
				return
			}

			_ = conn.SetReadDeadline(time.Now().Add(75 * time.Second))
			h.devHub.UpdatePing(normMAC)

			if msgType == websocket.TextMessage {
				text := strings.TrimSpace(string(msg))
				lower := strings.ToLower(text)

				if strings.Contains(lower, "ping") {
					_ = safeWriteMessage(websocket.TextMessage, []byte("pong"))
					continue
				}

				// Check if payload is status report from ESP32
				if strings.Contains(lower, "status") {
					var statusReport struct {
						Status  string `json:"status"`
						VideoID string `json:"video_id"`
					}
					if err := json.Unmarshal(msg, &statusReport); err == nil && statusReport.Status != "" {
						_ = h.audioStore.UpdatePlaybackStatus(ctx, ownerID, statusReport.VideoID, statusReport.Status)
					}
				}
			}
		}
	}()

	// 2. Command Poller & Ping Ticker Loop
	cmdTicker := time.NewTicker(1 * time.Second)
	defer cmdTicker.Stop()

	pingTicker := time.NewTicker(25 * time.Second)
	defer pingTicker.Stop()

	// Base URL for audio stream
	baseURL := h.cfg.ServerBaseURL
	if baseURL == "" {
		baseURL = "http://xiaozhiscig.biz.id"
	}
	// For ESP32 boards, always force HTTP to prevent TLS memory overhead
	if strings.HasPrefix(baseURL, "https://") {
		baseURL = "http://" + strings.TrimPrefix(baseURL, "https://")
	}
	baseURL = strings.TrimRight(baseURL, "/")

	for {
		select {
		case <-ctx.Done():
			// Clean disconnect
			h.sseHub.Broadcast("device", fmt.Sprintf("ESP32 [%s] terputus dari persistent WebSocket", normMAC), map[string]any{
				"mac":      normMAC,
				"owner_id": ownerID,
				"action":   "disconnected",
			})
			log.Printf("[WS-DEVICE] ESP32 Disconnected: MAC=%s", normMAC)
			return

		case <-pingTicker.C:
			// Send WebSocket Ping to maintain connection through firewalls/proxies
			if err := safeWriteMessage(websocket.PingMessage, []byte{}); err != nil {
				log.Printf("[WS-DEVICE] Ping error for %s: %v", normMAC, err)
				return
			}

		case <-cmdTicker.C:
			// Poll pending audio commands for this device owner
			if ownerID <= 0 {
				continue
			}

			commands, err := h.audioStore.GetPendingCommands(ctx, ownerID)
			if err != nil || len(commands) == 0 {
				continue
			}

			cmd := commands[0]
			_ = h.audioStore.AckCommand(ctx, ownerID, cmd.ID)

			streamURL := fmt.Sprintf("%s/api/audio/stream/%s?mac=%s&chip=%s&owner_id=%d", baseURL, cmd.VideoID, normMAC, chip, ownerID)

			log.Printf("[WS-DEVICE] Dispatching audio command to %s: ID=%d, Title=%s", normMAC, cmd.ID, cmd.Title)

			h.sseHub.Broadcast("audio", fmt.Sprintf("Kirim audio command ke ESP32 [%s]: '%s' (ID: %d)", normMAC, cmd.Title, cmd.ID), map[string]any{
				"mac":        normMAC,
				"owner_id":   ownerID,
				"command_id": cmd.ID,
				"video_id":   cmd.VideoID,
				"title":      cmd.Title,
			})

			cmdPayload := map[string]any{
				"type":       "audio_command",
				"action":     "play",
				"command_id": cmd.ID,
				"video_id":   cmd.VideoID,
				"title":      cmd.Title,
				"stream_url": streamURL,
				"duration":   cmd.Duration,
			}

			payloadBytes, _ := json.Marshal(cmdPayload)
			if err := safeWriteMessage(websocket.TextMessage, payloadBytes); err != nil {
				log.Printf("[WS-DEVICE] Failed to send command to %s: %v", normMAC, err)
				return
			}
		}
	}
}
