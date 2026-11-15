package handler

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const (
	videoAudioChunkSize   = 1536
	videoAudioStartupSecs = 4
	videoAudioAheadSecs   = 6
	// Each 60 ms Opus packet has an Ogg page header and a segment table.
	videoAudioOggOverhead = 500
)

func bitrateKbps(bitrate string) int {
	kbps, _ := strconv.Atoi(strings.TrimSuffix(bitrate, "k"))
	return kbps
}

func videoAudioByteRate(bitrate string) int {
	return bitrateKbps(bitrate)*1000/8 + videoAudioOggOverhead
}

func videoAudioStartupBytes(bitrate string) int {
	return videoAudioByteRate(bitrate) * videoAudioStartupSecs
}

func bufferVideoAudio(source io.Reader, bitrate string) (io.Reader, int, error) {
	buffer := make([]byte, videoAudioStartupBytes(bitrate))
	n, err := io.ReadFull(source, buffer)
	if err == io.EOF || err == io.ErrUnexpectedEOF {
		err = nil
	}
	return io.MultiReader(bytes.NewReader(buffer[:n]), source), n, err
}

type videoAudioChunk struct {
	data []byte
	err  error
}

// Read ahead while sending at playback speed. FFmpeg can refill the bounded
// queue as soon as the upstream connection recovers from a short interruption.
func readVideoAudio(ctx context.Context, source io.Reader, bitrate string) <-chan videoAudioChunk {
	maxQueuedBytes := videoAudioByteRate(bitrate) * videoAudioAheadSecs
	chunks := make(chan videoAudioChunk, (maxQueuedBytes+videoAudioChunkSize-1)/videoAudioChunkSize)
	go func() {
		defer close(chunks)
		for ctx.Err() == nil {
			data := make([]byte, videoAudioChunkSize)
			n, err := io.ReadFull(source, data)
			if err == io.ErrUnexpectedEOF {
				err = io.EOF
			}
			select {
			case chunks <- videoAudioChunk{data: data[:n], err: err}:
			case <-ctx.Done():
				return
			}
			if err != nil {
				return
			}
		}
	}()
	return chunks
}

func (h *AudioStreamHandler) streamVideoAudio(ctx context.Context, w http.ResponseWriter, source io.Reader, flusher http.Flusher, bitrate, sessionID string, ownerID int64) (int64, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	chunks := readVideoAudio(ctx, source, bitrate)
	startupBytes := int64(videoAudioStartupBytes(bitrate))
	bytesPerSec := videoAudioByteRate(bitrate) * 145 / 100
	var totalBytes int64
	var nextWrite time.Time
	lastReport := time.Now()
	for {
		select {
		case <-ctx.Done():
			return totalBytes, ctx.Err()
		case chunk, ok := <-chunks:
			if !ok {
				return totalBytes, ctx.Err()
			}
			if len(chunk.data) > 0 {
				if delay := time.Until(nextWrite); delay > 0 {
					timer := time.NewTimer(delay)
					select {
					case <-timer.C:
					case <-ctx.Done():
						timer.Stop()
						return totalBytes, ctx.Err()
					}
				}
				n, err := w.Write(chunk.data)
				totalBytes += int64(n)
				if err != nil {
					return totalBytes, err
				}
				if flusher != nil {
					flusher.Flush()
				}
				now := time.Now()
				if totalBytes > startupBytes {
					pacedBytes := min(int64(n), totalBytes-startupBytes)
					if nextWrite.Before(now) {
						nextWrite = now
					}
					nextWrite = nextWrite.Add(time.Duration(pacedBytes) * time.Second / time.Duration(bytesPerSec))
				}
				if sessionID != "" && now.Sub(lastReport) >= 3*time.Second {
					lastReport = now
					go h.notifyPlaybackProgress(sessionID, ownerID, totalBytes)
				}
			}
			if chunk.err == io.EOF {
				return totalBytes, nil
			}
			if chunk.err != nil {
				return totalBytes, chunk.err
			}
		}
	}
}
