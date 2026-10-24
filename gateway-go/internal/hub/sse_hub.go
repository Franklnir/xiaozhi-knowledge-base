package hub

import (
	"fmt"
	"sync"
	"time"
)

// SSEEvent represents a single event sent to Server-Sent Events subscribers.
type SSEEvent struct {
	ID        string         `json:"id"`
	Category  string         `json:"category"`
	Message   string         `json:"message"`
	Time      string         `json:"time"`
	Timestamp string         `json:"timestamp"`
	Metadata  map[string]any `json:"metadata"`
}

// SSEHub manages fan-out broadcast for SSE subscribers and maintains a ring buffer of recent logs.
type SSEHub struct {
	mu          sync.RWMutex
	subscribers map[chan SSEEvent]struct{}
	buffer      []SSEEvent
	maxBuffer   int
	counter     uint64
}

// NewSSEHub creates a new SSEHub with default buffer capacity.
func NewSSEHub() *SSEHub {
	return &SSEHub{
		subscribers: make(map[chan SSEEvent]struct{}),
		buffer:      make([]SSEEvent, 0, 60),
		maxBuffer:   60,
	}
}

// Subscribe registers a new subscriber channel and returns an unsubscribe cleanup function.
func (h *SSEHub) Subscribe() (chan SSEEvent, func()) {
	ch := make(chan SSEEvent, 128)

	h.mu.Lock()
	h.subscribers[ch] = struct{}{}
	h.mu.Unlock()

	unsubscribe := func() {
		h.mu.Lock()
		delete(h.subscribers, ch)
		h.mu.Unlock()
		close(ch)
	}

	return ch, unsubscribe
}

// Broadcast creates and broadcasts an event to all active subscribers.
func (h *SSEHub) Broadcast(category, message string, metadata map[string]any) SSEEvent {
	now := time.Now().UTC()
	h.mu.Lock()
	h.counter++
	eventID := fmt.Sprintf("log_%d_%d", now.UnixMilli(), h.counter)

	if metadata == nil {
		metadata = make(map[string]any)
	}

	evt := SSEEvent{
		ID:        eventID,
		Category:  category,
		Message:   message,
		Time:      now.Format("15:04:05"),
		Timestamp: now.Format(time.RFC3339),
		Metadata:  metadata,
	}

	// Ring buffer
	if len(h.buffer) >= h.maxBuffer {
		h.buffer = h.buffer[1:]
	}
	h.buffer = append(h.buffer, evt)

	// Fan-out non-blocking broadcast
	for ch := range h.subscribers {
		select {
		case ch <- evt:
		default:
			// Slow subscriber buffer full; drop to prevent memory growth
		}
	}
	h.mu.Unlock()

	return evt
}

// BroadcastEvent broadcasts an existing SSEEvent.
func (h *SSEHub) BroadcastEvent(evt SSEEvent) {
	if evt.ID == "" {
		evt.ID = fmt.Sprintf("log_%d", time.Now().UnixMilli())
	}
	if evt.Time == "" {
		evt.Time = time.Now().Format("15:04:05")
	}
	if evt.Timestamp == "" {
		evt.Timestamp = time.Now().UTC().Format(time.RFC3339)
	}
	if evt.Metadata == nil {
		evt.Metadata = make(map[string]any)
	}

	h.mu.Lock()
	if len(h.buffer) >= h.maxBuffer {
		h.buffer = h.buffer[1:]
	}
	h.buffer = append(h.buffer, evt)

	for ch := range h.subscribers {
		select {
		case ch <- evt:
		default:
		}
	}
	h.mu.Unlock()
}

// GetRecent returns a copy of the most recent events up to limit.
func (h *SSEHub) GetRecent(limit int) []SSEEvent {
	h.mu.RLock()
	defer h.mu.RUnlock()

	if limit <= 0 || limit > len(h.buffer) {
		limit = len(h.buffer)
	}

	start := len(h.buffer) - limit
	result := make([]SSEEvent, limit)
	copy(result, h.buffer[start:])
	return result
}

// SubscriberCount returns current active subscriber count.
func (h *SSEHub) SubscriberCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.subscribers)
}
