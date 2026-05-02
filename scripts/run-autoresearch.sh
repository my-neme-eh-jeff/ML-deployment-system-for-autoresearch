#!/bin/sh
# Entrypoint for the autoresearch Job: dvc pull → exec auto_loop with passed args.
set -e

VENV_BIN=/app/.venv/bin
cd /app

# DVC needs a git repo to find the project root, but we don't bake .git into the
# image. Initialize a throwaway one in the writable layer.
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  git init -q
  git config user.email "autoresearch@bot.local"
  git config user.name "autoresearch-bot"
  git add -A >/dev/null 2>&1 || true
  git commit -qm "initial" >/dev/null 2>&1 || true
fi

echo "[run-autoresearch] dvc pull..."
"$VENV_BIN/dvc" pull

echo "[run-autoresearch] starting auto_loop $*"
exec "$VENV_BIN/python" -m auto_experiment.auto_loop "$@"
