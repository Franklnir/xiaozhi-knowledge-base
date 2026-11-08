package config

import (
	"fmt"
	"net/url"
	"os"

	"github.com/joho/godotenv"
)

type Config struct {
	Port             string
	DatabaseURL      string
	ServerBaseURL    string
	PythonBackendURL string
	AudioCacheDir    string
	MQTTBrokerURL    string
	MQTTUsername     string
	MQTTPassword     string
	AppSecretKey     string
	InternalAPIKey   string
}

func LoadConfig() *Config {
	// Try loading from .env if present (in local directory or parent)
	_ = godotenv.Load(".env")
	_ = godotenv.Load("../.env")

	port := os.Getenv("GATEWAY_PORT")
	if port == "" {
		port = os.Getenv("PORT")
	}
	if port == "" {
		port = "8080"
	}

	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		host := getEnvOrDefault("POSTGRES_HOST", "localhost")
		pgPort := getEnvOrDefault("POSTGRES_PORT", "5432")
		db := getEnvOrDefault("POSTGRES_DB", "xiaozhi")
		user := getEnvOrDefault("POSTGRES_USER", "xiaozhi_app")
		password := getEnvOrDefault("POSTGRES_PASSWORD", "xiaozhi_secret")
		sslmode := getEnvOrDefault("POSTGRES_SSLMODE", "prefer")

		dbURL = fmt.Sprintf("postgres://%s:%s@%s:%s/%s?sslmode=%s",
			url.QueryEscape(user),
			url.QueryEscape(password),
			host,
			pgPort,
			db,
			sslmode,
		)
	}

	baseURL := getEnvOrDefault("SERVER_BASE_URL", "https://xiaozhiscig.biz.id")
	pythonURL := getEnvOrDefault("PYTHON_BACKEND_URL", "http://127.0.0.1:8080")
	audioCacheDir := getEnvOrDefault("AUDIO_CACHE_DIR", "/app/data/audio_cache")
	mqttURL := getEnvOrDefault("MQTT_BROKER_URL", "tcp://xiaozhi-mosquitto:1883")
	mqttUser := os.Getenv("MQTT_USERNAME")
	mqttPass := os.Getenv("MQTT_PASSWORD")
	appSecret := getEnvOrDefault("APP_SECRET_KEY", "")
	internalKey := getEnvOrDefault("GATEWAY_INTERNAL_KEY", appSecret)

	return &Config{
		Port:             port,
		DatabaseURL:      dbURL,
		ServerBaseURL:    baseURL,
		PythonBackendURL: pythonURL,
		AudioCacheDir:    audioCacheDir,
		MQTTBrokerURL:    mqttURL,
		MQTTUsername:     mqttUser,
		MQTTPassword:     mqttPass,
		AppSecretKey:     appSecret,
		InternalAPIKey:   internalKey,
	}
}

func getEnvOrDefault(key, fallback string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return fallback
}
