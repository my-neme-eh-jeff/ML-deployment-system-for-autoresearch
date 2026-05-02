#!/bin/sh
# Container entrypoint for the autoresearch K8s Job.
#
# 1. Fetches data from the DVC remote (Workload Identity provides GCS auth).
# 2. Hands control to auto_experiment.auto_loop with whatever flags the Job passes.
#
# Override default flags by setting `args:` on the container in the Job manifest.
set -e

VENV_BIN=/app/.venv/bin
cd /app

# DVC expects a git repo to determine project root. The container starts without
# .git (we don't copy it — keeps the image small). Initialize a throwaway git
# repo so dvc commands work. Stays in the writable container layer; not pushed.
echo "[run-autoresearch] initializing throwaway git repo for DVC..."
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  git init -q
  git config user.email "autoresearch@bot.local"
  git config user.name "autoresearch-bot"
  git add -A >/dev/null 2>&1 || true
  git commit -qm "initial" >/dev/null 2>&1 || true
fi

echo "[run-autoresearch] dvc pull (fetching data from gs://customer-churn-dvc-remote)..."
"$VENV_BIN/dvc" pull

echo "[run-autoresearch] starting auto_experiment.auto_loop with args: $*"
exec "$VENV_BIN/python" -m auto_experiment.auto_loop "$@"
