# Customer Churn Prediction — End-to-End MLOps Pipeline

An end-to-end MLOps project that takes a customer churn prediction model from raw data to production-ready deployment. The ML model is intentionally kept simple — the real value is in the infrastructure: reproducible pipelines, data versioning, experiment tracking, a model registry with automated promotion, Kubernetes-native orchestration, and GitOps-driven deployment.

## Motivation

Most ML tutorials stop at `model.fit()`. In production, that's maybe 10% of the work. The rest is:

- How do you version your data so results are reproducible six months later?
- How do you compare experiment runs and decide which model goes to production?
- How do you move from "works on my laptop" to running on Kubernetes?
- How do you deploy a new model without downtime — and roll back if it's worse?

This project answers all of those by building two parallel pipeline paths (local DVC and Kubernetes-native Kubeflow) that share the same MLflow model registry and GCS storage backend.

## Architecture

### End-to-end flow

```
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                       Local Development                                 │
 │                                                                         │
 │   make repro  (MLFLOW_TRACKING_URI=http://localhost:5000)               │
 │       │                                                                 │
 │       ▼                                                                 │
 │   DVC pipeline:  preprocess ──► train ──► evaluate                     │
 │                                  │              │                       │
 │                          writes run_id.txt   champion/challenger        │
 │                          to models/          decision via AUC-ROC       │
 │                                              comparison                 │
 │                                                  │                      │
 │              logs runs + registers model ────────┘                      │
 │              via port-forward (localhost:5000)                          │
 └──────────────────────────────┬──────────────────────────────────────────┘
                                │  git push
                                ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                      GitHub Actions CI/CD                               │
 │                                                                         │
 │   lint (ruff) ──► test (pytest)                                         │
 │         │                                                               │
 │         ▼  (main branch only)                                           │
 │   dvc pull (GCS) ──► dvc repro ──► dvc push (GCS)                      │
 │                          │                                              │
 │                    ephemeral MLflow                                     │
 │                    server in CI                                         │
 │                          │                                              │
 │                          ▼                                              │
 │   docker build ──► push ghcr.io/my-neme-eh-jeff/churn-api:SHA          │
 │                          │                                              │
 │                   update k8s/deployment.yaml image tag                  │
 │                   git commit [skip ci] ──► git push                     │
 └──────────────────────────────┬──────────────────────────────────────────┘
                                │  k8s/deployment.yaml changed
                                ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                    vind cluster (local Kubernetes)                      │
 │                                                                         │
 │  ┌─────────────────────────────────────────────────────────────────┐   │
 │  │  argocd namespace                                                │   │
 │  │  ArgoCD (LoadBalancer: 192.168.148.253, --insecure HTTP)         │   │
 │  │    └── watches github.com/my-neme-eh-jeff/.../k8s/ on main      │   │
 │  │    └── auto-syncs on every git push → deploys churn-api         │   │
 │  └───────────────────────────────┬─────────────────────────────────┘   │
 │                                  │ deploys                              │
 │                                  ▼                                      │
 │  ┌────────────────────┐    ┌─────────────────────────────────────────┐ │
 │  │  mlflow namespace  │    │  churn-serving namespace                │ │
 │  │                    │    │                                         │ │
 │  │  MLflow server     │◄───│  churn-api (2 pods)                     │ │
 │  │  ├── SQLite DB     │    │  image: ghcr.io/.../churn-api:SHA       │ │
 │  │  ├── artifacts/    │    │  imagePullPolicy: Always                │ │
 │  │  └── --serve-      │    │                                         │ │
 │  │      artifacts     │    │  on startup:                            │ │
 │  │                    │    │  mlflow.sklearn.load_model(             │ │
 │  │  v1 ── @champion ──┼────┤    "models:/churn-model@champion")      │ │
 │  │  v2                │    │                                         │ │
 │  │  v3 ── @challenger │    │  POST /predict → {"churn": 1,           │ │
 │  └────────────────────┘    │               "churn_probability": 0.6} │ │
 │      port-forward          └─────────────────────────────────────────┘ │
 │      localhost:5000                                                     │
 └─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                        Storage Layer                                    │
 │                                                                         │
 │   Google Cloud Storage (gs://customer-churn-dvc-remote)                │
 │   └── dvc-store/   ← datasets + model pkl (DVC-managed)               │
 └─────────────────────────────────────────────────────────────────────────┘
```

### Champion/challenger promotion

```
  make repro
      │
      ▼
  New model version registered in MLflow
      │
      ▼
  Compare AUC-ROC vs current @champion
      │
      ├── Better?  → set @champion alias → git push → ArgoCD deploys
      │                                  → new pods load updated champion
      │
      └── Worse?   → set @challenger alias only → prod unchanged
                     (manual: make promote)
```

## Model Registry and Promotion Flow

The project implements a champion/challenger pattern for model deployment:

```
  Training run
       │
       ▼
  Register new model version (v1, v2, v3...)
       │
       ▼
  Compare AUC-ROC against current champion
       │
       ├── Better? ──► Promote to "champion" ──► ArgoCD deploys it
       │
       └── Worse?  ──► Tag as "challenger" ──► Nothing changes in prod
                       (manual promotion available via `make promote`)
```

This ensures that a bad model never silently replaces a good one. The champion alias in MLflow is the single source of truth for what's deployed.

## Dataset

[Telco Customer Churn](https://www.kaggle.com/datasets/blastchar/telco-customer-churn) from IBM/Kaggle. 7,043 customers, 19 features (demographics, account info, services, billing), binary churn target. The dataset is DVC-tracked — git stores a lightweight pointer file; the actual data lives in GCS.

## Pipeline Stages

Defined in `dvc.yaml`, executed with `dvc repro`:

| Stage | Script | Input | Output | What it does |
|-------|--------|-------|--------|-------------|
| **preprocess** | `src/preprocess.py` | `data/churn_data.csv` | `train.csv`, `test.csv`, `stats.json` | Clean `TotalCharges`, encode target, stratified 80/20 split |
| **train** | `src/train.py` | `train.csv` | `churn_model.pkl` | Fit sklearn Pipeline (StandardScaler + OneHotEncoder + RandomForest), log to MLflow, register model |
| **evaluate** | `src/evaluate.py` | `test.csv`, `churn_model.pkl` | `metrics.json` | Score model, log metrics to MLflow, champion/challenger promotion |

Current baseline metrics (no hyperparameter tuning):

| Metric | Value |
|--------|-------|
| Accuracy | 0.7807 |
| AUC-ROC | 0.8162 |
| F1 | 0.5353 |
| Precision | 0.6117 |
| Recall | 0.4759 |

## Two Pipeline Paths

This project implements the same ML pipeline in two ways — to demonstrate how local development translates to production Kubernetes orchestration:

**DVC Pipeline** (local development and CI)
- Runs on any machine with `uv run dvc repro`
- Hash-based caching — only re-runs stages whose inputs changed
- Data versioned in GCS — any git commit maps to exact data + model

**Kubeflow Pipelines** (Kubernetes-native)
- Same logic, but each stage runs in its own container/pod
- Defined in `pipelines/churn_pipeline.py` using KFP SDK
- Compiles to YAML for submission to a KFP instance on the cluster
- Steps can independently scale (e.g., GPU for training, CPU for preprocessing)

## Project Structure

```
├── src/
│   ├── preprocess.py          # Data cleaning and train/test split
│   ├── train.py               # Model training + MLflow logging + registry
│   ├── evaluate.py            # Evaluation + champion/challenger promotion
│   ├── promote.py             # Manual model promotion script
│   └── api.py                 # FastAPI inference server
├── pipelines/
│   ├── churn_pipeline.py      # Kubeflow Pipelines definition
│   └── churn_pipeline.yaml    # Compiled pipeline (generated)
├── tests/                     # 10 pytest tests covering all stages
├── data/
│   ├── churn_data.csv.dvc     # DVC pointer to raw dataset in GCS
│   └── processed/             # Generated splits (DVC-tracked)
├── models/                    # Trained model (DVC-tracked)
├── k8s/                       # Kubernetes manifests (ArgoCD target)
├── argocd/                    # ArgoCD application config
├── .github/workflows/ci.yaml  # GitHub Actions: lint, test, pipeline, KFP compile
├── dvc.yaml                   # Pipeline DAG definition
├── dvc.lock                   # Pinned hashes of all inputs/outputs
├── metrics.json               # Latest evaluation metrics
├── Makefile                   # Convenience commands
├── Dockerfile                 # Inference API container
└── pyproject.toml             # Dependencies (managed by uv)
```

## Quick Start

```bash
# Install dependencies
uv sync

# Pull data from GCS
uv run dvc pull

# Run the full pipeline
make repro

# View experiment runs in MLflow
make mlflow
# Open localhost:5000

# Run tests
make test

# Start the inference API
make serve
# POST localhost:8000/predict
```

## Tools and Why

| Tool | Role | Why |
|------|------|-----|
| **DVC** | Data/model versioning + local pipeline | Git-native, lightweight, hash-based caching |
| **MLflow** | Experiment tracking + model registry | Industry standard, great UI, champion/challenger aliases |
| **Kubeflow Pipelines** | K8s-native orchestration | Each step is a container, scales independently |
| **GCS** | Cloud object storage | DVC remote for data and model artifacts |
| **vind** | Local Kubernetes (vCluster in Docker) | Lighter than kind, built-in LoadBalancer |
| **ArgoCD** | GitOps deployment | Push to git = deploy to cluster |
| **GitHub Actions** | CI/CD | Lint, test, run pipeline, build images |
| **uv** | Python package management | Fast, replaces pip/poetry/pyenv |
| **ruff** | Linting + formatting | Replaces flake8/black/isort, Rust-based |
