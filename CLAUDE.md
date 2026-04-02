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
| ArgoCD | - | GitOps deployment (watches k8s/ directory) |
| ruff | 0.15+ | Linting + formatting |
| pytest | 9.0+ | Testing |

## Commands

```bash
make repro          # Run full DVC pipeline (preprocess → train → evaluate)
make train          # Train model only
make test           # Run pytest suite (10 tests)
make lint           # ruff check + format check
make serve          # Start FastAPI on localhost:8000
make mlflow         # MLflow UI on localhost:5000
make promote        # Manually promote challenger → champion
make compile-kfp    # Compile Kubeflow Pipeline to YAML
make clean          # Remove generated artifacts
make docker-build   # Build inference container
make docker-run     # Run inference container
```

## Architecture

### DVC pipeline (local development)
```
data/churn_data.csv → preprocess → train.csv/test.csv → train → churn_model.pkl → evaluate → metrics.json
```
Each stage declared in `dvc.yaml` with deps/outs. DVC tracks hashes in `dvc.lock` and only re-runs changed stages.

### MLflow model registry
- Every `train` run registers a new model version under `churn-model`
- `evaluate` compares new model AUC-ROC against current "champion" alias
- If better → auto-promoted to "champion"; if worse → tagged as "challenger" only
- `src/promote.py` for manual promotion
- Champion model is what gets deployed by ArgoCD

### Kubeflow Pipelines (K8s orchestration)
- Same pipeline logic as DVC but containerized — each stage is a pod
- Defined in `pipelines/churn_pipeline.py`, compiles to `pipelines/churn_pipeline.yaml`
- Uses `@dsl.component` decorators with `base_image=python:3.12-slim`
- Reads raw data from GCS, logs to MLflow, handles champion/challenger

### Data storage
- Raw dataset: Kaggle Telco Customer Churn (7,043 rows, 19 features)
- DVC remote: `gs://customer-churn-dvc-remote/dvc-store` (GCS)
- DVC pointer files (`.dvc`) are in git; actual data is in GCS
- MLflow artifacts stored locally in `mlruns/` (gitignored)

### CI/CD (GitHub Actions)
- **lint-and-test**: Runs on every push/PR — ruff + pytest
- **pipeline**: Main branch only — dvc pull → dvc repro → dvc push → docker build (needs `GCP_SA_KEY` secret)
- **compile-kfp**: Compiles Kubeflow pipeline, uploads as artifact

## File layout

```
src/
  preprocess.py     — Stage 1: clean TotalCharges, encode target, 80/20 split
  train.py          — Stage 2: fit RandomForest, log to MLflow, register model
  evaluate.py       — Stage 3: score model, log metrics, champion/challenger promotion
  promote.py        — Manual champion promotion script
  api.py            — FastAPI inference server (/predict, /health)
pipelines/
  churn_pipeline.py — Kubeflow Pipelines version of the same DAG
  churn_pipeline.yaml — Compiled KFP pipeline (generated)
tests/
  conftest.py       — Fixtures: sample_raw_data, sample_processed_data
  test_preprocess.py — 5 tests: splits, encoding, blank handling, stats
  test_train.py     — 3 tests: pipeline structure, model save, MLflow registry
  test_evaluate.py  — 2 tests: metrics file, champion promotion
data/
  churn_data.csv.dvc — DVC pointer to raw dataset
  processed/         — Generated train.csv, test.csv, stats.json
models/              — Generated churn_model.pkl (DVC-tracked)
k8s/                 — Kubernetes manifests (ArgoCD target) [TODO]
argocd/              — ArgoCD application config [TODO]
```

## Current model metrics (baseline)

| Metric | Value |
|--------|-------|
| Accuracy | 0.7807 |
| AUC-ROC | 0.8162 |
| F1 | 0.5353 |
| Precision | 0.6117 |
| Recall | 0.4759 |

Champion: `churn-model` v1

## What's done vs TODO

### Done
- [x] DVC pipeline (preprocess → train → evaluate)
- [x] DVC remote on GCS (`gs://customer-churn-dvc-remote`)
- [x] MLflow experiment tracking + model registry (champion/challenger)
- [x] Kubeflow Pipelines definition (compiles to YAML)
- [x] GitHub Actions CI/CD (lint, test, pipeline, kfp compile)
- [x] Tests (10 passing)
- [x] FastAPI inference server
- [x] Dockerfile
- [x] Pre-commit hooks (ruff)
- [x] vind cluster running (`churn-cluster`)

### TODO
- [ ] Install KFP standalone on vind cluster and run the pipeline
- [ ] ArgoCD setup on vind — watch k8s/ dir, deploy champion model
- [ ] K8s manifests for model serving
- [ ] Data validation (Pandera or Great Expectations) — as a DVC stage
- [ ] Run scaling/transform experiments (log transform vs StandardScaler vs nothing for tree models)
- [ ] Local end-to-end demo run + video recording

### Explicitly out of scope
- Evidently AI / data drift monitoring / auto-retraining (too complex for now)
- Model serving benchmarking (TTFT etc.) — handled in separate `autoscaler` project
- Internet-facing serving — local demo only

## Key decisions and context

- **StandardScaler on numeric features**: Currently applied but is a no-op for RandomForest. Kept for pipeline correctness if model type changes. User aware — planned as an experiment (log transform vs StandardScaler vs nothing).
- **MLflow v3 model registry uses aliases** (champion/challenger), not the old stages (Staging/Production). Aliases are the modern approach.
- **DVC + Kubeflow Pipelines coexist**: DVC for local dev + data versioning, KFP for K8s orchestration. They're complementary, not competing.
- **protobuf**: mlflow 3.10 + dvc-gs + kfp all coexist on protobuf 6.x. Was a pain to resolve.
- **pandas pinned to <3**: MLflow 3.10 requires pandas <3.

## Git and tooling preferences

- **Never use pip** — always `uv add`, `uv run`, `uv sync`
- **Never use kind** — use vind (vcluster with Docker driver)
- **Never add co-authored-by lines** to commits
- **SSH remote**: `git@github-personal:my-neme-eh-jeff/customer_churn_CICD.git` (custom SSH alias for the `my-neme-eh-jeff` GitHub account)
- **Use real datasets** from Kaggle/research, not synthetic generated ones
- **GCS for storage** — user has `gcloud` CLI logged in with `aman2003raj0@gmail.com` (personal) and `aman.nambisan@atlan.com` (work, used by ADC). The Atlan account has `storage.objectAdmin` on the DVC bucket.

## Next session goals

1. Run the whole thing locally end-to-end (DVC pipeline + MLflow UI + KFP on vind)
2. Set up Karpathy's auto research
3. User will ask questions about the code after exploring it
