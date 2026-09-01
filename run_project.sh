#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$ROOT_DIR/main"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-8000}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but not installed." >&2
  exit 1
fi

if docker compose -f "$COMPOSE_FILE" config >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE")
elif command -v docker-compose >/dev/null 2>&1 && docker-compose -f "$COMPOSE_FILE" config >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose -f "$COMPOSE_FILE")
else
  echo "Unable to find a working docker compose command." >&2
  exit 1
fi

cd "$ROOT_DIR"
echo "Starting fare enforcement service..."
"${COMPOSE_CMD[@]}" up --build -d

echo "Waiting for health check..."
for _ in $(seq 1 30); do
  if curl -fsS "http://${APP_HOST}:${APP_PORT}/health" >/dev/null 2>&1; then
    echo "Service is healthy: http://${APP_HOST}:${APP_PORT}/health"
    exit 0
  fi
  sleep 1
done

echo "The service did not become healthy in time." >&2
"${COMPOSE_CMD[@]}" logs --tail=100 app >&2
exit 1
