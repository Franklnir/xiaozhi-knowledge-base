package store

import (
	"context"
	"fmt"
	"time"

	"gateway-go/internal/db"
)

type AudioCommand struct {
	ID        int64     `json:"id"`
	OwnerID   int64     `json:"owner_id"`
	Title     string    `json:"title"`
	StreamURL string    `json:"stream_url"`
	VideoURL  string    `json:"video_url"`
	Duration  string    `json:"duration"`
	VideoID   string    `json:"video_id"`
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"created_at"`
}

type AudioStore struct {
	db *db.DB
}

// RecentVideoCommand returns only a fresh command for the exact owner and video.
func (s *AudioStore) RecentVideoCommand(ctx context.Context, ownerID int64, videoID string) (int64, string) {
	var id int64
	var streamURL string
	err := s.db.Pool.QueryRow(ctx, `
		SELECT id, stream_url FROM audio_queue
		WHERE owner_id = $1 AND video_id = $2 AND created_at >= NOW() - INTERVAL '2 minutes'
		  AND status IN ('pending', 'played')
		ORDER BY id DESC LIMIT 1
	`, ownerID, videoID).Scan(&id, &streamURL)
	if err != nil {
		return 0, ""
	}
	return id, streamURL
}

func NewAudioStore(db *db.DB) *AudioStore {
	return &AudioStore{db: db}
}

// GetPendingCommands retrieves pending commands for a user.
// Parity with Python: If there are multiple pending commands, older ones are auto-acknowledged, keeping only the latest.
// Stale commands older than 30 minutes are automatically marked as played.
func (s *AudioStore) GetPendingCommands(ctx context.Context, ownerID int64) ([]AudioCommand, error) {
	conn, err := s.db.Pool.Acquire(ctx)
	if err != nil {
		return nil, fmt.Errorf("acquire conn error: %w", err)
	}
	defer conn.Release()

	// Bypass RLS as system
	_, _ = conn.Exec(ctx, "SELECT set_config('app.user_role', 'system', true), set_config('app.is_admin', 'true', true);")

	// Clean up stale pending commands older than 30 minutes
	_, _ = conn.Exec(ctx, `
		UPDATE audio_queue
		SET status = 'played'
		WHERE owner_id = $1 AND status = 'pending' AND created_at < NOW() - INTERVAL '30 minutes'
	`, ownerID)

	rows, err := conn.Query(ctx, `
		SELECT id, owner_id, COALESCE(title, ''), stream_url, COALESCE(video_url, ''),
		       COALESCE(duration, ''), COALESCE(video_id, ''), status, created_at
		FROM audio_queue
		WHERE owner_id = $1 AND status = 'pending'
		ORDER BY id ASC
	`, ownerID)
	if err != nil {
		return nil, fmt.Errorf("query pending commands error: %w", err)
	}
	defer rows.Close()

	var commands []AudioCommand
	for rows.Next() {
		var c AudioCommand
		if err := rows.Scan(
			&c.ID, &c.OwnerID, &c.Title, &c.StreamURL, &c.VideoURL,
			&c.Duration, &c.VideoID, &c.Status, &c.CreatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan command error: %w", err)
		}
		commands = append(commands, c)
	}

	if len(commands) > 1 {
		latest := commands[len(commands)-1]
		// Auto-ack older commands
		_, _ = conn.Exec(ctx, `
			UPDATE audio_queue
			SET status = 'played'
			WHERE owner_id = $1 AND id < $2 AND status = 'pending'
		`, ownerID, latest.ID)
		commands = []AudioCommand{latest}
	}

	return commands, nil
}

// AckCommand marks a command as played
func (s *AudioStore) AckCommand(ctx context.Context, ownerID int64, commandID int64) error {
	conn, err := s.db.Pool.Acquire(ctx)
	if err != nil {
		return fmt.Errorf("acquire conn error: %w", err)
	}
	defer conn.Release()

	_, _ = conn.Exec(ctx, "SELECT set_config('app.user_role', 'system', true), set_config('app.is_admin', 'true', true);")

	_, err = conn.Exec(ctx, `
		UPDATE audio_queue
		SET status = 'played'
		WHERE id = $1 AND owner_id = $2
	`, commandID, ownerID)
	return err
}

// UpdatePlaybackStatus updates the status of audio currently playing or finished
func (s *AudioStore) UpdatePlaybackStatus(ctx context.Context, ownerID int64, videoID, status string) error {
	conn, err := s.db.Pool.Acquire(ctx)
	if err != nil {
		return fmt.Errorf("acquire conn error: %w", err)
	}
	defer conn.Release()

	_, _ = conn.Exec(ctx, "SELECT set_config('app.user_role', 'system', true), set_config('app.is_admin', 'true', true);")

	if videoID != "" && ownerID > 0 {
		_, _ = conn.Exec(ctx, `
			UPDATE audio_queue
			SET status = $1
			WHERE owner_id = $2 AND video_id = $3 AND status IN ('pending', 'playing')
		`, status, ownerID, videoID)
	}
	return nil
}

// GetTitleForVideo retrieves the title of a video from recent audio_queue if known
func (s *AudioStore) GetTitleForVideo(ctx context.Context, videoID string) string {
	conn, err := s.db.Pool.Acquire(ctx)
	if err != nil {
		return ""
	}
	defer conn.Release()
	_, _ = conn.Exec(ctx, "SELECT set_config('app.user_role', 'system', true), set_config('app.is_admin', 'true', true);")

	var title string
	_ = conn.QueryRow(ctx, `
		SELECT COALESCE(title, '') FROM audio_queue
		WHERE video_id = $1 AND title != ''
		ORDER BY id DESC LIMIT 1
	`, videoID).Scan(&title)
	return title
}
