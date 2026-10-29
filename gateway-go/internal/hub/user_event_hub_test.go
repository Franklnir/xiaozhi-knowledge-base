package hub

import (
	"testing"
	"time"
)

func TestUserEventHub_Isolation(t *testing.T) {
	hub := NewUserEventHub()

	// User 100 subscribes
	ch100, unsub100 := hub.Subscribe(100)
	defer unsub100()

	// User 200 subscribes
	ch200, unsub200 := hub.Subscribe(200)
	defer unsub200()

	if hub.TotalSubscribers() != 2 {
		t.Fatalf("expected 2 total subscribers, got %d", hub.TotalSubscribers())
	}
	if hub.SubscriberCount(100) != 1 || hub.SubscriberCount(200) != 1 {
		t.Fatalf("expected 1 subscriber per user")
	}

	// Broadcast to User 100 only
	hub.Broadcast(100, "material_created", map[string]any{"title": "Materi User 100"})

	// User 100 must receive it
	select {
	case evt := <-ch100:
		if evt.UserID != 100 || evt.Event != "material_created" {
			t.Errorf("unexpected event received by user 100: %+v", evt)
		}
		if evt.Data["title"] != "Materi User 100" {
			t.Errorf("wrong data: %+v", evt.Data)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("user 100 timed out waiting for event")
	}

	// User 200 must NOT receive it
	select {
	case evt := <-ch200:
		t.Fatalf("CRITICAL SECURITY VIOLATION: User 200 received User 100 event: %+v", evt)
	case <-time.After(200 * time.Millisecond):
		// Expected: no event received
	}

	// Now broadcast to User 200
	hub.Broadcast(200, "relay_update", map[string]any{"status": "ON"})

	select {
	case evt := <-ch200:
		if evt.UserID != 200 || evt.Event != "relay_update" {
			t.Errorf("unexpected event received by user 200: %+v", evt)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("user 200 timed out waiting for event")
	}

	// User 100 must NOT receive User 200 event
	select {
	case evt := <-ch100:
		t.Fatalf("CRITICAL SECURITY VIOLATION: User 100 received User 200 event: %+v", evt)
	case <-time.After(200 * time.Millisecond):
		// Expected
	}
}

func TestUserEventHub_Unsubscribe(t *testing.T) {
	hub := NewUserEventHub()

	_, unsub := hub.Subscribe(42)
	if hub.SubscriberCount(42) != 1 {
		t.Fatalf("expected 1 subscriber")
	}

	unsub()
	if hub.SubscriberCount(42) != 0 {
		t.Fatalf("expected 0 subscribers after unsub")
	}
	if hub.ActiveUserCount() != 0 {
		t.Fatalf("expected 0 active users after cleanup")
	}
}
