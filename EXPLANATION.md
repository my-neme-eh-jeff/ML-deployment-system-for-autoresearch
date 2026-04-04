# How Everything Works — End-to-End Guide

This file explains the full project from first principles. Written for someone who has built parts of this but wants a mental model of how all the pieces connect.

---

## 1. The Big Picture

We're predicting which telecom customers will cancel their subscription (churn). The ML model itself is simple (a Random Forest). **The complexity is all in the infrastructure**: how we version data, track experiments, promote models, orchestrate pipelines, and deploy to production.

```
You on your laptop
       │
       │ git push
       ▼
┌─────────────────────────────────────────────────────────────┐
│                   GitHub Actions (CI/CD)                    │
│                                                             │
│  lint → test → train model → register in MLflow            │
│       → build Docker image → push to ghcr.io               │
│       → update k8s/deployment.yaml → push [skip ci]        │
└────────────────────────────┬────────────────────────────────┘
                             │ k8s/deployment.yaml changed
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              GKE Cluster (asia-south1, Google Cloud)        │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │  MLflow  │  │  KFP     │  │  ArgoCD  │  │ churn-api │  │
│  │(tracking │  │(pipeline │  │(deploys  │  │(serves    │  │
│  │+registry)│  │ UI+runs) │  │ from git)│  │predictions│  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────┘  │
│                                                             │
│  ┌──────────────────────┐    ┌──────────────────────────┐  │
│  │  CloudSQL PostgreSQL │    │  GCS Bucket              │  │
│  │  (MLflow's database) │    │  (model artifacts + data)│  │
│  └──────────────────────┘    └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. The Data Pipeline — DVC

### What is DVC?

DVC (Data Version Control) is like Git for data and ML pipelines. Git tracks `.py` files; DVC tracks large files (CSVs, model pickles) by storing them in GCS and keeping a tiny pointer file in Git.

### The pipeline

```
Raw data (7,043 rows, CSV)
         │
         ▼  src/preprocess.py
Clean + split (80% train, 20% test)
         │
         ▼  src/train.py
Train RandomForest → churn_model.pkl
         │
         ▼  src/evaluate.py
Score model → compare vs @champion → promote if better
```

These three steps are declared in `dvc.yaml`. DVC knows:
- **What depends on what**: if `train.py` changes, re-run train and evaluate but skip preprocess
- **What changed**: by hashing file contents (not timestamps)

Run the full pipeline: `make repro`
(This sets MLFLOW_TRACKING_URI=http://localhost:5000 automatically.)

### Where data lives

```
Your laptop  <──── git pull ────>  GitHub
                                      │
                                      │ (tiny .dvc pointer files)
GCS Bucket  <────  dvc push/pull ──── │
(actual CSV,                          │
 pkl files)                        dvc.lock
                                   (exact hashes)
```

**Key insight**: Git only stores tiny text files that say "the model is at hash abc123 in GCS." The actual 19MB model lives in GCS. Anyone who clones the repo and runs `dvc pull` gets the exact same data.

---

## 3. MLflow — Experiment Tracking + Model Registry

### Two jobs, one tool

**Job 1: Experiment tracking**
Every time you train a model, MLflow logs:
- Parameters: `n_estimators=100`, `model_type=RandomForestClassifier`
- Metrics: `auc_roc=0.8162`, `accuracy=0.7807`, `f1=0.5353`
- The model artifact (the actual .pkl file)

You can compare runs in the MLflow UI at `http://34.180.20.197:5000` → Experiments → churn-prediction.

**Job 2: Model Registry**
A named store for promoted models:

```
Run 1: auc=0.810  ──┐
Run 2: auc=0.816  ──┤──► "churn-model" registry
Run 3: auc=0.821  ──┘
            │
            │  aliases (tags, not versions)
            ▼
   @champion = v3 (auc=0.821)  ◄── churn-api loads THIS
   @challenger = v2 (auc=0.816)
```

The alias `@champion` is the single source of truth. The inference API always loads `models:/churn-model@champion`. When a better model is promoted, all pods automatically serve it on restart.

### Champion/challenger promotion

```
New model trained (e.g. v4, auc=0.830)
              │
              ▼
     Is 0.830 > current champion auc?
              │
         Yes  │  No
              │──────► Tag v4 as @challenger only
              │         (manually promote with: make promote)
              ▼
     Set @champion alias = v4
     Old champion becomes @challenger
              │
              ▼
     churn-api pods load v4 on next restart
```

### Why MLflow needs CloudSQL

On your local vind cluster, MLflow used SQLite (a single file). Every pod restart risked wiping it. On GKE, MLflow uses:
- **CloudSQL PostgreSQL** (`churn-mlflow`, db-f1-micro): the database. Persists through pod crashes, node replacements, cluster recreations.
- **GCS bucket** (`churn-mlflow-artifacts-project-8018ed81`): the model artifacts (the actual .pkl files, conda.yaml, etc.)

The `cloud-sql-proxy` container runs as a sidecar inside the MLflow pod — it creates a secure local TCP tunnel to CloudSQL without needing a public IP or credentials in environment variables.

---

## 4. Kubeflow Pipelines (KFP)

### What it is

KFP is a system for running ML pipelines on Kubernetes. Each step (preprocess, train, evaluate) runs in its own container/pod.

**Same pipeline as DVC, different execution model:**

```
DVC pipeline (local):                KFP pipeline (Kubernetes):

dvc repro                            Submit to KFP API
    ├── python preprocess.py         ↓
    ├── python train.py              Pod 1: preprocess container
    └── python evaluate.py             └── writes train.csv to GCS
                                     Pod 2: train container
                                       └── reads train.csv from GCS
                                           writes model to GCS
                                     Pod 3: evaluate container
                                       └── reads model + test.csv
                                           promotes @champion
```

**Key difference**: DVC runs sequentially on your laptop. KFP runs each step independently on the cluster — steps can run in parallel, on different hardware, with different resource limits.

### Why we have both

- **DVC**: for local development and CI/CD (fast iteration, no cluster required)
- **KFP**: for production training runs where you want each step containerized and auditable

### The pipeline file

`pipelines/churn_pipeline.py` defines the DAG using the KFP SDK. Running `make compile-kfp` compiles it to `pipelines/churn_pipeline.yaml` (a YAML file describing the pipeline graph). You upload that YAML to the KFP UI at `http://34.93.2.209`.

---

## 5. ArgoCD — GitOps Deployment

### What is GitOps?

Instead of running `kubectl apply` manually, you commit manifests to Git and ArgoCD continuously reconciles the cluster to match what's in Git.

```
You change k8s/deployment.yaml in Git
              │
              ▼
ArgoCD detects the change (polls every 3 min, or on git push hook)
              │
              ▼
ArgoCD applies it to the cluster
              │
              ▼
New pods roll out (old pods stay up during transition)
```

### The flow for a new model

```
1. make repro → new model trained → @champion updated in MLflow
2. CI builds new Docker image → pushes to ghcr.io
3. CI updates k8s/deployment.yaml with new image SHA → git commit [skip ci]
4. ArgoCD detects deployment.yaml changed → applies to cluster
5. New pods start → load @champion model from MLflow → serve predictions
```

**Why [skip ci]?** To prevent an infinite loop: CI pushes a commit → CI runs again → CI pushes another commit → ...

### What ArgoCD watches

ArgoCD watches the `k8s/` directory of the `main` branch of:
`https://github.com/my-neme-eh-jeff/customer_churn_CICD`

This means **committing any `.yaml` file to `k8s/` deploys it automatically**.

---

## 6. The CI/CD Pipeline

```
Every push to main:
─────────────────────────────────────────────────────────
Job 1: lint-and-test
  └── ruff check (lint) + ruff format + pytest (10 tests)

Job 2: pipeline (only on main branch pushes, after lint passes)
  ├── Authenticate to GCP via Workload Identity Federation
  ├── dvc pull  → download data/models from GCS
  ├── Start ephemeral MLflow server (local SQLite, throwaway)
  ├── dvc repro → run full pipeline (preprocess + train + evaluate)
  ├── dvc push  → upload new artifacts to GCS
  ├── docker buildx build --platform linux/amd64,linux/arm64 → ghcr.io
  └── Update k8s/deployment.yaml with new image SHA → push [skip ci]

Job 3: compile-kfp
  └── python pipelines/churn_pipeline.py → upload churn_pipeline.yaml artifact
```

**Known gap**: The CI pipeline uses an ephemeral MLflow (throwaway SQLite), not the real GKE MLflow. Champion promotion in CI goes to a database that gets deleted after the job. The GKE MLflow is only updated when you run `make repro` locally. This is documented in README as a known limitation.

---

## 7. Auto-Experiment Loop (autoresearch-inspired)

### What it does

`auto_experiment/auto_loop.py` runs an autonomous improvement loop:

```
while experiments_remaining:
    1. Read current code (train.py, params.yaml, preprocess.py)
    2. Read experiment history (what was tried, what worked)
    3. ┌────────────────────────────────────────────────┐
       │  Call Claude API                               │
       │  "Here's the current model code. Here's what  │
       │   was tried. Propose ONE specific change to    │
       │   improve AUC-ROC."                            │
       │                                                │
       │  Claude returns JSON with new file contents    │
       └────────────────────────────────────────────────┘
    4. Apply the proposed changes to train.py / params.yaml
    5. Run ruff --fix (lint the generated code)
    6. Run dvc repro (preprocess → train → evaluate)
    7. Read metrics.json → new AUC-ROC
    8. If AUC improved by ≥ 0.001:
         → git commit "auto-exp: hist_gradient_boost | 0.816 → 0.834"
         → evaluate.py auto-promotes to @champion
    9. Else:
         → git checkout -- (revert all changes)
   10. Log to MLflow "auto-experiment" experiment
   11. Append to auto_experiment/history.tsv
```

### Where it runs

Currently: **locally on your laptop**. Run with:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
make mlflow-kill && make mlflow   # port-forward to GKE MLflow
make auto-experiment               # 20 experiments, 2h budget
```

### Could it run in the cluster via KFP?

Yes, but it's more complex:
- The loop needs to modify Git files and commit → needs a git clone mounted as a volume
- The loop needs the Claude API key as a Kubernetes Secret
- Each "iteration" would be a KFP pipeline run
- Results accumulate across runs via the history.tsv in GCS

This is on the TODO list. For now, local is simpler and good enough for demos.

---

## 8. Branches — What to Merge

```
main
  │
  ├── feature/auto-experiment  ← ALREADY MERGED to main (April 3)
  └── feature/gke-production   ← ALREADY MERGED to main (April 4)
```

Both feature branches have already been merged into `main`. You should delete them locally to keep things clean:

```bash
git branch -d feature/auto-experiment
git branch -d feature/gke-production
```

And delete on remote:
```bash
git push origin --delete feature/auto-experiment
git push origin --delete feature/gke-production
```

---

## 9. Cost Analysis — How Long Will $300 Last?

### Current monthly cost (24/7 running)

| Component | Cost/month | Notes |
|-----------|-----------|-------|
| GKE Autopilot compute | ~$110 | ~2 vCPU + ~6GB RAM across all pods |
| CloudSQL db-f1-micro | $7.70 | PostgreSQL, always on |
| Load Balancer IPs (4) | $20 | $5/IP/month |
| Persistent Disks (PVCs) | $4 | MySQL + MinIO PVCs, 40GB total |
| GCS storage | $1 | Artifacts + DVC data |
| **Total** | **~$143/month** | |

**$300 free credits ÷ $143/month ≈ 2.1 months running 24/7.**

Be aware: GCP free trial also has a 90-day limit. Credits expire after 90 days even if unused.

### How to sleep the cluster (save ~$118/month)

When not using:
```bash
make cluster-sleep   # scale everything to 0 pods
```

Cost while sleeping: ~$25/month (CloudSQL + Load Balancer reserved IPs + PVCs).
$300 free credits ÷ $25/month = **12 months in sleep mode**.

### How to wake it up

```bash
make cluster-wake    # scale all deployments back up
# Wait ~3 min for pods to start
make bootstrap       # if model registry was cleared (rare)
# Check URLs:
make gke-urls
```

### Scale commands (added to Makefile)

```bash
make cluster-sleep   # scale everything to 0 replicas
make cluster-wake    # scale back up to normal replicas
make cluster-status  # see what's running and what's scaled down
```

---

## 10. How to Demo This

1. `make cluster-wake` (from your laptop, 3 min wait)
2. `make gke-urls` → shows live IPs
3. Demo flow:
   - Show **MLflow UI** (experiment runs, champion model)
   - Show **ArgoCD UI** (git sync, deployment status)
   - Show **KFP UI** (upload pipeline YAML, show DAG)
   - Show **Prediction API** (`curl http://34.180.37.1/predict`)
4. Optionally run `make auto-experiment-dry-run` to show Claude proposing a model improvement
5. `make cluster-sleep` when done

---

## 11. What's Not Working (Honest Assessment)

| Item | Status | Why |
|------|--------|-----|
| CI champion promotion | ❌ Fake | CI uses ephemeral MLflow; GKE MLflow not updated by CI |
| KFP pipeline submission | ⚠️ Manual only | `make kfp-run` works, CI doesn't auto-submit |
| Auto-experiment on cluster | ❌ Local only | Needs git commit access from within the cluster |
| TLS / HTTPS | ❌ HTTP only | No domain name; would need cert-manager + domain |
| ArgoCD auth | ⚠️ Default password | `Y6p9-krPfkEhm4Sd` — fine for demo, not prod |
| Multi-region / HA | ❌ Single zone | Free tier, demo only |

---

## 12. Quick Reference

```bash
# Connect to GKE cluster
make gke-connect

# See all pod statuses
make gke-status

# Get all live URLs
make gke-urls

# Run the ML pipeline locally (against GKE MLflow)
make mlflow-kill && make mlflow  # terminal 1
make repro                        # terminal 2

# Train + register champion
make bootstrap

# Run auto-experiment loop (needs ANTHROPIC_API_KEY in .env)
make auto-experiment-dry-run  # preview only
make auto-experiment           # run 20 experiments

# Compile KFP pipeline
make compile-kfp

# Submit KFP pipeline to GKE
make kfp-run

# Sleep / wake cluster
make cluster-sleep
make cluster-wake

# Run tests
make test

# Check that everything is working
curl http://34.180.37.1/health
curl -X POST http://34.180.37.1/predict -H "Content-Type: application/json" \
  -d '{"gender":"Female","SeniorCitizen":0,"Partner":"Yes","Dependents":"No",
       "tenure":12,"PhoneService":"Yes","MultipleLines":"No",
       "InternetService":"Fiber optic","OnlineSecurity":"No","OnlineBackup":"No",
       "DeviceProtection":"No","TechSupport":"No","StreamingTV":"No",
       "StreamingMovies":"No","Contract":"Month-to-month","PaperlessBilling":"Yes",
       "PaymentMethod":"Electronic check","MonthlyCharges":70.35,"TotalCharges":846.0}'
```
