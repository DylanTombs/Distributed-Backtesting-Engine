#!/usr/bin/env bash
# deploy_api.sh — build the linux/amd64 ml_backtest binary and deploy the
# hosted API to Fly.io (Phase 7.4).
#
# Prerequisites: docker, flyctl (authenticated), and Fly secrets already set:
#   flyctl secrets set API_KEYS=... ANTHROPIC_API_KEY=... ALLOWED_EXTENSION_IDS=...
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Building linux/amd64 backtester image (pins binary to current SHA)"
docker build --platform linux/amd64 -f Dockerfile.backtester -t tt-backtester \
    --build-arg CMAKE_TARGET=backtest .

echo "==> Extracting transparent-strategy binary (no LibTorch, 8.6)"
container_id=$(docker create tt-backtester)
trap 'docker rm -f "$container_id" >/dev/null' EXIT
docker cp "$container_id:/app/backtest" backtester/backtest

echo "==> Deploying to Fly.io"
flyctl deploy --remote-only

echo "==> Verifying health"
curl --fail --max-time 10 "https://tradingtransformer-api.fly.dev/api/health"
echo
echo "Deploy complete."
