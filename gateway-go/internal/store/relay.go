package store

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"strings"
	"sync"
	"time"

	"gateway-go/internal/db"
)

type RelayRoomInfo struct {
	ID               int64      `json:"id"`
	OwnerID          int64      `json:"owner_id"`
	NamaTempat       string     `json:"nama_tempat"`
	APISlug          string     `json:"api_slug"`
	APITokenHash     string     `json:"api_token_hash"`
	APIClientID      string     `json:"api_client_id"`
	DeviceLastSeenAt *time.Time `json:"device_last_seen_at"`
	CachedAt         time.Time  `json:"-"`
}

type RelayDeviceInfo struct {
	RelayNumber int    `json:"relay"`
	NamaRelay   string `json:"name"`
	Status      string `json:"status"`
	Desired     string `json:"desired_status"`
}

type RelayStore struct {
	db              *db.DB
	slugCacheMu     sync.RWMutex
	slugCache       map[string]*RelayRoomInfo
	lastSeenTouchMu sync.Mutex
	lastSeenTouch   map[int64]time.Time
}

func NewRelayStore(database *db.DB) *RelayStore {
	return &RelayStore{
		db:            database,
		slugCache:     make(map[string]*RelayRoomInfo),
		lastSeenTouch: make(map[int64]time.Time),
	}
}

// NormalizeRelayState strictly validates status to either "ON" or "OFF"
func NormalizeRelayState(st string) string {
	s := strings.ToUpper(strings.TrimSpace(st))
	if s == "ON" || s == "1" || s == "TRUE" || s == "NYALA" || s == "HIDUP" {
		return "ON"
	}
	return "OFF"
}

// GetRoomBySlug finds a relay room by its unique slug (with 30-second in-memory caching)
func (s *RelayStore) GetRoomBySlug(ctx context.Context, slug string) (*RelayRoomInfo, error) {
	slug = strings.TrimSpace(slug)
	if slug == "" {
		return nil, fmt.Errorf("slug kosong")
	}

	// 1. Check in-memory cache
	s.slugCacheMu.RLock()
	cached, found := s.slugCache[slug]
	s.slugCacheMu.RUnlock()

	if found && cached != nil && time.Since(cached.CachedAt) < 30*time.Second {
		return cached, nil
	}

	// 2. Query Postgres
	conn, err := s.db.Pool.Acquire(ctx)
	if err != nil {
		return nil, fmt.Errorf("gagal acquire database connection: %w", err)
	}
	defer conn.Release()

	// Set RLS system context
	tx, err := conn.Begin(ctx)
	if err != nil {
		return nil, fmt.Errorf("gagal begin tx: %w", err)
	}
	defer tx.Rollback(ctx)

	if err := s.db.SetSystemContext(ctx, tx); err != nil {
		return nil, fmt.Errorf("gagal set system context: %w", err)
	}

	var room RelayRoomInfo
	query := `
		SELECT id, owner_id, nama_tempat, api_slug, COALESCE(api_token_hash, ''), COALESCE(api_client_id, ''), device_last_seen_at
		FROM relay_rooms
		WHERE api_slug = $1
		LIMIT 1;
	`
	err = tx.QueryRow(ctx, query, slug).Scan(
		&room.ID,
		&room.OwnerID,
		&room.NamaTempat,
		&room.APISlug,
		&room.APITokenHash,
		&room.APIClientID,
		&room.DeviceLastSeenAt,
	)
	if err != nil {
		return nil, err
	}

	room.CachedAt = time.Now()

	// Update cache
	s.slugCacheMu.Lock()
	s.slugCache[slug] = &room
	s.slugCacheMu.Unlock()

	return &room, nil
}

// InvalidateSlugCache evicts a slug from cache if room was modified
func (s *RelayStore) InvalidateSlugCache(slug string) {
	s.slugCacheMu.Lock()
	delete(s.slugCache, slug)
	s.slugCacheMu.Unlock()
}

// VerifyToken compares sha256(token) against room.APITokenHash using constant-time comparison
func (s *RelayStore) VerifyToken(room *RelayRoomInfo, token string) bool {
	if room == nil || token == "" {
		return false
	}
	h := sha256.Sum256([]byte(token))
	hashHex := hex.EncodeToString(h[:])

	if room.APITokenHash != "" {
		return subtle.ConstantTimeCompare([]byte(hashHex), []byte(room.APITokenHash)) == 1
	}
	return false
}

// GetRoomRelays retrieves all relay channels for a room
func (s *RelayStore) GetRoomRelays(ctx context.Context, roomID int64) ([]RelayDeviceInfo, error) {
	conn, err := s.db.Pool.Acquire(ctx)
	if err != nil {
		return nil, fmt.Errorf("gagal acquire database connection: %w", err)
	}
	defer conn.Release()

	tx, err := conn.Begin(ctx)
	if err != nil {
		return nil, fmt.Errorf("gagal begin tx: %w", err)
	}
	defer tx.Rollback(ctx)

	if err := s.db.SetSystemContext(ctx, tx); err != nil {
		return nil, fmt.Errorf("gagal set system context: %w", err)
	}

	query := `
		SELECT relay_number, COALESCE(nama_relay, ''), status
		FROM relay_devices
		WHERE room_id = $1
		ORDER BY relay_number ASC;
	`
	rows, err := tx.Query(ctx, query, roomID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var relays []RelayDeviceInfo
	for rows.Next() {
		var r RelayDeviceInfo
		if err := rows.Scan(&r.RelayNumber, &r.NamaRelay, &r.Status); err != nil {
			return nil, err
		}
		if r.NamaRelay == "" {
			r.NamaRelay = fmt.Sprintf("Relay %d", r.RelayNumber)
		}
		r.Desired = r.Status
		relays = append(relays, r)
	}

	return relays, rows.Err()
}

// TouchDeviceLastSeen updates updated_at and device_last_seen_at (throttled to at most once per 4 seconds)
func (s *RelayStore) TouchDeviceLastSeen(ctx context.Context, roomID int64) {
	now := time.Now()
	s.lastSeenTouchMu.Lock()
	lastTouch, exists := s.lastSeenTouch[roomID]
	if exists && now.Sub(lastTouch) < 4*time.Second {
		s.lastSeenTouchMu.Unlock()
		return
	}
	s.lastSeenTouch[roomID] = now
	s.lastSeenTouchMu.Unlock()

	go func() {
		bgCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()

		conn, err := s.db.Pool.Acquire(bgCtx)
		if err != nil {
			return
		}
		defer conn.Release()

		tx, err := conn.Begin(bgCtx)
		if err != nil {
			return
		}
		defer tx.Rollback(bgCtx)

		if err := s.db.SetSystemContext(bgCtx, tx); err != nil {
			return
		}

		query := `UPDATE relay_rooms SET updated_at = NOW(), device_last_seen_at = NOW() WHERE id = $1;`
		_, _ = tx.Exec(bgCtx, query, roomID)
		_ = tx.Commit(bgCtx)
	}()
}

// UpdateRelayStatus updates a single relay's status in PostgreSQL
func (s *RelayStore) UpdateRelayStatus(ctx context.Context, roomID int64, relayNum int, status string) error {
	statusClean := NormalizeRelayState(status)

	conn, err := s.db.Pool.Acquire(ctx)
	if err != nil {
		return fmt.Errorf("database acquire error: %w", err)
	}
	defer conn.Release()

	tx, err := conn.Begin(ctx)
	if err != nil {
		return fmt.Errorf("tx begin error: %w", err)
	}
	defer tx.Rollback(ctx)

	if err := s.db.SetSystemContext(ctx, tx); err != nil {
		return fmt.Errorf("system context error: %w", err)
	}

	query := `
		UPDATE relay_devices
		SET status = $1
		WHERE room_id = $2 AND relay_number = $3;
	`
	_, err = tx.Exec(ctx, query, statusClean, roomID, relayNum)
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}
