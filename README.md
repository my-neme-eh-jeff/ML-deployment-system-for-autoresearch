# Customer Churn Prediction — End-to-End MLOps Pipeline

An end-to-end MLOps project that takes a customer churn prediction model from raw data to production-ready deployment. The ML model is intentionally kept simple — the real value is in the infrastructure: reproducible pipelines, data versioning, experiment tracking, a model registry with automated promotion, Kubernetes-native orchestration, and GitOps-driven deployment.

## Motivation

Learn the following items  

- How do you version your data so results are reproducible six months later?
- How do you compare experiment runs and decide which model goes to production?
- How do you move from "works on my laptop" to running on Kubernetes for ML?
- How do you deploy a new model without downtime — and roll back if it's worse? _Follow up: How do you make your platform so strong that HITL can be completely removed with the help of autonomous agents iteratively improving your model using auto-research -> https://github.com/my-neme-eh-jeff/customer_churn_CICD/pull/1_

This project answers all of those by building two parallel pipeline paths (local DVC and Kubernetes-native Kubeflow) that share the same MLflow model registry and GCS storage backend.

## Screenshots 

<img width="3024" height="1736" alt="image" src="https://github.com/user-attachments/assets/0f8be2bb-ae70-498a-8d3a-dabf05ee45ce" />

<img width="3024" height="1074" alt="image" src="https://github.com/user-attachments/assets/2932d38f-e976-4514-a728-b6c80c9d1d55" />

<img width="1512" height="868" alt="Screenshot 2026-04-02 at 5 10 29 PM" src="https://github.com/user-attachments/assets/4c4780e4-4cf9-4c27-8ad1-547ead8aeae9" />

<img width="3024" height="1720" alt="image" src="https://github.com/user-attachments/assets/8466e9ca-a601-4d24-8176-ce1fae7aa450" />


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
# NOTE: 'make mlflow' port-forward must be running in a separate terminal first
make repro

# View experiment runs in MLflow
make mlflow-kill   # kill anything already on :5000 (e.g. stray 'mlflow ui')
make mlflow        # port-forward cluster MLflow → localhost:5000
# Open localhost:5000

# Run tests
make test

# Start the inference API (locally, against cluster MLflow port-forward)
make serve
# POST localhost:8000/predict
```

## Live Deployment (GKE)

The stack runs on **GKE Autopilot** (`asia-south1`) using GCP free trial credits. Single-region, single zone — this is a portfolio demo, not a production system designed for HA.

| Service | URL |
|---------|-----|
| **MLflow UI** | `http://34.180.20.197:5000` |
| **ArgoCD UI** | `http://34.100.246.237` (admin / Y6p9-krPfkEhm4Sd) |
| **KFP UI** | `http://34.93.2.209` |
| **Prediction API** | `http://34.180.37.1/predict` |

```bash
# Live prediction
curl -X POST http://34.180.37.1/predict \
  -H "Content-Type: application/json" \
  -d '{"gender":"Female","SeniorCitizen":0,"Partner":"Yes","Dependents":"No",
       "tenure":12,"PhoneService":"Yes","MultipleLines":"No",
       "InternetService":"Fiber optic","OnlineSecurity":"No","OnlineBackup":"No",
       "DeviceProtection":"No","TechSupport":"No","StreamingTV":"No",
       "StreamingMovies":"No","Contract":"Month-to-month","PaperlessBilling":"Yes",
       "PaymentMethod":"Electronic check","MonthlyCharges":70.35,"TotalCharges":846.0}'
# → {"churn":1,"churn_probability":0.71}

# Health check
curl http://34.180.37.1/health
curl http://34.180.37.1/health/live
```

### Infrastructure (GKE)

| Component | Details |
|-----------|---------|
| GKE Autopilot | `mlops-cluster`, `asia-south1`, 2 nodes (autoscales) |
| MLflow backend | CloudSQL PostgreSQL 15 (db-f1-micro) |
| MLflow artifacts | GCS bucket `churn-mlflow-artifacts-project-8018ed81` |
| DVC remote | GCS bucket `customer-churn-dvc-remote` (pre-existing) |
| Container images | `ghcr.io/my-neme-eh-jeff/churn-api` (multi-arch: amd64 + arm64) |
| Workload Identity | Pods use GCP SAs via WI — no service account keys |

### Honest limitations

- **Single zone** (asia-south1-c) — no HA. A zone outage takes everything down.
- **Free trial credits** — the cluster runs on Google Cloud's $300 free trial. IPs may change if the cluster is recreated.
- **CI MLflow** — the CI pipeline still uses an ephemeral MLflow for `dvc repro`. Champion promotion in CI is to a throwaway DB; the GKE MLflow is seeded manually via `make bootstrap`. Fixing this requires a stable CI-accessible MLflow endpoint.
- **KFP** — Kubeflow Pipelines is deployed and the UI is accessible. The pipeline YAML is compiled by CI. Actual pipeline runs via `make kfp-run` still need to be triggered manually.

## Cluster Setup (first-time or after MLflow data loss)

```bash
# Connect kubectl to GKE
gcloud container clusters get-credentials mlops-cluster \
  --region=asia-south1 --project=project-8018ed81-1dfe-470e-aad

# In a separate terminal, port-forward GKE MLflow
make mlflow-kill && make mlflow

# Bootstrap: train model + register @champion in GKE MLflow
make bootstrap

# Restart churn-api to load the champion
kubectl rollout restart deployment/churn-api -n churn-serving
```

### Gotcha: local `mlflow ui` shadows the port-forward

If `mlflow ui` is already running on port 5000, `make mlflow` will silently bind to the wrong process — `make repro` writes to your local `mlflow.db` instead of the cluster. Always run `make mlflow-kill` first.

## Tools and Why

| Tool | Role | Why |
|------|------|-----|
| **DVC** | Data/model versioning + local pipeline | Git-native, lightweight, hash-based caching |
| **MLflow** | Experiment tracking + model registry | Industry standard, great UI, champion/challenger aliases |
| **Kubeflow Pipelines** | K8s-native orchestration | Each step is a container, scales independently |
| **GKE Autopilot** | Managed Kubernetes | No node management, pay-per-pod, portfolio-grade |
| **CloudSQL** | PostgreSQL for MLflow backend | Persistent, managed, survives pod restarts |
| **GCS** | Cloud object storage | DVC data versioning + MLflow artifact store |
| **ArgoCD** | GitOps deployment | Push to git = deploy to cluster |
| **GitHub Actions** | CI/CD | Lint, test, run pipeline, build multi-arch images |
| **uv** | Python package management | Fast, replaces pip/poetry/pyenv |
| **ruff** | Linting + formatting | Replaces flake8/black/isort, Rust-based |
