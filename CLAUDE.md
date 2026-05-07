# ML Deployment System for Autoresearch

## Project overview

End-to-end MLOps project: any binary-classification CSV plugs in via `params.yaml`, an LLM (Claude) iteratively improves the model, GitOps reconciles to production. The ML model itself is intentionally simple — the real focus is the infrastructure: DVC pipelines, MLflow experiment tracking + model registry, Kubeflow Pipelines, ArgoCD GitOps, and CI/CD.

**Owner:** Aman — targeting MLOps Engineer and Data Engineer roles. This project is a portfolio/learning piece.

**Collaboration style:** Do NOT just build things silently. Aman wants to learn each concept deeply — explain the "why," share industry examples, ask questions to check understanding, and share blog posts. Teach end-to-end before and after implementing. Think of it as pair programming with a teaching component.

## Lessons from previous sessions — don't repeat these

These are real failures that cost real time. Read before starting.

### "Done" means "running in the cluster," not "code written"

For any change that targets cloud infra, declaring it complete requires all four:
1. `git push` succeeded.
2. CI run finished green (`gh run view <id> --json conclusion`).
3. ArgoCD reconciled (`kubectl get applications.argoproj.io -A` → `Synced/Healthy`).
4. The endpoint behaves as expected (`curl /predict` returns the shape you predicted).

Anything before (4) is "code written," not done. Aman has had to push back twice when "done" was declared at step 1.

### Grep before you delete

Small comments and config strings can be load-bearing. `make autoresearch-run` uses `sed` to find the line `# args: [REWRITE_ME]` in `jobs/autoresearch-job.yaml` and inject CLI flags. Deleting that comment during a cleanup pass broke the substitution silently — runs went out with the Dockerfile default (1 iter, dry-run) and exited. Before deleting any comment or renaming any string in a config file: `rg <string>` the repo first.

### Cross-reference config flags across train + evaluate + predict

If a `params.yaml` flag mutates the schema `train.py` sees, `src/evaluate.py` and `src/api.py` MUST apply the same transformation. The `add_charges_per_month` flag added a column in train; the saved sklearn pipeline expected it; evaluate didn't reproduce it; every iter that toggled the flag crashed `model.predict()`. Centralize feature engineering in one helper both train and evaluate import. Add tests that cover the "with-flag" path.

### Pre-flight live test before triggering a 10-min CI run

Unit tests don't catch prompt fragility. The system prompt told Sonnet 4.6 "return ONLY valid JSON," and 4.6 ignored it once the experiment history grew enough — it led with reasoning prose. Iters 4 and 5 of the smoke run failed because the prompt wasn't smoke-tested live before pushing. For changes to LLM prompts or output schemas: make ONE real API call with the production prompt locally before pushing.

### Use `[skip ci]` for docs-only / lessons / e2e.md commits

Every push to `main` triggers ~10–14 min of multi-arch Docker builds. Anything that doesn't change `src/`, `pipelines/`, `Dockerfile*`, `pyproject.toml`, `uv.lock`, or `data/` should land with `[skip ci]` in the subject. `gh run list` should not be cluttered with doc-update CI runs.

### Don't claim a champion advanced unless it actually did

`evaluate.py` only promotes when AUC strictly beats the existing `@champion`. The autoresearch loop's per-run "IMPROVED" log is *relative to its own session baseline*, not to the cluster registry. They can diverge — a run can "IMPROVE" 0.8162 → 0.8326 yet leave the cluster `@champion` unchanged because yesterday's champion was 0.8346. Verify with `mlflow.MlflowClient().get_model_version_by_alias(MODEL_NAME, "champion")` before claiming the served model changed.

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
make docker-build   # Build inference container (tags as ghcr.io/my-neme-eh-jeff/inference-api:latest)
make docker-push    # Push to ghcr.io
make docker-run     # Run inference container locally
make deploy-mlflow  # Apply k8s/mlflow.yaml to cluster (one-time setup)
make deploy-argocd  # Apply argocd/application.yaml
make argocd-ui      # Print ArgoCD LoadBalancer IP
make k8s-status     # Show pod status across all namespaces
make demo           # Port-forward MLflow + inference-api, print ArgoCD IP
make demo-stop      # Kill all port-forwards
```

## Architecture (what's actually running)

### GKE Deployment (production)

**GCP Project:** `project-8018ed81-1dfe-470e-aad`  
**Billing account:** `01411E-7B7536-664426` ($300 free trial credits)  
**Cluster:** `mlops-cluster` — GKE Autopilot, `asia-south1`

| Namespace | Service | Public URL |
|-----------|---------|-----------|
| `mlflow` | MLflow 3.x (CloudSQL + GCS) | `http://34.180.20.197:5000` |
| `argocd` | ArgoCD v3.x | `http://34.100.246.237` |
| `kubeflow` | KFP UI | `http://34.93.2.209` |
| `inference` | inference-api (2 pods, HPA 2-10) | `http://34.47.242.89` |

ArgoCD credentials: `admin` / `TMwwd4OpkcL6fPRy` (re-read with `kubectl get secret -n argocd argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d`)

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

To connect kubectl to GKE: `gcloud container clusters get-credentials mlops-cluster --region=asia-south1 --project=project-8018ed81-1dfe-470e-aad`

### DVC pipeline (local development)
```
data/<dataset>.{csv,parquet} → preprocess → train.csv/test.csv → train → classifier.pkl + run_id.txt → evaluate → metrics.json
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
- Every `train` run registers a new model version under `classifier`
- `evaluate` compares AUC-ROC against current `@champion` alias; better → auto-promotes
- `src/promote.py` for manual promotion (`make promote`)
- **After fresh MLflow deploy**: run `make mlflow-kill && make mlflow` then `make bootstrap` to seed the model registry.

### API serving (inference-api)
- Loads champion model at startup via `mlflow.sklearn.load_model("models:/classifier@champion")`
- `MLFLOW_TRACKING_URI=http://mlflow.mlflow.svc.cluster.local:5000` set in k8s/deployment.yaml
- `imagePullPolicy: Always` — always pulls from `ghcr.io/my-neme-eh-jeff/inference-api`
- Returns 503 from `/health` if model not loaded (pod won't receive traffic until ready)
- Image is public on ghcr.io — no pull secret needed
- **Dockerfile CMD**: uses `/app/.venv/bin/uvicorn` directly (NOT `uv run uvicorn`). `uv run` re-syncs the entire venv at every container start — 15-20s overhead + filesystem contention that causes the Python process to hang in D-state on Docker overlay filesystems.
- **Probe split**: liveness hits `/health/live` (always 200 = uvicorn is alive), readiness hits `/health` (503 until model loads). Do NOT use the same endpoint for both — the liveness probe will kill pods that are alive but still loading the model.
- **Probe delays**: liveness `initialDelaySeconds: 30` (Python package imports from Docker overlay FS take ~20s in the cluster), readiness `initialDelaySeconds: 10` (checks frequently, pod just stays "not ready" until model loads, never killed).
- **Memory**: 2Gi limit. MLflow client + scikit-learn model in memory needs headroom.
- **inference-api has a public LoadBalancer IP** (`http://34.47.242.89`) — no port-forward needed.
- **Multi-arch image**: built as `linux/amd64,linux/arm64` via `docker buildx` in CI (GKE nodes are amd64, Mac dev is arm64). Local QEMU-based cross-builds segfault on Mac M-chips — always let CI build the image.
- **GHCR package access**: the package `my-neme-eh-jeff/inference-api` must grant Actions access to repo `my-neme-eh-jeff/ML-deployment-system-for-autoresearch` with Write role. Do this at `github.com/users/my-neme-eh-jeff/packages/container/inference-api/settings`.

### ArgoCD (GitOps)
- Watches `k8s/` directory on `main` branch of `github.com/my-neme-eh-jeff/ML-deployment-system-for-autoresearch`
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
- Raw dataset (active): Kaggle IEEE-CIS Fraud Detection (200K row subsample, 339 numeric + ~14 categorical features). Pluggable via `params.yaml`.
- DVC remote: `gs://customer-churn-dvc-remote/dvc-store` (GCS)
- MLflow artifacts: stored in cluster PVC at `/mlflow/artifacts/`, served via `--serve-artifacts`

### Autoresearch loop (in-cluster Job → KFP submission)
- Submit with `make autoresearch-run AUTORESEARCH_N=5 AUTORESEARCH_HOURS=2` — creates a unique-named K8s Job from `jobs/autoresearch-job.yaml`.
- Per iteration: read state → call Claude API (Sonnet 4.6) → mutate `params.yaml` / `src/train.py` / `src/preprocess.py` → submit a KFP run → wait → read metrics from MLflow → if AUC improved, commit to a feature branch via GitHub App + GraphQL `createCommitOnBranch`. PR opened at the end with cost summary.
- KFP host: `http://ml-pipeline.kubeflow.svc.cluster.local:8888` (in-cluster). UI at `http://34.93.2.209`.
- GitHub App: `ML-deployment-for-autoresearch` (App ID `3576508`, install `128892452`). PEM stored in GCP Secret Manager as `github-app-key`. Workload Identity grants `secretmanager.secretAccessor` to `autoresearch-sa`.
- Cost tracking: Anthropic `usage.input_tokens` / `output_tokens` are logged per iteration to MLflow's `auto-experiment` runs and to `auto_experiment/history.tsv`. PR body includes a totals summary.
- Anthropic key: `kubectl create secret generic anthropic --from-env-file=.env -n inference` (use `make autoresearch-secret`).

### Resetting state for a fresh autoresearch run

`make reset-for-fresh-run` wipes everything that shouldn't carry over between unrelated runs and rebuilds a clean v1 baseline. **Run it before:** a dataset swap, a fresh demo recording, or any time you want the AUC trajectory plot to start from a known floor.

What it destroys (in order):
1. **`auto_experiment/history.tsv`** — truncates back to the header row. Without this, a new run's iteration counter and trajectory plot are polluted by old rows.
2. **MLflow `classifier` registered model** — deletes all versions (v1..vN) and the `@champion` alias. Forces the next training run to register as v1.
3. **Local DVC outputs** (`data/processed/*`, `models/`, `metrics.json`) and re-runs `dvc repro --force` against the cluster MLflow → registers the current `params.yaml` baseline as v1.
4. **Sets `classifier@champion` → v1** so `inference-api` has something to serve.
5. **Restarts `inference-api` pods** to drop any cached old version.

Prerequisites: `make mlflow-kill && make mlflow` port-forward to `localhost:5000` must be running (the MLflow client calls hit the cluster, not local SQLite).

After it returns: `auto_experiment/history.tsv` has only the header, MLflow shows `classifier` v1 only with `@champion`, and `kubectl get pods -n inference` shows the fresh rollout. From here, `make autoresearch-run AUTORESEARCH_N=N` produces a clean N-iter trajectory.

## File layout

```
src/
  preprocess.py     — Stage 1: clean TotalCharges, encode target, 80/20 split
  train.py          — Stage 2: fit RandomForest, log to MLflow, register model, write run_id.txt
  evaluate.py       — Stage 3: score model, read run_id.txt, log metrics, champion/challenger
  promote.py        — Manual champion promotion script
  api.py            — FastAPI inference server — loads @champion from MLflow registry at startup
pipelines/
  pipeline.py        — Kubeflow Pipelines version of the same DAG
  pipeline.yaml      — Compiled KFP pipeline (generated)
tests/
  conftest.py       — Fixtures: sample_raw_data, sample_processed_data
  test_preprocess.py — 5 tests
  test_train.py     — 3 tests
  test_evaluate.py  — 2 tests
data/
  ieee_cis.parquet.dvc — DVC pointer to raw dataset
  processed/         — Generated train.csv, test.csv, stats.json
models/              — Generated classifier.pkl + run_id.txt (both DVC-tracked)
k8s/
  mlflow.yaml        — MLflow Deployment + Service + PVC (namespace: mlflow)
  deployment.yaml    — inference-api Deployment (namespace: inference)
  service.yaml       — inference-api LoadBalancer Service
  namespace.yaml     — inference namespace
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

Champion: `classifier` v1 (alias `@champion` in cluster MLflow, re-bootstrapped 2026-04-02)

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
- [x] inference-api: imagePullPolicy Always, loads from cluster MLflow
- [x] End-to-end loop verified: make repro → MLflow champion → ghcr.io push → ArgoCD deploy → pod loads model → /predict works
- [x] Pre-commit hooks (ruff)

### TODO
- [ ] Swap dataset to IEEE-CIS Fraud Detection (590K × 433) for the autoresearch demo
- [ ] Bad-baseline strategy (LogisticRegression + 1 feature) for a dramatic AUC trajectory
- [ ] Long autoresearch run (50+ iters) on the new dataset, capture trajectory plot
- [ ] Phase 2 API improvements: model version in /predict response, deeper health check
- [ ] Demo video

### Explicitly out of scope
- Evidently AI / data drift monitoring / auto-retraining
- Model serving benchmarking (TTFT etc.) — handled in separate `autoscaler` project
- Internet-facing serving — local demo only

## Key decisions and context

- **MLflow uses uvicorn + --allowed-hosts=* + 2Gi memory**: MLflow 3.x security middleware (`--allowed-hosts`) only works with uvicorn. Without `--allowed-hosts=*`, pods connecting via `mlflow.mlflow.svc.cluster.local` get a 403 (DNS rebinding false positive). Uvicorn defaults to 1 worker, which is stable in constrained environments. `--gunicorn-opts=--workers=1` was removed because it's incompatible with `--allowed-hosts`.
- **MLflow --serve-artifacts**: Server proxies all artifact uploads/downloads via HTTP. Clients (local training via port-forward, pods via ClusterIP) don't need direct GCS/filesystem access — everything goes through the MLflow HTTP API.
- **ArgoCD runs --insecure (HTTP)**: TLS + gRPC-web over kubectl port-forward is unreliable (drops connections). Running on HTTP port 8080 is stable. Exposed via LoadBalancer (no port-forward). This is standard for local dev.
- **ArgoCD patched via args, not command**: The container entrypoint is `tini --`, so `command` would override tini. Use `args: ["argocd-server", "--insecure"]` instead.
- **api.py loads from MLflow registry, not disk**: `mlflow.sklearn.load_model("models:/classifier@champion")` — champion alias is the single source of truth. No model baked into image.
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
- **SSH remote**: `git@github-personal:my-neme-eh-jeff/ML-deployment-system-for-autoresearch.git` (custom SSH alias for the `my-neme-eh-jeff` GitHub account)
- **Use real datasets** from Kaggle/research, not synthetic generated ones
- **GCS for storage** — user has `gcloud` CLI logged in with `aman2003raj0@gmail.com` (personal) and `aman.nambisan@atlan.com` (work, used by ADC). The Atlan account has `storage.objectAdmin` on the DVC bucket.
- **GitHub accounts**: `Aman-Nambisan` (personal, logged in via gh CLI) and `my-neme-eh-jeff` (portfolio account, used for this project). ghcr.io image is under `my-neme-eh-jeff`.

## Known infra quirks

- **vind cluster EOFs**: The vind cluster API server occasionally returns EOF/connection reset under load. Wait 10-15s and retry — it recovers on its own.
- **kubectl port-forward + ArgoCD**: Never use port-forward for ArgoCD. Use the LoadBalancer IP (`http://34.100.246.237`) directly — port-forward over TLS/gRPC drops connections.
- **MLflow startup**: MLflow 3.x takes ~30-60s to become ready. readinessProbe has `failureThreshold: 10`. If pod is restarting, check memory — needs 2Gi limit.
- **make demo**: Port-forwards MLflow (5000) and inference-api (8001). ArgoCD is accessed via LoadBalancer directly — no port-forward in demo.
- **ghcr.io package visibility**: Must be set to Public in GitHub Packages settings. Do this via web UI — the REST API returns 404 for visibility changes on user packages.
- **Local `mlflow ui` shadows port-forward**: If `mlflow ui` or any process is already on port 5000, `kubectl port-forward` silently fails and `make repro` writes to local disk instead of the cluster. Always run `make mlflow-kill` before `make mlflow` to ensure the port is free. Check with `lsof -i :5000`.
- **MLflow PVC data is NOT auto-bootstrapped**: A fresh cluster or MLflow restart starts with an empty DB. After any MLflow redeploy, run `make repro` to re-register the model and set `@champion`. inference-api will return 503 until `@champion` exists.
- **ArgoCD fights manual kubectl apply**: ArgoCD auto-syncs every ~3 minutes. Any `kubectl apply` to k8s/ resources will be reverted unless the change is also committed to git. Always commit + push first, then optionally apply manually to skip the wait.
- **MLflow 3.x --allowed-hosts + --gunicorn-opts are mutually exclusive**: Security middleware only works with uvicorn. If you add `--allowed-hosts`, remove `--gunicorn-opts` (uvicorn is the default and uses 1 worker by default).
- **inference-api permission denied in ArgoCD UI**: Intermittent — happens when argocd-repo-server restarts. Refresh the page; it resolves on its own.
- **KFP standalone on GKE Autopilot — 4 components disabled**: `cache-deployer-deployment` and `cache-server` fail GKE Warden's CSR-rejection rule (CSRs with `system:` prefix not allowed on Autopilot — structural incompatibility). `ml-pipeline-viewer-crd` and `ml-pipeline-visualizationserver` are visualization extras, unused. All four stay at `replicas: 0`; `cluster-wake` skips them.
- **ArgoCD — 2 unused components disabled**: `argocd-applicationset-controller` (we don't use ApplicationSet CRs) and `argocd-notifications-controller` (no Slack/email integration). Stay at `replicas: 0`.
- **KFP PVCs MUST be regional**: `minio-pvc` and `mysql-pv-claim` use `storageClassName: standard-rwo-regional`, 5Gi each. Zonal PD (the default `standard-rwo`) locks the disk to one zone — after cluster-sleep, Autopilot may bring new nodes up in a different zone, and zonal PVCs can't follow → `1 node(s) didn't match PersistentVolume's node affinity`. Regional PD replicates across 2 zones in the region. Keep PVCs at 5Gi (regional = 2× SSD quota usage; bigger blows the SSD cap — see below).
- **GCP free-trial SSD cap**: 250 GB in `asia-south1`, **cannot be raised on free trial**. Current usage ~220 GB (2 Autopilot nodes × ~100 GB boot disks + 2× 5Gi regional PVCs replicated = 200 + 10 + 10). Adding a 3rd node fails because each new node needs another ~100 GB of SSD. Fit everything in 2 nodes by minimizing pod count; production fix would be GCS-backed minio + CloudSQL-backed KFP mysql to free PVC quota.
- **MLflow image pinned to `v3.11.1`**: `k8s/mlflow.yaml` pins `ghcr.io/mlflow/mlflow:v3.11.1` (matches the migrated DB schema). Default `imagePullPolicy` for non-`:latest` tags is `IfNotPresent`, so pod restarts won't auto-pull a newer schema. **If you bump this tag**, first run a one-shot Job (image `ghcr.io/mlflow/mlflow:NEW_TAG`, sidecar `gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.11.4`, command `mlflow db upgrade postgresql://mlflow_user:$PASS@127.0.0.1:5432/mlflow_db`), then `kubectl rollout restart deployment/mlflow`. (Historical: an unpinned `:latest` was the source of recurring "Detected out-of-date database schema" CrashLoopBackOff incidents.)

## Active direction (decided 2026-05-03)

The project is being repositioned: **the churn dataset was an arbitrary choice; the framework should be a generic LLM-driven AutoML harness**. Anyone can plug in a binary-classification CSV and run autoresearch against it.

**New project name:** `ml-deployment-system-for-autoresearch` (the GitHub repo and image registry will need to follow; Aman renames the repo manually in the GitHub UI, then we update the SSH remote, ArgoCD `source.repoURL`, and image tags).

**Demo dataset:** IEEE-CIS Fraud Detection (Kaggle, 590K × 433). Bigger headroom (0.55–0.94) and more authentic ML problem than Telco Churn (7K × 19, ceiling 0.84).

**Bad baseline:** `DecisionTreeClassifier(max_depth=1, max_features=1)` on a single feature. Should land near AUC 0.50–0.55. The point is to give the autoresearch loop a real trajectory to traverse on a real dataset.

**Schema-in-params:** preprocess / train / evaluate must read `target_column`, `numeric_features`, `categorical_features`, `csv_path` from `params.yaml`. No more hardcoded `TARGET = "Churn"` or hardcoded feature lists. This is the actual "any binary CSV plugs in" move.

### Pending work, in order

1. **Rename + plug-and-play refactor + IEEE-CIS dataset + bad baseline** — one coordinated branch / PR. Don't slice it.
2. **CI speedup** — drop arm64 from the inference image (GKE is amd64; arm64 is for local Mac dev only and can be built per-laptop), add buildx GHA cache, add path filters so docs-only changes don't trigger image rebuilds. Estimate: 14 min → 3-6 min on incremental, ~30 sec on docs-only.
3. **First real autoresearch run** — 10-20 iters on IEEE-CIS, captured AUC trajectory plot.
4. **Demo video** — Aman owns.

### Explicitly out of scope

- Evidently AI / drift monitoring / auto-retraining
- Model serving benchmarking
- Internet-facing serving
