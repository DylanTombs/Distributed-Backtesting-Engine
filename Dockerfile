# =============================================================================
# Dockerfile — Google Cloud Run image for the hosted API (Phase 10.1, ADR-050)
#
# Built remotely by Cloud Build (`gcloud run deploy --source .`), so the C++
# binary is compiled INSIDE the image — no local Docker daemon and no risk of
# shipping a stale or wrong-arch binary. Transparent-strategy engine only
# (no LibTorch): the experimental ML path 400s cleanly on this image.
#
# Both stages are debian bookworm so the compiled binary and the runtime share
# one glibc.
# =============================================================================

# ---- Stage 1: compile the rules engine --------------------------------------
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backtester/ backtester/

# BUILD_TESTING=OFF skips the GoogleTest download; LibTorch is absent so only
# the `backtest` target is configured.
RUN cmake -S backtester -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTING=OFF \
    && cmake --build build --target backtest --parallel

# ---- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY research/ ./research/
COPY backtester/data/ ./backtester/data/
COPY backtest_config.yaml .
COPY --from=builder /build/build/backtest ./backtester/backtest

ENV PROJECT_ROOT=/app \
    OHLCV_CACHE_DIR=/tmp/ohlcv \
    CACHE_WARMUP_DISABLED=1

# Cloud Run injects PORT (default 8080); shell form so it expands.
CMD exec uvicorn research.api.app:app --host 0.0.0.0 --port ${PORT:-8080}
