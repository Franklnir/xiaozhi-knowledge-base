package hub

import (
	"fmt"
	"sync"
	"time"
)

// UserEvent represents a real-time event dispatched to a specific user.
type UserEvent struct {
	ID        string         `json:"id"`
	UserID    int64          `json:"user_id"`
	Event     string         `json:"event"`
	Data      map[string]any `json:"data"`
	Time      string         `json:"time"`
	Timestamp string         `json:"timestamp"`
}

// UserEventHub manages isolated, per-user fan-out SSE broadcasting.
// Strictly multi-tenant safe: subscribers for User A will NEVER receive events for User B.
type UserEventHub struct {
	mu          sync.RWMutex
	subscribers map[int64]map[chan UserEvent]struct{}
	counter     uint64
}

// NewUserEventHub creates an initialized UserEventHub.
func NewUserEventHub() *UserEventHub {
	return &UserEventHub{
		subscribers: make(map[int64]map[chan UserEvent]struct{}),
	}
}

// Subscribe registers a new subscriber channel for a specific user ID.
// Returns the event channel and an unsubscribe cleanup function.
func (h *UserEventHub) Subscribe(userID int64) (chan UserEvent, func()) {
	ch := make(chan UserEvent, 64)

	h.mu.Lock()
	if _, exists := h.subscribers[userID]; !exists {
		h.subscribers[userID] = make(map[chan UserEvent]struct{})
	}
	h.subscribers[userID][ch] = struct{}{}
	h.mu.Unlock()

	unsubscribe := func() {
		h.mu.Lock()
		if userSubs, exists := h.subscribers[userID]; exists {
			delete(userSubs, ch)
			if len(userSubs) == 0 {
				delete(h.subscribers, userID)
			}
		}
		h.mu.Unlock()
		close(ch)
	}

	return ch, unsubscribe
}

// Broadcast dispatches an event strictly to subscribers belonging to userID.
// Non-blocking select ensures a slow subscriber never blocks other users or server threads.
func (h *UserEventHub) Broadcast(userID int64, event string, data map[string]any) UserEvent {
	now := time.Now().UTC()
	h.mu.Lock()
	h.counter++
	eventID := fmt.Sprintf("evt_%d_%d", now.UnixMilli(), h.counter)

	if data == nil {
		data = make(map[string]any)
	}

	evt := UserEvent{
		ID:        eventID,
		UserID:    userID,
		Event:     event,
		Data:      data,
		Time:      now.Format("15:04:05"),
		Timestamp: now.Format(time.RFC3339),
	}

	userSubs, exists := h.subscribers[userID]
	if exists {
		for ch := range userSubs {
			select {
			case ch <- evt:
			default:
				// Buffer full for this subscriber, drop to prevent memory growth
			}
		}
	}
	h.mu.Unlock()

	return evt
}

// SubscriberCount returns active subscriber count for a specific user ID.
func (h *UserEventHub) SubscriberCount(userID int64) int {
	h.mu.RLock()
	defer h.mu.RUnlock()

	if userSubs, exists := h.subscribers[userID]; exists {
		return len(userSubs)
	}
	return 0
}

// TotalSubscribers returns total active SSE connections across all users.
func (h *UserEventHub) TotalSubscribers() int {
	h.mu.RLock()
	defer h.mu.RUnlock()

	total := 0
	for _, userSubs := range h.subscribers {
		total += len(userSubs)
	}
	return total
}

// ActiveUserCount returns number of unique users with at least one active connection.
func (h *UserEventHub) ActiveUserCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.subscribers)
}
