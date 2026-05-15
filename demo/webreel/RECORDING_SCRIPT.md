# Autoresearch demo — recording script + execution plan

> **Single source of truth for shooting the demo.** Read top to bottom on recording day.
> Supersedes earlier scattered planning (`RECORDING_PLAN_TRUST_FIRST.md`, README hints).
> All council-flagged config issues have been applied to `configs/*.config.json`.

## TL;DR

| | |
|---|---|
| **What this is** | An *agentic release-control demo*, not a fraud-detection benchmark. |
| **Length target** | 90s LinkedIn cut + 4min long-form cut from the same masters. |
| **Visual motif** | The same run ID appears in KFP, MLflow tag, PR body, deployment annotation, and `/health`. |
| **Tooling** | WebReel for the 4 browser scenes, asciinema for the terminal, DaVinci Resolve to stitch. |
| **Hard rule** | Do not record ArgoCD login. Do not caption a count (e.g. "20 runs") unless the UI shows it. |

---

## 1. What this demo actually proves

Three claims, each backed by visible receipts:

1. **An LLM proposes code, the cluster runs it, only metric-winning candidates ship.** Receipt: MLflow run → KFP run → merged PR → live `/health` showing the new version.
2. **The release path is auditable.** Receipt: same KFP run ID visible across MLflow tag, PR body, and `k8s/deployment.yaml` annotation.
3. **The cluster is the source of truth, not a slide.** Receipt: real terminal, real `curl`, real ArgoCD Synced/Healthy state.

What the demo is **NOT** trying to claim: that the fraud model is production-grade, that "any binary CSV plugs in" is fully validated (it's tested on two datasets), or that this is HA. Those overclaims are explicitly rejected in §10.

---

## 2. The 6-beat narrative arc

Total raw footage target: ~5 min. Final 90s cut keeps beats 1, 5, 6, 7. Final 4min cut keeps everything.

| # | Beat | Length | What's on screen |
|---|---|---|---|
| 1 | **Hook** | 15s | Composite frame: MLflow `@champion vN`, GitHub merged PR (Verified badge), ArgoCD Synced/Healthy, terminal `curl /health` showing `model_version`. |
| 2 | **System map** | 20s | Static slide from the README architecture diagram. Mermaid render is fine. |
| 3 | **Live kick-off** | 35s | Terminal: `git rev-parse --short HEAD` then `make autoresearch-run AUTORESEARCH_N=…`. Speed-ramp the wait. |
| 4 | **KFP proof** | 35s | DAG view of one successful run. preprocess + train + evaluate green. Linger on **the KFP run ID** (top of details panel). |
| 5 | **MLflow proof** | 50s | Runs list → sort by AUC → Chart → click winning run → Tags section → **hover `kfp_run_id`** (must match the KFP ID from beat 4). |
| 6 | **GitHub accountability proof** | 55s | Merged `auto-exp:` PRs list → click most recent → PR body containing run IDs → Files changed → params.yaml diff → Verified badge. |
| 7 | **ArgoCD + live API proof** | 45s | Synced/Healthy → app resource tree → **hover `mlops/classifier-version`** annotation. Cut to terminal `curl /health \| jq` showing same version. |
| 8 | **Scope decisions** | 30s | Spoken-over slide. "Single-zone GKE Autopilot, FastAPI serving by design, signed PRs + metric gates as the trust boundary. Production-hardening = OTel + policy on LLM diffs + traffic-aware serving — next layer, not this layer." |

> **Why this order:** kick-off proves it's real → KFP/MLflow/GitHub prove the audit chain → ArgoCD/API prove it actually shipped → scope slide demonstrates seniority. Demo crescendos at the ID-matching reveal across beats 4–7.

---

## 3. The ID-matching visual motif (the credibility lever)

The single most persuasive thing in this demo: **the same identifier reappears in five places**, and the viewer can see it each time. Threading this through requires capturing the same ID in each scene's footage, then drawing the connection in post (callouts, color highlight, or a small overlay).

| Scene | Where the ID appears | What to capture |
|---|---|---|
| KFP details | Top of run-details panel (the UUID) | A few seconds of the panel visible, ID in frame |
| MLflow run tags | `kfp_run_id` tag value | Hover step — already in `mlflow.config.json` |
| GitHub PR body | The PR description lists `KFP run: <id>`, `MLflow run: <run_id>`, `version: vN` | Linger on PR body before clicking Files changed |
| `k8s/deployment.yaml` annotation | `mlops/classifier-version` and `mlops/classifier-run-id` | Hover step — already in `argocd.config.json` |
| `curl /health` | JSON response includes `model_version` + (if exposed) `run_id` | Terminal capture beat 7 |

> Cross-reference: commit `e3d992f` (`fix(kfp_run_id): pass tag client-side`) is what enables this — the tag is actually populated now. Before that commit it wouldn't have been there reliably.

In post: pick a single color (e.g. `#22d3ee`), and every time the same ID reappears in a different surface, briefly highlight it with that color. Viewer recognizes the pattern by beat 6 and the demo lands.

---

## 4. Preflight checklist (run before any recording)

```bash
# === Repo state ===
cd ~/Desktop/code/experiment/customer_churn
git status --short                       # should be clean (or only your audit MDs)
git rev-parse --short HEAD               # note this — useful in narration

# === Live API receipt ===
curl -s http://34.47.242.89/health | jq .
# expect: model_version + model_run_id, both non-null

# === Cluster status ===
kubectl get applications.argoproj.io -A  # all Synced/Healthy
kubectl get pods -n inference            # 2/2 running, all Ready
kubectl get pods -n mlflow               # 1/1, MLflow up
kubectl get pods -n kubeflow | grep ml-pipeline  # 1/1, KFP up

# === GitOps manifest proof ===
rg -n 'mlops/classifier-version|mlops/classifier-run-id|image:' k8s/deployment.yaml

# === Recent PR trail (use the REAL prefix — auto-exp not autoresearch) ===
gh pr list --state merged --search 'auto-exp' --limit 10

# === MLflow champion ===
MLFLOW_TRACKING_URI=http://34.180.20.197:5000 uv run python -c "
import mlflow; c = mlflow.MlflowClient()
v = c.get_model_version_by_alias('classifier', 'champion')
print(f'@champion → v{v.version} (run_id={v.run_id})')
"
```

**Manual UI checks** (before pressing record):
- MLflow Experiments view: the table is sorted, the Chart view is preconfigured for AUC trajectory, the winning run's Tags include `kfp_run_id`.
- KFP: the latest run is a real autoresearch run (not a bootstrap/manual test). Click in once to verify the DAG looks clean.
- GitHub: open the PR list with `is:pr is:merged auto-exp` in the search box. Confirm there are real merged PRs visible.
- ArgoCD: app is **already authenticated** in a Chrome profile. Test by reloading.

**Paste-into-notes block** for narration:
```
HEAD:                <git rev>
@champion:           v<N>  (run_id=<mlflow run id>)
kfp_run_id (tag):    <kfp uuid>
recent PR:           #<num>  (auto-exp: <name> | AUC ... → ...)
curl /health output: model_version=<N>  model_run_id=<run id>
```

Keep this open in a side notes window during recording.

---

## 5. The recording sequence (step-by-step)

Each step lists what to run + what to verify before moving on.

### Step 0 — sanity smoke test (cluster-independent, 30s)
```bash
cd ~/Desktop/code/experiment/customer_churn-webreel-demo/demo/webreel
pnpm run record:smoke
open ../captures/raw/00-smoke.mp4
```
**Verify:** the MP4 plays. If this fails, the rest of the day fails — fix here before going further.

### Step 1 — start the terminal capture (5s of setup, then ambient ~30 min)
```bash
brew install asciinema agg ffmpeg     # one-time
./scripts/capture-terminal.sh "make autoresearch-run AUTORESEARCH_N=20 AUTORESEARCH_HOURS=4.0"
```
**Verify:** asciinema reports "recording..." and the autoresearch logs are scrolling.
Let it run. Get a coffee.

### Step 2 — preflight check (after step 1's run completes, ~30 min later)
Re-run the checklist in §4. Note specific IDs in your notes window.

### Step 3 — preview each browser scene (10–15 min total)
```bash
cd ~/Desktop/code/experiment/customer_churn-webreel-demo/demo/webreel
cp .env.example .env && $EDITOR .env   # MLFLOW_URL, KFP_URL, ARGOCD_URL filled
set -a; source .env; set +a

pnpm run preview:mlflow                 # visible browser, no recording — verify selectors hit
pnpm run preview:kfp
pnpm run preview:github
# (skip preview:argocd — see §6)
```
**Verify:** each preview completes without `--verbose` reporting a missed step. If a step misses, adjust the selector and re-preview.

### Step 4 — record the 3 unauthenticated browser scenes
```bash
pnpm run record:mlflow
pnpm run record:kfp
pnpm run record:github
```
**Outputs:** `../captures/raw/{01-mlflow,02-kfp,04-github}.mp4`

### Step 5 — ArgoCD (special case, see §6)

### Step 6 — terminal `curl /health` clip
Open a clean terminal window (no other history visible). Record with QuickTime or Screen Studio:
```bash
curl -s http://34.47.242.89/health | jq .
git log -1 --oneline -- k8s/deployment.yaml
gh pr view <PR_NUMBER> --json title,body | jq .
```
~15 seconds of footage. Output → `../captures/raw/06-terminal-receipts.mov`.

---

## 6. ArgoCD — the only awkward scene

webreel has no `storageState` / cookie persistence. The previous config typed the admin password on camera. **Don't.**

Pick one of these three:

| Option | Pros | Cons |
|---|---|---|
| **A — capture a screenshot in the editor** | Zero leak risk, fast | Less impressive, no motion |
| **B — record manually with QuickTime after logging in** | Real motion, no script | Manual; you're the cursor |
| **C — temporarily disable ArgoCD auth, record with webreel, re-enable** | Scripted, reproducible | Cluster-wide auth disable; restore IMMEDIATELY after |

Option C if you want the scripted version of `argocd.config.json` to run cleanly:
```bash
# DISABLE — do this only with no other users on the cluster
kubectl -n argocd patch configmap argocd-cmd-params-cm \
  --type merge -p '{"data":{"server.disable.auth":"true"}}'
kubectl -n argocd rollout restart deployment/argocd-server
sleep 30
pnpm run record:argocd
# RE-ENABLE IMMEDIATELY
kubectl -n argocd patch configmap argocd-cmd-params-cm \
  --type merge -p '{"data":{"server.disable.auth":"false"}}'
kubectl -n argocd rollout restart deployment/argocd-server
```

Recommendation: **option A for the LinkedIn 90s cut, option C for the 4min long-form** if you want motion. Either way, do a frame-by-frame review of the raw clip for any password ghosts before publishing.

---

## 7. Per-scene narration (drop-in voiceover script)

> Speak each line over the corresponding visible state. Match exact wording in captions for accessibility.

### Beat 1 — Hook (15s)
*"I built an agentic MLOps loop. Claude proposes changes, Kubeflow trains them, MLflow promotes only winners, GitHub records the signed PR, and ArgoCD rolls the live API. The point isn't the model — it's the accountable automation."*

### Beat 2 — System map (20s)
*"Five components. One contract: a model only ships if it beats the current champion's AUC, and only after the rollout commit lands in git. Everything else — KFP, MLflow, ArgoCD, the inference pods — is downstream of that."*

### Beat 3 — Live kick-off (35s)
*"This is a real run. The loop reads the current code, asks Claude for one change, applies it, submits a Kubeflow pipeline, and waits for the result. Twenty iterations, four hours of budget, runs in the cluster — not on my laptop."*

### Beat 4 — KFP proof (35s)
*"Each iteration is a real Kubeflow run. Preprocess, train, evaluate — three containers on three pods. The evaluate step decides whether to set the new model as champion. The KFP run ID — that UUID — is the receipt."*

### Beat 5 — MLflow proof (50s)
*"MLflow tracks every run. Sorted by AUC, the winner's at the top. Each run is tagged with the KFP run ID — same one I just showed. So when I'm looking at the registered model in MLflow, I can trace it all the way back to the exact pipeline execution that produced it. No 'latest run' guessing."*

### Beat 6 — GitHub accountability proof (55s)
*"This is the part most agentic-AI demos skip. Every model promotion has a corresponding merged PR. The PR body has the KFP run ID, the MLflow run ID, and the AUC delta. The commit is signed by a GitHub App — that's the green Verified badge — not a personal access token. The diff is small and inspectable, because it's just config and feature engineering. If a future regulator asks 'why does the model in production look like this', the answer's a `git log` away."*

### Beat 7 — ArgoCD + live API (45s)
*"ArgoCD reconciles the cluster to git every three minutes. The Deployment annotation contains the same classifier version — the link between 'what git says' and 'what's actually running.' And the live API: `curl /health` returns the same version. Five surfaces, one ID. That's the audit chain."*

### Beat 8 — Scope decisions (30s)
*"Some honest engineering: this is a single-zone GKE Autopilot cluster, not HA. The serving layer is FastAPI by design — KServe and Triton are the next layer when traffic shape demands it. The agentic trust boundary today is signed PRs plus metric gates. The natural next steps — OpenTelemetry observability, policy checks on LLM-generated diffs, traffic-aware serving — are deferred, not denied."*

Total spoken time: ~3:45. Comfortable for a 4-min cut. The 90s LinkedIn cut keeps beats 1, 5–7 only.

---

## 8. Post-production plan (DaVinci Resolve, free)

1. **Import all captures** to `demo/captures/raw/`. Create a new Resolve project at 1920×1080, 30fps.
2. **Place clips in beat order.** Drop each into the timeline.
3. **Speed-ramp dead UI loading** — anywhere a page is just loading, ramp 4–8×. Real-time on state changes.
4. **Add ID-matching overlays.** Each time the same ID appears in a different surface, add a 1s rectangle highlight + text label in `#22d3ee`. This is the credibility motif from §3.
5. **Voiceover.** Record in Descript or QuickTime, drop on the timeline. Lower-third subtitles auto-generated from the transcript (Descript or DaVinci's built-in).
6. **Music.** Sparse ambient pad (Epidemic Sound / Artlist). Duck under voiceover.
7. **Exports:**
   - LinkedIn 90s: 1920×1080, mp4, ≤200MB
   - YouTube 4min: 1920×1080, mp4
   - README GIF (top of page): 20s teaser, 800px wide, ≤8MB (ffmpeg `-vf "fps=15,scale=800:-1:flags=lanczos,palettegen"` → palette pipeline)

---

## 9. Tooling stack — 2026 landscape

### kite.video — verdict

**A web/macOS screen recorder + auto-editor**, *not* an interactive demo platform like Arcade. YC S23, 2-person team (Derek Feehrer, Todd Ashley), public Product Hunt launch March 2026. Closest sibling = Screen Studio. Feeds on raw screen recordings, auto-applies Apple-style cursor zooms, 3D device mockups, kinetic text, AI voiceover, music bed.

**Pricing:** generous free tier (4K, unlimited projects, AI voiceover, music) **with a small Kite watermark**. Pro is **$19/mo annual ($29/mo monthly)** for watermark removal + custom branding + priority rendering. Outputs MP4 only — no iframe embed.

**Verdict for this project:** worth using as a **post-processing layer**, not a replacement. Feed WebReel MP4s into Kite for auto-zoom/voiceover/mockup polish. The free tier is fine for testing — the watermark is small enough to live with if you can't justify $19/mo.

### Industry tool table (2026)

| Tool | Category | Pricing | Verdict for this project |
|---|---|---|---|
| **Screen Studio** | macOS recorder + auto-edit | **$229 lifetime** / $9-29/mo | **Highest-leverage purchase.** Stripe/Vercel/Google/Adobe/Raycast all use it. Native app + terminal captures with Apple-style auto-zoom. |
| **kite.video** | Web/Mac auto-editor | Free w/ watermark; $19/mo Pro | Good post-processing layer. Optional. |
| **Tella.com** | Browser recorder | Free (7-day expiry); $13-19/mo | Skip — weaker for long demos. |
| **Loom** | Async video | Free 720p; $18+/mo | Skip — quality cap too low for portfolio. |
| **Arcade / Supademo** | Interactive HTML demos | Free tier; $32-42/mo | Skip for the video; consider for embedding a clickable demo on the README later. |
| **Descript** | Transcript-driven NLE + AI voice | Free 60min/mo; $16-24/mo | Strong if you want to edit by typing. |
| **CleanShot X** | macOS screencap + annotation | **$29 one-time** | Useful utility, not a full editor. |
| **OBS Studio** | Multi-source recorder | **Free** | Best for the 30-min long-run terminal capture if asciinema isn't enough. |
| **DaVinci Resolve** | Pro NLE w/ speed-ramp curves | **Free** | Real NLE — handles everything iMovie can't (speed curves, ID-overlay graphics, multi-track audio). |
| **Final Cut Pro** | macOS NLE | $300 one-time | Overkill given Resolve is free. |
| **WebReel** | OSS scripted demo CLI | Free | Already set up — keep. |
| **ElevenLabs** | AI voice cloning | **$5/mo Starter** | Clone your voice or pick a stock one. Best-in-class for narration. |
| **HeyGen / Synthesia** | AI avatars | $29-30/mo | **Skip** — avatars read as low-effort for MLOps content. |
| **Submagic** | Animated word-by-word captions | ~€23/mo | Strong for the LinkedIn 90s cut. Word-by-word burned captions land hard on social. |

### AI voiceover convention in 2026

The dominant pattern for dev-tool demos: **ElevenLabs cloned voice** (or stock voice) layered over screen footage. Recording your own voice is still common but no longer the default for portfolio work — re-recording when copy changes is too slow. HeyGen/Synthesia avatars are avoided for technical content. Speed-ramping: 1× on state changes, **4×-10×** for waiting/loading, 1.5×-2× on routine clicking. DaVinci's speed curve editor does this cleanly for free.

### Top 3 additions to the current stack (ranked)

1. **Screen Studio — $229 lifetime.** Single highest-leverage purchase. WebReel can't capture the terminal, native apps, or anything outside Chrome cleanly. Screen Studio fills exactly that gap with the auto-zoom/cursor-smoothing the demo's aesthetic depends on. Replaces: manual zooming in DaVinci.
2. **ElevenLabs Starter — $5/mo.** Clone your voice once, then narrate from a script. Re-record narration in 60 seconds when copy changes — matches WebReel's reproducibility ethos.
3. **kite.video — free w/ watermark, $19/mo Pro.** Optional post-processing layer for hero-shot polish on the LinkedIn cut. The 3D device mockup of the inference-API tab looks great on social. Augments, doesn't replace.

### Anti-recommendations (skip these)

- **Arcade / Supademo** — produces *interactive clickable demos* (iframes), not video. Wrong artifact for this case.
- **HeyGen / Synthesia avatars** — uncanny-valley avatar narrating MLOps content reads as low-effort in 2026. Use ElevenLabs over real footage instead.
- **Loom (paid) / Reflect.run** — Loom's quality cap doesn't fit polished portfolio work; Reflect is QA automation, not a demo tool (name overlap only).

### The recommended hybrid workflow

```
WebReel (browser scenes)          ──┐
Screen Studio (terminal, native)  ──┼──→ DaVinci Resolve (composite, speed-ramp, ID overlays)
asciinema (long-run terminal)     ──┤
ElevenLabs (voiceover)            ──┘
                                       ↓
                            kite.video (optional polish layer)
                                       ↓
                            Final MP4: 90s LinkedIn + 4min long-form
```

---

## 10. What NOT to say

Avoid (per council review + earlier user feedback):

- "production-grade" without qualifier
- "the live model can never get worse"
- "any binary CSV plugs in" → use *"schema-driven via params.yaml, validated on two datasets so far"*
- "zero risk"
- "Claude improves the model 30× while I sleep"
- "20 runs" (or any specific count) if the UI doesn't show that count when you press record
- "source diffs trained immediately" — only true if the KFP image was rebuilt to include the diff before the run
- "Known Limitations" framing — say *"scope decisions"* (positive framing of what's in/out for this iteration)

Use instead:

- "portfolio-scale production pattern"
- "only metric-winning candidates trigger rollout"
- "the release path is auditable"
- "an agentic release-control demo, not a fraud-detection benchmark"

---

## 11. Distribution playbook

- **LinkedIn post (your audience):** the 90s cut + a 2-3 sentence caption that frames the project as *agentic release control, not autoML*. Pin the link to the GitHub repo. First line should be the question your audience actually has: *"How do you stop an AI from shipping a worse model to production?"*
- **GitHub README top:** the 20s teaser GIF. Embed below the H1, above the architecture diagram.
- **Resume / portfolio site:** the 4min cut, with a 2-paragraph write-up below the embed framing the work as *MLOps + LLMOps* not just *MLOps*.
- **Twitter/X thread (optional):** 8 tweets, one per beat, each with a 15s clip pulled from the master. Higher discoverability than LinkedIn.

---

## 12. Cross-references

- `configs/*.config.json` — the 4 scene configs + smoke. All council fixes applied:
  - `mlflow.config.json` — dropped "20" counts, added `kfp_run_id` hover step
  - `kfp.config.json` — dropped train-node "Metrics" click (KFP doesn't show metrics there), replaced with evaluate-step logs
  - `argocd.config.json` — dropped password-typing steps, added `mlops/classifier-version` hover for ID-matching
  - `github-prs.config.json` — search query `autoresearch` → `auto-exp` (matches real PR title prefix)
- `smoke/index.html` + `configs/smoke.config.json` — toolchain test
- `scripts/capture-terminal.sh` — asciinema wrapper
- `README.md` — installation, validate/preview/record flow
- `RECORDING_PLAN_TRUST_FIRST.md` — original council pushback (superseded by this doc)
- Root `EXPLANATION.md §16-17` — pitching/positioning material this script references
