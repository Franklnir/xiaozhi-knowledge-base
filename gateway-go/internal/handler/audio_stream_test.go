package handler

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestParseBitrateBps(t *testing.T) {
	tests := []struct {
		bitrate  string
		expected int
	}{
		{"8k", 1080},
		{"12k", 1620},
		{"16k", 2160},
		{"24k", 3240},
		{"32k", 4320},
		{"invalid", 3240}, // default 24k
	}

	for _, tt := range tests {
		bps := parseBitrateBps(tt.bitrate)
		if bps != tt.expected {
			t.Errorf("parseBitrateBps(%s) = %d, expected %d", tt.bitrate, bps, tt.expected)
		}
	}
}

func TestStreamWithAdaptivePacingInitialBurst(t *testing.T) {
	h := &AudioStreamHandler{}
	w := httptest.NewRecorder()

	// 10KB dummy data (10 chunks of 1024)
	data := strings.Repeat("A", 10*1024)
	r := strings.NewReader(data)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	start := time.Now()
	total, err := h.streamWithAdaptivePacing(ctx, w, r, nil, "16k")
	elapsed := time.Since(start)

	// Since context timeout is 2s, it should have streamed initial 4 chunks instantly
	// and paced subsequent chunks (~474ms each).
	if total < 4096 {
		t.Errorf("Expected at least initial burst of 4096 bytes, got %d (err: %v)", total, err)
	}
	if elapsed < 500*time.Millisecond {
		t.Errorf("Expected pacing delay for chunks beyond initial burst, elapsed: %v", elapsed)
	}
}
