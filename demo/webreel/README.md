# WebReel demo setup

Scripted browser-demo configs for the autoresearch project. Each scene is a separate
`webreel record` invocation; the final video is stitched in DaVinci Resolve / iMovie / Final Cut.

## What's here

```
demo/
├── .gitignore                    # ignores captures/
├── captures/                     # output dir (raw clips + final stitch). Gitignored.
└── webreel/
    ├── .env.example              # cluster URLs + ArgoCD password
    ├── .gitignore                # ignores node_modules, .webreel, .env
    ├── package.json              # webreel pnpm dep + record/validate scripts
    ├── pnpm-lock.yaml            # pinned lockfile
    ├── configs/
    │   ├── smoke.config.json     # scene 0 — toolchain smoke test (static HTML, no cluster)
    │   ├── mlflow.config.json    # scene 1 — 20 runs, AUC trajectory, click into winning run
    │   ├── kfp.config.json       # scene 2 — DAG view, click preprocess + train nodes
    │   ├── argocd.config.json    # scene 3 — login, inference-api Synced/Healthy
    │   └── github-prs.config.json # scene 4 — merged autoresearch PRs, click into one, diff
    ├── smoke/
    │   └── index.html            # static page used by smoke.config.json
    └── scripts/
        └── capture-terminal.sh   # scene 5 — terminal recorded via asciinema (browser tool can't)
```

The skill at `.claude/skills/webreel/SKILL.md` is the official Vercel-Labs Claude Code
skill, copied verbatim from `vercel-labs/webreel`. Future Claude Code sessions in this
repo auto-discover it (the file is gitignored — local-only).

## Why webreel and not QuickTime / Playwright

| Concern | webreel | QuickTime | Playwright recordVideo |
|---|---|---|---|
| Cursor smoothing + click-zoom | ✅ native | ❌ | ❌ |
| Keystroke HUD overlay | ✅ native | ❌ | ❌ |
| Diff-able / reproducible | ✅ JSON config | ❌ live capture | ⚠️ JS code |
| Browser tabs only | ⚠️ yes — no terminal | ✅ records anything | ⚠️ browser only |
| Multi-pane / picture-in-picture | ❌ post-production | ❌ post-production | ❌ post-production |
| Output quality | ✅ MP4 H.264 @ 60fps | ✅ native | ⚠️ webm, low fps |

Verdict: webreel for the 4 browser scenes, asciinema for the terminal, DaVinci for the composite.

## End-to-end flow

```
                                ┌──────────────────────────────┐
                                │ STEP 1 — kick off the run    │
                                │ scripts/capture-terminal.sh  │ ──▶  ../captures/raw/05-terminal.{cast,gif,mp4}
                                │ wraps `asciinema rec`        │
                                └──────────────┬───────────────┘
                                               │
                                               ▼ (30 min — autoresearch runs)
                                               │
            ┌──────────────────────────────────┴────────────────────────────────────┐
            │ STEP 2 — record each browser scene AFTER the cluster has the state    │
            │                                                                       │
            │   npm run record:mlflow   ──▶  ../captures/raw/01-mlflow.mp4          │
            │   npm run record:kfp      ──▶  ../captures/raw/02-kfp.mp4             │
            │   npm run record:argocd   ──▶  ../captures/raw/03-argocd.mp4          │
            │   npm run record:github   ──▶  ../captures/raw/04-github.mp4          │
            │                                                                       │
            │   (npm run record:all also exists — runs them sequentially.)          │
            └──────────────────────────────────┬────────────────────────────────────┘
                                               │
                                               ▼
                                ┌──────────────────────────────┐
                                │ STEP 3 — stitch in editor    │
                                │ DaVinci Resolve (free) or    │
                                │ iMovie. Speed-ramp dead       │
                                │ stretches 4–8×.              │
                                └──────────────────────────────┘
```

## Quick start

```bash
cd demo/webreel
pnpm install                       # installs webreel locally (already done if this dir exists)
cp .env.example .env               # fill in MLFLOW_URL, KFP_URL, ARGOCD_URL, ARGOCD_PASSWORD
pnpm run validate:all              # check all configs against the official schema
```

(First `record` invocation auto-downloads `chrome-headless-shell` + `ffmpeg` to `~/.webreel/bin/`.
No separate `webreel install` step in v0.1.4 — it just-in-time fetches on demand.)

### Toolchain smoke test (no cluster needed)

A 7-step cluster-independent test against a local static HTML page — proves Chrome,
the step engine, and ffmpeg encoding are all wired up:

```bash
pnpm run record:smoke              # produces ../captures/raw/00-smoke.{mp4,png}
```

Verified output: H.264 baseline / yuvj420p / BT.709 / 1280×720 / 30fps / faststart MP4.
Matches Cursor / Vercel landing-page demo specs.

> **v0.1.4 timing quirk:** the recorded MP4 duration is much shorter than the wall-time
> of the steps (e.g. ~0.17s for ~4s of step actions in the smoke). webreel at this version
> captures one frame per step transition rather than continuous frames during pauses.
> The composite pass animates the cursor between those keyframes. For longer on-screen
> dwell time per step, raise the per-step `delay` and `pause.ms`. This is a known
> v0.1.x behaviour — expect the picture to change in 0.2.x as the project matures.

Then, **AFTER** the cluster is up + a real autoresearch run has populated MLflow/KFP/PRs:

```bash
# Preview each scene first (visible browser, no recording — for selector tweaks):
pnpm run preview:mlflow
pnpm run preview:kfp
pnpm run preview:argocd
pnpm run preview:github

# Once happy with the steps, record:
set -a; source .env; set +a       # load env vars into shell
pnpm run record:all                # records all 4 scenes back-to-back
```

Outputs land in `../captures/raw/`.

## The terminal pane

webreel cannot capture the terminal. Use the helper:

```bash
brew install asciinema agg ffmpeg                  # one-time
./scripts/capture-terminal.sh "make autoresearch-run AUTORESEARCH_N=20 AUTORESEARCH_HOURS=4.0"
```

Produces `../captures/raw/05-terminal.{cast,gif,mp4}`. The GIF is what you drop into the
video editor as a picture-in-picture overlay during scenes 1/2.

## Selectors — expect first preview to fail

All four configs use text-based matching wherever possible (`{"action":"click","text":"Chart"}`)
because React UIs (MLflow, KFP, ArgoCD) auto-generate CSS class names that drift between
versions. The first `npm run preview:<scene>` for each will probably need a couple of
selector adjustments — that's normal. webreel's `--verbose` flag tells you which step missed.

```bash
./node_modules/.bin/webreel preview mlflow-trajectory --verbose -c configs/mlflow.config.json
```

## ArgoCD auth caveat

webreel has no `storageState` or cookie persistence. The ArgoCD scene logs in inside the
recording (`type` action types your password from `$ARGOCD_PASSWORD`). Two options:

1. **Type a temporary password on camera** — `kubectl create secret`-rotate ArgoCD's admin
   password before recording, accept that the recorded password is one-use, rotate again after.
2. **Skip the login segment** — capture ArgoCD as a screenshot (after manual login) and
   composite it into the video as a still. Less impressive but zero leak risk.

If you go option 1: do NOT publish the raw clip anywhere — only the speed-ramped, edited
final video where keystroke HUDs are blurred or cut. The `keystroke HUD` in webreel
displays each key as you type — visible in slow-frame screenshots.

## Skill integration (Claude Code)

The skill at `.claude/skills/webreel/SKILL.md` triggers on phrases like "edit the webreel
config" or "record the demo." If you're in a Claude Code session and want help iterating
on a config, mention webreel and the skill auto-loads.

## Vercel pattern reference

This setup mirrors what Screen Studio / Vercel WebReel users do — per-pane capture,
composite in post, speed-ramp dead air. Confirmed customers on screen.studio:
Stripe, Vercel, Google, Adobe, Framer, Raycast. Cursor's marketing videos look
identical aesthetically (auto-zoom, smoothed cursor, motion blur) — likely the same stack.
