package handler

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"gateway-go/internal/config"
	"gateway-go/internal/hub"
)

func TestAdminLogStreamChecksAccessBeforeSendingEvents(t *testing.T) {
	authBackend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/admin/api/logs/authorize" || r.Header.Get("Accept") != "application/json" {
			t.Error("Gateway should request the admin authorization endpoint")
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		switch {
		case r.Header.Get("Cookie") == "session=test-admin":
			w.WriteHeader(http.StatusNoContent)
		case r.Header.Get("Authorization") == "Bearer test-user":
			w.WriteHeader(http.StatusForbidden)
		case r.Header.Get("Cookie") == "session=redirect":
			http.Redirect(w, r, "/login", http.StatusTemporaryRedirect)
		default:
			w.WriteHeader(http.StatusUnauthorized)
		}
	}))
	defer authBackend.Close()

	handler := NewSSEHandler(&config.Config{PythonBackendURL: authBackend.URL}, hub.NewSSEHub(), nil, nil)
	stream := httptest.NewServer(http.HandlerFunc(handler.HandleLogsStream))
	defer stream.Close()
	client := &http.Client{Timeout: 3 * time.Second}
	for _, tc := range []struct {
		label, header, credential string
		status                    int
	}{
		{"anonymous", "", "", http.StatusUnauthorized},
		{"ordinary account", "Authorization", "Bearer test-user", http.StatusForbidden},
		{"redirected verification", "Cookie", "session=redirect", http.StatusServiceUnavailable},
		{"administrator", "Cookie", "session=test-admin", http.StatusOK},
	} {
		t.Run(tc.label, func(t *testing.T) {
			request, err := http.NewRequest(http.MethodGet, stream.URL, nil)
			if err != nil {
				t.Fatal(err)
			}
			if tc.header != "" {
				request.Header.Set(tc.header, tc.credential)
			}
			response, err := client.Do(request)
			if err != nil {
				t.Fatal(err)
			}
			defer response.Body.Close()
			if response.StatusCode != tc.status {
				t.Errorf("Expected status %d, got %d", tc.status, response.StatusCode)
			}
			if sse := response.Header.Get("Content-Type") == "text/event-stream"; sse != (tc.status == http.StatusOK) {
				t.Errorf("Unexpected stream availability: %t", sse)
			}
		})
	}
}
