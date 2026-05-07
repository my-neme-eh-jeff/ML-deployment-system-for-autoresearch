# How Everything Works — End-to-End Technical Guide

> Written so that someone who has never seen this project can understand
> exactly what happens when a model is trained, promoted, and deployed.
> Over-explanation is intentional. Every "but why?" should be answered.

---

## Table of Contents

1. [The One-Sentence Version](#1-the-one-sentence-version)
2. [The Players](#2-the-players--what-each-tool-does)
3. [Component Lifecycle — Every Detail](#3-component-lifecycle--every-detail) (GCS, CloudSQL, MLflow, KFP, ArgoCD, inference-api, CI/CD, auto_loop, DVC)
4. [What Is @champion?](#4-what-is-champion-the-most-important-concept)
5. [The Full Deployment Chain](#5-the-full-deployment-chain--step-by-step)
6. [What Happens When an Experiment FAILS](#6-what-happens-when-an-experiment-fails)
7. [What Is an Annotation?](#7-what-is-an-annotation-and-why-does-it-cause-a-restart)
8. [Who Does What](#8-who-does-what--the-responsibility-map)
9. [The Auto-Experiment Loop](#9-the-auto-experiment-loop--detailed-walkthrough)
10. [Two Flows, Two Triggers](#10-two-flows-two-triggers-code-vs-model)
11. [DVC vs KFP](#11-dvc-vs-kfp--why-both-exist)
12. [The Two-Workload Split](#12-the-two-workload-split-controller--kfp-pods)
13. [Cost and Sleep/Wake](#13-cost-and-sleepwake)
14. [Design Choices Worth Calling Out](#14-design-choices-worth-calling-out)
15. [Quick Reference](#15-quick-reference)
16. [Pitching & Industry Positioning](#16-pitching--industry-positioning)
17. [Demo Recording Plan](#17-demo-recording-plan)

---

## 1. The One-Sentence Version

An LLM (Claude) proposes changes to improve a binary-classification model, KFP trains and evaluates each proposal on Kubernetes, and if the model improves, the auto-loop pushes a git commit that makes ArgoCD automatically roll out new serving pods that load the improved model from MLflow.

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
inference-api          Serves predictions from the champion model "the waiter who brings you the food"
auto_loop.py       Asks Claude for ideas, runs experiments    "the scientist who designs experiments"
```

**Critical insight:** These tools do NOT communicate with each other directly.
- MLflow doesn't tell ArgoCD anything.
- ArgoCD doesn't know MLflow exists.
- KFP doesn't restart pods.
- The auto-loop controller is the one that connects them — through **git commits**.

---

## 3. Component Lifecycle — Every Detail

This section documents every component: what it is, where it runs, what goes in, what comes out, what Kubernetes resources it uses, and why we chose it over alternatives.

---

### 3.1 GCS (Google Cloud Storage)

**What it is:** Cloud object storage. Think of it as a file system in the cloud — you put files in, you get files out.

**Where it runs:** Google's infrastructure (managed, not in our cluster).

**Two separate buckets, two separate jobs:**

```text
gs://customer-churn-dvc-remote/
├── dvc-store/         ← DVC cache (content-addressed blobs of CSVs, pkl files)
│                        Written by: dvc push (from laptop or CI)
│                        Read by: dvc pull (on laptop or CI)
│
└── raw/
    └── ieee_cis.parquet ← Raw dataset uploaded manually for KFP access
                         Written by: gsutil cp (one-time)
                         Read by: KFP preprocess step

gs://churn-mlflow-artifacts-project-8018ed81/
└── 1/                 ← MLflow experiment artifacts
    ├── run_abc123/
    │   └── artifacts/model/
    │       ├── model.pkl       (19MB sklearn pipeline)
    │       ├── MLmodel         (metadata)
    │       └── conda.yaml      (environment)
    ├── run_def456/...
    └── run_ghi789/...
                         Written by: MLflow server (when train step calls log_model)
                         Read by: MLflow server (when inference-api calls load_model)
```

**Why GCS over S3/Azure/local?** We're on GCP. GKE pods access GCS natively via Workload Identity — no credentials to manage.

---

### 3.2 CloudSQL (PostgreSQL)

**What it is:** Google-managed PostgreSQL database. MLflow's "brain" — stores every experiment run, metric, parameter, model version, and alias.

**Where it runs:** Google-managed VM in `asia-south1-c`. NOT in our GKE cluster. It's a separate managed service.

**Instance details:**

```text
Instance name: churn-mlflow
Engine:        PostgreSQL 15
Tier:          db-f1-micro (shared CPU, 0.6GB RAM, cheapest tier)
Region:        asia-south1-c
Database:      mlflow_db
User:          mlflow_user
Public IP:     34.14.223.94 (not used — pods connect via Cloud SQL Proxy)
```

**What's stored in it:**

```text
Tables (managed by MLflow, created automatically):
  experiments       → {id: 1, name: "training"}
  runs              → {run_id: "abc123", experiment_id: 1, status: "FINISHED", ...}
  params            → {run_id: "abc123", key: "n_estimators", value: "100"}
  metrics           → {run_id: "abc123", key: "auc_roc", value: 0.834}
  model_versions    → {name: "classifier", version: 3, run_id: "abc123",
                        artifact_uri: "gs://churn-mlflow-artifacts/.../model"}
  registered_model_aliases → {name: "classifier", alias: "champion", version: "3"}
```

**How pods connect:** They DON'T connect directly. A Cloud SQL Auth Proxy sidecar container runs inside the MLflow pod and creates a secure tunnel:

```text
MLflow container → localhost:5432 → Cloud SQL Proxy sidecar → CloudSQL instance
                   (TCP tunnel)     (authenticates via       (actual PostgreSQL)
                                     Workload Identity)
```

**Why CloudSQL over SQLite?** SQLite is a file. When the pod restarts, the file can be lost (it was on a PVC that was zone-locked and unreliable). CloudSQL is a managed service — it survives pod crashes, node replacements, even cluster deletion. Our model registry persists no matter what.

---

### 3.3 MLflow Tracking Server

**What it is:** An HTTP server that provides a REST API for logging experiment data AND a web UI for viewing it. It does NOT train models. It does NOT serve predictions. It just stores and retrieves experiment metadata and artifacts.

**Where it runs:**

```text
Namespace:    mlflow
Deployment:   mlflow (1 replica)
Containers:   2 (mlflow server + cloud-sql-proxy sidecar)
Service:      mlflow (LoadBalancer, port 5000)
Public URL:   http://34.180.20.197:5000
K8s SA:       mlflow-sa (Workload Identity → GCP mlflow-sa → GCS + CloudSQL access)
```

**What it stores (two backends):**

```text
CloudSQL PostgreSQL (the database):
  - Experiment names and IDs
  - Run metadata (params, metrics, tags, status)
  - Model registry (version numbers, aliases like @champion)
  - Links between versions and artifact URIs

GCS bucket (the files):
  - Model artifacts (pkl, MLmodel, conda.yaml)
  - Any files logged with mlflow.log_artifact()
```

**Two separate interfaces, both go through the same server:**

```text
Interface 1: REST API (used by training pods and inference-api)
  POST /api/2.0/mlflow/runs/create           ← train step creates a run
  POST /api/2.0/mlflow/runs/log-parameter    ← train step logs params
  POST /api/2.0/mlflow/runs/log-metric       ← evaluate step logs metrics
  PUT  /api/2.0/mlflow/registered-models/alias ← evaluate step sets @champion
  GET  /api/2.0/mlflow/registered-models/alias?name=classifier&alias=champion
       ↑ inference-api calls this at startup to find the model URI

Interface 2: Web UI (used by humans in browser)
  http://34.180.20.197:5000 → shows experiments, runs, metrics, model registry
  The UI makes the same REST API calls that the pods do — it's a single-page app.
```

**The `--serve-artifacts` flag:** MLflow acts as a proxy for GCS. When a pod calls `log_model()`, the model bytes go to the MLflow server via HTTP, and the server writes them to GCS. When a pod calls `load_model()`, the server reads from GCS and sends the bytes back. Pods never need direct GCS credentials for model artifacts — only the MLflow server needs them.

**The `--disable-security-middleware` flag:** MLflow 3.x added DNS rebinding and CORS protection. On GKE, the MLflow UI (served from `34.180.20.197`) makes API calls to itself (`34.180.20.197/api/...`), which the middleware blocks as "cross-origin". This flag disables all security middleware. Safe for an internal cluster; not safe for public internet.

**Why MLflow over Weights & Biases / Neptune / ClearML?** MLflow is open-source, self-hosted, and the industry standard for experiment tracking. No vendor lock-in, no per-run pricing. We control the data.

---

### 3.4 Kubeflow Pipelines (KFP)

**What it is:** A system for defining, running, and tracking ML pipelines on Kubernetes. Each step (preprocess, train, evaluate) runs in its own container on its own pod with its own resource limits.

**KFP is the TRAINING orchestrator. It decides WHAT runs, WHERE, and in what ORDER. It does NOT serve predictions.**

**Where it runs:**

```text
Namespace:    kubeflow
Components:
  ml-pipeline           → API server (receives pipeline submissions, manages runs)
  ml-pipeline-ui        → Web UI (LoadBalancer at http://34.93.2.209)
  mysql                 → Internal database (stores pipeline definitions, run history)
  minio                 → Internal object store (stores step-to-step artifacts)
  workflow-controller   → Argo Workflows engine (creates pods for each pipeline step)
  metadata-grpc         → ML Metadata store (lineage tracking)
  + several supporting services (persistence agent, scheduled workflow, cache server)
```

**How a pipeline run works (exact lifecycle):**

```text
1. You (or auto_loop.py) call:
   kfp_client.create_run_from_pipeline_package("pipeline.yaml", args={...})
         │
         │  HTTP POST to ml-pipeline API server
         ▼
2. ml-pipeline stores the run in MySQL and creates an Argo Workflow resource
         │
         ▼
3. workflow-controller (Argo) reads the Workflow and creates pods for each step:
   
   For EACH step in the pipeline:
   ┌────────────────────────────────────────────────────────────────────┐
   │  Pod: classifier-training-pipeline-xxx-system-container-impl-yyy │
   │  Namespace: kubeflow                                              │
   │  Image: ghcr.io/my-neme-eh-jeff/pipeline-kfp:latest                │
   │  Service Account: pipeline-runner (Workload Identity → GCS)      │
   │                                                                    │
   │  Containers:                                                       │
   │    init: argoexec (quay.io/argoproj/argoexec:v3.4.17)            │
   │      → Sets up artifact passing between steps                     │
   │    main: the actual Python function from @dsl.component           │
   │      → Runs: preprocess() or train() or evaluate()               │
   │                                                                    │
   │  Resources (explicitly set to fit on 2-node cluster):             │
   │    CPU: 200m-300m request, 500m-1000m limit                       │
   │    Memory: 512Mi request, 1-2Gi limit                             │
   │                                                                    │
   │  Artifact I/O:                                                     │
   │    Inputs: downloaded from MinIO (KFP's internal artifact store)  │
   │    Outputs: uploaded to MinIO after step completes                │
   └────────────────────────────────────────────────────────────────────┘

4. Steps execute sequentially (preprocess → train → evaluate):

   PREPROCESS POD:
     Input:  raw_data_gcs_path = "gs://customer-churn-dvc-remote/raw/ieee_cis.parquet"
             (reads directly from GCS using gcsfs library)
     Output: train_csv (KFP Dataset artifact → stored in MinIO)
             test_csv  (KFP Dataset artifact → stored in MinIO)
             stats     (KFP Artifact → stored in MinIO)

   TRAIN POD:
     Input:  train_csv (downloaded from MinIO, written by preprocess)
     Does:   Fits sklearn pipeline
             Calls mlflow.sklearn.log_model() → sends model to MLflow → MLflow stores in GCS
             Calls mlflow.log_params() → stores in CloudSQL via MLflow
     Output: model_artifact (KFP Model artifact → stored in MinIO)
             (ALSO: model stored in GCS via MLflow, registered in CloudSQL as new version)

   EVALUATE POD:
     Input:  test_csv (from MinIO), model_artifact (from MinIO)
     Does:   Loads model from the KFP artifact (not from MLflow)
             Computes AUC-ROC, accuracy, F1
             Calls mlflow.log_metrics() → stores in CloudSQL via MLflow
             Compares AUC vs current @champion
             If better: mlflow.set_registered_model_alias("champion", new_version)
     Output: metrics (KFP Artifact → stored in MinIO)

5. ml-pipeline marks the run as SUCCEEDED or FAILED
```

**Two artifact stores running simultaneously (this is confusing but correct):**

```text
MinIO (KFP's internal artifact store):
  - Stores KFP step-to-step artifacts (train_csv, test_csv, model_artifact)
  - Used ONLY by KFP for passing data between pipeline steps
  - Lives at minio.kubeflow.svc.cluster.local:9000
  - Data here is ephemeral — cleared when runs are deleted

GCS via MLflow (production artifact store):
  - Stores the PRODUCTION model artifacts (model.pkl, MLmodel, conda.yaml)
  - Written by train step via mlflow.log_model()
  - Read by inference-api via mlflow.load_model()
  - Persists forever in gs://churn-mlflow-artifacts-...
```

**The base image (`ghcr.io/my-neme-eh-jeff/pipeline-kfp:latest`):**
Pipeline steps run inside this image. It has pandas, sklearn, mlflow, gcsfs, and kfp pre-installed. We built a custom image because GKE Autopilot limits ephemeral storage to 1Gi — runtime `pip install` of these packages exceeds that limit and causes pod eviction.

**Why KFP over Airflow / Prefect / Dagster?** KFP is Kubernetes-native (each step is a pod), part of the Kubeflow ecosystem (alongside KServe, Katib), and has a built-in pipeline visualization UI. Airflow is more general-purpose (ETL, data engineering); KFP is specifically designed for ML pipelines.

---

### 3.5 ArgoCD

**What it is:** A GitOps controller. It continuously compares the Kubernetes cluster state against a Git repository and makes the cluster match Git.

**Where it runs:**

```text
Namespace:    argocd
Components:
  argocd-server                  → UI + API (LoadBalancer at http://34.100.246.237)
  argocd-application-controller  → The reconciler (compares git vs cluster every 3 min)
  argocd-repo-server             → Clones git repos, renders manifests
  argocd-redis                   → Cache for application state
  argocd-dex-server              → Authentication (SSO, not used by us)
  argocd-notifications           → Notification delivery (not configured)
  argocd-applicationset          → Multi-cluster management (not used)

Application definition: argocd/application.yaml
  source:
    repoURL: https://github.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch.git
    targetRevision: main
    path: k8s                ← ArgoCD watches EVERY yaml file in this directory
  destination:
    server: https://kubernetes.default.svc
    namespace: inference  ← resources get applied here
  syncPolicy:
    automated:
      prune: true            ← deletes resources removed from git
      selfHeal: true         ← reverts manual kubectl changes to match git
```

**The sync loop (what ArgoCD actually does):**

```text
Every ~3 minutes (or on webhook):
  1. Clone https://github.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch.git
  2. Read all YAML files in k8s/ directory
  3. Compare each resource against the live cluster state
  4. If different → kubectl apply the git version
  5. If same → do nothing

Example:
  Git says:   k8s/deployment.yaml → annotation "mlflow/champion-version: 3"
  Cluster has: deployment.yaml → annotation "mlflow/champion-version: 2"
  ArgoCD:     DIFFERENT → applies git version → Kubernetes creates new pods
```

**What ArgoCD manages (the k8s/ directory):**

```text
k8s/
├── deployment.yaml          → inference-api Deployment (image, probes, resources, annotations)
├── service.yaml             → inference-api LoadBalancer Service (port 80 → 8000)
├── namespace.yaml           → inference namespace
├── mlflow.yaml              → MLflow Deployment + Service + Namespace (all-in-one)
├── serviceaccounts.yaml     → K8s SAs with Workload Identity annotations
├── hpa.yaml                 → HorizontalPodAutoscaler for inference-api (2-10 replicas)
├── pdb.yaml                 → PodDisruptionBudgets for inference-api and mlflow
└── kfp-expose.yaml          → Patches KFP UI to LoadBalancer
```

**Why ArgoCD over Flux / Helm / raw kubectl?** ArgoCD has the best UI (shows sync status, diff view, resource tree). Flux is equally good for GitOps but has no built-in UI. Raw kubectl doesn't track desired state — you can't tell if the cluster has drifted from what you intended.

---

### 3.6 inference-api (Inference Server)

**What it is:** A FastAPI web server that loads the champion model from MLflow at startup and serves prediction requests via HTTP.

**Where it runs:**

```text
Namespace:    inference
Deployment:   inference-api (2 replicas, HPA scales 2-10)
Image:        ghcr.io/my-neme-eh-jeff/inference-api:latest (multi-arch amd64+arm64)
Service:      inference-api (LoadBalancer, port 80 → container port 8000)
Public URL:   http://34.47.242.89
K8s SA:       inference-api-sa (no GCS access needed — goes through MLflow)
```

**Lifecycle of a pod (from creation to serving):**

```text
1. Kubernetes creates the pod (triggered by ArgoCD sync or scaling event)
2. Container starts: /app/.venv/bin/uvicorn src.api:app --host 0.0.0.0 --port 8000
3. Uvicorn binds to port 8000 IMMEDIATELY (< 2 seconds)
4. FastAPI startup event fires → spawns background thread:
     threading.Thread(target=_load_model_in_background).start()
5. Background thread calls:
     mlflow.sklearn.load_model("models:/classifier@champion")
     → HTTP to MLflow server → resolves @champion → downloads model.pkl from GCS
     → deserializes into Python object (global variable `model`)
     This takes 30-60 seconds.
6. WHILE model is loading:
     /health/live → 200 (liveness probe passes → pod is not killed)
     /health      → 503 (readiness probe fails → pod receives no traffic)
     /predict     → 503 (model not ready)
7. Model finishes loading:
     /health      → 200 (readiness probe passes → pod receives traffic)
     /predict     → works (uses in-memory model object, ~1ms per prediction)
```

**Probes (how Kubernetes knows the pod is healthy):**

```text
Liveness probe:    GET /health/live every 15s, initial delay 30s
  Returns: 200 ALWAYS (if uvicorn is running, the process is alive)
  Purpose: Restarts the pod ONLY if the process crashes entirely
  Does NOT kill the pod during model loading (unlike the old design)

Readiness probe:   GET /health every 10s, initial delay 10s
  Returns: 200 if model loaded, 503 if still loading
  Purpose: Removes pod from service endpoints until model is ready
  Prevents traffic going to a pod that can't serve predictions yet
```

**Endpoints:**

```text
GET  /health/live  → {"status": "alive"}                     (always 200)
GET  /health       → {"status": "healthy", "model_loaded": true}  (200 or 503)
POST /predict      → {"prediction": 1, "probability": 0.71, "model_version": "N"}
     Body: JSON with feature columns matching params.yaml schema
```

**Why FastAPI over Flask / KServe / Seldon?** FastAPI has async support, automatic OpenAPI docs, and Pydantic validation. KServe/Seldon are ML-specific serving frameworks that add auto-scaling, canary deployments, and multi-model serving — but they require Istio/Knative (heavy dependencies that won't fit on our 2-node cluster). For a single sklearn model on CPU, FastAPI is the right level of complexity. In interviews: "I used FastAPI to understand serving internals; for production multi-model serving I'd evaluate KServe."

---

### 3.7 GitHub Actions (CI/CD)

**What it is:** Cloud-hosted workflow runner triggered by git push events.

**Where it runs:** GitHub's infrastructure (Ubuntu runners, NOT in our GKE cluster).

**Three jobs, triggered on every push to main:**

```text
Job 1: lint-and-test (runs on every push + PR)
  ├── ruff check (linting)
  ├── ruff format --check (formatting)
  └── pytest tests/ -v (10 tests with tiny synthetic data)
  Duration: ~35 seconds

Job 2: pipeline (runs only on main branch pushes, after lint passes)
  ├── Authenticate to GCP (Workload Identity Federation → github-cicd SA)
  ├── dvc pull (download data from GCS)
  ├── Start ephemeral MLflow (sqlite:///mlflow_ci.db — THROWAWAY)
  ├── dvc repro (run pipeline against ephemeral MLflow)
  ├── dvc push (upload artifacts to GCS)
  ├── docker buildx build --platform linux/amd64,linux/arm64 → inference-api
  ├── docker buildx build --platform linux/amd64,linux/arm64 → pipeline-kfp
  ├── Push both images to ghcr.io
  └── Update k8s/deployment.yaml image SHA → git commit [skip ci] → push
  Duration: ~14 minutes (docker buildx is slow for multi-arch)

Job 3: compile-kfp (runs on every push after lint passes)
  ├── python pipelines/pipeline.py (compiles to YAML)
  └── Upload pipeline.yaml as GitHub Actions artifact
  Duration: ~15 seconds
```

**Known architectural gap:** The `dvc repro` in Job 2 trains against an ephemeral MLflow that is deleted when the job ends. The champion promotion goes to a throwaway database. This is documented in the README as a known limitation. The fix (not yet implemented): remove `dvc repro` from CI entirely — CI should only lint, test, and build images. Training should happen via KFP.

---

### 3.8 auto_loop.py (Auto-Experiment Controller)

**What it is:** A Python script that orchestrates autonomous model improvement by calling Claude API, submitting KFP pipeline runs, and committing improvements to git.

**Where it runs:** Currently on your laptop. Target: Kubernetes Job in the cluster.

**Inputs:**

```text
- configs/params.yaml     (current hyperparameters)
- src/train.py            (current model code)
- src/preprocess.py       (current preprocessing code)
- auto_experiment/program.md  (research directions for Claude)
- auto_experiment/history.tsv (what was tried, what worked)
- MLflow API              (current @champion version and AUC)
- ANTHROPIC_API_KEY       (from .env file)
```

**Outputs (on successful experiment):**

```text
- Modified configs/params.yaml (the improvement that worked)
- Modified k8s/deployment.yaml (annotation bump for deployment)
- git commit on main branch
- New MLflow run in "auto-experiment" experiment
- New row in auto_experiment/history.tsv
```

**Outputs (on failed experiment):**

```text
- git checkout -- . (all changes reverted, clean state)
- New MLflow run in "auto-experiment" experiment (outcome: "reverted")
- New row in auto_experiment/history.tsv (outcome: "reverted")
- NOTHING committed, NOTHING deployed
```

**Why Claude over GPT-4 / Gemini / local LLMs?** Claude's API has structured JSON output, long context windows (for sending full source files), and strong code generation. The choice is pragmatic — any LLM with JSON output support would work. The `--model` flag in auto_loop.py allows switching models.

---

### 3.9 DVC (Data Version Control)

**What it is:** A CLI tool that versions large files (CSVs, model pickles) by storing them in GCS and keeping only tiny pointer files in git.

**Where it runs:** Your laptop and CI runners (NOT in the cluster).

**What it tracks:**

```text
Git repository:                          GCS bucket:
  data/ieee_cis.parquet.dvc  ──────────→  gs://customer-churn-dvc-remote/dvc-store/ab/cd1234...
  (12 bytes, hash pointer)               (binary parquet artifact)
  
  dvc.lock                 ──────────→  Hashes of ALL pipeline stage inputs/outputs
  (records exact state)                  If any input changes, the stage re-runs
```

**The pipeline (dvc.yaml):**

```text
stages:
  preprocess:
    cmd: uv run python src/preprocess.py
    deps: [data/ieee_cis.parquet, src/preprocess.py, configs/params.yaml]
    outs: [data/processed/train.csv, data/processed/test.csv, data/processed/stats.json]

  train:
    cmd: uv run python src/train.py
    deps: [data/processed/train.csv, src/train.py, configs/params.yaml]
    outs: [models/classifier.pkl, models/run_id.txt]

  evaluate:
    cmd: uv run python src/evaluate.py
    deps: [data/processed/test.csv, models/classifier.pkl, src/evaluate.py, models/run_id.txt]
    metrics: [metrics.json]
```

**DVC's role vs KFP's role:**

```text
DVC  = local development pipeline runner + data versioning
KFP  = production pipeline runner on Kubernetes

Same logical pipeline, different execution environments.
DVC does NOT run in production. KFP does.
DVC is ALSO used for data versioning (dvc push/pull) which KFP does not do.
```

**Why DVC over raw GCS / git-lfs / lakefs?** DVC integrates with git natively (pointer files are committed alongside code). Hash-based caching means `dvc repro` skips unchanged stages. The `.dvc` files make data reproducibility a git operation: `git checkout v1.0 && dvc pull` gives you the exact data from that version.

---

## 4. What Is @champion? (The Most Important Concept)

### It's a pointer, like a git branch

In git, `main` is a branch name that points to a specific commit. You can move it to point to a different commit. The old commit still exists — you just changed where the name points.

MLflow's `@champion` works exactly the same way:

```
Git:                                 MLflow:
────                                 ──────
main → commit abc123                 @champion → model version 2
                                     @challenger → model version 3

You run: git checkout -B main def456 You run: client.set_registered_model_alias(
                                               "classifier", "champion", "3")

Now:                                 Now:
main → commit def456                 @champion → model version 3
(abc123 still exists)                (version 2 still exists)
```

### Where @champion lives

It's a row in MLflow's PostgreSQL database (CloudSQL). Not a file, not a Kubernetes object, not a git tag. Just a database entry that says: *"the alias 'champion' for model 'classifier' currently points to version 2."*

### What loads @champion

The inference-api pods, at startup, run:
```python
model = mlflow.sklearn.load_model("models:/classifier@champion")
```

This call goes to the MLflow server, which resolves `@champion` to version 2 (or whatever it currently points to), downloads that model's pickle file from GCS, and deserializes it into memory.

### The critical gap

**Moving the @champion pointer does NOT restart pods.** If @champion moves from v2 to v3 but the pods don't restart, they keep serving v2. The pods loaded v2 at startup and have it in memory. They don't poll MLflow for changes.

This gap is what the annotation-bump mechanism closes (explained in Section 6).

---

## 5. The Full Deployment Chain — Step by Step

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
    → Registers model as "classifier" version 3
    → Outputs: run_id (passed to evaluate step)

  Pod 3: evaluate
    → Reads test.csv + model artifact
    → Computes AUC-ROC = 0.834
    → Reads current @champion from MLflow → version 2, AUC = 0.816
    → 0.834 > 0.816?  ──── YES ────────────────────────┐
    │                                                    │
    │  Calls MLflow API:                                 │
    │  client.set_registered_model_alias(                │
    │    "classifier", "champion", "3")                 │
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
  New pods start → call mlflow.sklearn.load_model("models:/classifier@champion")
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

## 6. What Happens When an Experiment FAILS

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

## 7. What Is an Annotation and Why Does It Cause a Restart?

### Annotations in plain English

Every Kubernetes object has metadata. Annotations are key-value pairs in that metadata — like sticky notes you can attach to any resource:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: inference-api
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

## 8. Who Does What — The Responsibility Map

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
│  inference-api             "The waiter"                                     │
│  ─────────                                                              │
│  Loads @champion model from MLflow at startup.                          │
│  Serves predictions via /predict endpoint.                              │
│  Does NOT know about experiments, Git, or ArgoCD.                       │
│  Only knows: "load @champion, serve requests."                          │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 9. The Auto-Experiment Loop — Detailed Walkthrough

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

## 10. Two Flows, Two Triggers (Code vs Model)

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

## 11. DVC vs KFP — Why Both Exist

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

## 12. The Two-Workload Split (Controller + KFP Pods)

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

## 13. Cost and Sleep/Wake

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

## 14. Design Choices Worth Calling Out

Three engineering decisions that shape how the system behaves under autoresearch load. These are deliberate, not accidents.

### 14.1 Annotation-driven rollout, not image-tag-only

The container image is published with the commit SHA tag and `:latest`; pods use `imagePullPolicy: Always`. The thing that *triggers* a rollout is **not** the image change — it's the bump of `mlops/classifier-version` and `mlops/classifier-run-id` annotations on `spec.template.metadata` of the inference Deployment. Mutating annotations on the pod template changes the PodSpec hash, which is what Kubernetes uses to decide "is this a new ReplicaSet?" → rolling restart fires.

**Why not just bump the image SHA?** Two reasons.
1. **Audit record in git.** The annotation in `k8s/deployment.yaml` records *which trained model is live in production* — readable in `git log` without querying MLflow.
2. **Decouples CI from the rollout chain.** ArgoCD reconciles purely on the manifest in git. No coupling between the CI pipeline's success and the model rollout. A successful autoresearch PR can roll the deployment even if the post-merge CI fails to bump the SHA.

### 14.2 In-cluster autoresearch with Workload Identity

The autoresearch loop runs as a Kubernetes Job inside the cluster (`jobs/autoresearch-job.yaml`), not from a laptop. The Job's ServiceAccount (`autoresearch-sa`) binds to a GCP service account via Workload Identity, granting:

- `secretmanager.secretAccessor` on the GitHub App PEM secret
- `storage.objectAdmin` on the DVC remote bucket
- `aiplatform.user` for any future Vertex AI calls (currently unused)

**Why this matters.** No long-lived credentials live on a developer laptop. The Anthropic API key is in a K8s Secret in the `inference` namespace. The GitHub App PEM never touches disk — it's fetched from Secret Manager at job start. A production run survives the dev machine being asleep.

### 14.3 Run-id linking, not "latest run" search

`train.py` writes `models/run_id.txt` after `mlflow.start_run()`. `evaluate.py` reads that file and logs metrics into the *exact* run train.py created. There's no "find the most recent run" search.

**Why this matters under autoresearch.** When the loop is running, multiple pipeline runs can complete in close succession. A "search for latest" pattern can attribute evaluation metrics to the wrong run. The run-id link makes the train→evaluate→register sequence ironclad.

---

## 15. Quick Reference

### Live URLs (wake cluster first with `make cluster-wake`)

| Service | URL |
|---------|-----|
| Prediction API | `http://34.47.242.89/predict` |
| MLflow UI | `http://34.180.20.197:5000` (click "Model training" tab, not "GenAI") |
| ArgoCD UI | `http://34.100.246.237` (admin / `TMwwd4OpkcL6fPRy`) |
| KFP UI | `http://34.93.2.209` |

### Test the prediction API

```bash
curl -X POST http://34.47.242.89/predict \
  -H "Content-Type: application/json" \
  -d '{"data": { ...feature columns matching params.yaml schema... }}'
# → {"prediction": 1, "probability": 0.71, "model_version": "N"}
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

---

## 16. Pitching & Industry Positioning

This section is for *you* (the owner) — how to talk about this project so it lands.

### 16.1 What this project actually is

Strip away the buzzwords and one sentence captures it:

> An LLM is a contributor to this codebase. It proposes diffs. The cluster runs them. Only the winners ship.

That framing — *Claude as a teammate, not a tool* — is the hook. Everything else (KFP, MLflow, ArgoCD, GitHub App, signed PRs) is the *infrastructure* that makes it production-safe.

### 16.2 Don't pitch as

- "I built a churn predictor." → Generic, model is intentionally simple.
- "I built MLOps infrastructure." → Commodity in 2026, every platform engineer claims this.
- "I built a learning project." → Apologetic; undercuts everything that follows.
- "I built an autoML tool." → Wrong category. AutoML is hyperparameter search. This is *agentic engineering*.

### 16.3 Do pitch as

**Agentic AI engineering.** The system is in the same category as Cursor, Claude Code, Devin, Cognition's agent-driven coding tools, and the rapidly emerging "AI as a teammate" pattern. The differentiator: most agentic coding tools edit code on a developer's laptop. This one edits code, *trains the result on Kubernetes*, and ships only the winners to a live serving deployment — with a signed-PR audit trail.

**LLMOps + GitOps fusion.** Two production patterns most platforms keep separate:
- *LLMOps* = operating LLMs as runtime components (this project: Claude is a runtime decision-maker).
- *GitOps* = declarative infrastructure where git is the source of truth (this project: ArgoCD reconciles annotations).

Each is in demand alone. The intersection is rare.

**Compound AI system** (Berkeley AI Research's term for multi-component AI systems). Five participants — Claude, KFP, MLflow, GitHub, ArgoCD — none coordinating directly, each doing one thing. Reconciliation through git, not through a central orchestrator. Read [BAIR's Compound AI Systems](https://bair.berkeley.edu/blog/2024/02/18/compound-ai-systems/) post; this project is one.

### 16.4 2026 industry trends to lean into

| Trend | This project's angle |
|---|---|
| **Agentic AI / AI agents** | Claude proposes structured diffs via tool-use; cluster executes; loop continues |
| **AI as a teammate** (Cursor/Claude Code/Devin influence) | Each successful iter is a signed PR — Claude is a member of the dev team |
| **LLMOps** (operating LLMs in production) | Token costs logged per iter, model-version observability via `/predict` |
| **GitOps** (declarative infra, ArgoCD/FluxCD) | The only way prod state changes is by merging a PR |
| **Compound AI systems** | Five components, decoupled, audit trail |
| **Tool-use / structured outputs** | Anthropic `tool_use` with strict JSON schema, no parsing fragility |
| **AutoML evolution** | Not random search over hyperparameters; LLM uses code reading + history to propose informed mutations |

### 16.5 Three pitch templates

**LinkedIn / portfolio one-liner (2-3 sentences):**

> Built an autonomous AI engineer that improves ML models and ships them to production end-to-end. Claude proposes a code change via tool-use → KFP trains on Kubernetes → if AUC beats champion by ≥ threshold, a signed PR opens → CI validates → auto-merge → ArgoCD rolls inference pods. Zero human in the loop; every model live in prod is traceable to a merged PR with the LLM's reasoning.

**Interview answer ("walk me through a recent project"):**

> I wanted to see what happens when you let an LLM be a contributor to a codebase, not just an autocomplete. So I built a system where Claude proposes diffs to a binary-classification ML pipeline. The diffs train on a Kubeflow Pipeline in a real GKE cluster, get evaluated against the current `@champion` in MLflow, and if they win by a threshold, a GitHub App opens a PR via the GraphQL `createCommitOnBranch` API with auto-merge enabled. Once CI passes, the PR squash-merges to main. ArgoCD picks up the manifest change, rolls the deployment, and new pods load the new champion. The interesting parts aren't the model — the model is intentionally trivial. The interesting parts are the agentic loop, the per-iter PR audit trail, and the GitOps reconciliation chain that makes the whole thing safe.

**Cover letter / Twitter ("why should I care"):**

> What if the AI is a teammate, not a tool? An LLM proposes diffs to my ML codebase. The cluster runs them. Only the winners ship. My model improved 30× while I slept — and every change is a signed PR I can read in the morning.

### 16.6 Role-specific framing

- **MLOps Engineer / ML Platform Engineer.** Lead with: GKE Autopilot, Kubeflow Pipelines, MLflow registry with `@champion`/`@challenger` aliases, ArgoCD GitOps reconciliation, multi-arch ghcr.io image, Workload Identity, signed CI commits via GitHub App.
- **LLMOps / AI Engineer.** Lead with: Anthropic tool-use schema, per-iter token logging to MLflow, cost-per-improvement tracking, autonomous loop with early-stop, structured-output reliability under long context.
- **Data Engineer.** Lead with: schema-in-params plug-and-play (any binary-classification CSV), DVC + GCS data versioning, parquet-aware preprocessing, KFP DAG with metadata lineage.
- **Senior / Staff role.** Lead with: the *decision* to use annotation-driven rollout instead of image-tag-only, the *decision* to use a per-iter PR pattern instead of direct push, the *decision* to use Workload Identity instead of mounted secrets. These are the trade-offs senior engineers want to hear about.

### 16.7 Things to *say*, not just have

A portfolio piece is a conversation starter. The conversation goes well when you have the *one-line takeaway* per concept ready:

| Concept | Your one-liner |
|---|---|
| Why annotation, not image SHA | "I want the audit trail in `git log`, not in MLflow's UI." |
| Why a GitHub App, not a PAT | "Signed commits + revocable install token + per-repo scope. PATs are a 2018 pattern." |
| Why ArgoCD, not Helm + kubectl apply | "I want git to be the single source of truth for what's running. Helm is a templating language; it doesn't reconcile." |
| Why MLflow `@champion` alias, not stages | "Aliases are pointers — moveable, atomic. Stages were renamed-and-deprecated in MLflow 3." |
| Why DVC and KFP both exist | "DVC for local fast-iteration; KFP for Kubernetes-native training. Same DAG, different runners." |
| Why CloudSQL, not SQLite-on-PVC | "PVCs are zone-locked on Autopilot; CloudSQL survives node moves. Lost the registry twice on SQLite before swapping." |

If someone asks "why X?", you answer in one sentence. That's the difference between *built it* and *understand it*.

---

## 17. Demo Recording Plan

A 3-4 minute recorded walkthrough is the single highest-leverage artifact this project produces. A recruiter clicks the GitHub repo, sees the README, and may or may not stay; a *video* converts a skim into a watch. This section is the recording playbook.

### 17.1 The 3-4 minute narrative arc

**Length budget: 210 seconds total. Anything longer loses recruiter attention; anything shorter doesn't earn trust.**

| Beat | Time | What's on screen | Voiceover idea |
|---|---|---|---|
| **1. Hook** | 0–15s | The final AUC trajectory plot (will exist after the 20-iter run lands). Caption: "Claude moved AUC 0.749 → 0.93 in 30 minutes, autonomously." | "What happens when you let an LLM ship code to production?" |
| **2. Before** | 15–45s | Terminal: `curl /health` → `{"model_version": "1"}`. Side panel: MLflow registry showing only v1, AUC 0.749. | "Vanilla decision tree. Two features. Recall under 9%. The starting line." |
| **3. Live kick-off** | 45s–1:30 | Terminal: `make autoresearch-run AUTORESEARCH_N=20`. Side panel: KFP UI showing the first pipeline run going green. Side panel: GitHub PRs tab showing the first PR open. | "One command. Claude proposes a diff via tool-use. KFP trains it. MLflow checks if it beat the champion. If yes — GitHub App opens a signed PR. Auto-merge. ArgoCD picks it up. New pods load the new model." |
| **4. Time-lapse** | 1:30–2:30 | Multi-pane, sped up 4–8×. Pane 1 (left, big): autoresearch logs streaming. Pane 2 (top-right): MLflow registry, versions appearing. Pane 3 (bottom-right): GitHub PRs page, merged trail growing. Caption per kept iter with AUC delta. | "Each iter is a hypothesis. Each merged PR is an experiment that won. Each rolled deployment is the model getting better." |
| **5. The audit** | 2:30–3:00 | Switch to GitHub PRs (state=closed). Click into one PR. Show the diff Claude wrote. Show the signed-commit checkmark. Show the auto-merge timestamp. | "This isn't just a script. Every change is a signed PR with the LLM's reasoning. I can read every decision the system made overnight." |
| **6. Wrap** | 3:00–3:30 | Final trajectory plot. Caption: "AUC 0.749 → 0.93. 20 iters. ~$2.50 in tokens. No human in the loop." | "The AI is a teammate. The cluster is the environment. Git is the source of truth." |

### 17.2 Tab / window layout

Open these in advance and arrange them so each one is a single command/keystroke away:

**Browser (Chrome with one window, multiple tabs):**
1. `http://34.180.20.197:5000` — MLflow UI, click "Models" → `classifier`
2. `http://34.93.2.209` — KFP UI, click "Runs"
3. `http://34.100.246.237` — ArgoCD UI, log in, open `inference-api` app
4. `https://github.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch/pulls?state=closed` — merged PR trail
5. `https://github.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch/actions` — CI runs (only show on the audit beat, briefly)

**Terminal (one big window):**
- Pre-stage: `make autoresearch-logs` ready in clipboard
- Pre-stage: a loop that polls `/health` and prints the model_version field, e.g.:
  ```bash
  while true; do
    curl -s http://34.47.242.89/health | jq -r '"\(now | strftime("%H:%M:%S")) — model_version=\(.model_version)"'
    sleep 5
  done
  ```

**Code editor (optional, only for the "what Claude wrote" close-up in beat 5):**
- Open one merged PR in your editor's diff view, or use GitHub's web diff.

### 17.3 Tooling — pick one of three

**Option A: Sequential recordings + iMovie composite (recommended).**
- Record each window/source separately with QuickTime (`Cmd + Shift + 5` → Record Selected Portion).
- Each recording: 60–90s, focused on one element.
- Composite in iMovie (free) or DaVinci Resolve (free): main source full-screen, secondary source as picture-in-picture. Speed up the time-lapse parts 4–8×. Voiceover separately.
- **Why this is the recommended path:** Each clip is independent; if you flub one, you re-record only that clip. No live coordination overhead.

**Option B: OBS Studio (free, multi-source live composite).**
- `brew install --cask obs`. Set up scenes with multiple sources (Display Capture for screen, Window Capture for specific browser tabs).
- Record once, all panes simultaneously, get a finished composite out.
- **Why you'd pick this:** Less editing. **Why you might not:** First-time setup is ~30 min; you have one shot per take.

**Option C: ScreenFlow ($169 Mac).**
- Records each source as a separate track simultaneously, then you compose in its timeline editor. Best of both worlds. Worth it only if you'll do this often.

### 17.4 Pre-flight checklist

Run all of these *before* hitting record. T-15 minutes:

```bash
# Cluster + state
make cluster-wake               # if cluster is asleep
make gke-status                 # all pods Running
make mlflow-kill && make mlflow # port-forward in a side terminal
make reset-for-fresh-run        # clean v1 baseline (vanilla DT)
curl http://34.47.242.89/health # confirm: model_version: "1"

# Git + GitHub
gh pr list --state open         # should be empty (no stale auto-PRs)
gh run list --limit 3           # no in-flight CI on main

# Anthropic
kubectl get secret anthropic -n inference  # exists; if not: make autoresearch-secret

# Browser tabs (open all 5 from §17.2)
# Terminal panes (logs ready, /health-poll loop ready)
# Voice: water nearby, do one rehearsal pass
```

### 17.5 During-recording principles

- **Lead with the outcome.** The trajectory plot is the most powerful single image. Open with it, return to it at the end.
- **Show the audit trail.** The merged PRs are the part that lands "this isn't just a script." Spend 30s on this beat.
- **Speed up wait time honestly.** Time-lapse with a visible timestamp overlay, e.g. "+12:34 elapsed". Don't fake it; viewers can tell.
- **Don't read the README.** Viewers can read. Voiceover should add context the screen doesn't.
- **Don't show error states.** If an iter fails during the take, cut around it in post. The narrative is "the system works"; failures muddy that.
- **One sentence per beat.** Resist the urge to over-explain. The architecture diagram in the README does the deep explaining.

### 17.6 Post-production

- **Speed**: 4× the time-lapse beat (4) — viewers see ~15 minutes of cluster activity in ~60 seconds.
- **Captions**: one caption per beat, large font, contrasting color. AUC deltas in the time-lapse.
- **Audio**: voiceover recorded *separately* in a quiet room, layered in iMovie. Live narration always sounds nervous and breathy.
- **Outro**: a still frame of the final trajectory + GitHub repo URL + "Read more in the README" call-to-action.

### 17.7 The 20-second teaser GIF (for README + LinkedIn)

A standalone 20-second GIF, embedded at the very top of the README, beats any prose intro:

- 0–5s: Trajectory plot drawing the AUC climb.
- 5–10s: GitHub PRs page filling up with merged PRs (sped up 8×).
- 10–15s: ArgoCD app showing rolling restart events.
- 15–20s: Final number — "AUC 0.749 → 0.93, no human in the loop."

Export from iMovie/DaVinci as MP4 → convert to GIF via `gifski` or `ffmpeg`. Keep file size under 5 MB so GitHub renders it inline.

### 17.8 What to do *if* something fails on screen

The autoresearch loop is robust to single-iter failures (timeouts, Claude errors, sub-threshold reverts) — it logs them to history.tsv and continues. **Don't react on camera.** Let the next iter succeed; the time-lapse smooths over the bumps.

The only thing worth pausing recording for: the *cluster* itself going unresponsive (CloudSQL connection drop, ArgoCD reconcile stall > 5 min, KFP scheduler error). If that happens, stop, fix off-camera, restart the take.

---
