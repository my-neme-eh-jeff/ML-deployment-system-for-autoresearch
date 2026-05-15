#!/bin/sh
# Entrypoint for the autoresearch Job: refresh live state → dvc pull → exec auto_loop.
set -e

VENV_BIN=/app/.venv/bin
cd /app

REPO_OWNER="my-neme-eh-jeff"
REPO_NAME="ML-deployment-system-for-autoresearch"

# Resolve the live-state refresh commit. By default we pin to the current HEAD
# of `main` resolved ONCE at pod start — so every file in this refresh comes
# from the same commit, not a moving target. An operator can override via
# AUTORESEARCH_REFRESH_SHA for incident replay or to roll back the pod's
# view of state without rebuilding the image.
#
# Why pin at all: fetching from `…/raw/main/<path>` per file is a TOCTOU
# window — a merge to main between the first and last curl produces an
# inconsistent set of files (e.g. dvc.lock that doesn't match the params.yaml
# we just pulled). Pinning to a SHA closes that window and also makes the
# pod's behavior reproducible for the (commit SHA, env) tuple.
if [ -n "$AUTORESEARCH_REFRESH_SHA" ]; then
  REFRESH_SHA="$AUTORESEARCH_REFRESH_SHA"
  echo "[run-autoresearch] using operator-supplied AUTORESEARCH_REFRESH_SHA=$REFRESH_SHA"
else
  # `git ls-remote` is unauthenticated, no rate limit, and resolves to the
  # current HEAD commit SHA without cloning. If it fails (network blip,
  # registry-only env), fall back to keeping the image-baked files.
  REFRESH_SHA=$(git ls-remote "https://github.com/$REPO_OWNER/$REPO_NAME.git" refs/heads/main 2>/dev/null | awk '{print $1}' | head -1)
fi

if [ -z "$REFRESH_SHA" ]; then
  echo "[run-autoresearch] WARN: could not resolve refresh SHA; keeping image-baked files"
else
  echo "[run-autoresearch] refreshing live state pinned to $REPO_OWNER/$REPO_NAME @ $REFRESH_SHA"
  REFRESH_SHA="$REFRESH_SHA" "$VENV_BIN/python" - <<'PYEOF'
import hashlib
import os
import urllib.request
import urllib.error

OWNER = "my-neme-eh-jeff"
REPO = "ML-deployment-system-for-autoresearch"
SHA = os.environ["REFRESH_SHA"]
RAW = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{SHA}"
FILES = (
    "auto_experiment/history.tsv",
    "configs/params.yaml",
    "src/train.py",
    "src/preprocess.py",
    "src/features.py",
    "dvc.yaml",
    "dvc.lock",
)
for path in FILES:
    try:
        url = f"{RAW}/{path}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read()
        with open(path, "wb") as f:
            f.write(body)
        digest = hashlib.sha256(body).hexdigest()[:12]
        print(f"  ✓ {path} ({len(body)} bytes, sha256:{digest})")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        # Pinned-SHA fetches that 404 mean the path doesn't exist at that
        # commit (legitimate — older snapshots may pre-date a file). Keep
        # the image-baked version and log it.
        print(f"  ✗ {path} — kept image-baked version ({e})")
PYEOF
fi

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
