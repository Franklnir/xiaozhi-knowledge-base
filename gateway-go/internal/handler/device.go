package handler

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"gateway-go/internal/config"
	"gateway-go/internal/store"
)

type DeviceHandler struct {
	cfg         *config.Config
	deviceStore *store.DeviceStore
	audioStore  *store.AudioStore
}

func NewDeviceHandler(cfg *config.Config, deviceStore *store.DeviceStore, audioStore *store.AudioStore) *DeviceHandler {
	return &DeviceHandler{
		cfg:         cfg,
		deviceStore: deviceStore,
		audioStore:  audioStore,
	}
}

type FormattedCommand struct {
	ID        int64  `json:"id"`
	OwnerID   int64  `json:"owner_id"`
	Title     string `json:"title"`
	StreamURL string `json:"stream_url"`
	VideoURL  string `json:"video_url"`
	Duration  string `json:"duration"`
	VideoID   string `json:"video_id"`
	Status    string `json:"status"`
	CreatedAt string `json:"created_at"`
}

type CommandsResponse struct {
	Success   bool               `json:"success"`
	Commands  []FormattedCommand `json:"commands"`
	Chip      string             `json:"chip"`
	MacStatus *string            `json:"mac_status"`
}

// HandleGetCommands handles GET /api/device/audio/commands
func (h *DeviceHandler) HandleGetCommands(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	token := strings.TrimSpace(q.Get("token"))
	mac := strings.TrimSpace(q.Get("mac"))
	chip := strings.TrimSpace(q.Get("chip"))

	if mac == "" {
		mac = r.Header.Get("Device-Id")
	}
	if mac == "" {
		mac = r.Header.Get("X-Device-Mac")
	}
	if mac == "" {
		mac = r.Header.Get("X-MAC-Address")
	}

	detectedChip := chip
	if detectedChip == "" {
		detectedChip = r.Header.Get("X-Device-Chip")
	}
	if detectedChip == "" {
		detectedChip = r.Header.Get("Device-Chip")
	}
	if detectedChip == "" {
		detectedChip = r.Header.Get("X-Chip")
	}
	detectedChip = strings.ToLower(strings.TrimSpace(detectedChip))

	ctx := r.Context()
	ownerID, err := h.deviceStore.ResolveOwner(ctx, token, mac)
	if err != nil || ownerID == 0 {
		writeJSON(w, http.StatusOK, CommandsResponse{
			Success:   true,
			Commands:  []FormattedCommand{},
			Chip:      detectedChip,
			MacStatus: nil,
		})
		return
	}

	commands, err := h.audioStore.GetPendingCommands(ctx, ownerID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	baseURL := h.cfg.ServerBaseURL
	if baseURL == "" {
		scheme := "http"
		if r.TLS != nil || r.Header.Get("X-Forwarded-Proto") == "https" {
			scheme = "https"
		}
		baseURL = fmt.Sprintf("%s://%s", scheme, r.Host)
	}
	baseURL = strings.TrimRight(baseURL, "/")

	formatted := make([]FormattedCommand, 0, len(commands))
	for _, cmd := range commands {
		surl := cmd.StreamURL
		if strings.HasPrefix(surl, "/") {
			surl = fmt.Sprintf("%s%s", baseURL, surl)
		}
		sep := "?"
		if strings.Contains(surl, "?") {
			sep = "&"
		}

		if !strings.Contains(surl, "owner_id=") && ownerID > 0 {
			surl = fmt.Sprintf("%s%sowner_id=%d", surl, sep, ownerID)
			sep = "&"
		}
		if !strings.Contains(surl, "mac=") && mac != "" {
			surl = fmt.Sprintf("%s%smac=%s", surl, sep, mac)
			sep = "&"
		}
		if !strings.Contains(surl, "chip=") && detectedChip != "" {
			surl = fmt.Sprintf("%s%schip=%s", surl, sep, detectedChip)
		}

		formatted = append(formatted, FormattedCommand{
			ID:        cmd.ID,
			OwnerID:   cmd.OwnerID,
			Title:     cmd.Title,
			StreamURL: surl,
			VideoURL:  cmd.VideoURL,
			Duration:  cmd.Duration,
			VideoID:   cmd.VideoID,
			Status:    cmd.Status,
			CreatedAt: cmd.CreatedAt.Format("2006-01-02 15:04:05"),
		})
	}

	macStatus := "Tersimpan OK"
	writeJSON(w, http.StatusOK, CommandsResponse{
		Success:   true,
		Commands:  formatted,
		Chip:      detectedChip,
		MacStatus: &macStatus,
	})
}

type AckPayload struct {
	Token     string `json:"token"`
	CommandID any    `json:"command_id"`
	Mac       string `json:"mac"`
}

// HandleAck handles POST /api/device/audio/ack
func (h *DeviceHandler) HandleAck(w http.ResponseWriter, r *http.Request) {
	var payload AckPayload
	if strings.Contains(r.Header.Get("Content-Type"), "application/json") {
		_ = json.NewDecoder(r.Body).Decode(&payload)
	} else {
		_ = r.ParseForm()
		payload.Token = r.FormValue("token")
		payload.CommandID = r.FormValue("command_id")
		payload.Mac = r.FormValue("mac")
	}

	mac := payload.Mac
	if mac == "" {
		mac = r.Header.Get("Device-Id")
	}

	ctx := r.Context()
	ownerID, _ := h.deviceStore.ResolveOwner(ctx, payload.Token, mac)

	var cmdID int64
	switch v := payload.CommandID.(type) {
	case float64:
		cmdID = int64(v)
	case string:
		cmdID, _ = strconv.ParseInt(v, 10, 64)
	}

	if ownerID > 0 && cmdID > 0 {
		_ = h.audioStore.AckCommand(ctx, ownerID, cmdID)
		writeJSON(w, http.StatusOK, map[string]any{
			"success": true,
			"message": "Command acknowledged",
		})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"success": true,
	})
}

type StatusPayload struct {
	Status  string `json:"status"`
	Mac     string `json:"mac"`
	VideoID string `json:"video_id"`
	Token   string `json:"token"`
}

// HandleStatus handles POST /api/device/audio/status
func (h *DeviceHandler) HandleStatus(w http.ResponseWriter, r *http.Request) {
	var payload StatusPayload
	if strings.Contains(r.Header.Get("Content-Type"), "application/json") {
		_ = json.NewDecoder(r.Body).Decode(&payload)
	} else {
		_ = r.ParseForm()
		payload.Status = r.FormValue("status")
		payload.Mac = r.FormValue("mac")
		if payload.Mac == "" {
			payload.Mac = r.FormValue("device_id")
		}
		payload.VideoID = r.FormValue("video_id")
		payload.Token = r.FormValue("token")
	}

	status := strings.ToLower(strings.TrimSpace(payload.Status))
	if status == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"success": false,
			"error":   "status parameter is required",
		})
		return
	}

	mac := payload.Mac
	if mac == "" {
		mac = r.Header.Get("Device-Id")
	}

	ctx := r.Context()
	ownerID, _ := h.deviceStore.ResolveOwner(ctx, payload.Token, mac)
	_ = h.audioStore.UpdatePlaybackStatus(ctx, ownerID, payload.VideoID, status)

	writeJSON(w, http.StatusOK, map[string]any{
		"success": true,
	})
}

func writeJSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(data)
}
