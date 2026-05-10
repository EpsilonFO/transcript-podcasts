#!/usr/bin/env bash
# Pull latest code from GitHub and redeploy the transcript-podcasts container.
# Run on the VPS after pushing changes from your local machine.

set -euo pipefail

cd "$(dirname "$0")"

echo "==> Pulling latest code"
git pull --ff-only

echo "==> Rebuilding and restarting container"
docker compose up -d --build

echo "==> Waiting for uvicorn to come up"
sleep 2

echo "==> Recent logs:"
docker compose logs --tail 20 transcript-backend

echo
echo "Deploy done."
