#!/bin/sh
# Entrypoint for the autoresearch Job: refresh live state → dvc pull → exec auto_loop.
set -e

VENV_BIN=/app/.venv/bin
cd /app

# Refresh live state from origin/main BEFORE the loop reads it.
#
# The image bakes in whatever was on main when CI built it. State-only commits
# (like a fresh `make reset-for-fresh-run` push using [skip ci]) don't trigger
# a rebuild, so the image can be stale by hours or days. These four files are
# what auto_loop.py and Claude both read each iter; refreshing them prevents
# the loop from chasing the previous run's history.
echo "[run-autoresearch] refreshing live state from origin/main..."
"$VENV_BIN/python" - <<'PYEOF'
import urllib.request, urllib.error, os
RAW = "https://raw.githubusercontent.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch/main"
FILES = (
    "auto_experiment/history.tsv",
    "configs/params.yaml",
    "src/train.py",
    "src/preprocess.py",
)
for path in FILES:
    try:
        urllib.request.urlretrieve(f"{RAW}/{path}", path)
        size = os.path.getsize(path)
        print(f"  ✓ {path} ({size} bytes)")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"  ✗ {path} — kept baked-in version ({e})")
PYEOF

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
