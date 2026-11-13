package handler

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
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

type PlaybackProgressPayload struct {
	SessionID     string `json:"session_id"`
	UserID        int64  `json:"user_id"`
	BytesStreamed int64  `json:"bytes_streamed"`
}

type PlaybackEndPayload struct {
	SessionID  string `json:"session_id"`
	UserID     int64  `json:"user_id"`
	Reason     string `json:"reason"`
	TotalBytes int64  `json:"total_bytes"`
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

func (h *AudioStreamHandler) notifyPlaybackProgress(sessionID string, ownerID int64, bytesStreamed int64) {
	if h.cfg.PythonBackendURL == "" || sessionID == "" {
		return
	}
	payload := PlaybackProgressPayload{
		SessionID:     sessionID,
		UserID:        ownerID,
		BytesStreamed: bytesStreamed,
	}
	body, _ := json.Marshal(payload)
	url := fmt.Sprintf("%s/internal/playback/progress", strings.TrimRight(h.cfg.PythonBackendURL, "/"))
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
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
}

func (h *AudioStreamHandler) notifyPlaybackEnd(ownerID int64, sessionID, reason string, totalBytes int64) {
	if h.cfg.PythonBackendURL == "" {
		return
	}
	go func() {
		payload := PlaybackEndPayload{
			SessionID:  sessionID,
			UserID:     ownerID,
			Reason:     reason,
			TotalBytes: totalBytes,
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
var youtubeVideoID = regexp.MustCompile(`^[a-zA-Z0-9_-]{11}$`)

func isConfirmedVideoStream(streamURL, mac string) bool {
	isVideo, commandMAC := confirmedVideoStreamDevice(streamURL)
	deviceMAC := store.NormalizeMAC(mac)
	return isVideo && deviceMAC != "" && commandMAC == deviceMAC
}

func confirmedVideoStreamDevice(streamURL string) (bool, string) {
	parsed, err := url.Parse(streamURL)
	if err != nil {
		return false, ""
	}
	params := parsed.Query()
	rawMAC := params.Get("mac")
	mac := store.NormalizeMAC(rawMAC)
	return params.Get("mode") == "video" && (rawMAC == "" || mac != ""), mac
}

func resolveVideoBitrate(requested string, rssi *int, chip string, sampleRate int) string {
	bitrate := ResolveBitrate(requested, rssi, chip)
	if requested != "" && !strings.EqualFold(requested, "auto") && rssi != nil {
		adaptive := ResolveBitrate("auto", rssi, chip)
		if bitrateKbps(adaptive) < bitrateKbps(bitrate) {
			bitrate = adaptive
		}
	}
	limit := "24k"
	if sampleRate == 16000 {
		limit = "16k"
	}
	if bitrateKbps(bitrate) > bitrateKbps(limit) {
		return limit
	}
	return bitrate
}

func bitrateKbps(bitrate string) int {
	kbps, _ := strconv.Atoi(strings.TrimSuffix(bitrate, "k"))
	return kbps
}

// Read enough Opus data before sending the first video audio bytes so small
// FFmpeg reads do not consume the player's initial burst one page at a time.
func bufferVideoAudio(source io.Reader, bitrate string) (io.Reader, int, error) {
	buffer := make([]byte, max(2600, 3*bitrateKbps(bitrate)*1000/8))
	n, err := io.ReadFull(source, buffer)
	if err == io.EOF || err == io.ErrUnexpectedEOF {
		err = nil
	}
	return io.MultiReader(bytes.NewReader(buffer[:n]), source), n, err
}

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
				return "16k"
			} else if r >= -75 {
				return "12k"
			} else if r >= -82 {
				return "10k"
			} else if r >= -88 {
				return "8k"
			}
			return "6k"
		}
		return "16k"
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
	isVideo := q.Get("mode") == "video" && youtubeVideoID.MatchString(videoID)
	var videoCommandID int64
	// Some firmware rebuilds the stream URL and drops the playback mode.
	deviceMAC := store.NormalizeMAC(mac)
	if ownerID > 0 && deviceMAC != "" && youtubeVideoID.MatchString(videoID) {
		id, streamURL := h.audioStore.RecentVideoCommand(r.Context(), ownerID, videoID)
		confirmed, commandMAC := confirmedVideoStreamDevice(streamURL)
		if confirmed && commandMAC == "" {
			// No MAC in the MCP session. Correlate only with a device of the same owner.
			deviceOwner, _ := h.deviceStore.ResolveOwner(r.Context(), "", mac)
			confirmed = deviceOwner == ownerID
		} else {
			confirmed = confirmed && commandMAC == deviceMAC
		}
		if confirmed {
			isVideo = true
			videoCommandID = id
		}
	}
	if isVideo {
		bitrate = resolveVideoBitrate(requestedBr, rssi, chip, sampleRate)
	}
	safeVid := safeFilenameRegex.ReplaceAllString(videoID, "_")

	// 1. FAST PATH: Check local disk cache (Playlist track already transcoded)
	cachePattern := filepath.Join(h.cfg.AudioCacheDir, fmt.Sprintf("%s_*.opus", safeVid))
	if isVideo {
		cachePattern = filepath.Join(h.cfg.AudioCacheDir, fmt.Sprintf("%s_%d_%s.opus", safeVid, sampleRate, bitrate))
	}
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
				if videoCommandID != 0 {
					_ = h.audioStore.AckCommand(r.Context(), ownerID, videoCommandID)
				}

				totalBytes, _ := h.streamWithAdaptivePacing(r.Context(), w, streamReader, flusher, bitrate, sessionID, ownerID)
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
				h.notifyPlaybackEnd(ownerID, sessionID, reason, totalBytes)
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
	reconnectAtEOF := "1"
	if isVideo {
		reconnectAtEOF = "0"
	}
	args := []string{
		"-reconnect", "1",
		"-reconnect_at_eof", reconnectAtEOF,
		"-reconnect_streamed", "1",
		"-reconnect_delay_max", "5",
		"-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n",
	}
	if isVideo {
		args = append(args, "-rw_timeout", "8000000")
	}

	if startSec > 0 {
		args = append(args, "-ss", fmt.Sprintf("%.2f", startSec))
	}

	vbr := "on"
	if isVideo {
		vbr = "constrained"
	}
	args = append(args,
		"-i", resolveData.SourceURL,
		"-vn",
		"-ac", "1",
		"-ar", strconv.Itoa(sampleRate),
		"-c:a", "libopus",
		"-b:a", bitrate,
		"-vbr", vbr,
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
	if isVideo {
		buffered, n, err := bufferVideoAudio(stdout, bitrate)
		if err != nil || n == 0 || (startSec > 0 && n < 2600) {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
			log.Printf("[VIDEO AUDIO] Initial buffer unavailable for %s", videoID)
			if startSec > 0 && err == nil && n < 2600 {
				http.Error(w, "Track reached end of file", http.StatusRequestedRangeNotSatisfiable)
			} else {
				http.Error(w, "Unable to read video audio", http.StatusBadGateway)
			}
			return
		}
		streamReader = buffered
	} else if startSec > 0 {
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
	if videoCommandID != 0 {
		_ = h.audioStore.AckCommand(r.Context(), ownerID, videoCommandID)
	}
	var totalBytes int64
	defer func() {
		reason := "finished"
		if r.Context().Err() != nil {
			reason = "stopped"
		}
		h.notifyPlaybackEnd(ownerID, sessionID, reason, totalBytes)
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

	streamBytes, _ := h.streamWithAdaptivePacing(r.Context(), w, streamReader, flusher, bitrate, sessionID, ownerID)
	totalBytes = streamBytes

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
func (h *AudioStreamHandler) streamWithAdaptivePacing(ctx context.Context, w http.ResponseWriter, r io.Reader, flusher http.Flusher, bitrate string, sessionID string, ownerID int64) (int64, error) {
	bytesPerSec := parseBitrateBps(bitrate)
	buf := make([]byte, 2048)
	totalBytes := int64(0)
	loopCount := 0
	targetSendTime := time.Now()
	lastReportTime := time.Now()

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

			// Periodically notify Python backend about streamed bytes (every ~3s)
			if sessionID != "" && time.Since(lastReportTime) >= 3*time.Second {
				lastReportTime = time.Now()
				currentBytes := totalBytes
				go h.notifyPlaybackProgress(sessionID, ownerID, currentBytes)
			}

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
