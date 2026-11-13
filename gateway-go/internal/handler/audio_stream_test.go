package handler

import (
	"bytes"
	"io"
	"strings"
	"testing"
)

func TestConfirmedVideoAudioProfile(t *testing.T) {
	streamURL := "/api/audio/stream/abcdefghijk?mode=video&mac=AA%3ABB%3ACC%3ADD%3AEE%3A01"
	if !isConfirmedVideoStream(streamURL, "aa-bb-cc-dd-ee-01") {
		t.Fatal("expected the confirmed video request to match its device")
	}
	if isConfirmedVideoStream(streamURL, "AA:BB:CC:DD:EE:02") {
		t.Fatal("a different device must not inherit the video request")
	}
	if confirmed, mac := confirmedVideoStreamDevice("/api/audio/stream/abcdefghijk?mode=video"); !confirmed || mac != "" {
		t.Fatal("a video request without a MAC must be checked against the device owner")
	}
	if bitrate := resolveVideoBitrate("40k", nil, "esp32s3", 24000); bitrate != "24k" {
		t.Errorf("video bitrate = %s, want 24k", bitrate)
	}
	rssi := -89
	if bitrate := resolveVideoBitrate("48k", &rssi, "esp32s3", 24000); bitrate != "12k" {
		t.Errorf("weak Wi-Fi video bitrate = %s, want 12k", bitrate)
	}
	if bitrate := ResolveBitrate("40k", nil, "esp32s3"); bitrate != "48k" {
		t.Errorf("music bitrate = %s, want existing 48k", bitrate)
	}
}

func TestBufferVideoAudioPreservesStream(t *testing.T) {
	for _, size := range []int{300, 10000} {
		original := strings.Repeat("a", size)
		source := strings.NewReader(original)
		stream, n, err := bufferVideoAudio(source, "24k")
		if err != nil || n != min(size, 9000) {
			t.Fatalf("buffer for %d bytes = %d, %v", size, n, err)
		}
		data, err := io.ReadAll(stream)
		if err != nil || !bytes.Equal(data, []byte(original)) {
			t.Fatalf("buffer did not preserve the %d-byte stream: %v", size, err)
		}
	}
}
