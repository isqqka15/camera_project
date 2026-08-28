#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$REPO_DIR/main/docker-compose.yml"
SERIAL_DEVICE="${ARDUINO_DEVICE:-/dev/ttyUSB0}"

cd "$REPO_DIR"

echo "Updating source from origin/main..."
git pull origin main

if [[ -e "$SERIAL_DEVICE" ]]; then
    echo "Setting permissions on $SERIAL_DEVICE..."
    chmod 666 "$SERIAL_DEVICE"
else
    echo "Warning: $SERIAL_DEVICE is not present; continuing without the RFID device." >&2
fi

echo "Rebuilding and starting containers..."
docker-compose -f "$COMPOSE_FILE" up -d --build

echo "Removing unused Docker images..."
docker image prune -f

echo "Running containers:"
docker-compose -f "$COMPOSE_FILE" ps
