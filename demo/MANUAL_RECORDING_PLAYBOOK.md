# Manual recording playbook — autoresearch demo

> Replaces the scripted-webreel approach. You hand-record with Screen Studio.
> This doc is everything you need: tool stack, pre-recording prep, scene-by-scene
> click script with exact narration. Read top to bottom on shoot day.

## 1. Tool stack — what to use for what

Three tiers based on budget. All produce a portfolio-quality LinkedIn 90s + YouTube 4min cut.

### Tier 1 — Minimum viable ($0 + your voice)
| Need | Tool | Cost |
|---|---|---|
| Capture + light edit | **QuickTime** + **iMovie** (macOS built-in) | $0 |
| Voiceover | Your voice into QuickTime | $0 |
| Captions | iMovie auto-generate | $0 |
| Total | | **$0** |

Trade-off: cursor is plain, no auto-zoom. Looks like a "screenshare" not a "demo." Adequate for 1.0 of your demo.

### Tier 2 — Polished portfolio ($229 one-time, recommended)
| Need | Tool | Cost |
|---|---|---|
| Capture + auto-zoom + cursor smoothing + light edit | **Screen Studio** (macOS) | $229 lifetime ($9/mo annual) |
| Voiceover | Screen Studio's built-in recording + ElevenLabs voice clone | $5/mo if cloning |
| Final stitch + speed-ramp | **DaVinci Resolve** | Free |
| Captions | Screen Studio's built-in or DaVinci Studio Voice | Free |
| Total | | **$229 + optional $5/mo** |

**Why Screen Studio:** Stripe / Vercel / Google / Adobe / Framer / Raycast all use it. The signature look (auto-zoom on click, smoothed cursor, soft motion blur, rounded-corner window frame) is what your viewer recognizes as "polished SaaS demo." It's the single highest-leverage purchase for portfolio work.

### Tier 3 — Pro polish (~$300 + monthly subs)
Add on top of Tier 2:
| Need | Tool | Cost |
|---|---|---|
| Animated word-by-word captions for social cuts | **Submagic** | ~$23/mo |
| Voice cloning for re-recordable narration | **ElevenLabs Starter** | $5/mo |
| Optional auto-3D mockups for hero shots | **kite.video** Pro | $19/mo (free w/ watermark) |

### What to SKIP

- **Vercel WebReel** — duration bug + weak cursor aesthetic + selectors break on real UIs. We tested it; it's not ready
- **Loom / Reflect.run / Tella** — wrong tier or wrong category
- **Arcade / Supademo** — produces interactive iframe demos, not video
- **HeyGen / Synthesia AI avatars** — reads as low-effort for technical content
- **Sora 2 / Veo 3 generative video** — they mangle UI text (run IDs become "pixel patterns that look like text")

## 2. Pre-recording prep — DO ALL OF THIS BEFORE PRESSING RECORD

The single biggest amateur mistake is recording with the UI in a "first-time-user" state. Set it up first. Each of these takes 30 seconds.

### A. Browser tabs to pin (Chrome profile with all four logged in)

Open in one window, in this exact order (matches the recording sequence below):

1. **MLflow** — `http://34.180.20.197:5000/#/experiments/4`
   - Click **"Columns"** in top-right of the runs table → enable `auc_roc`, `kfp_run_id` (Tag)
   - Click the `auc_roc` column header to sort **descending** (best at top)
   - Switch to **Chart view** in another tab → configure a line chart of `auc_roc` over time (drag `auc_roc` into Y-axis)
   - Switch back to the Table view; the column sort + visible metrics persist in URL state

2. **Kubeflow Pipelines** — `http://34.93.2.209/#/runs`
   - Click the most recent finished run — open it in the same tab
   - The DAG view should show preprocess → train → evaluate, all green

3. **ArgoCD** — `http://34.100.246.237/applications/inference-api`
   - **Already logged in** (admin / TMwwd4OpkcL6fPRy)
   - You should land on the resource tree directly, not the login page

4. **GitHub PRs** — `https://github.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch/pulls?q=is%3Apr+is%3Amerged+auto-exp`
   - Confirm there are at least 2-3 merged `auto-exp:` PRs visible
   - Click into the most recent one — make sure the PR body shows `KFP run ID`, `MLflow run ID`, `version`

### B. Terminal setup

- One clean terminal window, **font size 18+** (Cmd+Plus in Terminal/iTerm). Default is too small to read on video
- Background color: dark (matches Mac dock aesthetic)
- Clear history: `clear && history -c`
- Pre-stage the commands you'll run as a shell file `~/demo-commands.sh`:
  ```bash
  # === Beat 3: live kick-off ===
  cd ~/Desktop/code/experiment/ML-deployment-system-for-autoresearch
  git rev-parse --short HEAD
  make autoresearch-run AUTORESEARCH_N=2 AUTORESEARCH_HOURS=1.0
  # (don't actually run during recording — show the command and cut)

  # === Beat 7: live API receipt ===
  curl -s http://34.47.242.89/health | jq .

  # === Beat 7: prediction call ===
  curl -s -X POST http://34.47.242.89/predict \
    -H 'Content-Type: application/json' \
    -d '{"TransactionAmt": 100, "ProductCD": "W"}' | jq .
  ```

### C. State snapshot — paste these into a sticky note for narration

Run these commands ONCE before recording day, paste the outputs into a notes window you keep open during narration:

```bash
# MLflow @champion
MLFLOW_TRACKING_URI=http://34.180.20.197:5000 uv run python -c "
import mlflow; c = mlflow.MlflowClient()
v = c.get_model_version_by_alias('classifier', 'champion')
print(f'@champion → v{v.version} (run_id={v.run_id})')
"

# Most recent PR number
gh pr list --state merged --search 'auto-exp' --limit 1

# Most recent KFP run ID (from MLflow tag)
MLFLOW_TRACKING_URI=http://34.180.20.197:5000 uv run python -c "
import mlflow; c = mlflow.MlflowClient()
v = c.get_model_version_by_alias('classifier', 'champion')
run = c.get_run(v.run_id)
print(f'kfp_run_id tag = {run.data.tags.get(\"kfp_run_id\")}')
"
```

Your sticky note should look like:
```
HEAD:           <git short hash>
@champion:      v<N> (mlflow run <prefix>)
KFP run ID:     <uuid prefix>
Latest PR:      #<num> auto-exp: <name>
```

You'll reference these IDs verbally during narration to make the audit-chain story land.

### D. Display setup

- **Hide your dock** (System Settings → Desktop & Dock → Automatically hide and show the Dock)
- **Hide your menu bar** (same panel → Automatically hide and show the menu bar in fullscreen)
- **Notification Do Not Disturb on** (avoids notification pop-ups mid-record)
- **Quit Slack / iMessage / email / browser tabs with previews** (any notification surface)
- **Resolution: 1920×1080** for the browser windows. Use Rectangle / Magnet to snap them
- If you have a multi-monitor setup: **record from one screen only**, hide the others

## 3. Scene-by-scene click script

Each beat: target length, what to open, what to click, what to say. Record each scene as a SEPARATE Screen Studio recording — easier to re-take individual scenes than to redo the whole thing.

### Beat 1 — Hook (15s)
**On screen:** Composite frame. Recommended: record a single still at the end of recording day showing all 4 surfaces in a 2×2 grid (MLflow trajectory + GitHub PR with Verified badge + ArgoCD Synced/Healthy + terminal showing `/health` response). Build the composite in Screen Studio's canvas.

**Narration:**
> "I built an agentic MLOps loop. Claude proposes code changes, Kubeflow trains them, MLflow promotes only winners, GitHub records the signed PR, and ArgoCD rolls the live API. The point isn't the model. It's the accountable automation."

**Recording approach:** Don't try to capture this live — it's a composite. Take screenshots of each surface after all other scenes are done, arrange in Screen Studio's editor.

---

### Beat 2 — System map (20s)
**On screen:** Static slide. Use the README's Mermaid architecture diagram, rendered. Take a clean screenshot, drop it in Screen Studio with a slow pan/zoom.

**Narration:**
> "Five components. One contract: a model only ships if it beats the current champion's AUC, and only after the rollout commit lands in git. Everything else — KFP, MLflow, ArgoCD, the inference pods — is downstream of that."

---

### Beat 3 — Live kick-off (35s)
**On screen:** Your prepared terminal window.

**Click sequence (DO NOT type live — paste from `demo-commands.sh`):**
1. Show `cd ~/Desktop/code/experiment/ML-deployment-system-for-autoresearch` already executed
2. Type `git rev-parse --short HEAD` — show the hash
3. Type `make autoresearch-run AUTORESEARCH_N=20 AUTORESEARCH_HOURS=4.0` and press enter
4. Let the first 3-4 lines of output appear (Job created)
5. Cut

**Narration:**
> "This is a real run. The loop reads the current code, asks Claude for one change, applies it, submits a Kubeflow pipeline, and waits for the result. Twenty iterations, four hours of budget, runs in the cluster — not on my laptop."

**Speed-ramp the dead air in post.** Real-time on `make autoresearch-run` typing, 4× on output scrolling.

---

### Beat 4 — KFP proof (35s)
**On screen:** Tab 2 (KFP UI), already on a finished run with the DAG visible.

**Click sequence:**
1. Settle on the DAG view — preprocess → train → evaluate, all green (2s)
2. **Highlight the KFP run ID at top of the page** (your cursor hover near it for 2-3s) — this is the FIRST appearance of the ID-matching motif
3. Click the **preprocess** node — side panel opens
4. Click **Logs** tab in the side panel — show the real container logs (5s)
5. Click the **evaluate** node (skip train — it has no metrics tab)
6. Click **Logs** — find a line like `AUC X > champion Y, promoting` and let it linger (4s)

**Narration:**
> "Each iteration is a real Kubeflow run. Preprocess, train, evaluate — three containers on three pods. The evaluate step decides whether to set the new model as champion. The KFP run ID — that UUID — is the receipt I'll reference in every other surface."

---

### Beat 5 — MLflow proof (50s)
**On screen:** Tab 1 (MLflow), runs table for experiment 4, sorted by AUC descending.

**Click sequence:**
1. Settle on the runs table (2s)
2. Hover the auc_roc column header (1s) — visual cue that this is sorted
3. Click the top run (winning candidate)
4. Land on the run details page — Parameters + Metrics visible (3s)
5. Scroll down to the **Tags** section
6. **Hover on `kfp_run_id`** for 3-4 seconds — this is the SECOND appearance of the ID, identical to beat 4
7. Scroll further to **Registered Models** — show `classifier @champion`

**Narration:**
> "MLflow tracks every run. Sorted by AUC, the winner's at the top. Each run is tagged with the KFP run ID — same one I just showed you. So when I'm looking at the registered model in MLflow, I can trace it all the way back to the exact pipeline execution that produced it. No 'latest run' guessing."

---

### Beat 6 — GitHub accountability proof (55s) — THE MOST IMPORTANT SCENE
**On screen:** Tab 4 (GitHub merged PRs), filtered to `is:pr is:merged auto-exp`.

**Click sequence:**
1. Settle on the PR list (3s)
2. Scroll up to show that there are several merged PRs (3s)
3. Click the most recent merged `auto-exp:` PR
4. Land on the PR conversation view
5. **Hover on the PR body section that lists `KFP run ID` / `MLflow run ID` / `version`** for 4s — THIRD appearance of the ID
6. Click the **Files changed** tab
7. Show the `params.yaml` diff — usually 1-3 lines (5s)
8. Scroll down to show the commit signature footer — the **green "Verified" badge**
9. Linger on Verified (3s)

**Narration:**
> "This is the part most agentic-AI demos skip. Every model promotion has a corresponding merged PR. The PR body has the KFP run ID, the MLflow run ID, and the AUC delta. The commit is signed by a GitHub App — that's the green Verified badge — not a personal access token. The diff is small and inspectable, because it's just config and feature engineering. If a future regulator asks 'why does the model in production look like this', the answer's a git log away."

---

### Beat 7 — ArgoCD + live API proof (45s)
**On screen:** Tab 3 (ArgoCD inference-api app).

**Click sequence:**
1. Settle on the resource tree — Deployment → ReplicaSet → 2 Pods, all green (3s)
2. Banner shows **Synced / Healthy** — let it sit (2s)
3. Click on the **Deployment** node in the tree
4. Side panel opens — scroll to **Metadata → Annotations**
5. **Hover on `mlops/classifier-version`** for 3s — FOURTH appearance of the ID
6. **Cmd+Tab to your terminal**
7. Paste `curl -s http://34.47.242.89/health | jq .` and press enter
8. Let the JSON output appear — `model_version` matches the annotation (3s) — FIFTH appearance
9. Optional: paste the predict call as a bonus shot

**Narration:**
> "ArgoCD reconciles the cluster to git every three minutes. The Deployment annotation contains the same classifier version — the link between 'what git says' and 'what's actually running.' And the live API: curl /health returns the same version. Five surfaces, one ID. That's the audit chain."

---

### Beat 8 — Scope decisions (30s)
**On screen:** A slide with bullet points. Build in Keynote or Figma; export to PNG; drop in Screen Studio.

```
Scope decisions for this iteration

✓  Agentic release control proven end-to-end
✓  Signed PR + metric gate = trust boundary
✓  GitOps reconciliation under autoresearch concurrency

Next layer (not this layer):
  · OpenTelemetry traces + alerting on regressions
  · Policy checks on LLM-generated diffs
  · Traffic-aware serving (canary / shadow)
```

**Narration:**
> "Some honest engineering. This is a single-zone GKE Autopilot cluster, not HA. The serving layer is FastAPI by design — KServe and Triton are the next layer when traffic shape demands it. The agentic trust boundary today is signed PRs plus metric gates. The natural next steps — OpenTelemetry, policy checks on LLM diffs, traffic-aware serving — are deferred, not denied."

## 4. The ID-matching visual motif — emphasize in post

The single most persuasive thing in this demo: the same identifier reappears in **five places**:
1. Beat 4 — KFP run details page
2. Beat 5 — MLflow run Tags section, `kfp_run_id`
3. Beat 6 — GitHub PR body
4. Beat 7 — ArgoCD Deployment annotation `mlops/classifier-version`
5. Beat 7 — Terminal `/health` response

**In DaVinci/Screen Studio post:** every time the same ID appears, briefly highlight it with a 1-second rectangle in `#22d3ee` (cyan). By beat 6 the viewer recognizes the pattern. By beat 7 it lands.

Screen Studio specifically has a "Highlight" annotation tool — use that. Don't overdo it: 1 second per appearance, not flashing.

## 5. Recording order on shoot day

Do scenes in this order — NOT the playback order. Reasons noted.

1. **Beat 4 (KFP)** — first because you need the KFP run ID to anchor everything else
2. **Beat 5 (MLflow)** — confirms the same ID appears in the MLflow tag
3. **Beat 6 (GitHub)** — confirms the same ID appears in the PR body
4. **Beat 7 (ArgoCD + terminal)** — confirms it appears in the annotation and the API
5. **Beat 3 (terminal kick-off)** — easy, no dependencies, can record anytime
6. **Beat 8 (scope slide)** — pure recording, no UI
7. **Beat 2 (system map slide)** — pure recording, no UI
8. **Beat 1 (composite hero shot)** — last, because you need screenshots of all the above

Total active recording time: ~45-60 min if no retakes. Plan for 2× that with retakes.

## 6. Post-production (DaVinci Resolve, free)

1. **Import** all Screen Studio exports + screenshots into a new 1920×1080 30fps project
2. **Place in playback order** (not recording order)
3. **Speed-ramp dead air**:
   - 4-8× on `make autoresearch-run` output scroll
   - 4× on page loads
   - 2× on routine clicks
   - 1× on the ID-hover moments (keep these real-time)
4. **Add ID-matching color overlays** as described in §4
5. **Record voiceover** in Descript or QuickTime (or generate with ElevenLabs Starter)
6. **Drop voiceover** on the timeline. Lower volume of click sounds to -20dB
7. **Generate captions** in DaVinci's Studio Voice (free) or Submagic (paid, word-by-word for social)
8. **Music** — sparse ambient pad. Epidemic Sound / Artlist subscription. Duck under voiceover at -18dB
9. **Export**:
   - LinkedIn 90s: 1920×1080, mp4, H.264, ≤200MB. Keep beats 1, 5, 6, 7 only
   - YouTube 4min: 1920×1080, mp4, H.264. All beats
   - README GIF teaser: 20s, 800px wide, ≤8MB. Use ffmpeg palettegen for quality

## 7. Distribution

- **LinkedIn post:** 90s cut + 2-3 sentence caption. First line is the question your audience has: *"How do you stop an AI from shipping a worse model to production?"* Then the demo and the link to the GitHub repo
- **GitHub README top:** the 20s GIF teaser, below the H1, above the architecture diagram
- **Portfolio site / resume:** the 4min cut, framed as **MLOps + LLMOps**, not just MLOps
- **Twitter/X (optional):** thread of 7 tweets, one per beat, each a 15s clip from the master. More discoverable than LinkedIn

## 8. Honest pre-shoot checklist

- [ ] Screen Studio installed and licensed
- [ ] All 4 browser tabs pre-configured per §2A
- [ ] Terminal font size 18+, dark theme, history cleared (§2B)
- [ ] State snapshot pasted in sticky note (§2C)
- [ ] Dock + menu bar hidden, DND on, distracting apps quit (§2D)
- [ ] `demo-commands.sh` staged so you don't type live
- [ ] Quiet room, microphone tested (if recording own voice)
- [ ] One real autoresearch run completed within the last hour (UIs have fresh state to show)
- [ ] You've read this doc top to bottom once

When all checked → press record on Beat 4 first.

## 9. What I'd actually do for v1.0

If you're optimizing for **shipping the demo in one weekend**:
- Buy **Screen Studio ($229)**
- Skip the AI voice — record your own voice. Adds 30 min, saves a subscription
- Skip Submagic — use DaVinci Studio Voice captions. Free, good enough for a portfolio
- Skip the architecture slide (beat 2) initially — go straight from beat 1 to beat 3. Add beat 2 in v1.1 if you want a longer cut
- 6 beats × ~5 min recording each = ~30 min total recording
- 90 min editing in Screen Studio's built-in editor
- One 90s LinkedIn cut + one 3min YouTube cut. Ship Sunday night.

That's a real one-weekend project for a portfolio piece you'll lean on for 12+ months. **$229 total, ~6 hours of work.**
