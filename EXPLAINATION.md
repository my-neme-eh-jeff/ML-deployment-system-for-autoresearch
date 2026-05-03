# How this thing actually works (as of 2026-05-03)

Plain-language walkthrough of every component and what it does on every
trigger. Treats nothing as magic.

---

## TL;DR

You run `make autoresearch-run`. A pod wakes up, talks to Claude, edits some
files, kicks off a Kubernetes pipeline that retrains the model, decides
whether the result was better, and if it was, opens a pull request. Once
that PR merges, GitHub Actions rebuilds the inference Docker image and
ArgoCD swaps it into the cluster. The next `/predict` call is served by
the better model.

Everything except the initial `make` command (and, today, the PR merge
click) happens without you.

---

## The pieces

| Thing | Where | What it stores or does |
| --- | --- | --- |
| `data/ieee_cis.parquet` | DVC pointer in repo, content on GCS | The dataset (200K-row IEEE-CIS Fraud Detection subsample, 28 MB). |
| `configs/params.yaml` | repo | The "schema" — which CSV, target column, numeric/categorical features, model type, hyperparameters. The autoresearch loop edits this. |
| `src/preprocess.py` | repo | Reads `params.yaml + the parquet`, writes `data/processed/{train,test}.csv` and `data/processed/stats.json`. |
| `src/train.py` | repo | Reads train.csv + params, fits a sklearn pipeline, writes pickle, registers in MLflow as `classifier` v_N, writes `models/run_id.txt`. |
| `src/evaluate.py` | repo | Reads test.csv + the pickle, computes metrics, logs to MLflow on the train run_id. If the new AUC > existing `@champion` AUC, sets `@champion` to the new version. |
| `src/api.py` | repo, baked into `inference-api` Docker image | FastAPI server. At startup loads `models:/classifier@champion` from MLflow. Serves `/predict`. |
| `pipelines/pipeline.py` | repo | Compiles to `pipelines/pipeline.yaml`, the Kubeflow Pipelines DAG. Each step shells out to one of the `src/*.py` files. |
| `auto_experiment/auto_loop.py` | repo, baked into `autoresearch-loop` Docker image | The loop. Talks to Claude API, mutates files, submits KFP runs, opens PRs. |
| `auto_experiment/program.md` | repo | The system prompt the loop sends to Claude. |
| `MLflow` | GKE namespace `mlflow`, backed by CloudSQL Postgres + GCS artifacts | The model registry. Holds every classifier version + the `@champion` alias. |
| `Kubeflow Pipelines` | GKE namespace `kubeflow` | Runs the preprocess → train → evaluate DAG as Argo workflows. |
| `ArgoCD` | GKE namespace `argocd` | Watches the `k8s/` directory in this repo on `main`. When deployment.yaml changes, applies it. |
| `inference-api` Deployment | GKE namespace `inference` | 2 pods serving `/predict`. Image rebuilt by CI on every merge to main that touches `src/`. |
| `autoresearch-real-*` Job | GKE namespace `inference` | One-shot Job per `make autoresearch-run`. Lives until the loop finishes (~minutes), then cleans itself up. |
| GitHub App `ML-deployment-for-autoresearch` | GitHub | The bot identity the autoresearch loop uses to commit improvements + open PRs. |

---

## The flow on a single successful improvement iteration

When you run `make autoresearch-run AUTORESEARCH_N=5`, here's exactly what
happens for each iteration that ends up improving AUC:

1. **The Job pod is alive.** It pulled `dvc pull` to get the parquet locally,
   started `auto_experiment.auto_loop`, and is on iteration N of 5.
2. **Loop reads its state:** the current `params.yaml`, `train.py`,
   `preprocess.py`, `program.md`, `data/processed/stats.json["all_columns"]`
   (the catalog), and the last 10 entries from `auto_experiment/history.tsv`.
3. **Loop calls Claude** with that state and the `propose_experiment` tool.
   Claude returns a structured proposal: rationale, an experiment name, and
   full new contents of any of the three editable files.
4. **Loop applies the proposal** to disk (writes new `params.yaml` etc.).
5. **Loop submits a KFP run** with the mutated `params.yaml` content as the
   `params_yaml` pipeline argument.
6. **KFP runs the pipeline** in three sequential pods:
   - `preprocess` pod: `gcsfs` byte-copies the parquet from GCS to disk,
     runs `src/preprocess.py`. Writes `train.csv`, `test.csv`, `stats.json`
     to KFP artifact storage.
   - `train` pod: loads the train.csv, fits the sklearn pipeline, registers
     a new classifier version in **cluster MLflow** (not ephemeral —
     this is the real production registry), writes `run_id.txt`.
   - `evaluate` pod: loads test.csv + the pickle, computes metrics, logs
     them on the same MLflow run_id, and **if the new AUC > existing
     `@champion` AUC, sets `@champion` to the new version**. This is the
     promotion. It happens inside the KFP pipeline, automatically.
7. **Loop polls KFP** until the run state is `SUCCEEDED`. Then it queries
   cluster MLflow for the latest run on the `training` experiment and reads
   its AUC.
8. **If AUC improved by ≥ `min_improvement` (0.001)**, the loop:
   - Mints a 1-hour GitHub App installation token (PEM in GCP Secret
     Manager, fetched via Workload Identity).
   - Calls GitHub's GraphQL `createCommitOnBranch` with the new file
     contents — atomic multi-file commit, signed by the App, lands on
     `auto/run-<job-name>` in the CICD repo.
   - Logs the iteration to MLflow `auto-experiment` and to
     `auto_experiment/history.tsv`.
9. **If AUC did not improve**, the loop reverts the file changes locally
   (`git checkout --`) and continues. The MLflow registration of the new
   version still happened in step 6, but it's tagged `@challenger`, not
   `@champion`.
10. **At the end of the run** (after all N iterations), the loop opens a
    pull request from the `auto/...` branch back to `main`, with a body
    that summarizes iterations, total tokens, and estimated USD cost.

That's the autonomous part.

---

## What still needs a human (today)

| Step | Currently | Should be |
| --- | --- | --- |
| Initial trigger | `make autoresearch-run` | (intentionally manual) |
| PR review/merge | Claude clicks merge via GitHub App; or you click in the UI | Either: auto-merge after CI passes, OR a real human reviewer for PR-as-audit-log |
| `kubectl rollout restart` after a PR merge that only touched `params.yaml` (no `src/` change) | Manual — required because path-filtered CI didn't rebuild the api image, so deployment.yaml SHA didn't bump, so ArgoCD didn't roll | Real fix: bump a deployment annotation in the PR, or have inference-api poll MLflow for new versions |
| Force-setting `@champion` to reset demo state | Manual via `set_registered_model_alias(...)` | Shouldn't happen in production. Demo-only |

The third row is the real production gap. In a textbook setup, every model
promotion should trigger a deployment refresh. Today our CI image-SHA-bump
mechanism only fires when `src/` changes, so a "params-only" autoresearch
improvement can leave the cluster serving a stale `@champion` until something
else triggers a pod restart.

---

## What happens after a PR merges

Triggered automatically by the merge:

1. **GitHub Actions runs** on the merge commit.
2. **`lint-and-test` job** — ruff + pytest. Always.
3. **`pipeline` job** (only on push to main) checks which subtrees changed:
   - `training` filter (src, configs, dvc, data) → re-runs `dvc repro --force` against an ephemeral CI MLflow, then `dvc push` to GCS.
   - `api` filter (Dockerfile, src, pyproject) → builds + pushes a new
     `ghcr.io/.../inference-api:<sha>` Docker image.
   - `kfp` filter → builds + pushes `pipeline-kfp:latest`.
   - `autoresearch` filter → builds + pushes `autoresearch-loop:<sha>`.
4. **If api was rebuilt**, CI rewrites `k8s/deployment.yaml`'s image SHA and
   pushes it back to main as a `ci: ... [skip ci]` commit.
5. **ArgoCD** notices the new deployment.yaml within ~3 minutes, applies it,
   triggers a rolling restart of inference-api.
6. **New pods start.** Each one calls `mlflow.sklearn.load_model(
   "models:/classifier@champion")` against the cluster MLflow. They get
   whatever version `@champion` currently points at.
7. Old pods terminate; LoadBalancer IP serves predictions from the new pods.

When this goes wrong: if the merged PR only changed configs, none of the
docker images rebuilt → deployment.yaml didn't bump → ArgoCD didn't roll →
running pods still hold the old champion. That's the manual rollout-restart
gap above.

---

## Where the cluster state currently lives

| Question | Answer |
| --- | --- |
| Where's the model? | `models:/classifier@champion` in MLflow (CloudSQL + GCS-backed). Live IP `http://34.180.20.197:5000`. |
| Where's the dataset? | `gs://customer-churn-dvc-remote/raw/ieee_cis.parquet` (raw upload + DVC store) |
| Where's the inference container running? | `inference` namespace, 2 inference-api pods. LoadBalancer `http://34.180.37.1`. |
| Where do KFP runs live? | `kubeflow` namespace. UI at `http://34.93.2.209`. |
| What does ArgoCD watch? | The `k8s/` directory in this repo on `main`. Auto-syncs every ~3 min. |
| Where's the GitHub App PEM? | GCP Secret Manager, secret name `github-app-key`. |
| What service account talks to GitHub? | `autoresearch-sa` (KSA in inference) → bound via Workload Identity to GCP SA `autoresearch-sa@…`, which has `secretmanager.secretAccessor` and `storage.objectViewer`. |

---

## Cost model (per autoresearch run)

Estimated for a 5-iteration run on this dataset:

| Bucket | Cost |
| --- | --- |
| Anthropic API (Sonnet 4.6) | ~$0.10–0.20 |
| GKE pod compute (5× KFP runs × 3 pods × ~3 min) | sub-cent on free tier |
| GCS storage (parquet + DVC objects) | sub-cent |
| GitHub Actions (2 builds × ~5 min) | $0 (public repo) |

A 15-iter run extrapolates to ~$0.50–$1.00.

---

## Where the loop can fail (today's known gaps)

1. Claude proposes columns that don't exist → preprocess + train both warn-skip.
   Recoverable; iter loses some signal but doesn't crash. Catalog forwarding
   makes this rare.
2. KFP cluster has scheduling pressure → run can timeout at 900 s. Loop reverts.
3. CI run has a transient race on `git push` of deployment.yaml. Mitigated by
   `git pull --rebase + retry once`.
4. Cluster MLflow has a schema migration mid-flight → pods can't load model.
   Fixed by pinning MLflow image to a digest.
5. The "params-only PR doesn't roll the deployment" gap described above.
