package store

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"gateway-go/internal/db"
)

func TestRelayStoreRespectsOwnerRLS(t *testing.T) {
	dsn := os.Getenv("XIAOZHI_TEST_POSTGRES_DSN")
	if dsn == "" {
		t.Skip("Set XIAOZHI_TEST_POSTGRES_DSN to a separate migrated test database.")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	database, err := db.NewPostgresPool(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()

	suffix := time.Now().UnixNano()
	var owners []int64
	defer func() {
		cleanupCtx, release := context.WithTimeout(context.Background(), 5*time.Second)
		defer release()
		if _, err := database.Pool.Exec(cleanupCtx, "DELETE FROM users WHERE id = ANY($1)", owners); err != nil {
			t.Error(err)
		}
	}()
	for _, account := range []string{"a", "b"} {
		var id int64
		err = database.Pool.QueryRow(ctx,
			"INSERT INTO users (username, password_hash) VALUES ($1, 'test-only') RETURNING id",
			fmt.Sprintf("relay_%s_%d", account, suffix),
		).Scan(&id)
		if err != nil {
			t.Fatal(err)
		}
		owners = append(owners, id)
	}

	var roomID int64
	slug := fmt.Sprintf("relay-test-%d", suffix)
	err = database.Pool.QueryRow(ctx, `
		INSERT INTO relay_rooms (owner_id, nama_tempat, api_slug)
		VALUES ($1, 'Test room', $2) RETURNING id
	`, owners[0], slug).Scan(&roomID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = database.Pool.Exec(ctx,
		"INSERT INTO relay_devices (room_id, relay_number, nama_relay) VALUES ($1, 1, 'Test relay')", roomID,
	); err != nil {
		t.Fatal(err)
	}

	store := NewRelayStore(database)
	room, err := store.GetRoomBySlug(ctx, slug)
	if err != nil || room.OwnerID != owners[0] {
		t.Fatalf("Room lookup failed: %v", err)
	}
	if relays, err := store.GetRoomRelays(ctx, owners[1], roomID); err != nil || len(relays) != 0 {
		t.Fatalf("Other account should see zero relay rows, got %d, %v", len(relays), err)
	}
	if err := store.UpdateRelayStatus(ctx, owners[1], roomID, 1, "ON"); err != nil {
		t.Fatal(err)
	}
	if relays, err := store.GetRoomRelays(ctx, owners[0], roomID); err != nil || len(relays) != 1 || relays[0].Status != "OFF" {
		t.Fatalf("Other account must not change relay state: %+v, %v", relays, err)
	}
	if err := store.UpdateRelayStatus(ctx, owners[0], roomID, 1, "ON"); err != nil {
		t.Fatal(err)
	}
	if relays, err := store.GetRoomRelays(ctx, owners[0], roomID); err != nil || len(relays) != 1 || relays[0].Status != "ON" {
		t.Fatalf("Owner relay status update failed: %+v, %v", relays, err)
	}
}
