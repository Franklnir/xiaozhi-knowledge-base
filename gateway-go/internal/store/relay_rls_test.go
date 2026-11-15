package store

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"gateway-go/internal/db"
)

func TestAccountStoresRespectOwnerRLS(t *testing.T) {
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

	t.Run("ESP32 ownership", func(t *testing.T) {
		mac := NormalizeMAC(fmt.Sprintf("02%010x", suffix&0xffffffffff))
		if _, err := database.Pool.Exec(ctx, `
			INSERT INTO registered_devices (owner_id, device_id, device_name, device_type, status, last_active_at)
			VALUES ($1, $2, 'Synthetic ESP32', 'esp32', 'ACTIVE', NOW() - INTERVAL '1 day')
		`, owners[0], mac); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			cleanupCtx, release := context.WithTimeout(context.Background(), 5*time.Second)
			defer release()
			if _, err := database.Pool.Exec(cleanupCtx, "DELETE FROM registered_devices WHERE device_id = $1", mac); err != nil {
				t.Error(err)
			}
		})
		devices := NewDeviceStore(database)
		owner, err := devices.ResolveOwner(ctx, "", mac)
		if err != nil || owner != owners[0] {
			t.Fatalf("Device resolution failed: %v", err)
		}
		infoOwner, name, _ := devices.GetDeviceInfo(ctx, mac)
		if infoOwner != owners[0] || name != "Synthetic ESP32" {
			t.Fatal("Device information could not be read by the service")
		}
		for _, account := range []struct {
			id      int64
			updated bool
		}{{owners[1], false}, {owners[0], true}} {
			if err := devices.RecordDeviceActivity(ctx, mac, account.id); err != nil {
				t.Fatal(err)
			}
			var fresh bool
			err := database.Pool.QueryRow(ctx,
				"SELECT last_active_at > NOW() - INTERVAL '1 minute' FROM registered_devices WHERE device_id = $1", mac,
			).Scan(&fresh)
			if err != nil || fresh != account.updated {
				t.Fatalf("Unexpected device activity visibility: %v, %v", fresh, err)
			}
		}
	})
}
