# How Everything Works — End-to-End Technical Guide

> Written so that someone who has never seen this project can understand
> exactly what happens when a model is trained, promoted, and deployed.
> Over-explanation is intentional. Every "but why?" should be answered.

---

## Table of Contents

1. [The One-Sentence Version](#1-the-one-sentence-version)
2. [The Players — What Each Tool Does](#2-the-players--what-each-tool-does)
3. [What Is @champion? (The Most Important Concept)](#3-what-is-champion-the-most-important-concept)
4. [The Full Deployment Chain — Step by Step](#4-the-full-deployment-chain--step-by-step)
5. [What Happens When an Experiment FAILS](#5-what-happens-when-an-experiment-fails)
6. [What Is an Annotation and Why Does It Cause a Restart?](#6-what-is-an-annotation-and-why-does-it-cause-a-restart)
7. [Who Does What — The Responsibility Map](#7-who-does-what--the-responsibility-map)
8. [The Auto-Experiment Loop — Detailed Walkthrough](#8-the-auto-experiment-loop--detailed-walkthrough)
9. [Two Flows, Two Triggers (Code vs Model)](#9-two-flows-two-triggers-code-vs-model)
10. [DVC vs KFP — Why Both Exist](#10-dvc-vs-kfp--why-both-exist)
11. [The Two-Workload Split (Controller + KFP Pods)](#11-the-two-workload-split-controller--kfp-pods)
12. [Cost and Sleep/Wake](#12-cost-and-sleepwake)
13. [Known Limitations (Honest Assessment)](#13-known-limitations-honest-assessment)
14. [Quick Reference](#14-quick-reference)

---

## 1. The One-Sentence Version

An LLM (Claude) proposes changes to improve a churn prediction model, KFP trains and evaluates each proposal on Kubernetes, and if the model improves, the auto-loop pushes a git commit that makes ArgoCD automatically roll out new serving pods that load the improved model from MLflow.

---

## 2. The Players — What Each Tool Does

Think of this as a relay race. Each tool does ONE job and passes the baton:

```
Tool               What it does                              Analogy
────               ────────────                              ───────
DVC                Versions data files in GCS                "git for CSVs and model files"
MLflow             Stores experiment results + model registry "a database of every model ever trained"
KFP                Runs training pipeline on Kubernetes       "each step in its own container"
ArgoCD             Deploys whatever is in git to the cluster  "if git says X, cluster becomes X"
churn-api          Serves predictions from the champion model "the waiter who brings you the food"
auto_loop.py       Asks Claude for ideas, runs experiments    "the scientist who designs experiments"
```

**Critical insight:** These tools do NOT communicate with each other directly.
- MLflow doesn't tell ArgoCD anything.
- ArgoCD doesn't know MLflow exists.
- KFP doesn't restart pods.
- The auto-loop controller is the one that connects them — through **git commits**.

---

## 3. What Is @champion? (The Most Important Concept)

### It's a pointer, like a git branch

In git, `main` is a branch name that points to a specific commit. You can move it to point to a different commit. The old commit still exists — you just changed where the name points.

MLflow's `@champion` works exactly the same way:

```
Git:                                 MLflow:
────                                 ──────
main → commit abc123                 @champion → model version 2
                                     @challenger → model version 3

You run: git checkout -B main def456 You run: client.set_registered_model_alias(
                                               "churn-model", "champion", "3")

Now:                                 Now:
main → commit def456                 @champion → model version 3
(abc123 still exists)                (version 2 still exists)
```

### Where @champion lives

It's a row in MLflow's PostgreSQL database (CloudSQL). Not a file, not a Kubernetes object, not a git tag. Just a database entry that says: *"the alias 'champion' for model 'churn-model' currently points to version 2."*

### What loads @champion

The churn-api pods, at startup, run:
```python
model = mlflow.sklearn.load_model("models:/churn-model@champion")
```

This call goes to the MLflow server, which resolves `@champion` to version 2 (or whatever it currently points to), downloads that model's pickle file from GCS, and deserializes it into memory.

### The critical gap

**Moving the @champion pointer does NOT restart pods.** If @champion moves from v2 to v3 but the pods don't restart, they keep serving v2. The pods loaded v2 at startup and have it in memory. They don't poll MLflow for changes.

This gap is what the annotation-bump mechanism closes (explained in Section 6).

---

## 4. The Full Deployment Chain — Step by Step

Here is exactly what happens when the auto-loop finds a better model. Every step, every actor, every decision.

```
STEP 1: Auto-loop controller (auto_loop.py) proposes an experiment
═══════════════════════════════════════════════════════════════════

  auto_loop.py reads current code + history
        │
        ▼
  Calls Claude API: "Propose ONE change to improve AUC-ROC"
        │
        ▼
  Claude returns JSON:
    { "experiment_name": "hist_gradient_boost",
      "rationale": "HistGBM handles nulls natively...",
      "params_yaml": "model_type: HistGradientBoostingClassifier" }
        │
        ▼
  auto_loop.py writes the new params.yaml to disk
  auto_loop.py records: champion_before = 2  (reads from MLflow API)


STEP 2: KFP pipeline trains and evaluates the model
════════════════════════════════════════════════════

  auto_loop.py calls: kfp_client.create_run_from_pipeline_package(...)
        │
        ▼
  KFP creates 3 pods on GKE (sequentially):

  Pod 1: preprocess
    → Reads raw CSV from GCS
    → Cleans data, splits 80/20
    → Writes train.csv and test.csv as KFP artifacts

  Pod 2: train
    → Reads train.csv
    → Fits HistGradientBoostingClassifier (the proposed change)
    → Logs params + model artifact to PRODUCTION MLflow (CloudSQL)
    → Registers model as "churn-model" version 3
    → Outputs: run_id (passed to evaluate step)

  Pod 3: evaluate
    → Reads test.csv + model artifact
    → Computes AUC-ROC = 0.834
    → Reads current @champion from MLflow → version 2, AUC = 0.816
    → 0.834 > 0.816?  ──── YES ────────────────────────┐
    │                                                    │
    │  Calls MLflow API:                                 │
    │  client.set_registered_model_alias(                │
    │    "churn-model", "champion", "3")                 │
    │                                                    │
    │  @champion now points to version 3                 │
    │  (But pods still serve v2! Nobody restarted them.) │
    └────────────────────────────────────────────────────┘


STEP 3: Auto-loop detects the promotion and triggers deployment
═══════════════════════════════════════════════════════════════

  auto_loop.py polls KFP: run status? → SUCCEEDED
        │
        ▼
  Reads MLflow: champion_after = 3
  Compares: champion_after (3) != champion_before (2)
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │  THIS IS THE KEY STEP                                   │
  │                                                         │
  │  auto_loop.py edits k8s/deployment.yaml:                │
  │                                                         │
  │  Before:                                                │
  │    annotations:                                         │
  │      mlflow/champion-version: "2"                       │
  │                                                         │
  │  After:                                                 │
  │    annotations:                                         │
  │      mlflow/champion-version: "3"                       │
  │                                                         │
  │  Then:                                                  │
  │    git add k8s/deployment.yaml configs/params.yaml      │
  │    git commit -m "auto-exp: hist_gradient_boost |       │
  │                   AUC 0.816 → 0.834"                    │
  │    git push origin main                                 │
  └─────────────────────────────────────────────────────────┘


STEP 4: ArgoCD detects the git change and deploys
══════════════════════════════════════════════════

  ArgoCD (always watching the main branch):
        │
        ▼
  Detects: k8s/deployment.yaml changed on main
  Compares cluster state vs git state:
    Cluster: annotation "mlflow/champion-version" = "2"
    Git:     annotation "mlflow/champion-version" = "3"
        │
        ▼
  Applies the change to the cluster (kubectl apply)
        │
        ▼
  Kubernetes sees: pod template changed (annotation is different)
        │
        ▼
  Creates NEW ReplicaSet with new pods
        │
        ▼
  New pods start → call mlflow.sklearn.load_model("models:/churn-model@champion")
  @champion resolves to version 3 → downloads v3 model from GCS
  Readiness probe passes → pod receives traffic
        │
        ▼
  Old pods (serving v2) terminate gracefully
        │
        ▼
  DONE. All traffic now served by v3.
```

---

## 5. What Happens When an Experiment FAILS

Two types of failure, both are safe:

### Case A: Model is worse than champion

```
KFP evaluate step:
  New model v3: AUC = 0.810
  Current @champion v2: AUC = 0.816
  0.810 < 0.816 → NOT BETTER
        │
        ▼
  Sets @challenger alias to v3 (NOT @champion)
  @champion STILL points to v2
        │
        ▼
  Back in auto_loop.py:
    champion_after = 2 (unchanged)
    champion_before = 2 (unchanged)
    2 == 2 → NO promotion happened
        │
        ▼
  auto_loop.py does NOT edit deployment.yaml
  auto_loop.py does NOT commit to git
  auto_loop.py runs: git checkout -- configs/params.yaml src/train.py
    (reverts Claude's proposed changes to a clean state)
        │
        ▼
  ArgoCD: nothing changed in git → nothing to deploy
  Pods: still serving v2, completely undisturbed
  Bad model v3: exists in MLflow as @challenger (auditable) but never deployed
```

### Case B: Pipeline crashes (code error, OOM, timeout)

```
KFP run status: FAILED
        │
        ▼
  auto_loop.py detects failure
  auto_loop.py runs: git checkout -- configs/params.yaml src/train.py
    (reverts Claude's proposed changes)
  Logs "failed" to history.tsv and MLflow auto-experiment
        │
        ▼
  @champion: unchanged
  deployment.yaml: unchanged
  ArgoCD: nothing to deploy
  Pods: undisturbed
```

**Summary: bad experiments can NEVER trigger a deployment.** The deployment annotation only changes if the champion alias actually moved. The champion alias only moves if AUC improves. If AUC doesn't improve, nothing changes in git, so ArgoCD has nothing to deploy.

---

## 6. What Is an Annotation and Why Does It Cause a Restart?

### Annotations in plain English

Every Kubernetes object has metadata. Annotations are key-value pairs in that metadata — like sticky notes you can attach to any resource:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: churn-api
  annotations:                           # ← annotations on the Deployment itself
    description: "Inference API"         #   (these do NOT cause pod restarts)
spec:
  template:
    metadata:
      annotations:                       # ← annotations on the POD TEMPLATE
        mlflow/champion-version: "2"     #   (changing THESE causes pod restarts!)
```

There are two places for annotations:
1. **On the Deployment** — just metadata, changing it does nothing to pods
2. **On the Pod Template** (inside `spec.template.metadata`) — changing this tells Kubernetes "the pod specification changed, create new pods"

### Why changing a pod template annotation causes a restart

Kubernetes Deployments work by managing ReplicaSets. A ReplicaSet is a "template" for pods. When you change ANYTHING in the pod template (image, env var, annotation, label), Kubernetes creates a **new ReplicaSet** with the new template and gradually shifts traffic from old pods to new pods. This is a rolling update.

```
Before (annotation = "2"):
  ReplicaSet-A (template: annotation "2")
    ├── Pod-1 (serving v2 model)
    └── Pod-2 (serving v2 model)

After git push changes annotation to "3":
  ReplicaSet-A (template: annotation "2")    ← being scaled down
    ├── Pod-1 (terminating)
    └── Pod-2 (terminating)
  ReplicaSet-B (template: annotation "3")    ← being scaled up
    ├── Pod-3 (starting, loading v3 model)
    └── Pod-4 (starting, loading v3 model)
```

The annotation itself is meaningless to Kubernetes — it doesn't read `"mlflow/champion-version"`. The restart happens because the pod template changed. The annotation is just a convenient way to force a pod template change without changing the image or any other functional setting.

### Why not just use `kubectl rollout restart`?

Because that's an imperative command, not GitOps. ArgoCD treats git as the source of truth. If you run `kubectl rollout restart` directly:
1. ArgoCD might detect "drift" and revert it
2. There's no git record of when/why the restart happened
3. You can't `git revert` a `kubectl` command

The annotation in git is auditable: you can `git log k8s/deployment.yaml` and see exactly when each champion was promoted, what the old champion was, and which experiment caused the change.

---

## 7. Who Does What — The Responsibility Map

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  Claude (LLM)          "The scientist"                                  │
│  ─────────────                                                          │
│  Proposes ONE code/config change per iteration.                         │
│  Has no access to the cluster, MLflow, or Kubernetes.                   │
│  Only produces a JSON with proposed file contents.                      │
│                                                                          │
│  auto_loop.py          "The lab manager"                                │
│  ────────────                                                           │
│  Applies Claude's proposal to files.                                    │
│  Submits KFP pipeline runs.                                             │
│  Waits for results.                                                     │
│  Reads MLflow to check if champion changed.                             │
│  Commits to git if champion was promoted.                               │
│  Reverts files if experiment failed or didn't improve.                  │
│  THIS is the component that connects everything.                        │
│                                                                          │
│  KFP Pipeline          "The lab equipment"                              │
│  ────────────                                                           │
│  Runs preprocess → train → evaluate as containers on GKE.              │
│  Logs everything to production MLflow.                                  │
│  The evaluate step moves the @champion alias IF the model is better.   │
│  Does NOT trigger any deployment.                                       │
│  Does NOT commit to git.                                                │
│                                                                          │
│  MLflow                "The lab notebook"                               │
│  ──────                                                                 │
│  Passive database. Stores runs, metrics, model versions, aliases.       │
│  Does NOT send notifications.                                           │
│  Does NOT trigger deployments.                                          │
│  Does NOT know about Kubernetes.                                        │
│  Just answers questions when asked: "What's the current @champion?"    │
│                                                                          │
│  ArgoCD                "The factory operator"                           │
│  ──────                                                                 │
│  Watches k8s/ directory in git.                                         │
│  When deployment.yaml changes → applies to cluster → pods restart.     │
│  Does NOT know about MLflow, models, or experiments.                    │
│  Only knows: "git says X, cluster should match X."                      │
│                                                                          │
│  churn-api             "The waiter"                                     │
│  ─────────                                                              │
│  Loads @champion model from MLflow at startup.                          │
│  Serves predictions via /predict endpoint.                              │
│  Does NOT know about experiments, Git, or ArgoCD.                       │
│  Only knows: "load @champion, serve requests."                          │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 8. The Auto-Experiment Loop — Detailed Walkthrough

```
auto_loop.py starts (K8s Job or local)
      │
      │ 1. git clone (gets latest code from GitHub)
      │
      │ 2. Read:
      │    - configs/params.yaml (current hyperparameters)
      │    - src/train.py (current model code)
      │    - history.tsv (what was tried before, what worked, what didn't)
      │    - MLflow: current @champion version + AUC
      │
      │ 3. Call Claude API:
      │    System prompt: program.md (research directions, constraints)
      │    User prompt: current code + history + "propose ONE change"
      │
      │    Claude returns: { "experiment_name": "...",
      │                      "rationale": "...",
      │                      "params_yaml": "... new content ..." }
      │
      │ 4. Apply change: write new params.yaml (or train.py) to disk
      │    Record: champion_before = current @champion version
      │
      │ 5. Submit KFP pipeline run: kfp_client.create_run(...)
      │    Wait for completion (poll every 30s)
      │
      │ 6. KFP pipeline runs on GKE:
      │    preprocess → train → evaluate
      │    evaluate decides: promote to @champion or not
      │
      │ 7. Read MLflow: champion_after = current @champion version
      │
      │ 8a. champion_after != champion_before → IMPROVED!
      │     - Edit k8s/deployment.yaml annotation
      │     - git commit: "auto-exp: <name> | AUC <old> → <new>"
      │     - git push → ArgoCD syncs → pods restart → serve new model
      │
      │ 8b. champion_after == champion_before → NOT improved
      │     - git checkout -- . (revert ALL changes, clean slate)
      │     - Nothing committed, nothing deployed
      │
      │ 9. Log to MLflow "auto-experiment" experiment
      │    Log to auto_experiment/history.tsv
      │
      │ 10. REPEAT (back to step 2) for N iterations
      │
      └─── EXIT when budget exhausted (N experiments or T hours)
```

### Why we revert code on failure

Claude proposed `HistGradientBoosting`. We tried it. AUC got worse. If we leave `params.yaml` with `model_type: HistGradientBoostingClassifier`, the NEXT iteration starts from a broken state. Claude might propose "add class_weight" on top of HistGBM, compounding the failure.

By reverting (`git checkout -- .`), the next iteration starts from the **last known good state** — the code that produced the current champion. Claude gets a clean starting point every time.

---

## 9. Two Flows, Two Triggers (Code vs Model)

There are two completely independent reasons a deployment might happen:

```
FLOW 1: Code changed (developer pushes new src/ code)
══════════════════════════════════════════════════════

  git push (changes to src/api.py, Dockerfile, etc.)
       │
       ▼
  CI: lint → test → docker build → push new image to ghcr.io
       │
       ▼
  CI updates k8s/deployment.yaml: image SHA changed
       │
       ▼
  ArgoCD: detects image SHA change → rolls out new pods
       │
       ▼
  New pods (with new code) load SAME @champion model from MLflow
  (model didn't change, only the serving code changed)


FLOW 2: Model improved (auto-loop found a better model)
═══════════════════════════════════════════════════════

  auto_loop.py experiment succeeds → @champion moves to v3
       │
       ▼
  auto_loop.py updates annotation in k8s/deployment.yaml
  git commit + push
       │
       ▼
  ArgoCD: detects annotation change → rolls out new pods
       │
       ▼
  New pods (with SAME code) load NEW @champion v3 from MLflow
  (code didn't change, only the model changed)
```

**These flows never interfere with each other.** A code change doesn't affect which model is served (it's always `@champion`). A model change doesn't affect what code runs (it's the same Docker image).

---

## 10. DVC vs KFP — Why Both Exist

```
                  DVC                           KFP
                  ───                           ───
  Runs on:       Your laptop                    Kubernetes cluster
  Good for:      Fast local iteration           Production training
  Command:       make repro                     make kfp-run
  Caching:       dvc.lock (hash-based)          KFP metadata (per-component)
  Steps run:     As Python subprocesses         As separate containers (pods)
  MLflow:        Via port-forward               Direct cluster DNS

  Use DVC when:  You're developing locally, debugging code, testing changes
  Use KFP when:  You're running real training for deployment, auto-experiments
```

DVC also versions data — `dvc push/pull` syncs large files (CSVs, model pickles) with GCS so any collaborator can reproduce your exact dataset.

In the auto-experiment loop, **KFP is the runner, not DVC**. The auto-loop submits KFP pipeline runs. DVC is for local development only.

---

## 11. The Two-Workload Split (Controller + KFP Pods)

```
Workload 1: auto-loop controller              Workload 2: KFP pipeline pods
──────────────────────────────                 ──────────────────────────────
Type: K8s Job (run once, exit)                 Type: Ephemeral pods (created per step)
Purpose: Brain (decisions)                     Purpose: Muscle (compute)
Does: Claude API calls, git ops,              Does: Data processing, model training,
      KFP submission, result checking                model evaluation, MLflow logging
Needs: ANTHROPIC_API_KEY, GitHub PAT          Needs: GCS access, MLflow access
Resources: Tiny (just API calls)              Resources: As needed (CPU, memory, GPU)
```

**Why not one workload?** The LLM call (Claude) and the training run (KFP) have different resource profiles. Claude calls are I/O-bound (waiting for API response). Training is CPU-bound. Bundling them wastes resources. Plus, KFP gives you per-step isolation, caching, and the pipeline visualization UI — which you lose if you run everything in one container.

---

## 12. Cost and Sleep/Wake

### Monthly cost (24/7 running)

| Component | $/month |
|-----------|---------|
| GKE Autopilot compute | ~$110 |
| CloudSQL db-f1-micro | $8 |
| Load Balancer IPs (4) | $20 |
| PVCs + GCS | $5 |
| **Total** | **~$143** |

**$300 free credits / $143 = ~2 months running 24/7.**

### Sleep to save money

```bash
make cluster-sleep    # Scale all pods to 0. Cost drops to ~$25/month.
make cluster-wake     # Scale back up. Same IPs. ~3 min.
```

---

## 13. Known Limitations (Honest Assessment)

| Item | Status | Detail |
|------|--------|--------|
| CI champion promotion | Fake | CI uses ephemeral MLflow; trained model is discarded |
| Auto-loop on cluster | Local only | Runs on laptop, not as a K8s Job (yet) |
| TLS / HTTPS | No | HTTP only, no domain, no cert-manager |
| Single zone | Yes | asia-south1, no HA, free tier |
| Hot model reload | No | Pods must restart to load new champion |
| KFP evaluate race | Exists | Uses "latest run" search, should pass run_id |

---

## 14. Quick Reference

### Live URLs (wake cluster first with `make cluster-wake`)

| Service | URL |
|---------|-----|
| Prediction API | `http://34.180.37.1/predict` |
| MLflow UI | `http://34.180.20.197:5000` (click "Model training" tab, not "GenAI") |
| ArgoCD UI | `http://34.100.246.237` (admin / Y6p9-krPfkEhm4Sd) |
| KFP UI | `http://34.93.2.209` |

### Test the prediction API

```bash
curl -X POST http://34.180.37.1/predict \
  -H "Content-Type: application/json" \
  -d '{
    "gender": "Female", "SeniorCitizen": 0, "Partner": "Yes",
    "Dependents": "No", "tenure": 12, "PhoneService": "Yes",
    "MultipleLines": "No", "InternetService": "Fiber optic",
    "OnlineSecurity": "No", "OnlineBackup": "No",
    "DeviceProtection": "No", "TechSupport": "No",
    "StreamingTV": "No", "StreamingMovies": "No",
    "Contract": "Month-to-month", "PaperlessBilling": "Yes",
    "PaymentMethod": "Electronic check",
    "MonthlyCharges": 70.35, "TotalCharges": 846.0
  }'
# → {"churn": 1, "churn_probability": 0.71}
```

### Common commands

```bash
make gke-connect              # connect kubectl to GKE
make gke-status               # pod health across all namespaces
make gke-urls                 # print live IPs
make cluster-sleep            # scale to 0 (save money)
make cluster-wake             # scale back up
make bootstrap                # seed @champion in MLflow (after fresh deploy)
make mlflow-kill && make mlflow  # port-forward MLflow to localhost:5000
make repro                    # run DVC pipeline locally (dev/debug)
make auto-experiment-dry-run  # preview Claude's proposal without running
make auto-experiment          # run 20 experiments locally
make kfp-run                  # submit compiled pipeline to KFP
make test                     # run 10 pytest tests
```
