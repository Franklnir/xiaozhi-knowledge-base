package store

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"

	"gateway-go/internal/db"
)

type DeviceStore struct {
	db *db.DB
}

func NewDeviceStore(db *db.DB) *DeviceStore {
	return &DeviceStore{db: db}
}

// XiaozhiTokenHash computes the 16-character SHA-256 token hash matching Python backend
func XiaozhiTokenHash(token string) string {
	if token == "" {
		return ""
	}
	h := sha256.Sum256([]byte(token))
	full := hex.EncodeToString(h[:])
	if len(full) > 16 {
		return full[:16]
	}
	return full
}

// NormalizeMAC standardizes MAC addresses into XX:XX:XX:XX:XX:XX format
func NormalizeMAC(mac string) string {
	mac = strings.TrimSpace(mac)
	if strings.HasPrefix(strings.ToLower(mac), "esp32-") {
		mac = mac[6:]
	}
	// remove all colons and hyphens
	clean := strings.ReplaceAll(strings.ReplaceAll(mac, ":", ""), "-", "")
	if len(clean) != 12 {
		return strings.ToUpper(mac)
	}
	var sb strings.Builder
	for i := 0; i < 12; i += 2 {
		if i > 0 {
			sb.WriteString(":")
		}
		sb.WriteString(clean[i : i+2])
	}
	return strings.ToUpper(sb.String())
}

// CleanHexMAC returns the raw 12-char hex lowercase MAC
func CleanHexMAC(mac string) string {
	mac = strings.TrimSpace(mac)
	if strings.HasPrefix(strings.ToLower(mac), "esp32-") {
		mac = mac[6:]
	}
	return strings.ToLower(strings.ReplaceAll(strings.ReplaceAll(mac, ":", ""), "-", ""))
}

// ResolveOwner resolves user owner_id based on token or ESP32 device MAC
func (s *DeviceStore) ResolveOwner(ctx context.Context, token, rawMAC string) (int64, error) {
	conn, err := s.db.Pool.Acquire(ctx)
	if err != nil {
		return 0, fmt.Errorf("acquire conn error: %w", err)
	}
	defer conn.Release()

	// Bypass RLS as system
	_, _ = conn.Exec(ctx, "SELECT set_config('app.user_role', 'system', true), set_config('app.is_admin', 'true', true);")

	// 1. By Token
	if token != "" {
		tHash := XiaozhiTokenHash(token)
		var ownerID int64
		err := conn.QueryRow(ctx, "SELECT user_id FROM xiaozhi_tokens WHERE token_hash = $1 LIMIT 1", tHash).Scan(&ownerID)
		if err == nil && ownerID > 0 {
			return ownerID, nil
		}
	}

	normMAC := NormalizeMAC(rawMAC)
	hexMAC := CleanHexMAC(rawMAC)

	if normMAC != "" {
		// 2. Active Slot in xiaozhi_tokens
		var ownerID int64
		err := conn.QueryRow(ctx, `
			SELECT user_id FROM xiaozhi_tokens
			WHERE (LOWER(board_mac) = LOWER($1) OR LOWER(REPLACE(REPLACE(board_mac, ':', ''), '-', '')) = $2)
			  AND COALESCE(is_active, TRUE) = TRUE
			ORDER BY updated_at DESC LIMIT 1
		`, normMAC, hexMAC).Scan(&ownerID)
		if err == nil && ownerID > 0 {
			return ownerID, nil
		}

		// 3. Registered Devices
		err = conn.QueryRow(ctx, `
			SELECT owner_id FROM registered_devices
			WHERE (LOWER(device_id) = LOWER($1) OR LOWER(REPLACE(REPLACE(device_id, ':', ''), '-', '')) = $2)
			  AND (status IS NULL OR status != 'DETACHED')
			ORDER BY id DESC LIMIT 1
		`, normMAC, hexMAC).Scan(&ownerID)
		if err == nil && ownerID > 0 {
			return ownerID, nil
		}
	}

	// 4. Fallback: Check recent pending command in last 2 minutes
	var fallbackOwnerID int64
	err = conn.QueryRow(ctx, `
		SELECT owner_id FROM audio_queue
		WHERE status = 'pending' AND created_at >= NOW() - INTERVAL '2 minutes'
		ORDER BY id DESC LIMIT 1
	`).Scan(&fallbackOwnerID)
	if err == nil && fallbackOwnerID > 0 {
		return fallbackOwnerID, nil
	}

	return 0, nil
}
