package handler

import (
	"bytes"
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

type PlaybackStartPayload struct {
	UserID    int64  `json:"user_id"`
	VideoID   string `json:"video_id"`
	Title     string `json:"title"`
	DeviceMAC string `json:"device_mac"`
	Bitrate   string `json:"bitrate"`
	Chip      string `json:"chip"`
}

type PlaybackEndPayload struct {
	SessionID string `json:"session_id"`
	UserID    int64  `json:"user_id"`
	Reason    string `json:"reason"`
}

func (h *AudioStreamHandler) notifyPlaybackStart(ctx context.Context, ownerID int64, videoID, title, mac, bitrate, chip string) string {
	if h.cfg.PythonBackendURL == "" {
		return ""
	}
	payload := PlaybackStartPayload{
		UserID:    ownerID,
		VideoID:   videoID,
		Title:     title,
		DeviceMAC: mac,
		Bitrate:   bitrate,
		Chip:      chip,
	}
	body, _ := json.Marshal(payload)
	url := fmt.Sprintf("%s/internal/playback/start", strings.TrimRight(h.cfg.PythonBackendURL, "/"))

	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return ""
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := h.httpClient.Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()

	var res struct {
		SessionID string `json:"session_id"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&res)
	return res.SessionID
}

func (h *AudioStreamHandler) notifyPlaybackEnd(ownerID int64, sessionID, reason string) {
	if h.cfg.PythonBackendURL == "" {
		return
	}
	go func() {
		payload := PlaybackEndPayload{
			SessionID: sessionID,
			UserID:    ownerID,
			Reason:    reason,
		}
		body, _ := json.Marshal(payload)
		url := fmt.Sprintf("%s/internal/playback/end", strings.TrimRight(h.cfg.PythonBackendURL, "/"))
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
		if err != nil {
			return
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := h.httpClient.Do(req)
		if err == nil {
			_ = resp.Body.Close()
		}
	}()
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

	if isC3 {
		if rssi != nil && *rssi < 0 {
			r := *rssi
			if r >= -65 {
				return "24k"
			} else if r >= -75 {
				return "20k"
			} else if r >= -82 {
				return "16k"
			} else if r >= -88 {
				return "12k"
			}
			return "8k"
		}
		return "24k"
	}

	if rssi != nil && *rssi < 0 {
		r := *rssi
		if r >= -65 {
			return "24k"
		} else if r >= -75 {
			return "16k"
		} else if r >= -85 {
			return "12k"
		}
		return "8k"
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

	mac := strings.TrimSpace(q.Get("mac"))
	if mac == "" {
		mac = strings.TrimSpace(q.Get("device_id"))
	}
	if mac == "" {
		mac = r.Header.Get("Device-Id")
	}
	if mac == "" {
		mac = r.Header.Get("X-Device-Mac")
	}
	if mac == "" {
		mac = r.Header.Get("X-MAC-Address")
	}

	token := strings.TrimSpace(q.Get("token"))
	if token == "" {
		token = r.Header.Get("X-Token")
	}
	if token == "" && strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
		token = strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	}

	ownerIDStr := q.Get("owner_id")
	var ownerID int64
	if ownerIDStr != "" {
		ownerID, _ = strconv.ParseInt(ownerIDStr, 10, 64)
	}
	if ownerID == 0 {
		resolved, _ := h.deviceStore.ResolveOwner(r.Context(), token, mac)
		ownerID = resolved
	}

	bitrate := ResolveBitrate(requestedBr, rssi, chip)
	sampleRate := ResolveSampleRate(chip)
	safeVid := safeFilenameRegex.ReplaceAllString(videoID, "_")

	// 1. FAST PATH: Check local disk cache (Playlist track already transcoded)
	cachePattern := filepath.Join(h.cfg.AudioCacheDir, fmt.Sprintf("%s_*.opus", safeVid))
	matches, _ := filepath.Glob(cachePattern)
	if len(matches) > 0 {
		for _, match := range matches {
			fi, err := os.Stat(match)
			if err == nil && fi.Size() > 10240 {
				log.Printf("[AUDIO CACHE HIT] Serving cached audio for %s (size=%d bytes, file=%s, seek=%.2fs)", videoID, fi.Size(), match, startSec)

				var streamReader io.Reader
				var cmd *exec.Cmd

				if startSec == 0 {
					f, fErr := os.Open(match)
					if fErr != nil {
						continue
					}
					defer f.Close()
					streamReader = f
				} else {
					// Fast local SSD seek using ffmpeg stream copy: takes only ~20ms
					cmd = exec.CommandContext(r.Context(), "ffmpeg", "-ss", fmt.Sprintf("%.2f", startSec), "-i", match, "-c", "copy", "-f", "ogg", "pipe:1")
					stdout, pErr := cmd.StdoutPipe()
					if pErr != nil || cmd.Start() != nil {
						log.Printf("[AUDIO CACHE ERROR] Failed to start ffmpeg seek for %s: %v", videoID, pErr)
						http.Error(w, "Failed to seek audio", http.StatusInternalServerError)
						return
					}

					// Verify seek offset: if startSec is at or beyond track EOF, ffmpeg writes only Ogg container header (~2376B) with 0 audio frames
					buf := make([]byte, 4096)
					n, readErr := io.ReadAtLeast(stdout, buf, 2600)
					if n < 2600 && (readErr == io.EOF || readErr == io.ErrUnexpectedEOF) {
						if cmd.Process != nil {
							_ = cmd.Process.Kill()
						}
						_ = cmd.Wait()
						log.Printf("[AUDIO CACHE EOF] Seek %.2fs is at/beyond track duration for %s (read only %d bytes)", startSec, videoID, n)
						http.Error(w, "Track reached end of file", http.StatusRequestedRangeNotSatisfiable)
						return
					}
					streamReader = io.MultiReader(bytes.NewReader(buf[:n]), stdout)
				}

				w.Header().Set("Content-Type", "audio/ogg")
				w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
				w.Header().Set("Pragma", "no-cache")
				w.Header().Set("Expires", "0")
				w.Header().Set("Accept-Ranges", "none")
				w.Header().Set("X-Adaptive-Bitrate", bitrate)
				w.Header().Set("X-Audio-Sample-Rate", strconv.Itoa(sampleRate))
				w.Header().Set("X-Audio-Cache", "HIT")
				w.Header().Set("X-Device-MAC", mac)
				w.WriteHeader(http.StatusOK)

				flusher, _ := w.(http.Flusher)
				if flusher != nil {
					flusher.Flush()
				}

				title := h.audioStore.GetTitleForVideo(r.Context(), videoID)
				if title == "" {
					title = fmt.Sprintf("Cached Track %s", videoID)
				}
				sessionID := h.notifyPlaybackStart(r.Context(), ownerID, videoID, title, mac, bitrate, chip)

				totalBytes, _ := h.streamWithAdaptivePacing(r.Context(), w, streamReader, flusher, bitrate)
				if cmd != nil {
					if cmd.Process != nil {
						_ = cmd.Process.Kill()
					}
					_ = cmd.Wait()
				}

				reason := "finished"
				if r.Context().Err() != nil {
					reason = "stopped"
				}
				log.Printf("[AUDIO CACHE END] Video %s finished, streamed %d bytes", videoID, totalBytes)
				h.notifyPlaybackEnd(ownerID, sessionID, reason)
				return
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
	if err != nil {
		log.Printf("[AUDIO ERROR] Python resolver request failed for %s: %v", videoID, err)
		http.Error(w, "Unable to extract audio stream from YouTube", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		log.Printf("[AUDIO ERROR] Python resolver returned status %d for %s", resp.StatusCode, videoID)
		http.Error(w, "Unable to extract audio stream from YouTube", http.StatusBadGateway)
		return
	}

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
	}

	if startSec > 0 {
		args = append(args, "-ss", fmt.Sprintf("%.2f", startSec))
	}

	args = append(args,
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
	)

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

	var streamReader io.Reader = stdout
	if startSec > 0 {
		buf := make([]byte, 4096)
		n, readErr := io.ReadAtLeast(stdout, buf, 2600)
		if n < 2600 && (readErr == io.EOF || readErr == io.ErrUnexpectedEOF) {
			if cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			_ = cmd.Wait()
			log.Printf("[AUDIO STREAM EOF] Seek %.2fs is at/beyond track duration for %s (read only %d bytes)", startSec, videoID, n)
			http.Error(w, "Track reached end of file", http.StatusRequestedRangeNotSatisfiable)
			return
		}
		streamReader = io.MultiReader(bytes.NewReader(buf[:n]), stdout)
	}

	title := resolveData.Title
	if title == "" {
		title = h.audioStore.GetTitleForVideo(r.Context(), videoID)
	}
	if title == "" {
		title = fmt.Sprintf("YouTube %s", videoID)
	}
	sessionID := h.notifyPlaybackStart(r.Context(), ownerID, videoID, title, mac, bitrate, chip)
	defer func() {
		reason := "finished"
		if r.Context().Err() != nil {
			reason = "stopped"
		}
		h.notifyPlaybackEnd(ownerID, sessionID, reason)
	}()

	// Set audio headers for ESP32 and flush immediately so ESP32 client timer does not time out
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

	flusher, _ := w.(http.Flusher)
	if flusher != nil {
		flusher.Flush()
	}

	log.Printf("[STREAM START] Video %s -> ESP32 %s (%s, %dHz, br=%s)", videoID, mac, chip, sampleRate, bitrate)

	totalBytes, _ := h.streamWithAdaptivePacing(r.Context(), w, streamReader, flusher, bitrate)

	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
	_ = cmd.Wait()
	log.Printf("[STREAM END] Video %s finished, streamed %d bytes", videoID, totalBytes)
}

// parseBitrateBps parses bitrate strings like "16k", "24k", "32k" into bytes per second with 45% delivery headroom.
func parseBitrateBps(bitrate string) int {
	re := regexp.MustCompile(`^(\d+)`)
	matches := re.FindStringSubmatch(strings.TrimSpace(bitrate))
	kbps := 24
	if len(matches) > 1 {
		if val, err := strconv.Atoi(matches[1]); err == nil && val > 0 {
			kbps = val
		}
	}
	nominalBps := (kbps * 1000) / 8
	bytesPerSec := int(float64(nominalBps) * 1.45)
	if bytesPerSec < 2200 {
		bytesPerSec = 2200
	}
	return bytesPerSec
}

// streamWithAdaptivePacing delivers audio chunks to ESP32 with adaptive rate limiting.
// 15 chunk pertama (~30KB = ~6-8s audio) dikirim instan untuk mengisi buffer hardware ESP32.
// Selanjutnya dialirkan dengan pembatas laju (nominal + 45% headroom) agar buffer ESP32 tidak meluap (overflow).
func (h *AudioStreamHandler) streamWithAdaptivePacing(ctx context.Context, w http.ResponseWriter, r io.Reader, flusher http.Flusher, bitrate string) (int64, error) {
	bytesPerSec := parseBitrateBps(bitrate)
	buf := make([]byte, 2048)
	totalBytes := int64(0)
	loopCount := 0
	targetSendTime := time.Now()

	for {
		select {
		case <-ctx.Done():
			return totalBytes, ctx.Err()
		default:
		}

		n, rErr := r.Read(buf)
		if n > 0 {
			_, wErr := w.Write(buf[:n])
			if wErr != nil {
				// Client disconnected (e.g. ESP32 stopped or requested next track)
				return totalBytes, wErr
			}
			if flusher != nil {
				flusher.Flush()
			}
			totalBytes += int64(n)
			loopCount++

			// Adaptive Pacing:
			// 15 chunk pertama dikirim instan tanpa delay untuk mengisi buffer hardware ESP32
			if loopCount > 15 {
				chunkDur := time.Duration(float64(n) / float64(bytesPerSec) * float64(time.Second))
				targetSendTime = targetSendTime.Add(chunkDur)
				sleepDur := time.Until(targetSendTime)
				if sleepDur > 0 {
					if sleepDur > 400*time.Millisecond {
						sleepDur = 400 * time.Millisecond
					}
					select {
					case <-time.After(sleepDur):
					case <-ctx.Done():
						return totalBytes, ctx.Err()
					}
				} else if sleepDur < -1500*time.Millisecond {
					targetSendTime = time.Now()
				}
			}
		}

		if rErr != nil {
			if rErr == io.EOF {
				break
			}
			return totalBytes, rErr
		}
	}
	return totalBytes, nil
}
