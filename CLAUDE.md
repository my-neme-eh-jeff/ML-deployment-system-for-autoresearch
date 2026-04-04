# Customer Churn MLOps Project

## Project overview

End-to-end MLOps project for customer churn prediction. The ML model is intentionally simple (RandomForest) — the real focus is the infrastructure: DVC pipelines, MLflow experiment tracking + model registry, Kubeflow Pipelines, ArgoCD GitOps, and CI/CD.

**Owner:** Aman — targeting MLOps Engineer and Data Engineer roles. This project is a portfolio/learning piece.

**Collaboration style:** Do NOT just build things silently. Aman wants to learn each concept deeply — explain the "why," share industry examples, ask questions to check understanding, and share blog posts. Teach end-to-end before and after implementing. Think of it as pair programming with a teaching component.

## Tech stack

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12 | Runtime |
| uv | latest | Package manager (**never use pip**) |
| DVC | 3.67+ | Data/model versioning + pipeline orchestration (local) |
| DVC-GS | 3.0+ | GCS backend for DVC |
| MLflow | 3.10+ | Experiment tracking + model registry (champion/challenger) |
| Kubeflow Pipelines (kfp) | 2.16+ | K8s-native pipeline orchestration |
| scikit-learn | 1.8+ | Model training (RandomForest + sklearn Pipeline) |
| FastAPI | 0.135+ | Inference API server |
| vind (vcluster) | 0.31+ | Local Kubernetes cluster (**never use kind**) |
| ArgoCD | v3.3.6 | GitOps deployment (watches k8s/ directory) |
| ruff | 0.15+ | Linting + formatting |
| pytest | 9.0+ | Testing |

## Commands

```bash
make repro          # Run full DVC pipeline against cluster MLflow (requires make mlflow port-forward running)
make train          # Train model only
make test           # Run pytest suite
make lint           # ruff check + format check
make serve          # Start FastAPI locally (against cluster MLflow port-forward)
make mlflow         # Port-forward cluster MLflow to localhost:5000
make promote        # Manually promote challenger → champion
make compile-kfp    # Compile Kubeflow Pipeline to YAML
make clean          # Remove generated artifacts (data/processed, models, metrics.json)
make docker-build   # Build inference container (tags as ghcr.io/my-neme-eh-jeff/churn-api:latest)
make docker-push    # Push to ghcr.io
make docker-run     # Run inference container locally
make deploy-mlflow  # Apply k8s/mlflow.yaml to cluster (one-time setup)
make deploy-argocd  # Apply argocd/application.yaml
make argocd-ui      # Print ArgoCD LoadBalancer IP
make k8s-status     # Show pod status across all namespaces
make demo           # Port-forward MLflow + churn-api, print ArgoCD IP
make demo-stop      # Kill all port-forwards
```

## Architecture (what's actually running)

### GKE Deployment (production)

**GCP Project:** `project-8018ed81-1dfe-470e-aad`  
**Cluster:** `mlops-cluster` — GKE Autopilot, `asia-south1`

| Namespace | Service | Public URL |
|-----------|---------|-----------|
| `mlflow` | MLflow 3.x (CloudSQL + GCS) | `http://34.180.20.197:5000` |
| `argocd` | ArgoCD v3.x | `http://34.100.246.237` |
| `kubeflow` | KFP UI | `http://34.93.2.209` |
| `churn-serving` | churn-api (2 pods, HPA 2-10) | `http://34.180.37.1` |

ArgoCD credentials: `admin` / `Y6p9-krPfkEhm4Sd`

> **Note:** LoadBalancer IPs are stable on GKE (unlike the local vind setup). Run `make gke-status` to print live IPs.

### GCP Infrastructure

| Resource | Details |
|----------|---------|
| CloudSQL | `churn-mlflow` (PostgreSQL 15, db-f1-micro, asia-south1-c) |
| GCS MLflow artifacts | `gs://churn-mlflow-artifacts-project-8018ed81` |
| GCS DVC remote | `gs://customer-churn-dvc-remote` (pre-existing) |
| Artifact Registry | `asia-south1-docker.pkg.dev/.../churn-repo` |
| Workload Identity | `mlflow-sa`, `kfp-sa`, `github-cicd` bound to K8s SAs |

### Local vind cluster (deprecated — kept for reference)

The project started on a local vind (vCluster in Docker) cluster using SQLite MLflow on a PVC. Migrated to GKE to fix:
- SQLite data loss on pod restart → CloudSQL PostgreSQL
- Unstable LoadBalancer IPs → stable GKE IPs
- KFP never running → KFP standalone deployed
- Ephemeral MLflow in CI (fake champion promotion) → real GKE MLflow
- ARM64-only images crashing on amd64 nodes → multi-arch (amd64+arm64)

To connect kubectl to local vind: `vcluster connect churn-cluster`  
To connect kubectl to GKE: `gcloud container clusters get-credentials mlops-cluster --region=asia-south1 --project=project-8018ed81-1dfe-470e-aad`

### DVC pipeline (local development)
```
data/churn_data.csv → preprocess → train.csv/test.csv → train → churn_model.pkl + run_id.txt → evaluate → metrics.json
```
- Run with `make repro` (sets `MLFLOW_TRACKING_URI=http://localhost:5000` automatically)
- MLflow port-forward must be running first (`make mlflow` in separate terminal)
- Each stage declared in `dvc.yaml` with deps/outs. DVC tracks hashes in `dvc.lock`

### MLflow model registry (on GKE)
- GKE: CloudSQL PostgreSQL backend + GCS artifact store + Cloud SQL Auth Proxy sidecar
- Local: port-forward (`make mlflow-kill && make mlflow`) to access at `localhost:5000`
- Uses **uvicorn** (not gunicorn) + 2Gi memory limit. MLflow 3.x requires uvicorn for security middleware (`--allowed-hosts`). Don't add `--gunicorn-opts` — it's incompatible with `--allowed-hosts`.
- Uses `--allowed-hosts=*` to allow requests from pods via cluster DNS (`mlflow.mlflow.svc.cluster.local`). Without this, MLflow 3.x's DNS rebinding protection returns 403.
- `train.py` writes `models/run_id.txt` after training so `evaluate.py` logs to the exact run
- `evaluate.py` reads `run_id.txt` — no race-condition "most recent run" search
- Every `train` run registers a new model version under `churn-model`
- `evaluate` compares AUC-ROC against current `@champion` alias; better → auto-promotes
- `src/promote.py` for manual promotion (`make promote`)
- **After fresh MLflow deploy**: run `make mlflow-kill && make mlflow` then `make bootstrap` to seed the model registry.

### API serving (churn-api)
- Loads champion model at startup via `mlflow.sklearn.load_model("models:/churn-model@champion")`
- `MLFLOW_TRACKING_URI=http://mlflow.mlflow.svc.cluster.local:5000` set in k8s/deployment.yaml
- `imagePullPolicy: Always` — always pulls from `ghcr.io/my-neme-eh-jeff/churn-api`
- Returns 503 from `/health` if model not loaded (pod won't receive traffic until ready)
- Image is public on ghcr.io — no pull secret needed
- **Dockerfile CMD**: uses `/app/.venv/bin/uvicorn` directly (NOT `uv run uvicorn`). `uv run` re-syncs the entire venv at every container start — 15-20s overhead + filesystem contention that causes the Python process to hang in D-state on Docker overlay filesystems.
- **Probe split**: liveness hits `/health/live` (always 200 = uvicorn is alive), readiness hits `/health` (503 until model loads). Do NOT use the same endpoint for both — the liveness probe will kill pods that are alive but still loading the model.
- **Probe delays**: liveness `initialDelaySeconds: 30` (Python package imports from Docker overlay FS take ~20s in the cluster), readiness `initialDelaySeconds: 10` (checks frequently, pod just stays "not ready" until model loads, never killed).
- **Memory**: 2Gi limit. MLflow client + scikit-learn model in memory needs headroom.
- **churn-api has a public LoadBalancer IP** (`http://34.180.37.1`) — no port-forward needed.
- **Multi-arch image**: built as `linux/amd64,linux/arm64` via `docker buildx` in CI (GKE nodes are amd64, Mac dev is arm64). Local QEMU-based cross-builds segfault on Mac M-chips — always let CI build the image.
- **GHCR package access**: the package `my-neme-eh-jeff/churn-api` must grant Actions access to repo `my-neme-eh-jeff/customer_churn_CICD` with Write role. Do this at `github.com/users/my-neme-eh-jeff/packages/container/churn-api/settings`.

### ArgoCD (GitOps)
- Watches `k8s/` directory on `main` branch of `github.com/my-neme-eh-jeff/customer_churn_CICD`
- Auto-sync enabled — every push to `k8s/` triggers a redeploy
- GKE: exposed as LoadBalancer `http://34.100.246.237` — no port-forward needed
- Local vind: was running in `--insecure` HTTP mode (patch via `args` not `command` due to tini entrypoint)

### CI/CD (GitHub Actions)
- `lint-and-test`: Runs on every push/PR — ruff + pytest
- `pipeline` (main only): dvc pull → ephemeral MLflow → dvc repro → dvc push → **multi-arch docker build** (`linux/amd64,linux/arm64`) → push to ghcr.io → update `k8s/deployment.yaml` image tag → git commit `[skip ci]` → ArgoCD auto-deploys
- `compile-kfp`: Compiles Kubeflow pipeline YAML, uploads as artifact
- Permissions: `contents: write`, `packages: write`, `id-token: write`
- Node.js: uses `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` and `setup-uv@v6` to avoid Node.js 20 deprecation warnings

### Data storage
- Raw dataset: Kaggle Telco Customer Churn (7,043 rows, 19 features)
- DVC remote: `gs://customer-churn-dvc-remote/dvc-store` (GCS)
- MLflow artifacts: stored in cluster PVC at `/mlflow/artifacts/`, served via `--serve-artifacts`

## File layout

```
src/
  preprocess.py     — Stage 1: clean TotalCharges, encode target, 80/20 split
  train.py          — Stage 2: fit RandomForest, log to MLflow, register model, write run_id.txt
  evaluate.py       — Stage 3: score model, read run_id.txt, log metrics, champion/challenger
  promote.py        — Manual champion promotion script
  api.py            — FastAPI inference server — loads @champion from MLflow registry at startup
pipelines/
  churn_pipeline.py — Kubeflow Pipelines version of the same DAG
  churn_pipeline.yaml — Compiled KFP pipeline (generated)
tests/
  conftest.py       — Fixtures: sample_raw_data, sample_processed_data
  test_preprocess.py — 5 tests
  test_train.py     — 3 tests
  test_evaluate.py  — 2 tests
data/
  churn_data.csv.dvc — DVC pointer to raw dataset
  processed/         — Generated train.csv, test.csv, stats.json
models/              — Generated churn_model.pkl + run_id.txt (both DVC-tracked)
k8s/
  mlflow.yaml        — MLflow Deployment + Service + PVC (namespace: mlflow)
  deployment.yaml    — churn-api Deployment (namespace: churn-serving)
  service.yaml       — churn-api LoadBalancer Service
  namespace.yaml     — churn-serving namespace
argocd/
  application.yaml   — ArgoCD Application watching k8s/ on main branch
```

## Current model metrics (baseline)

| Metric | Value |
|--------|-------|
| Accuracy | 0.7807 |
| AUC-ROC | 0.8162 |
| F1 | 0.5353 |
| Precision | 0.6117 |
| Recall | 0.4759 |

Champion: `churn-model` v1 (alias `@champion` in cluster MLflow, re-bootstrapped 2026-04-02)

## What's done vs TODO

### Done
- [x] DVC pipeline (preprocess → train → evaluate) with run_id.txt linking
- [x] DVC remote on GCS (`gs://customer-churn-dvc-remote`)
- [x] MLflow on cluster (k8s/mlflow.yaml) with --serve-artifacts + PVC
- [x] MLflow experiment tracking + model registry (champion/challenger) via aliases
- [x] Kubeflow Pipelines definition (compiles to YAML)
- [x] GitHub Actions CI/CD: lint, test, dvc repro, docker push to ghcr.io, deployment.yaml update
- [x] Tests (10 passing)
- [x] FastAPI inference server loading @champion from MLflow registry (not from disk)
- [x] Dockerfile: Python 3.12, uv sync --frozen, no model baked in
- [x] ArgoCD: deployed, LoadBalancer, --insecure mode, auto-sync watching k8s/
- [x] churn-api: imagePullPolicy Always, loads from cluster MLflow
- [x] End-to-end loop verified: make repro → MLflow champion → ghcr.io push → ArgoCD deploy → pod loads model → /predict works
- [x] Pre-commit hooks (ruff)
- [x] vind cluster running (`churn-cluster`)

### TODO
- [ ] KFP standalone on vind cluster — install and run the pipeline on Kubernetes
- [ ] Data validation (Pandera or Great Expectations) — as a DVC stage
- [ ] Run scaling/transform experiments (log transform vs StandardScaler vs nothing for tree models)
- [ ] Phase 2 API improvements: model version in /predict response, deeper health check (run dummy prediction)
- [ ] Local end-to-end demo run + video recording
- [ ] Set up Karpathy's auto research

### Explicitly out of scope
- Evidently AI / data drift monitoring / auto-retraining
- Model serving benchmarking (TTFT etc.) — handled in separate `autoscaler` project
- Internet-facing serving — local demo only

## Key decisions and context

- **MLflow uses uvicorn + --allowed-hosts=* + 2Gi memory**: MLflow 3.x security middleware (`--allowed-hosts`) only works with uvicorn. Without `--allowed-hosts=*`, pods connecting via `mlflow.mlflow.svc.cluster.local` get a 403 (DNS rebinding false positive). Uvicorn defaults to 1 worker, which is stable in constrained environments. `--gunicorn-opts=--workers=1` was removed because it's incompatible with `--allowed-hosts`.
- **MLflow --serve-artifacts**: Server proxies all artifact uploads/downloads via HTTP. Clients (local training via port-forward, pods via ClusterIP) don't need direct GCS/filesystem access — everything goes through the MLflow HTTP API.
- **ArgoCD runs --insecure (HTTP)**: TLS + gRPC-web over kubectl port-forward is unreliable (drops connections). Running on HTTP port 8080 is stable. Exposed via LoadBalancer (no port-forward). This is standard for local dev.
- **ArgoCD patched via args, not command**: The container entrypoint is `tini --`, so `command` would override tini. Use `args: ["argocd-server", "--insecure"]` instead.
- **api.py loads from MLflow registry, not disk**: `mlflow.sklearn.load_model("models:/churn-model@champion")` — champion alias is the single source of truth. No model baked into image.
- **run_id.txt links train and evaluate**: evaluate.py reads `models/run_id.txt` written by train.py. This avoids the race condition of searching for "most recent run".
- **CI uses ephemeral MLflow**: GitHub Actions starts a local MLflow server for the pipeline run. The cluster MLflow is for production/demo only.
- **imagePullPolicy: Always + ghcr.io**: Image must be public on ghcr.io (set in GitHub Packages settings). No image pull secret configured.
- **StandardScaler on numeric features**: No-op for RandomForest. Kept for pipeline correctness if model type changes.
- **MLflow v3 model registry uses aliases** (champion/challenger), not the old stages (Staging/Production).
- **DVC + Kubeflow Pipelines coexist**: DVC for local dev + data versioning, KFP for K8s orchestration.
- **protobuf**: mlflow 3.10 + dvc-gs + kfp all coexist on protobuf 6.x.
- **pandas pinned to <3**: MLflow 3.10 requires pandas <3.

## Git and tooling preferences

- **Never use pip** — always `uv add`, `uv run`, `uv sync`
- **Never use kind** — use vind (vcluster with Docker driver)
- **Never add co-authored-by lines** to commits
- **SSH remote**: `git@github-personal:my-neme-eh-jeff/customer_churn_CICD.git` (custom SSH alias for the `my-neme-eh-jeff` GitHub account)
- **Use real datasets** from Kaggle/research, not synthetic generated ones
- **GCS for storage** — user has `gcloud` CLI logged in with `aman2003raj0@gmail.com` (personal) and `aman.nambisan@atlan.com` (work, used by ADC). The Atlan account has `storage.objectAdmin` on the DVC bucket.
- **GitHub accounts**: `Aman-Nambisan` (personal, logged in via gh CLI) and `my-neme-eh-jeff` (portfolio account, used for this project). ghcr.io image is under `my-neme-eh-jeff`.

## Known infra quirks

- **vind cluster EOFs**: The vind cluster API server occasionally returns EOF/connection reset under load. Wait 10-15s and retry — it recovers on its own.
- **kubectl port-forward + ArgoCD**: Never use port-forward for ArgoCD. Use the LoadBalancer IP (192.168.148.253) directly. Port-forward over TLS/gRPC drops connections.
- **MLflow startup**: MLflow 3.x takes ~30-60s to become ready. readinessProbe has `failureThreshold: 10`. If pod is restarting, check memory — needs 2Gi limit.
- **make demo**: Port-forwards MLflow (5000) and churn-api (8001). ArgoCD is accessed via LoadBalancer directly — no port-forward in demo.
- **ghcr.io package visibility**: Must be set to Public in GitHub Packages settings. Do this via web UI — the REST API returns 404 for visibility changes on user packages.
- **Local `mlflow ui` shadows port-forward**: If `mlflow ui` or any process is already on port 5000, `kubectl port-forward` silently fails and `make repro` writes to local disk instead of the cluster. Always run `make mlflow-kill` before `make mlflow` to ensure the port is free. Check with `lsof -i :5000`.
- **MLflow PVC data is NOT auto-bootstrapped**: A fresh cluster or MLflow restart starts with an empty DB. After any MLflow redeploy, run `make repro` to re-register the model and set `@champion`. churn-api will return 503 until `@champion` exists.
- **ArgoCD fights manual kubectl apply**: ArgoCD auto-syncs every ~3 minutes. Any `kubectl apply` to k8s/ resources will be reverted unless the change is also committed to git. Always commit + push first, then optionally apply manually to skip the wait.
- **MLflow 3.x --allowed-hosts + --gunicorn-opts are mutually exclusive**: Security middleware only works with uvicorn. If you add `--allowed-hosts`, remove `--gunicorn-opts` (uvicorn is the default and uses 1 worker by default).
- **churn-api permission denied in ArgoCD UI**: Intermittent — happens when argocd-repo-server restarts. Refresh the page; it resolves on its own.

## Next session goals

1. KFP on vind — install KFP standalone and submit a pipeline run
2. Data validation stage (Pandera) in the DVC pipeline
3. Phase 2 API improvements (model version in response, deeper health check)
4. Scaling/transform experiments (log transform vs StandardScaler vs nothing)
5. Set up Karpathy's auto research
6. Video recording of end-to-end demo
