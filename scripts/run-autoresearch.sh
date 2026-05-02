#!/bin/sh
# Container entrypoint for the autoresearch K8s Job.
#
# 1. Fetches data from the DVC remote (Workload Identity provides GCS auth).
# 2. Hands control to auto_experiment.auto_loop with whatever flags the Job passes.
#
# Override default flags by setting `args:` on the container in the Job manifest.
set -e

VENV_BIN=/app/.venv/bin

echo "[run-autoresearch] dvc pull (fetching data from gs://customer-churn-dvc-remote)..."
cd /app
"$VENV_BIN/dvc" pull

echo "[run-autoresearch] starting auto_experiment.auto_loop with args: $*"
exec "$VENV_BIN/python" -m auto_experiment.auto_loop "$@"
