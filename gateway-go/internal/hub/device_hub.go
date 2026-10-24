package hub

import (
	"sync"
	"time"
)

// ConnectedDevice represents an active ESP32 board WebSocket connection.
type ConnectedDevice struct {
	MAC         string    `json:"mac"`
	CleanMAC    string    `json:"clean_mac"`
	IP          string    `json:"ip"`
	Chip        string    `json:"chip"`
	OwnerID     int64     `json:"owner_id"`
	ConnectedAt time.Time `json:"connected_at"`
	LastPing    time.Time `json:"last_ping"`
}

// DeviceHub maintains in-memory tracking of all connected ESP32 boards.
type DeviceHub struct {
	mu      sync.RWMutex
	devices map[string]*ConnectedDevice
}

// NewDeviceHub creates a new DeviceHub.
func NewDeviceHub() *DeviceHub {
	return &DeviceHub{
		devices: make(map[string]*ConnectedDevice),
	}
}

// Register adds or updates a connected device.
func (h *DeviceHub) Register(dev *ConnectedDevice) {
	if dev == nil || dev.MAC == "" {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	h.devices[dev.MAC] = dev
}

// Unregister removes a device by MAC address.
func (h *DeviceHub) Unregister(mac string) {
	if mac == "" {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.devices, mac)
}

// UpdatePing records heartbeat/activity from a device.
func (h *DeviceHub) UpdatePing(mac string) {
	if mac == "" {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if dev, ok := h.devices[mac]; ok {
		dev.LastPing = time.Now()
	}
}

// Get looks up a connected device by MAC.
func (h *DeviceHub) Get(mac string) (*ConnectedDevice, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	dev, ok := h.devices[mac]
	return dev, ok
}

// Count returns the number of currently connected devices.
func (h *DeviceHub) Count() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.devices)
}

// List returns a slice of all currently connected devices.
func (h *DeviceHub) List() []*ConnectedDevice {
	h.mu.RLock()
	defer h.mu.RUnlock()
	list := make([]*ConnectedDevice, 0, len(h.devices))
	for _, dev := range h.devices {
		list = append(list, dev)
	}
	return list
}
