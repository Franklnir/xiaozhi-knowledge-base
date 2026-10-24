# Multi-stage build for ultra-lightweight Docker image (~15MB)
FROM golang:1.24-alpine AS builder

WORKDIR /app

# Cache dependencies
COPY go.mod go.sum ./
RUN go mod download

# Copy source code and compile statically
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-w -s" -o gateway ./cmd/server

# Minimal scratch runtime container
FROM alpine:3.20

RUN apk --no-cache add ca-certificates tzdata ffmpeg

WORKDIR /app
COPY --from=builder /app/gateway /app/gateway

EXPOSE 8080

CMD ["/app/gateway"]
