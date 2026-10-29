package handler

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"gateway-go/internal/config"
	"gateway-go/internal/hub"
)

func generateTestToken(userID int64, timestamp int64, secret string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(fmt.Sprintf("%d.%d", userID, timestamp)))
	sig := hex.EncodeToString(mac.Sum(nil))
	return fmt.Sprintf("%d.%d.%s", userID, timestamp, sig)
}

func TestVerifyStreamToken(t *testing.T) {
	secret := "test_secret_key_12345"
	cfg := &config.Config{
		AppSecretKey:   secret,
		InternalAPIKey: "internal_key",
	}
	userHub := hub.NewUserEventHub()
	h := NewUserEventsHandler(cfg, userHub)

	now := time.Now().Unix()

	// 1. Valid token
	validToken := generateTestToken(101, now, secret)
	uid, err := h.VerifyStreamToken(validToken)
	if err != nil {
		t.Fatalf("expected valid token to pass, got error: %v", err)
	}
	if uid != 101 {
		t.Fatalf("expected uid 101, got %d", uid)
	}

	// 2. Tampered signature
	tamperedToken := validToken[:len(validToken)-4] + "dead"
	_, err = h.VerifyStreamToken(tamperedToken)
	if err == nil {
		t.Fatal("expected tampered token to fail")
	}

	// 3. Expired token (> 24h ago)
	expiredToken := generateTestToken(101, now-90000, secret)
	_, err = h.VerifyStreamToken(expiredToken)
	if err == nil {
		t.Fatal("expected expired token to fail")
	}

	// 4. Invalid format
	_, err = h.VerifyStreamToken("invalid_token")
	if err == nil {
		t.Fatal("expected invalid format to fail")
	}
}

func TestHandleEmitUserEvent(t *testing.T) {
	cfg := &config.Config{}
	userHub := hub.NewUserEventHub()
	h := NewUserEventsHandler(cfg, userHub)

	// Subscribe User 500
	eventChan, unsub := userHub.Subscribe(500)
	defer unsub()

	// Emit via handler
	payload := `{"user_id": 500, "event": "material_created", "data": {"title": "Test Title"}}`
	req := httptest.NewRequest(http.MethodPost, "/api/internal/events/emit", strings.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.HandleEmitUserEvent(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d: %s", w.Code, w.Body.String())
	}

	// Verify subscriber received event
	select {
	case evt := <-eventChan:
		if evt.UserID != 500 || evt.Event != "material_created" {
			t.Fatalf("unexpected event: %+v", evt)
		}
		if evt.Data["title"] != "Test Title" {
			t.Fatalf("unexpected data: %+v", evt.Data)
		}
	case <-time.After(1 * time.Second):
		t.Fatal("subscriber did not receive emitted event")
	}
}
