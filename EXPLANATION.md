# MLOps Architecture — Technical Design Document

> Written for a teammate or manager picking up this project for the first time.
> Covers what exists, why it was built this way, what is wrong, and the target architecture.

---

## Table of Contents

1. [Project Goal](#1-project-goal)
2. [The Full System at a Glance](#2-the-full-system-at-a-glance)
3. [Component Deep Dives](#3-component-deep-dives)
4. [The Current CI/CD Flow — And Why It's Broken](#4-the-current-cicd-flow--and-why-its-broken)
5. [The Target Architecture](#5-the-target-architecture)
6. [The Auto-Experiment Loop](#6-the-auto-experiment-loop)
7. [The Deployment Gap — How Champion Promotion Triggers a Rollout](#7-the-deployment-gap--how-champion-promotion-triggers-a-rollout)
8. [The Two-Workload Split](#8-the-two-workload-split)
9. [What to Fix and in What Order](#9-what-to-fix-and-in-what-order)
10. [Cost and Operations](#10-cost-and-operations)
11. [What Is Working vs What Is Not](#11-what-is-working-vs-what-is-not)
12. [Quick Reference](#12-quick-reference)

---

## 1. Project Goal

Predict which Telco customers will churn. The **model is intentionally simple** (Random Forest). The point is the infrastructure around it:

> "Auto-research autonomously improves the model overnight. When it finds something better, it gets deployed automatically. Zero human in the loop."

Everything in this document is evaluated against that goal.

---

## 2. The Full System at a Glance

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TWO INDEPENDENT FLOWS                              │
│                                                                             │
│  CODE FLOW                          MODEL FLOW                              │
│  ──────────                         ───────────                             │
│  src/ changed                       Auto-loop proposes experiment           │
│      │                                  │                                   │
│      ▼                                  ▼                                   │
│  CI: lint → test                    KFP pipeline runs on GKE                │
│      │                              preprocess → train → evaluate           │
│      ▼                                  │                                   │
│  docker build → push image              ▼                                   │
│      │                              MLflow: @champion alias updated         │
│      ▼                                  │                                   │
│  k8s/deployment.yaml updated           ▼                                   │
│      │                              Rollout triggered (annotation bump)     │
│      ▼                                  │                                   │
│  ArgoCD syncs → new pods            churn-api pods restart                  │
│      │                                  │                                   │
│      └──────────────────────────────────┘                                   │
│                      Both paths end here:                                   │
│           churn-api serving predictions from @champion model                │
└─────────────────────────────────────────────────────────────────────────────┘
```

These flows must be kept **separate**. Code changes deploy new serving infrastructure. Model changes promote a new champion. They are independent events.

---

## 3. Component Deep Dives

### 3.1 DVC — Data Version Control

DVC is like Git, but for large files (CSVs, model pickles). Git stores a tiny pointer file (`.dvc`); the actual data lives in GCS.

```
What's in Git:                    What's in GCS:
──────────────                    ──────────────
data/churn_data.csv.dvc           data/churn_data.csv (7,043 rows)
models/churn_model.pkl.dvc        models/churn_model.pkl (19MB)
dvc.lock (file hashes)            data/processed/train.csv
                                  data/processed/test.csv
```

**DVC's role:** data versioning + local pipeline runner.
**DVC is NOT the production pipeline runner.** That's KFP.

When you run `make repro`, DVC executes `preprocess.py → train.py → evaluate.py` locally and tracks which files changed. If `train.py` changes but `preprocess.py` and the raw data don't, DVC skips preprocess and only re-runs train + evaluate. This is the caching mechanism.

### 3.2 MLflow — Experiment Tracking + Model Registry

Two jobs in one tool:

**Job 1: Experiment Tracking** — every training run logs parameters and metrics.

```
Training run logged to MLflow:
  experiment: "churn-prediction"
  run_id: abc123
  params:
    n_estimators: 100
    model_type: RandomForestClassifier
  metrics:
    auc_roc: 0.8162
    accuracy: 0.7807
    f1: 0.5353
  artifact: model.pkl (stored in GCS via --serve-artifacts)
```

**Job 2: Model Registry** — named versions with promotion aliases.

```
"churn-model" registry:
  v1: auc=0.816  ← @challenger
  v2: auc=0.834  ← @champion  ◄── churn-api ALWAYS loads this
  v3: auc=0.821  (no alias)
```

`@champion` is just a pointer. Moving it to v3 does NOT automatically redeploy anything. The pod must restart to pick up the new champion. This is the central deployment gap (see Section 7).

**Why CloudSQL instead of SQLite:**
SQLite is a single file on disk. When the MLflow pod restarts or the node is replaced, the file can be lost. CloudSQL is a managed PostgreSQL instance — it persists independently of pods and nodes. This is the difference between "data that survives crashes" and "data that doesn't."

```
Local setup (vind cluster):         GKE setup:
──────────────────────────          ──────────
MLflow pod                          MLflow pod
  └── /mlflow/mlflow.db             ├── cloud-sql-proxy sidecar
      (SQLite on PVC)               │     └── tunnel to CloudSQL
      LOST on PVC delete            └── PostgreSQL (managed, persists forever)
```

The `cloud-sql-proxy` sidecar runs inside the MLflow pod and creates a local TCP tunnel to CloudSQL. The MLflow server connects to `127.0.0.1:5432` as if it were a local database.

### 3.3 Kubeflow Pipelines (KFP)

KFP runs the same ML pipeline as DVC but as containerized steps on Kubernetes.

```
DVC (local):                        KFP (Kubernetes):
────────────                        ─────────────────
dvc repro                           kfp_client.create_run(...)
  → python preprocess.py              → Pod 1: preprocess
  → python train.py                     writes train.csv to GCS
  → python evaluate.py               → Pod 2: train
  (all on your laptop)                  reads train.csv from GCS
                                        writes model to GCS
                                        logs to MLflow
                                     → Pod 3: evaluate
                                        reads model from GCS
                                        promotes @champion in MLflow
```

**Key difference:** DVC runs steps as subprocesses on one machine. KFP runs each step in an isolated container on its own node. Each step can have different resource limits (Pod 2 could request a GPU if needed). Each step's outputs are immutable artifacts stored in GCS.

**Why both?** DVC is for local development (fast, no cluster needed). KFP is for production training runs (cloud-native, scalable, auditable).

**The pipeline definition:** `pipelines/churn_pipeline.py` is compiled to `pipelines/churn_pipeline.yaml` (a Kubernetes workflow definition). You upload that YAML to the KFP UI (`http://34.93.2.209`), or submit it via the Python client.

### 3.4 ArgoCD — GitOps Deployment Controller

ArgoCD watches a directory in a Git repository and continuously reconciles the Kubernetes cluster to match what's in Git.

```
Git repo (main branch, k8s/ directory)
        │
        │ ArgoCD polls every ~3 min
        ▼
ArgoCD compares:
  "Git says replicas: 2, image: ghcr.io/...@sha256:abc"
  "Cluster has replicas: 2, image: ghcr.io/...@sha256:abc"
  → SAME. Nothing to do.

Git repo changes (deployment.yaml updated):
  "Git says image: ghcr.io/...@sha256:xyz"
  "Cluster has image: ghcr.io/...@sha256:abc"
  → DIFFERENT. Apply the change.
  → New pods created with new image.
  → Old pods terminated after new ones pass readiness probe.
```

**What triggers a deploy:**
1. The Docker image SHA changes in `k8s/deployment.yaml` (code change)
2. An annotation in the pod template changes (model change, see Section 7)

**What does NOT trigger a deploy:** the MLflow `@champion` alias changing. ArgoCD doesn't know about MLflow.

### 3.5 churn-api — The Inference Server

A FastAPI application serving predictions. The critical design decision: **the model is NOT baked into the Docker image.**

```python
# On startup, the pod loads from MLflow registry:
model = mlflow.sklearn.load_model("models:/churn-model@champion")
```

This means:
- Deploying new code → build new image → new pods load current `@champion`
- Promoting a new `@champion` → existing pods still serve old model until restarted

The readiness probe (`/health`) returns 503 until the model finishes loading. The liveness probe (`/health/live`) returns 200 immediately (pod is alive, even if model isn't loaded yet). This prevents Kubernetes from killing a pod that is alive but still downloading the 19MB model artifact.

---

## 4. The Current CI/CD Flow — And Why It's Broken

Here is what CI actually does today:

```
git push to main
    │
    ▼
Job: lint-and-test (runs on every push)
    ├── ruff check (lint)
    ├── ruff format --check
    └── pytest tests/ (10 tests with tiny synthetic data)

Job: pipeline (runs on main branch only)
    ├── Authenticate to GCP (Workload Identity)
    ├── dvc pull (get data from GCS)
    ├── 🚨 START EPHEMERAL MLFLOW (SQLite, local to runner)
    ├── 🚨 dvc repro (trains model against EPHEMERAL MLflow)
    ├── dvc push (pushes DVC artifacts to GCS)
    ├── docker build --platform linux/amd64,linux/arm64
    ├── docker push → ghcr.io/my-neme-eh-jeff/churn-api:$SHA
    └── Update k8s/deployment.yaml image → git commit [skip ci] → push
```

**The two broken steps (🚨):**

```
CI runs dvc repro against ephemeral SQLite MLflow
                │
                ▼
Model trained, champion promoted in EPHEMERAL DB
                │
                ▼
GitHub Actions runner job ENDS
                │
                ▼
SQLite database DELETED FOREVER
                │
                ▼
GKE MLflow (CloudSQL) = unchanged, old champion still @champion
                │
                ▼
churn-api loads old model 
```

**CI is training a model that nobody will ever use.**

The CI job builds a Docker image of the serving code (FastAPI app), updates the deployment, and triggers a rollout. When the new pods start, they load `@champion` from GKE MLflow — which was never updated by CI. The CI training run was completely disconnected from the serving layer.

Additionally: every code push (even a README edit) triggers a full training run, wasting compute and time.

---

## 5. The Target Architecture

### Two Flows, Two Triggers, Zero Coupling

```
╔═══════════════════════════════════════════════════════════════════════════╗
║  CODE FLOW (triggered by: src/ code change)                              ║
║                                                                           ║
║  git push                                                                 ║
║    │                                                                      ║
║    ▼                                                                      ║
║  CI: lint → test (synthetic data, fast) → docker build → push image     ║
║    │                                                                      ║
║    ▼                                                                      ║
║  k8s/deployment.yaml updated with new image SHA                          ║
║    │                                                                      ║
║    ▼                                                                      ║
║  ArgoCD syncs → new pods with new code → load @champion from MLflow      ║
╚═══════════════════════════════════════════════════════════════════════════╝

╔═══════════════════════════════════════════════════════════════════════════╗
║  MODEL FLOW (triggered by: auto-loop controller)                         ║
║                                                                           ║
║  Auto-loop controller (K8s Job, runs when you want to experiment)        ║
║    │                                                                      ║
║    │ 1. git clone repo, read current state                               ║
║    │ 2. Claude API → propose ONE change to params.yaml / train.py        ║
║    │ 3. Apply change                                                      ║
║    │ 4. Submit KFP pipeline run via kfp.Client()                         ║
║    │                                                                      ║
║    ▼                                                                      ║
║  KFP pipeline (containerized pods on GKE):                               ║
║    Pod 1: preprocess → train.csv, test.csv                               ║
║    Pod 2: train → model.pkl, run_id → logs to PRODUCTION MLflow          ║
║    Pod 3: evaluate                                                        ║
║              │                                                           ║
║              ├── new AUC > @champion AUC?                                ║
║              │                                                           ║
║         YES  ▼                          NO                               ║
║    set @champion alias in MLflow   set @challenger alias                 ║
║    kubectl patch deployment        nothing deployed                      ║
║    (annotation bump triggers       nothing committed                     ║
║     ArgoCD rolling update)                                               ║
║              │                                                           ║
║    ▼ (back in controller)                                                ║
║    5. Read metrics from production MLflow                                ║
║    6. If improved: git commit changed files, git push                    ║
║       If not: git checkout -- (revert, clean state)                     ║
║    7. Log to MLflow "auto-experiment"                                    ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### What CI Should Do After the Fix

```yaml
# Correct CI/CD pipeline after fix:

jobs:
  lint-and-test:
    # Run on every push and PR
    # Uses tiny synthetic dataset — no dvc pull, no training
    steps:
      - lint, format check
      - pytest (fast smoke tests)

  build-and-deploy:
    # Runs ONLY when src/ or Dockerfile changed (not on docs/config changes)
    # Never runs dvc repro. Never touches MLflow.
    steps:
      - docker buildx build --platform linux/amd64,linux/arm64
      - docker push → ghcr.io
      - Update k8s/deployment.yaml image SHA
      - git push [skip ci]
      → ArgoCD deploys new image
```

---

## 6. The Auto-Experiment Loop

### What It Does

Each iteration:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Auto-loop Controller                        │
│                                                                 │
│  1. git pull (get latest code state)                           │
│                                                                 │
│  2. Read experiment history from MLflow + history.tsv           │
│     "Tried HistGBM (improved), tried class_weight (reverted)"  │
│                                                                 │
│  3. Call Claude API with:                                       │
│     - Current params.yaml / train.py / preprocess.py           │
│     - Full experiment history                                   │
│     - "Propose ONE change to improve AUC-ROC"                  │
│                                                                 │
│  4. Claude returns JSON:                                        │
│     {                                                           │
│       "experiment_name": "charges_per_month_feature",          │
│       "rationale": "Add TotalCharges/(tenure+1)...",           │
│       "params_yaml": "... new content ...",                     │
│       "train_py": null  (not changed)                          │
│     }                                                           │
│                                                                 │
│  5. Apply proposed changes to files                            │
│  6. Run ruff --fix (lint Claude's code)                        │
│                                                                 │
│  7. ┌──────────────────────────────────┐                       │
│     │  Submit KFP pipeline run         │                       │
│     │  → KFP executes on GKE cluster   │                       │
│     │  → controller waits (polls)      │                       │
│     └──────────────────────────────────┘                       │
│                                                                 │
│  8. Read result from production MLflow                         │
│     champion_before = v2 (auc=0.816)                           │
│     champion_after  = v3 (auc=0.834) ← promoted!              │
│                                                                 │
│  9a. IMPROVED: git commit + push                               │
│       "auto-exp: charges_per_month | AUC 0.816→0.834"         │
│       → ArgoCD rolling update (pods load new champion)         │
│                                                                 │
│  9b. NOT IMPROVED:                                              │
│       git checkout -- (revert params.yaml, train.py)           │
│       nothing committed, clean state for next iteration        │
│                                                                 │
│  10. Log experiment to MLflow "auto-experiment" experiment     │
│  11. Append to history.tsv in GCS                              │
│  12. Repeat for N iterations                                    │
└─────────────────────────────────────────────────────────────────┘
```

**Why we revert code changes:** Claude's proposed change didn't improve the model. We don't want that change permanently applied — the next iteration should start from a clean codebase so Claude can try something different.

### Why a Kubernetes Job, Not a Deployment

```
Deployment: "Keep this pod running forever. Restart if it crashes."
            → For services that handle requests (churn-api, MLflow, ArgoCD)

Job:        "Run this once. Do the work. Exit when done."
            → For batch tasks with a defined end (the auto-loop: run 20 experiments, stop)

CronJob:    "Create a Job on a schedule (e.g., every night at 2am)"
            → For scheduled automation
```

The auto-loop runs N experiments and stops. That's a Job. You launch it when you want to run experiments:

```bash
kubectl apply -f k8s/auto-loop-job.yaml   # launch the loop
kubectl logs -f job/auto-loop             # watch it work
kubectl delete job auto-loop              # stop early if needed
```

For a demo: run it manually once or twice. For production: make it a CronJob to run nightly.

A Deployment doesn't make sense here because it would restart the loop every time it finishes, running experiments forever even when you don't want it to.

### Where the Files Come From in the Controller Pod

```
Controller pod starts:
  1. git clone https://x-access-token:$GIT_TOKEN@github.com/.../repo.git
     (PAT stored as K8s Secret)
  
  Working directory has:
    configs/params.yaml     ← Claude can modify this
    src/train.py            ← Claude can modify this
    src/preprocess.py       ← Claude can modify this
    pipelines/churn_pipeline.yaml  ← submitted to KFP unchanged
    auto_experiment/history.tsv    ← experiment history

  After each iteration:
    git commit (if improved) + git push
    git checkout -- . (if not improved)
```

---

## 7. The Deployment Gap — How Champion Promotion Triggers a Rollout

This is the most technically subtle part of the architecture.

### The Problem

MLflow and Kubernetes don't talk to each other directly. When `@champion` changes in MLflow (a database event), the running pods don't know. They keep serving the old model.

```
MLflow registry:
  @champion → v2 (auc=0.816)   → churn-api is serving this

Evaluate step runs, promotes v3:
  @champion → v3 (auc=0.834)   ← MLflow updated

churn-api pods?   Still serving v2.
                  They won't know until they restart.
```

### Why Not Just Poll MLflow from Inside the Pod?

Technically possible — add a background thread to `api.py` that checks if `@champion` version changed every 60 seconds and reloads. But this is architecturally wrong for GitOps:

- The pod changes its behavior without any record in Git
- You cannot `git revert` to restore the previous model
- Audit trail breaks: you don't know when the model changed or why
- ArgoCD would fight with you: it doesn't know about the reload, state is inconsistent

**Hot reload without restart is not a supported or recommended pattern in open-source MLflow serving** (GitHub issue #4039, open since 2021).

### The Correct Pattern: Annotation Bump

In Kubernetes, **any change to the pod template spec causes a rolling restart**. We exploit this by adding a metadata annotation that records the current champion version:

```yaml
# k8s/deployment.yaml
spec:
  template:
    metadata:
      annotations:
        mlflow/champion-version: "2"           # ← this changing forces a rollout
        mlflow/champion-promoted-at: "2026-04-04T10:30:00Z"
```

When this annotation changes in Git:

```
Git: annotation "mlflow/champion-version" = "3"
Cluster: annotation "mlflow/champion-version" = "2"
            │
            ▼
ArgoCD detects diff → applies change
            │
            ▼
Kubernetes: pod template changed → creates new ReplicaSet
            │
            ▼
New pods start → load "models:/churn-model@champion" → gets v3
            │
            ▼
Old pods terminate (zero-downtime rolling update)
```

This is standard in the industry. KServe does it with `storageUri` in the `InferenceService` CRD. Seldon Core does it with `modelUri` in `SeldonDeployment`. We do it manually in `deployment.yaml`.

### Who Bumps the Annotation?

The auto-loop controller, after confirming the KFP run promoted a new champion:

```python
# In auto_loop.py, after KFP run completes:
if champion_after != champion_before:
    # Update the annotation in deployment.yaml
    update_deployment_annotation(
        "k8s/deployment.yaml",
        champion_version=champion_after,
        promoted_at=datetime.utcnow().isoformat()
    )
    git_commit_and_push(
        files=["configs/params.yaml", "k8s/deployment.yaml"],
        message=f"auto-exp: v{champion_after} promoted | AUC {old:.4f}→{new:.4f}"
    )
    # ArgoCD sees k8s/deployment.yaml changed → rolling update → new pods load v{champion_after}
```

### Alternative: Direct kubectl patch (Skip Git)

For immediacy, the KFP evaluate component can trigger the restart directly:

```python
# Inside pipelines/churn_pipeline.py evaluate component:
from kubernetes import client, config
config.load_incluster_config()  # running inside K8s
apps = client.AppsV1Api()
apps.patch_namespaced_deployment(
    name="churn-api",
    namespace="churn-serving",
    body={"spec": {"template": {"metadata": {"annotations": {
        "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat()
    }}}}}
)
```

This triggers a rollout immediately, without a Git commit. It's faster (seconds vs minutes for ArgoCD sync) but less pure from a GitOps perspective — the restart isn't recorded in Git. For a portfolio demo: acceptable. For strict GitOps: use the annotation bump in Git.

**For this project: both are implemented.** KFP does the direct patch for immediate feedback. The controller also bumps the Git annotation for auditability.

---

## 8. The Two-Workload Split

```
┌─────────────────────────────────────────────────────────────────────┐
│  Workload 1: Auto-loop Controller (K8s Job)                         │
│                                                                     │
│  Purpose: Orchestrator / Brain                                      │
│  Runtime: Short-lived (runs N experiments, exits)                   │
│  Resources: Tiny (just Python, Claude API calls, git operations)    │
│  Needs:                                                             │
│    - ANTHROPIC_API_KEY (K8s Secret)                                │
│    - GitHub PAT (K8s Secret) for git clone + push                  │
│    - KFP API endpoint (env var, not a secret)                       │
│    - RBAC: patch deployments in churn-serving (for annotation bump) │
│                                                                     │
│  What it does NOT do:                                               │
│    × Train models                                                   │
│    × Preprocess data                                                │
│    × Write to MLflow directly                                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Workload 2: KFP Pipeline Pods (created by KFP on demand)           │
│                                                                     │
│  Purpose: Compute worker / Muscle                                   │
│  Runtime: Ephemeral (created per step, deleted on completion)       │
│  Resources: As needed per step (could request GPU for train step)   │
│  Needs:                                                             │
│    - GCS access (Workload Identity via kfp-sa)                     │
│    - MLflow access (HTTP to mlflow.mlflow.svc.cluster.local:5000)  │
│    - RBAC: patch deployments in churn-serving (for rollout restart) │
│                                                                     │
│  What it does NOT do:                                               │
│    × Call Claude API                                                │
│    × Read/write git                                                 │
│    × Make scheduling decisions                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Why Not One Workload?

You could put everything in one container: LLM call + training + evaluation. But:

1. **Resource mismatch**: The LLM call is I/O-bound (waiting for Claude API). Training is CPU-bound. Packing them into one pod means the compute allocation is wrong for both phases.

2. **KFP's value is isolation**: Each step (preprocess, train, evaluate) runs in its own container. If training crashes, preprocess output is preserved. If you run 100 experiments, preprocess is cached (same data). This is KFP's core value proposition — you lose it if you run everything in one container.

3. **Audibility**: KFP records every run, every step, every artifact in ML-Metadata. This gives you the "what ran when" audit trail. If the controller absorbs the training, you lose this.

4. **Scalability**: KFP can schedule the train step on a GPU node and the preprocess step on a cheap CPU node. You can't do that with a single container.

### Why Not Build the Entire Loop as One Big KFP Pipeline?

KFP pipelines are **DAGs (Directed Acyclic Graphs)**. They cannot be recursive — a pipeline cannot submit itself. KFP also doesn't natively support "run this LLM call, then based on its output decide what to do" as a DAG pattern.

The closest approximation would be a KFP pipeline with `dsl.Condition` and a fixed set of experiment configurations predefined at compile time. But the whole point of autoresearch is that Claude *adapts* based on what failed before. This is inherently sequential and stateful — not a DAG.

---

## 9. What to Fix and in What Order

### The Three Open Loops

```
Gap 1: Training writes to wrong MLflow
────────────────────────────────────────
Current: CI runs dvc repro against ephemeral SQLite → model discarded
Fix:     Remove dvc repro from CI. Training only happens in KFP.

Gap 2: Champion promotion doesn't trigger a pod restart
─────────────────────────────────────────────────────────
Current: MLflow @champion changes → pods serve old model forever
Fix:     KFP evaluate step patches deployment (direct kubectl)
         + controller bumps annotation in Git (audit trail)

Gap 3: Auto-loop trains against local MLflow, not production
────────────────────────────────────────────────────────────
Current: auto_loop.py runs dvc repro locally, logs to local/port-forward MLflow
Fix:     Controller submits KFP run, reads results from production MLflow
```

### Implementation Steps (in order)

**Step 1: Remove dvc repro from CI** — 30 minutes
Remove the "Start ephemeral MLflow" and "Run DVC pipeline" steps from `ci.yaml`. The `pipeline` job becomes: authenticate → dvc pull (for data freshness check) → docker build → push → update deployment.yaml.

**Step 2: Fix the race condition in churn_pipeline.py evaluate** — 30 minutes
The KFP evaluate component currently uses `mlflow.search_runs(order_by=["start_time DESC"])` to find the latest run. This is a race condition. Fix: pass `run_id` as a named output from the train component to evaluate, same as `run_id.txt` in the DVC pipeline.

**Step 3: Add kubectl patch to KFP evaluate component** — 2 hours
After `client.set_registered_model_alias(..., "champion", ...)`, add Kubernetes Python client call to patch the deployment. Add RBAC (Role + RoleBinding) to allow KFP's service account to patch deployments in churn-serving.

**Step 4: Move auto_loop to submit KFP runs** — 1 day
Replace `run_pipeline()` (which calls `dvc repro`) with `kfp_client.create_run_from_pipeline_package(...)`. Wait for KFP run completion by polling `kfp_client.get_run(run_id)`. Read metrics from production MLflow after run completes.

**Step 5: Package auto_loop as a K8s Job** — 1 day
Write `k8s/auto-loop-job.yaml`. Store `ANTHROPIC_API_KEY` and GitHub PAT as K8s Secrets. Add `make kfp-auto-experiment` to run it.

---

## 10. Cost and Operations

### Monthly Cost (24/7 running)

| Component | $/month | Notes |
|-----------|---------|-------|
| GKE Autopilot compute | ~$110 | ~2 vCPU + ~6 GB RAM |
| CloudSQL db-f1-micro | $7.70 | PostgreSQL, always on |
| Load Balancer IPs (4) | $20 | $5/IP — stable across restarts |
| Persistent Disks (KFP MySQL + MinIO) | $4 | 40 GB standard |
| GCS storage | $1 | Artifacts + DVC data |
| **Total** | **~$143** | |

**$300 free credits ÷ $143/month ≈ 2.1 months running 24/7.**

GCP free trial expires after **90 days** regardless of remaining credits.

### Sleep / Wake Cycle

When not demonstrating:

```bash
make cluster-sleep   # Scale all pods to 0. Cost drops to ~$25/month.
                     # CloudSQL + LB IPs + PVCs still bill.

make cluster-wake    # Scale back up. Same IPs (stable on GKE).
                     # Wait ~3 min for pods to be ready.
make gke-urls        # Print current IPs
```

Cost while sleeping: **~$25/month → $300 lasts ~12 months in sleep mode.**

---

## 11. What Is Working vs What Is Not

### Working ✅

| What | Why it works |
|------|-------------|
| Prediction API (`/predict`, `/health`) | churn-api loads @champion from MLflow at startup |
| MLflow UI + model registry | CloudSQL backend survives pod restarts |
| ArgoCD UI (GitOps) | Watches k8s/ on main, auto-syncs |
| KFP UI (pipeline visualization) | UI accessible, pipeline YAML can be uploaded |
| Multi-arch Docker images | CI builds linux/amd64+arm64 via docker buildx |
| Workload Identity (GCS + CloudSQL) | No credentials in cluster; OAuth via GKE metadata server |
| HPA on churn-api (2-10 replicas) | Scales up on CPU load |
| Auto-loop locally (`make auto-experiment`) | Works, but trains against local/port-forward MLflow |

### Broken / Incomplete ❌

| What | Why it's broken | How to fix |
|------|----------------|-----------|
| CI champion promotion | Ephemeral MLflow, throwaway DB | Remove dvc repro from CI |
| Model promotion → pod restart | No trigger from MLflow to cluster | kubectl patch in KFP evaluate |
| Auto-loop → KFP | Loop runs dvc repro, not KFP | Step 4 above |
| KFP evaluate race condition | Uses "latest run" search | Pass run_id from train component |
| Auto-loop on cluster | Local only, needs laptop running | Package as K8s Job |
| TLS / HTTPS | HTTP only (no domain) | cert-manager + domain (out of scope) |

---

## 12. Quick Reference

### Live URLs (wake cluster first with `make cluster-wake`)

| Service | URL |
|---------|-----|
| Prediction API | `http://34.180.37.1/predict` |
| MLflow UI | `http://34.180.20.197:5000` |
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
make gke-connect         # connect kubectl to GKE
make gke-status          # pod health across all namespaces
make gke-urls            # print live IPs
make cluster-sleep       # scale to 0 (save money)
make cluster-wake        # scale back up
make bootstrap           # seed @champion in MLflow (after fresh deploy)
make mlflow-kill && make mlflow  # port-forward MLflow to localhost:5000
make repro               # run DVC pipeline locally (dev/debug)
make auto-experiment-dry-run     # preview Claude's proposal without running
make auto-experiment             # run 20 experiments locally
make compile-kfp         # compile churn_pipeline.yaml
make kfp-run             # submit compiled pipeline to KFP
make test                # run 10 pytest tests
```

### Branch status

Both feature branches (`feature/auto-experiment`, `feature/gke-production`) are already merged into `main`.

```bash
# Clean up local + remote branches:
git branch -d feature/auto-experiment feature/gke-production
git push origin --delete feature/auto-experiment feature/gke-production
```
