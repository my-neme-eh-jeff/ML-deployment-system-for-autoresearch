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

Open in one window, in this exact order (matches the recording sequence below).

**Browser global settings (do ONCE for the profile you'll record from):**
- **Hide bookmarks bar:** Cmd+Shift+B (toggles off). Bookmarks bar clutters the top of every shot.
- **Page zoom: 110%** for MLflow + KFP + ArgoCD (text is small by default; bump it for video legibility). For GitHub: 100% is fine.
- **Window size: 1920×1080.** Use Rectangle / Magnet → "Maximize Almost". DO NOT fullscreen — Mac fullscreen hides the menubar in a way that can cause black bars in the recording.
- **Theme: dark mode everywhere.** Matches the terminal + Screen Studio aesthetic. MLflow: top-right ☀️/🌙 toggle. ArgoCD: user menu → dark mode. GitHub: Settings → Appearance → Dark. KFP: no dark mode, leave it as-is.

**Tab 1 — MLflow** — `http://34.180.20.197:5000/#/experiments/4`
- The URL `experiments/4` is the `training` experiment where actual run artifacts land (NOT `0`, which is empty Default).
- Click the **"Columns"** button (top-right of the runs table, looks like a small filter icon ⚙️ or "Columns" label depending on MLflow version)
- In the dropdown, enable these checkboxes:
  - `Metrics → auc_roc`
  - `Metrics → average_precision`
  - `Metrics → f1`
  - `Tags → kfp_run_id`
  - `Tags → mlflow.runName` (usually on by default)
- Click outside the dropdown to close it
- Click the **`auc_roc`** column header **twice** → arrow points DOWN (descending sort). Best run is now row 1.
- Open the **Chart** tab (sibling tab next to "Table" near the top of the runs view) in a NEW browser tab so you can show trajectory later without losing the sorted Table view
  - In the Chart tab, click "+ New chart" → "Line chart" → X-axis: `start_time`, Y-axis: `auc_roc`. Save.
- Switch back to Tab 1 (the Table view). The URL has the sort state encoded; bookmark this exact URL.

**Tab 2 — Kubeflow Pipelines** — `http://34.93.2.209/#/runs`
- Left sidebar: confirm "Runs" is selected (not "Experiments")
- Top of table: filter to "Status: Succeeded" if there's a Status dropdown — avoids accidentally clicking a still-running iter
- Click the most recent finished run — open it in the SAME tab (single click on the run name link in the leftmost column)
- The detail page should show: top header with **Run ID** (UUID), and a DAG canvas in the center
- DAG nodes: `preprocess` → `train` → `evaluate`. All should be green. If any is red/yellow → that iteration failed; back-button and pick a green one
- Verify: zoom level shows all 3 node names readably. If too small, use the DAG zoom controls (top-right of canvas) to zoom in 25-50%
- Now click `preprocess` ONCE — right side panel opens (tabs: Input/Output | Logs | Pod | Visualizations)
- Click `Logs` tab in side panel to pre-load the logs view → DO NOT close the panel; the panel-open state needs to be the starting state of the recording
- Actually — to start cleanly: click somewhere on the empty canvas to close the panel, so the recording begins with NO side panel open. The shot list opens it deliberately.

**Tab 3 — ArgoCD** — `http://34.100.246.237/applications/inference-api`
- **Pre-login required.** Sign in as `admin` / `TMwwd4OpkcL6fPRy` on a separate session, then navigate to this URL. Cookies persist; on shoot day you should land directly on the app view, not the login page. **Verify by reloading.**
- You land on a "Tree" view by default. Top of page shows: app name banner with two pills — `Sync Status: Synced` (green) and `Health Status: Healthy` (green)
- The resource tree shows: `inference-api` (Application) → `inference-api` (Deployment) → `inference-api-<hash>` (ReplicaSet) → 2 Pods
- Click on the empty area to deselect anything. Recording starts with nothing selected.

**Tab 4 — GitHub PRs** — `https://github.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch/pulls?q=is%3Apr+is%3Amerged+auto-exp`
- The search bar at the top of the page should ALREADY show: `is:pr is:merged auto-exp` (URL pre-applies it)
- You should see at least 2-3 merged `auto-exp:` PRs in the list
- DO NOT click into a PR yet — that's a recording action. Recording starts on this list view.
- BUT pre-click one PR in a separate tab to verify its body has the right structure:
  - PR body should contain something like: `KFP run: <uuid>`, `MLflow run: <run_id>`, `@champion: vN → vM`, `AUC: 0.XXX → 0.YYY`
  - If a recent PR is missing these fields, pick an older one that has them for beat 6
- Close that scout tab. Recording tab stays on the list.

### A.1 The 3 "kill the recording" gotchas to check 5 minutes before pressing record

These will ruin a take if you miss them:
1. **GitHub notification badge.** If you have unread issues/notifications, the badge appears in the top-right of every GitHub page. Mark all as read (or sign out + sign in with a clean account) before recording beat 6. Same for the "New from your favorite repos" dashboard prompt.
2. **macOS "Charge battery" / Software Update banner.** Both can pop down from the menubar mid-record. Pre-dismiss: hover the menubar to check there's no badge, plug in the laptop, and run `softwareupdate -l` to dismiss the system update banner.
3. **ArgoCD session timeout.** Default is ~24h. Verify by reloading the ArgoCD tab within 10 min of recording — if it bounces to login, your "pre-login" state is stale.

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

## 3. Scene-by-scene shot list (time-coded)

Each beat below: target length, exact window state at start, time-coded shot list (every 1-3 seconds is a shot), word-for-word narration, and post-production notes.

**Record each beat as a SEPARATE Screen Studio recording.** Easier to re-take a 35-second clip than to redo the entire 5-minute video. After recording, you'll stitch them in playback order in DaVinci.

**Read the cursor choreography rules in Section 3.5 BEFORE recording.** They make the difference between "screenshare" and "demo."

---

### Beat 1 — Hook (15s)

**This is a COMPOSITE, not a live recording.** Build it last, in the editor.

**What's on screen:** 2×2 grid of stills — top-left: MLflow trajectory chart; top-right: GitHub PR with green Verified badge visible; bottom-left: ArgoCD app showing Synced/Healthy banner; bottom-right: terminal with `curl /health | jq` output visible.

**Recording approach:**
1. After ALL other beats are done, take 4 PNG screenshots (Cmd+Shift+4 → space → click each window):
   - MLflow Chart view showing the AUC line going up
   - The most recent merged PR's conversation page, scrolled to show the Verified commit
   - ArgoCD inference-api app, banner visible
   - Terminal with the curl output
2. Drop all 4 into Screen Studio's canvas
3. Arrange in 2×2 grid. Add 0.5s fade-in on each (staggered by 0.3s — top-left first, etc.)
4. Hold composite for 12 seconds while narration plays. Fade to white at 14s.

**Narration (15s, ~38 words):**
> "I built an agentic MLOps loop. Claude proposes code changes, Kubeflow trains them, MLflow promotes only winners, GitHub records the signed PR, and ArgoCD rolls the live API. The point isn't the model — it's the accountable automation."

**Post-production notes:**
- Add a subtle cyan tint to the matching ID text in any of the 4 panels (preview of the motif)
- Keep music at -22dB; narration at -6dB

---

### Beat 2 — System map (20s)

**Skip this for v1.0** unless you have a long-form cut. For the 90s LinkedIn version, cut directly from beat 1 to beat 3.

**What's on screen:** Static rendered Mermaid diagram from the README.

**Recording approach:**
1. Open the README in GitHub (rendered Mermaid)
2. Cmd+Shift+4 → drag a clean rectangle around just the diagram
3. Drop the screenshot in Screen Studio
4. Add a slow Ken Burns pan (Screen Studio: "Movement" → "Pan Left to Right" at 0.5× speed)

**Narration (20s, ~45 words):**
> "Five components. One contract: a model only ships if it beats the current champion's AUC, and only after the rollout commit lands in git. Everything else — KFP, MLflow, ArgoCD, the inference pods — is downstream of that."

---

### Beat 3 — Live kick-off (35s)

**What's on screen:** Your prepared terminal window. Font size 18+. Dark background.

**Pre-recording state:** Terminal is in `~/Desktop/code/experiment/ML-deployment-system-for-autoresearch`, history cleared (`clear && history -c`), 2 lines visible:
```
$ pwd
/Users/aman.nambisan/Desktop/code/experiment/ML-deployment-system-for-autoresearch
$ █
```

**Shot list:**
| T+ | Duration | Action | What viewer sees |
|---|---|---|---|
| 0:00 | 2s | Settle. Hands off keyboard. | Static terminal, prompt blinking. |
| 0:02 | 3s | Type `git rev-parse --short HEAD` slowly (don't paste). | Each char appears at ~80ms intervals. |
| 0:05 | 1s | Press Enter. | Hash appears, e.g. `b982654`. |
| 0:06 | 2s | Pause. | Hash visible. |
| 0:08 | 5s | Type `make autoresearch-run AUTORESEARCH_N=20 AUTORESEARCH_HOURS=4.0`. | Command appears. |
| 0:13 | 1s | Press Enter. | Cursor goes to next line. |
| 0:14 | 4s | Wait for output. | `job.batch/autoresearch-real-… created` line appears. |
| 0:18 | 8s | Let "Watch with: make autoresearch-logs" and a few more lines appear. | Job lifecycle visible. |
| 0:26 | 9s | Cut to: Screen Studio time-lapse → fast-forward to ~30 min later, terminal now showing `make autoresearch-logs` output streaming Claude's proposals + KFP run IDs + AUC results. | Cluster running, output flying by. |
| 0:35 | — | End. | Cut to beat 4. |

**Narration (35s, ~78 words):**
> "This is a real run. The loop reads the current code, asks Claude for one change, applies it, submits a Kubeflow pipeline, and waits for the result. [pause 1s] Twenty iterations, four hours of budget, runs in the cluster — not on my laptop. [pause 1s, while time-lapse plays] By the time it finishes, MLflow, KFP, GitHub, and ArgoCD all have receipts."

**Post-production notes:**
- Real-time on T+0:00 to T+0:18 (the typing + first output)
- 6× speed-ramp from T+0:18 to T+0:26 (time-lapse the dead air)
- Add a "30 min later..." text overlay at T+0:26 in upper-right
- 4× speed-ramp from T+0:26 to T+0:35

---

### Beat 4 — KFP proof (35s)

**What's on screen:** Tab 2 — KFP DAG view, no side panel open at start.

**The KFP run ID at the top of the page is the ID you'll reference in all subsequent beats.** Write it down on your sticky note before recording — you'll need to verify it appears in beats 5, 6, 7.

**Shot list:**
| T+ | Duration | Action | What viewer sees |
|---|---|---|---|
| 0:00 | 2s | Settle. Cursor parked top-left of the canvas. | DAG visible — preprocess → train → evaluate, all green. |
| 0:02 | 3s | Slowly move cursor to the top of the page where "Run ID: <uuid>" appears. Hover, don't click. | Cursor tracks toward the run ID. |
| 0:05 | 3s | Hold hover on the Run ID. | UUID is centered in viewer's attention. **POST: cyan highlight overlay around the UUID, holds 2s.** |
| 0:08 | 2s | Slowly move cursor to the `preprocess` node (leftmost in DAG). | Cursor tracks toward node. |
| 0:10 | 1s | Click `preprocess`. | Right side panel slides in (tabs: Input/Output, Logs, Pod, Visualizations). |
| 0:11 | 1s | Move cursor to "Logs" tab in side panel. | Cursor tracks. |
| 0:12 | 1s | Click "Logs". | Logs view loads inside the panel. |
| 0:13 | 4s | Hold. Scroll log content slowly (1 wheel tick per second). | Real container log lines visible — `Loading data from data/ieee_cis.parquet`, etc. |
| 0:17 | 1s | Move cursor to the `evaluate` node (rightmost in DAG, NOT train). | Cursor tracks. |
| 0:18 | 1s | Click `evaluate`. | Side panel updates to evaluate's content. |
| 0:19 | 1s | Click "Logs" tab again. | evaluate's logs load. |
| 0:20 | 1s | Slowly scroll down in the logs panel. | Looking for the promotion line. |
| 0:21 | 5s | Stop scrolling at line: `AUC 0.XXX > champion 0.YYY, setting alias @champion`. | Promotion decision visible. **POST: yellow underline highlight on the AUC values, holds 4s.** |
| 0:26 | 5s | Hold on the promotion line. | Viewer reads the line. |
| 0:31 | 4s | Slow pull-back zoom (Screen Studio: keyframe end with 100% zoom) to show the full DAG again. | Whole DAG visible, panel still showing logs. |
| 0:35 | — | End. | Cut to beat 5. |

**Narration (35s, ~85 words):**
> "Each iteration is a real Kubeflow run. Preprocess, train, evaluate — three containers on three pods. [hold for KFP run ID highlight] Remember this run ID. [pause as preprocess logs scroll] Real logs from a real container, not a mock. [pause] The evaluate step decides whether to set the new model as champion. [hold on the AUC promotion line] If the AUC beats the current champion, it sets the alias. That's what kicks off the GitOps chain you'll see next."

**Post-production notes:**
- KFP UI is busy. Crop the recording in DaVinci to hide the left sidebar (the navigation menu) — gain 20% of horizontal real estate
- Cyan rectangle highlight on the run ID (T+0:05 to T+0:08) — this is appearance #1 of the ID-matching motif

---

### Beat 5 — MLflow proof (50s)

**What's on screen:** Tab 1 — MLflow runs table for experiment 4. Already sorted by `auc_roc` descending. `auc_roc` and `kfp_run_id` columns visible.

**Pre-state verify:** The top row's `kfp_run_id` cell shows the SAME UUID prefix you wrote down from beat 4. If not — beat 4 recorded a different run; either re-record beat 4 against the run that matches the current @champion, or accept that beat 5's match won't visually land.

**Shot list:**
| T+ | Duration | Action | What viewer sees |
|---|---|---|---|
| 0:00 | 3s | Settle. Cursor top-right. | Runs table, ~5-10 rows visible, sorted by AUC desc. |
| 0:03 | 2s | Slow cursor glide to the `auc_roc` column header. Hover, don't click. | Cursor on column header. **POST: small "sorted desc" arrow visible.** |
| 0:05 | 2s | Hold. | Viewer sees the sort. |
| 0:07 | 2s | Slow cursor glide to the top row's run name (leftmost cell). | Cursor tracks. |
| 0:09 | 1s | Click. | Page navigates to run detail. |
| 0:10 | 3s | Run detail page loads. Cursor parked. | Parameters + Metrics sections visible. |
| 0:13 | 4s | Slow scroll down (3 wheel ticks). | Tags section comes into view. |
| 0:17 | 2s | Cursor moves to the `kfp_run_id` tag value (right side of the tag pill). | Cursor tracks. |
| 0:19 | 4s | Hover and HOLD. | UUID is centered. **POST: cyan rectangle highlight on the UUID — this is appearance #2 of the ID, visually IDENTICAL to beat 4's highlight.** |
| 0:23 | 3s | Hold longer than feels comfortable. | Gives viewer time to register the ID match. |
| 0:26 | 4s | Slow scroll up to Parameters + Metrics. | View the params (model_type, max_depth, etc.) + metrics (auc_roc, f1, average_precision). |
| 0:30 | 4s | Hold on metrics. | Numbers visible. |
| 0:34 | 4s | Cursor moves to "Registered Models" link/card (usually right side or bottom). | Cursor tracks. |
| 0:38 | 1s | Click → navigates to the registered model `classifier`. | Model detail page loads. |
| 0:39 | 3s | Hold on the model page. | Versions visible. v<N> has `@champion` alias visible. |
| 0:42 | 4s | Cursor hovers on `@champion` alias pill. | **POST: yellow circle highlight on the @champion pill.** |
| 0:46 | 4s | Hold. | Viewer registers "this is the version that's live". |
| 0:50 | — | End. | Cut to beat 6. |

**Narration (50s, ~120 words):**
> "MLflow tracks every run from the autoresearch loop. [hold on AUC sort] Sorted by AUC, the winner's at the top. [pause as run page loads] When I click in, I see the params Claude proposed, the metrics it earned, and — here's the receipt — [hold on kfp_run_id hover, 3-4 seconds] the KFP run ID tag. Same UUID I just showed you in Kubeflow. So when I'm looking at the model in MLflow, I can trace it all the way back to the exact pipeline execution that produced it. No 'latest run' guessing. [pause] The registered model carries an `@champion` alias — that's the one inference pulls at startup."

**Post-production notes:**
- Cyan rectangle highlight #2 at T+0:19-0:23
- This is the FIRST time the viewer sees a repeat ID. The motif starts landing here

---

### Beat 6 — GitHub accountability proof (55s) — THE MOST IMPORTANT SCENE

**What's on screen:** Tab 4 — GitHub merged PRs list, filtered to `is:pr is:merged auto-exp`.

**Pre-state verify:** The most recent PR in the list has a body containing both `KFP run:` and `MLflow run:`. If the most recent doesn't (PR template may have changed), pre-identify which PR you'll click. Open it in a scout tab to verify, close the scout tab, recording uses the main tab.

**Shot list:**
| T+ | Duration | Action | What viewer sees |
|---|---|---|---|
| 0:00 | 3s | Settle. Cursor parked above the PR list. | List of 5-10 merged `auto-exp:` PRs. |
| 0:03 | 4s | Slow scroll DOWN 1 page-height, then back UP. | Demonstrates there are many merged PRs (not just one) — visual receipt that the loop runs and ships frequently. |
| 0:07 | 2s | Cursor glides to the first (top) PR's title. | Cursor on PR title link. |
| 0:09 | 1s | Click. | Navigates to PR conversation view. |
| 0:10 | 3s | PR page loads. Cursor parked. | Title + description + commits visible. |
| 0:13 | 3s | Slow scroll to the PR body (immediately under title). | Body has structured info: KFP run, MLflow run, version, AUC. |
| 0:16 | 2s | Cursor moves to the `KFP run:` line. | Cursor tracks. |
| 0:18 | 5s | Hover, HOLD on the KFP run UUID in the body. | **POST: cyan rectangle highlight — appearance #3 of the ID.** Identical to beats 4 and 5. |
| 0:23 | 3s | Cursor moves to the `MLflow run:` line. | Cursor tracks. |
| 0:26 | 3s | Hover, HOLD. | **POST: cyan rectangle on MLflow run ID — secondary highlight.** |
| 0:29 | 2s | Cursor moves to "Files changed" tab. | Cursor tracks. |
| 0:31 | 1s | Click. | Files diff loads. |
| 0:32 | 4s | Slow zoom-in on the diff. | `configs/params.yaml` 1-3 line diff visible. |
| 0:36 | 4s | Hold. | Viewer reads the diff. |
| 0:40 | 3s | Cursor moves back to "Conversation" tab. | Cursor tracks. |
| 0:43 | 1s | Click. | Back to PR conversation. |
| 0:44 | 3s | Slow scroll DOWN to bottom of page, where commits are listed. | Commits + Verified badges visible. |
| 0:47 | 4s | Hover on the green "Verified" badge of a commit. | Tooltip appears: "This commit was signed with the committer's verified signature." **POST: green rectangle highlight on Verified.** |
| 0:51 | 4s | HOLD on Verified for longer than comfortable. | The Verified badge is the climax of this beat. |
| 0:55 | — | End. | Cut to beat 7. |

**Narration (55s, ~130 words):**
> "This is the part most agentic-AI demos skip. Every model promotion has a corresponding merged PR. [hold on scroll showing many PRs] Many of them, generated by Claude, merged through the same GitHub flow a human PR would use. [pause as PR page loads] The PR body has the KFP run ID — [hold on cyan highlight] same one from earlier — the MLflow run ID, and the AUC delta. [pause] The diff is small and inspectable, because it's just config and feature engineering. [pause] And the commit — [hold on Verified badge] is signed by a GitHub App, not a personal access token. That's the green Verified badge. If a future regulator asks 'why does the model in production look like this', the answer's a git log away."

**Post-production notes:**
- ID highlight #3 at T+0:18-0:23 — the same cyan, same shape, same duration as beats 4 and 5. Consistency is the motif
- Verified badge is the HERO shot of the entire demo. Hold it. Don't rush

---

### Beat 7 — ArgoCD + live API proof (45s)

**What's on screen:** Tab 3 — ArgoCD `inference-api` application, resource tree view. Already logged in.

**Pre-state verify:** Banner shows Synced (green) + Healthy (green). If either is yellow or red → something is mid-rollout; wait for it to settle.

**Shot list:**
| T+ | Duration | Action | What viewer sees |
|---|---|---|---|
| 0:00 | 3s | Settle. Cursor parked top-right of canvas. | Resource tree visible. Top banner: `Synced` (green pill) + `Healthy` (green pill). |
| 0:03 | 3s | Cursor moves toward the top banner. Hover on the green Synced pill. | **POST: green rectangle highlight on both pills, holds 3s.** |
| 0:06 | 3s | Hold. | Banner pills visible. |
| 0:09 | 2s | Cursor moves to the `inference-api` Deployment node in the tree. | Cursor tracks. |
| 0:11 | 1s | Click. | Right side panel opens with tabs: Summary, Manifest, Events, Logs, Diff. |
| 0:12 | 1s | Click "Manifest" tab in the panel. | YAML manifest view loads. |
| 0:13 | 4s | Slow scroll down the manifest until `spec.template.metadata.annotations` is visible. | Annotations block visible: `mlops/classifier-version: "<N>"`, `mlops/classifier-run-id: "<uuid>"`. |
| 0:17 | 2s | Cursor moves to the `mlops/classifier-version:` line. | Cursor tracks. |
| 0:19 | 5s | Hover and HOLD. | **POST: cyan rectangle on the version number — appearance #4 of the ID-matching motif (this is the model version, not the KFP UUID, but the IDEA matches: same identifier across surfaces).** |
| 0:24 | 3s | Cursor moves to `mlops/classifier-run-id:` line. | Cursor tracks. |
| 0:27 | 4s | Hover and HOLD on the run-id UUID value. | **POST: cyan rectangle — appearance #5, same UUID as beats 4, 5, 6.** |
| 0:31 | 1s | **Cmd+Tab to terminal.** | Window switches. |
| 0:32 | 3s | Paste `curl -s http://34.47.242.89/health \| jq .` (don't type — paste). | Command appears. |
| 0:35 | 1s | Press Enter. | Response renders. |
| 0:36 | 2s | JSON output appears. | `{"status": "healthy", "model_loaded": true, "model_version": "N", "model_run_id": "<uuid>"}` |
| 0:38 | 6s | HOLD on the response. | **POST: cyan rectangles on `model_version` and `model_run_id` — appearance #6, matches the ArgoCD annotation AND the KFP run ID.** |
| 0:44 | 1s | End. | Cut to beat 8. |

**Narration (45s, ~105 words):**
> "ArgoCD reconciles the cluster to git every three minutes. [hold on green pills] Synced means: the live cluster matches what's in git. Healthy means: the pods are actually running. [pause for manifest scroll] Inside the Deployment's pod-template annotations, the classifier version and the run ID — same UUID I've shown you four times already. [pause] And the live API: curl /health. [hold on response] The same version. The same run ID. Six surfaces. One identifier. That's the audit chain."

**Post-production notes:**
- This is the payoff scene. Six cyan highlights stacked, all the same UUID. Spend extra time editing this beat
- The Cmd+Tab transition is jarring — soften with a 0.2s cross-fade

---

### Beat 8 — Scope decisions (30s)

**What's on screen:** Static slide. Build in Keynote / Figma / Canva → export to 1920×1080 PNG → drop into Screen Studio.

**Slide content:**
```
Scope decisions for this iteration

  IN
  ✓  Agentic release control proven end-to-end
  ✓  Signed PR + metric gate = trust boundary
  ✓  GitOps reconciliation under autoresearch concurrency

  NEXT
  ·  OpenTelemetry traces + alerting on regressions
  ·  Policy checks on LLM-generated diffs
  ·  Traffic-aware serving (canary / shadow)
```

**Shot list:**
| T+ | Duration | Action | What viewer sees |
|---|---|---|---|
| 0:00 | 3s | Slide fades in (Screen Studio: 0.5s fade). | Title + IN/NEXT headers visible. |
| 0:03 | 12s | Hold. | Viewer reads the slide. |
| 0:15 | 15s | Slow Ken Burns zoom (10% over 15 seconds). | Subtle motion keeps it from feeling static. |
| 0:30 | — | Cut to end card. | "github.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch" |

**Narration (30s, ~70 words):**
> "Some honest engineering. This is a single-zone GKE Autopilot cluster, not HA. The serving layer is FastAPI by design — KServe and Triton are the next layer when traffic shape demands it. The agentic trust boundary today is signed PRs plus metric gates. The natural next steps — OpenTelemetry, policy checks on LLM diffs, traffic-aware serving — are deferred, not denied."

---

## 3.5. Cursor choreography — the difference between "demo" and "screenshare"

These five rules separate amateur recordings from polished ones. Internalize before pressing record.

1. **Move slowly.** Real cursors fly. Demo cursors glide. Aim for ~50% of your normal speed. If a click feels rushed in editing, your cursor was moving too fast.

2. **Pause before clicking.** Always 0.3-0.5s of dwell on the target before the click. Lets the viewer's eye catch up. Screen Studio's "click dwell" setting handles this automatically; if you're recording with QuickTime, force it manually.

3. **Don't shake.** No micro-jitter, no "wandering" cursor while reading the screen. Park the cursor outside the active area (top corners) when you want it offstage.

4. **Use hover as an anchor.** When something needs to land — an ID, a metric, a status pill — hover for 2-3 seconds, not 0.5s. Most amateurs move on too quickly.

5. **Cursor speed = viewer attention.** Fast cursor = viewer feels rushed. Slow cursor = viewer trusts the demo. Trust > speed.

## 3.6. Error recovery — what to do if a take fails mid-record

The temptation is to try to fix it live. **Don't.** Three rules:

1. **If a UI shows an error message you didn't expect** (e.g. MLflow returns 500, ArgoCD shows OutOfSync) — stop the recording, fix the cluster, re-press record. Never publish a take with a real error visible.

2. **If you misspeak** — stop, breathe, press record again from the start of that beat. Editing audio splices is harder than re-recording 35 seconds.

3. **If a wrong tab opens / popup appears** — stop. Mac's notification timing is unpredictable. The fix is "record again with notifications fully off" not "edit the popup out."

The reason: each beat is short. Re-recording costs 1 minute. Editing artifacts costs 30 minutes and looks worse.

## 3.7. What NOT to say (and what to say instead)

The single biggest credibility leak in tech demos is overclaiming. Avoid these exact phrases:

| Don't say | Say instead |
|---|---|
| "production-grade" | "portfolio-scale production pattern" |
| "the live model can never get worse" | "only metric-winning candidates trigger rollout" |
| "any binary CSV plugs in" | "schema-driven via params.yaml, validated on two datasets so far" |
| "zero risk" | "the trust boundary is signed PRs plus a metric gate" |
| "Claude improves the model 30× while I sleep" | "the loop iterates 20 times in 4 hours; not every iter wins" |
| "20 runs" (if the UI doesn't show 20) | "across the autoresearch run" (no count) |
| "source diffs trained immediately" | (only true if KFP image was rebuilt to include the diff) |
| "Known Limitations" (negative framing) | "Scope decisions for this iteration" (positive framing) |
| "real production system" | "real release-control pattern" |

The other rule: **never narrate things the viewer can't see.** If you say "the AUC went from 0.8 to 0.95," the AUC numbers need to be on screen at that moment.

## 3.8. Industry framing — how to talk about this for jobs

For LinkedIn / interviews / portfolio sites, frame the project around these 2026-relevant angles:

- **Agentic AI engineering** (Cursor / Claude Code / Devin parallel). "I built a system where an LLM is a contributor to the codebase. It proposes diffs. The cluster runs them. Only winners ship."
- **LLMOps + GitOps fusion**. "The MLOps + LLMOps overlap is where most teams are figuring out trust right now. This is one way to draw the line."
- **Compound AI system** ([BAIR term](https://bair.berkeley.edu/blog/2024/02/18/compound-ai-systems/)). Five decoupled components, reconciliation via git.
- **The audit chain** — the most novel piece. "Most agentic demos show capability. This one shows accountability."

Job-market tags to lean on: MLOps Engineer, LLMOps Engineer, ML Platform Engineer, AI Infrastructure Engineer, Senior Data Engineer with ML. The project hits all five with different emphases — pick the role + adjust the framing.

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
4. **Add ID-matching color overlays** as described in Section 4
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
- [ ] All 4 browser tabs pre-configured per Section 2A
- [ ] Terminal font size 18+, dark theme, history cleared (Section 2B)
- [ ] State snapshot pasted in sticky note (Section 2C)
- [ ] Dock + menu bar hidden, DND on, distracting apps quit (Section 2D)
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
