#!/usr/bin/env bash
# deploy_cloudrun.sh — deploy the hosted API to Google Cloud Run (Phase 10.1,
# ADR-050). Cloud Build compiles the C++ engine inside the image, so no local
# Docker daemon is needed.
#
# One-time prerequisites:
#   gcloud auth login
#   gcloud projects create <PROJECT_ID>   (or use an existing one)
#   gcloud config set project <PROJECT_ID>
#   gcloud billing accounts list && gcloud billing projects link ...  (or via console)
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
#
# Secrets are set separately so they never appear in this script or the repo:
#   gcloud run services update tradingtransformer-api --region "$REGION" \
#       --update-env-vars API_KEYS=<key1>,ANTHROPIC_API_KEY=<your-key>
set -euo pipefail

cd "$(dirname "$0")/.."

SERVICE="tradingtransformer-api"
# europe-west1: Tier-1 pricing region (free-tier CPU/memory allowances apply)
REGION="${CLOUDRUN_REGION:-europe-west1}"

echo "==> Deploying $SERVICE to Cloud Run ($REGION) via Cloud Build"
gcloud run deploy "$SERVICE" \
    --source . \
    --region "$REGION" \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 2 \
    --concurrency 20 \
    --timeout 120

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" \
      --format 'value(status.url)')

echo "==> Verifying health at $URL"
curl --fail --max-time 30 "$URL/api/health"
echo
echo "Deployed: $URL"
echo "Next: set secrets (see header comment), then re-run the health check"
echo "with an X-API-Key header to confirm auth is enforced."
