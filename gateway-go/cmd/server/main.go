package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"

	"gateway-go/internal/config"
	"gateway-go/internal/db"
	"gateway-go/internal/handler"
	"gateway-go/internal/store"
)

func main() {
	cfg := config.LoadConfig()

	log.Println("==================================================")
	log.Println("  Xiaozhi IoT Gateway (Go - Chi / net/http)")
	log.Printf("  Target Port : %s", cfg.Port)
	log.Println("==================================================")

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Initialize PostgreSQL Connection Pool
	database, err := db.NewPostgresPool(ctx, cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("[FATAL] Failed to connect to database: %v", err)
	}
	defer database.Close()

	// Stores
	deviceStore := store.NewDeviceStore(database)
	audioStore := store.NewAudioStore(database)

	// Handlers
	devHandler := handler.NewDeviceHandler(cfg, deviceStore, audioStore)
	streamHandler := handler.NewAudioStreamHandler(cfg, deviceStore, audioStore)

	// Router
	r := chi.NewRouter()

	// Essential Middlewares (Zero-bloat)
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)

	// CORS configuration
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{"*"},
		AllowedMethods:   []string{"GET", "POST", "OPTIONS"},
		AllowedHeaders:   []string{"*"},
		AllowCredentials: true,
		MaxAge:           300,
	}))

	// Health Check
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"status":"ok","service":"gateway-go"}`))
	})

	// Device Polling & Audio Queue Endpoints
	r.Get("/api/device/audio/commands", devHandler.HandleGetCommands)
	r.Post("/api/device/audio/ack", devHandler.HandleAck)
	r.Post("/api/device/audio/status", devHandler.HandleStatus)

	// High-Performance Audio Streaming Pipeline
	r.Get("/api/audio/stream/{video_id}", streamHandler.HandleStream)

	server := &http.Server{
		Addr:        ":" + cfg.Port,
		Handler:     r,
		ReadTimeout: 30 * time.Second,
		// WriteTimeout is 0 (disabled) to allow long-lived audio streaming to ESP32 without interruption
		WriteTimeout: 0,
		IdleTimeout:  120 * time.Second,
	}

	// Server runner
	go func() {
		log.Printf("[HTTP] Gateway listening on http://0.0.0.0:%s", cfg.Port)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[FATAL] Server closed with error: %v", err)
		}
	}()

	// Graceful Shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM)
	<-quit

	log.Println("[SHUTDOWN] Shutting down gateway gracefully...")
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutdownCancel()

	if err := server.Shutdown(shutdownCtx); err != nil {
		log.Printf("[ERROR] Server forced to shutdown: %v", err)
	}
	log.Println("[SHUTDOWN] Gateway stopped cleanly.")
}
