package handler

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"strings"
	"testing"
	"testing/iotest"
	"time"

	"github.com/go-chi/chi/v5"

	"gateway-go/internal/config"
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
	if bitrate := resolveVideoBitrate("auto", nil, "esp32s3"); bitrate != "48k" {
		t.Errorf("video on S3 bitrate = %s, want 48k", bitrate)
	}
	if bitrate := resolveVideoBitrate("48k", nil, "esp32c3"); bitrate != "16k" {
		t.Errorf("video on C3 bitrate = %s, want 16k", bitrate)
	}
	rssi := -89
	if bitrate := resolveVideoBitrate("48k", &rssi, "esp32s3"); bitrate != "12k" {
		t.Errorf("weak Wi-Fi video bitrate = %s, want 12k", bitrate)
	}
	if bitrate := ResolveBitrate("40k", nil, "esp32s3"); bitrate != "48k" {
		t.Errorf("music bitrate = %s, want existing 48k", bitrate)
	}
}

func TestBufferVideoAudioPreservesStream(t *testing.T) {
	for _, size := range []int{300, 18000} {
		original := strings.Repeat("a", size)
		source := strings.NewReader(original)
		stream, n, err := bufferVideoAudio(source, "24k")
		if err != nil || n != min(size, 14000) {
			t.Fatalf("buffer for %d bytes = %d, %v", size, n, err)
		}
		data, err := io.ReadAll(stream)
		if err != nil || !bytes.Equal(data, []byte(original)) {
			t.Fatalf("buffer did not preserve the %d-byte stream: %v", size, err)
		}
	}
}

func TestReadVideoAudioCoalescesSmallReads(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	original := strings.Repeat("ogg-page", 801)
	chunks := readVideoAudio(ctx, iotest.OneByteReader(strings.NewReader(original)), "6k")
	var received bytes.Buffer
	for chunk := range chunks {
		if chunk.err == nil && len(chunk.data) != videoAudioChunkSize {
			t.Fatalf("short write before EOF: %d bytes", len(chunk.data))
		}
		if chunk.err != nil && chunk.err != io.EOF {
			t.Fatal(chunk.err)
		}
		received.Write(chunk.data)
	}
	if received.String() != original {
		t.Fatalf("received %d bytes, expected %d unchanged", received.Len(), len(original))
	}
}

func TestRadioAudioProxiesTicketAndStream(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/audio/stream/radio_live_test-station" ||
			r.URL.Query().Get("radio_ticket") != "signed.test" || r.URL.Query().Get("chip") != "esp32s3" {
			http.Error(w, "Unexpected radio request", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "audio/ogg")
		w.Write([]byte("OggS fixture"))
	}))
	defer backend.Close()

	h := NewAudioStreamHandler(&config.Config{PythonBackendURL: backend.URL}, nil, nil)
	r := chi.NewRouter()
	r.Get("/api/audio/stream/{video_id}", h.HandleStream)
	request := httptest.NewRequest(http.MethodGet, "/api/audio/stream/radio_live_test-station?radio_ticket=signed.test&chip=esp32s3", nil)
	response := httptest.NewRecorder()
	r.ServeHTTP(response, request)
	if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "audio/ogg" || response.Body.String() != "OggS fixture" {
		t.Fatalf("unexpected radio response: %d %v %q", response.Code, response.Header(), response.Body.String())
	}
}

func TestYouTubeAudioHandlerWithFFmpeg(t *testing.T) {
	ffmpeg, err := exec.LookPath("ffmpeg")
	if err != nil {
		t.Skip("ffmpeg not available")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	source, err := exec.CommandContext(ctx, ffmpeg, "-v", "error", "-f", "lavfi",
		"-i", "sine=frequency=440:sample_rate=48000:duration=6",
		"-f", "wav", "pipe:1").Output()
	if err != nil {
		t.Fatalf("create audio fixture: %v", err)
	}
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.ServeContent(w, r, "sample.wav", time.Time{}, bytes.NewReader(source))
	}))
	defer origin.Close()
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/internal/audio/resolve/abcdefghijk" {
			fmt.Fprintf(w, `{"source_url": %q, "title": "Test audio"}`, origin.URL+"/sample.wav")
		} else {
			w.WriteHeader(http.StatusNoContent)
		}
	}))
	defer backend.Close()
	h := NewAudioStreamHandler(&config.Config{AudioCacheDir: t.TempDir(), PythonBackendURL: backend.URL}, nil, nil)
	r := chi.NewRouter()
	r.Get("/api/audio/stream/{video_id}", h.HandleStream)

	for _, mode := range []string{"video", "music"} {
		t.Run(mode, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, "/api/audio/stream/abcdefghijk?owner_id=1&chip=esp32s3&rssi=-60", nil).WithContext(ctx)
			if mode == "video" {
				request.URL.RawQuery += "&mode=video"
			}
			response := httptest.NewRecorder()
			r.ServeHTTP(response, request)
			if response.Code != http.StatusOK || response.Header().Get("X-Adaptive-Bitrate") != "48k" ||
				response.Header().Get("X-Audio-Sample-Rate") != "24000" {
				t.Fatalf("unexpected playback response: %d %v", response.Code, response.Header())
			}
			decode := exec.CommandContext(ctx, ffmpeg, "-v", "error", "-xerror", "-f", "ogg", "-i", "pipe:0",
				"-ar", "24000", "-f", "s16le", "pipe:1")
			decode.Stdin = response.Body
			pcm, err := decode.Output()
			if err != nil || len(pcm) != 6*24000*2 {
				t.Fatalf("did not decode all six seconds of audio: %d PCM bytes, %v", len(pcm), err)
			}
		})
	}
}
