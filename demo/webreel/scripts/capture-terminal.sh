#!/usr/bin/env bash
# Capture the autoresearch terminal session as an asciinema cast,
# then render it as a stylized GIF (for picture-in-picture overlay) and as MP4.
#
# webreel is browser-only — it can't record `kubectl logs`. This script handles
# the terminal pane separately so the demo composite has a real log feed.
#
# Prerequisites (one-time):
#   brew install asciinema agg ffmpeg
#
# Usage:
#   ./scripts/capture-terminal.sh "make autoresearch-run AUTORESEARCH_N=20 AUTORESEARCH_HOURS=4.0"
#
# Output:
#   ../captures/raw/05-terminal.cast   (raw asciinema cast — replay with `asciinema play`)
#   ../captures/raw/05-terminal.gif    (rendered GIF — drop into video editor as overlay)
#   ../captures/raw/05-terminal.mp4    (rendered MP4 — same content, smaller for stitching)

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/../../captures/raw"
mkdir -p "$out"

cmd="${1:-make autoresearch-logs}"

cast="$out/05-terminal.cast"
gif="$out/05-terminal.gif"
mp4="$out/05-terminal.mp4"

echo "── capturing: $cmd ──"
echo "── output:    $cast ──"
echo "── ctrl-d to end ──"

asciinema rec --overwrite --command "$cmd" "$cast"

echo "── rendering GIF (agg, ~4x speed via --speed) ──"
agg --font-family 'JetBrains Mono' --theme monokai --speed 4 "$cast" "$gif"

echo "── rendering MP4 (ffmpeg from GIF) ──"
ffmpeg -y -i "$gif" -movflags +faststart -pix_fmt yuv420p \
       -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" "$mp4" -loglevel error

echo
echo "── done ──"
ls -lh "$cast" "$gif" "$mp4"
