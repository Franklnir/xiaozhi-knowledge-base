package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"gateway-go/internal/config"
	"gateway-go/internal/store"
)

type AudioStreamHandler struct {
	cfg         *config.Config
	deviceStore *store.DeviceStore
	audioStore  *store.AudioStore
	httpClient  *http.Client
}

func NewAudioStreamHandler(cfg *config.Config, deviceStore *store.DeviceStore, audioStore *store.AudioStore) *AudioStreamHandler {
	return &AudioStreamHandler{
		cfg:         cfg,
		deviceStore: deviceStore,
		audioStore:  audioStore,
		httpClient: &http.Client{
			Timeout: 20 * time.Second,
		},
	}
}

var safeFilenameRegex = regexp.MustCompile(`[^a-zA-Z0-9_\-]`)

// ResolveBitrate chooses optimal bitrate according to ESP32 chip profile and Wi-Fi RSSI
func ResolveBitrate(requested string, rssi *int, chip string) string {
	req := strings.ToLower(strings.TrimSpace(requested))
	valid := map[string]bool{
		"6k": true, "8k": true, "9k": true, "10k": true, "11k": true,
		"12k": true, "16k": true, "20k": true, "24k": true, "30k": true,
		"32k": true, "48k": true,
	}
	if req != "" && req != "auto" && valid[req] {
		return req
	}

	chip = strings.ToLower(chip)
	isS3 := strings.Contains(chip, "s3")
	isC3 := strings.Contains(chip, "c3")

	if isS3 {
		if rssi != nil && *rssi < 0 {
			r := *rssi
			if r >= -65 {
				return "48k"
			} else if r >= -75 {
				return "32k"
			} else if r >= -82 {
				return "24k"
			} else if r >= -88 {
				return "16k"
			}
			return "12k"
		}
		return "48k"
	}

	if rssi != nil && *rssi < 0 {
		r := *rssi
		if r >= -65 {
			return "12k"
		} else if r >= -75 {
			return "11k"
		} else if r >= -85 {
			return "8k"
		}
		return "6k"
	}

	if isC3 {
		return "12k"
	}
	return "24k"
}

// ResolveSampleRate determines optimal sample rate for ESP32 DAC/I2S
func ResolveSampleRate(chip string) int {
	chip = strings.ToLower(chip)
	if strings.Contains(chip, "s3") {
		return 24000
	}
	if strings.Contains(chip, "c3") {
		return 16000
	}
	return 24000
}

type InternalResolveResponse struct {
	Success   bool   `json:"success"`
	VideoID   string `json:"video_id"`
	SourceURL string `json:"source_url"`
	Title     string `json:"title"`
	Error     string `json:"error,omitempty"`
}

// HandleStream handles GET /api/audio/stream/{video_id}
func (h *AudioStreamHandler) HandleStream(w http.ResponseWriter, r *http.Request) {
	videoID := chi.URLParam(r, "video_id")
	if videoID == "" {
		http.Error(w, "video_id is required", http.StatusBadRequest)
		return
	}

	q := r.URL.Query()
	requestedBr := q.Get("br")
	chip := q.Get("chip")
	if chip == "" {
		chip = r.Header.Get("X-Device-Chip")
	}
	if chip == "" {
		chip = r.Header.Get("Device-Chip")
	}
	if chip == "" {
		chip = r.Header.Get("X-Chip")
	}

	var rssi *int
	if rssiStr := q.Get("rssi"); rssiStr != "" {
		if val, err := strconv.Atoi(rssiStr); err == nil {
			rssi = &val
		}
	} else if headerRssi := r.Header.Get("X-WiFi-RSSI"); headerRssi != "" {
		if val, err := strconv.Atoi(strings.TrimSpace(headerRssi)); err == nil {
			rssi = &val
		}
	}

	var startSec float64
	if startStr := q.Get("start"); startStr != "" {
		if val, err := strconv.ParseFloat(startStr, 64); err == nil {
			startSec = val
		}
	}

	mac := q.Get("mac")
	if mac == "" {
		mac = r.Header.Get("Device-Id")
	}

	bitrate := ResolveBitrate(requestedBr, rssi, chip)
	sampleRate := ResolveSampleRate(chip)
	safeVid := safeFilenameRegex.ReplaceAllString(videoID, "_")

	// 1. FAST PATH: Check local disk cache (Playlist track already transcoded)
	cachePattern := filepath.Join(h.cfg.AudioCacheDir, fmt.Sprintf("%s_*.opus", safeVid))
	matches, _ := filepath.Glob(cachePattern)
	if len(matches) > 0 && startSec == 0 {
		for _, match := range matches {
			fi, err := os.Stat(match)
			if err == nil && fi.Size() > 10240 {
				log.Printf("[AUDIO CACHE HIT] Serving cached audio for %s (size=%d bytes, file=%s)", videoID, fi.Size(), match)
				w.Header().Set("Content-Type", "audio/ogg")
				w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
				w.Header().Set("Pragma", "no-cache")
				w.Header().Set("Expires", "0")
				w.Header().Set("Accept-Ranges", "none")
				w.Header().Set("X-Adaptive-Bitrate", bitrate)
				w.Header().Set("X-Audio-Sample-Rate", strconv.Itoa(sampleRate))
				w.Header().Set("X-Audio-Cache", "HIT")
				w.Header().Set("X-Device-MAC", mac)

				f, err := os.Open(match)
				if err == nil {
					defer f.Close()
					_, _ = io.Copy(w, f)
					return
				}
			}
		}
	}

	// 2. LIVE PATH: Resolve direct YouTube audio URL via Python backend resolver
	resolveURL := fmt.Sprintf("%s/internal/audio/resolve/%s", strings.TrimRight(h.cfg.PythonBackendURL, "/"), videoID)
	reqCtx, cancelReq := context.WithTimeout(r.Context(), 18*time.Second)
	defer cancelReq()

	req, err := http.NewRequestWithContext(reqCtx, "GET", resolveURL, nil)
	if err != nil {
		log.Printf("[AUDIO ERROR] Failed to create resolve request: %v", err)
		http.Error(w, "Failed to resolve audio", http.StatusInternalServerError)
		return
	}

	resp, err := h.httpClient.Do(req)
	if err != nil || resp.StatusCode != http.StatusOK {
		log.Printf("[AUDIO ERROR] Python resolver failed for %s: err=%v, status=%d", videoID, err, resp.StatusCode)
		http.Error(w, "Unable to extract audio stream from YouTube", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	var resolveData InternalResolveResponse
	if err := json.NewDecoder(resp.Body).Decode(&resolveData); err != nil || resolveData.SourceURL == "" {
		log.Printf("[AUDIO ERROR] Invalid resolver response for %s: %v", videoID, err)
		http.Error(w, "Direct audio URL not found", http.StatusNotFound)
		return
	}

	// 3. EXECUTE FFMPEG WITH ZERO-COPY PIPE
	ffmpegBin := "ffmpeg"
	args := []string{
		"-reconnect", "1",
		"-reconnect_at_eof", "1",
		"-reconnect_streamed", "1",
		"-reconnect_delay_max", "5",
		"-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n",
		"-i", resolveData.SourceURL,
		"-vn",
		"-ac", "1",
		"-ar", strconv.Itoa(sampleRate),
		"-c:a", "libopus",
		"-b:a", bitrate,
		"-vbr", "on",
		"-compression_level", "5",
		"-application", "audio",
		"-flush_packets", "1",
		"-frame_duration", "60",
		"-page_duration", "60000",
		"-f", "ogg",
		"pipe:1",
	}

	if startSec > 0 {
		args = append([]string{"-ss", fmt.Sprintf("%.2f", startSec)}, args...)
	}

	cmd := exec.CommandContext(r.Context(), ffmpegBin, args...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		log.Printf("[AUDIO ERROR] Failed to open FFmpeg stdout pipe: %v", err)
		http.Error(w, "Failed to initialize audio stream", http.StatusInternalServerError)
		return
	}

	if err := cmd.Start(); err != nil {
		log.Printf("[AUDIO ERROR] Failed to start FFmpeg: %v", err)
		http.Error(w, "Failed to start audio encoder", http.StatusInternalServerError)
		return
	}

	// Set audio headers for ESP32
	w.Header().Set("Content-Type", "audio/ogg")
	w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
	w.Header().Set("Pragma", "no-cache")
	w.Header().Set("Expires", "0")
	w.Header().Set("Accept-Ranges", "none")
	w.Header().Set("X-Adaptive-Bitrate", bitrate)
	w.Header().Set("X-Audio-Sample-Rate", strconv.Itoa(sampleRate))
	w.Header().Set("X-Audio-Cache", "MISS")
	w.Header().Set("X-Device-MAC", mac)
	w.WriteHeader(http.StatusOK)

	log.Printf("[STREAM START] Video %s -> ESP32 %s (%s, %dHz, br=%s)", videoID, mac, chip, sampleRate, bitrate)

	// Stream chunks directly with minimal memory buffer
	buf := make([]byte, 4096)
	flusher, hasFlusher := w.(http.Flusher)
	totalBytes := int64(0)

	for {
		n, rErr := stdout.Read(buf)
		if n > 0 {
			_, wErr := w.Write(buf[:n])
			if wErr != nil {
				// Client disconnected (e.g. ESP32 stopped or requested next track)
				break
			}
			if hasFlusher {
				flusher.Flush()
			}
			totalBytes += int64(n)
		}
		if rErr != nil {
			break
		}
	}

	_ = cmd.Wait()
	log.Printf("[STREAM END] Video %s finished, streamed %d bytes", videoID, totalBytes)
}
