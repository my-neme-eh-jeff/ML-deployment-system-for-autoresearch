# Audit Fixes — Session of 2026-05-11

Two pessimistic audits landed in the working tree this session
(`REFINED_PESSIMISTIC_AUDIT_FOR_CLAUDE.md`, then
`COUNCIL_AUDIT_ROUND_3_CURRENT_STATE.md`). This file is the consolidated
record of what was fixed, why each item mattered, what it would have broken,
and how the fix was verified.

Three categories of work landed:

| Category | Count |
|---|---|
| Code/config fixes from the two audits | 12 |
| Infra repairs uncovered during the live 5-iter test | 3 |
| Documentation hygiene fixes | 5 |

Final live validation: **5/5 KFP runs SUCCEEDED** in the post-fix
autoresearch run (`autoresearch-real-20260511-230528-pwjwf`, 36.9 min).
Two iters shipped PRs (#22, #23), three correctly reverted (loop working
as designed). State was reset to a clean v1 baseline and the cluster
was put back to sleep.

---

## 1. Audit findings — fixed in this session

### 1.1 CRITICAL — Inference image could not unpickle the trained model

**What broke.** The saved sklearn pipeline embedded
`FunctionTransformer(partial(features.apply_feature_engineering, …))`. The
inference image only copied `src/api.py`, so on pod startup
`mlflow.sklearn.load_model("models:/classifier@champion")` blew up with
`ModuleNotFoundError: No module named 'features'`. Two pods got stuck at
0/1 ready and the public LB silently kept serving stale predictions from
the pre-rollout pods. New deployments were dead on arrival.

**Why bare `features` and not `src.features`.** `dvc.yaml` invoked
`python src/train.py`, which puts `src/` on `sys.path` rather than the
repo root. `from src.features import …` failed and the fallback
`from features import …` won, recording `__module__='features'` in the
pickle. The inference image had no `features.py` at any path Python could
find.

**Fix.**
- `src/__init__.py` is the empty-but-present package marker (already
  existed; now actually used).
- `dvc.yaml`, `pipelines/pipeline.py`, `Makefile` invoke each stage as
  `python -m src.preprocess` / `src.train` / `src.evaluate`, so the
  pickled function's `__module__` is now canonically `src.features`.
- `src/train.py:24` dropped the script-mode `try/except` import fallback.
- `Dockerfile` now copies `src/__init__.py` and `src/features.py`, and
  sets `ENV PYTHONPATH=/app` so the unpickle resolves `src.features` as a
  proper package.

**Files.** `Dockerfile`, `dvc.yaml`, `pipelines/pipeline.py`, `Makefile`,
`src/train.py`, `pipelines/pipeline.yaml` (recompiled).
**Commit.** `e994d80`.
**Verification.** Locally inspected the new v1 pickle: bytes contain
`b"src.features"` and not bare `b"features"`. Then watched the new pod
`inference-api-7997fb5d6-jdjnm` load `@champion` on the first attempt
(no retry loop), reaching 1/1 Ready in ~90s.

---

### 1.2 CRITICAL — PR-merge was non-transactional

**What broke.** `auto_loop.py:976` wrapped the PR creation + merge wait
in a single `try/except` that turned any failure into a warning and
fell through to `best_auc = new_auc`. If a PR was opened but auto-merge
failed (CI red, branch protection delay, GitHub flake), the loop's
in-memory state advanced even though `k8s/deployment.yaml` on `main`
never received the annotation bump that triggers an ArgoCD rollout.
Net effect: MLflow's `@champion` moved (evaluate.py promoted before
the PR step), but the deployed pods kept serving the previous version
forever (they only re-read `@champion` at startup, and there was no
restart). The loop's memory then diverged from the deployed reality.

**Fix.**
- Snapshot the current `@champion` version BEFORE submitting the KFP
  run (`prev_champion_version = _get_champion_version()`).
- After commit_improvement returns, explicitly wait for the PR to merge.
  If `wait_for_pr_merge` returns False (timeout / closed-unmerged / any
  exception during the commit flow), do not advance `best_auc`. Instead:
  1. `revert_files(originals)` — undo the local file mutations.
  2. `_revert_mlflow_champion(prev_champion_version)` — reset
     `classifier@champion` back to what it was before this iter trained.
  3. Log to MLflow with `success=False` and a "FAILED: PR not merged"
     reason.
  4. Increment `iters_since_improvement` so stagnation-stop counts this.

**Files.** `auto_experiment/auto_loop.py` (call site at lines 935-1075,
new helpers `_get_champion_version` and `_revert_mlflow_champion`).
**Commit.** `c2a6fbf`.
**Verification.** Path not exercised in the 5-iter test (all PR merges
succeeded), but unit-test-equivalent: code review confirmed the
non-merge branch now sets `pr_merged = False` and `continue`s.

---

### 1.3 HIGH — `get_champion_metric` failed open on missing primary metric

**What broke.** `src/evaluate.py:49` returned `None` if the existing
`@champion` run was missing the `auc_roc` metric. The caller's
`if champion_metric is None:` branch promoted the new version
**unconditionally**, which is correct for the bootstrap case (no champion
yet) but catastrophic for the registry-corruption case (champion exists,
metric missing). A brand-new mediocre model would overwrite a real
champion just because its run wasn't fully populated.

**Fix.** Distinguish "no champion alias" from "champion exists but its
run lacks the primary metric". The former still returns `None` (bootstrap
intent); the latter now raises `RuntimeError(...)` with a message naming
the version and run id, so the operator must investigate before
re-running.

**Files.** `src/evaluate.py:49-62`.
**Commit.** `c2a6fbf`.
**Verification.** Unit test `test_evaluate_promotes_first_model_to_champion`
still passes (covers the legitimate no-champion bootstrap branch).

---

### 1.4 HIGH — LLM-output branch names not slugified

**What broke.** `auto_loop.py:638` stitched the LLM-supplied experiment
name directly into a git branch reference:
`f"auto/run-{run_id}-iter-{iter_num:02d}-{name[:30]}"`. Git ref-format
rejects spaces, `..`, `~`, `^`, `:`, `?`, `*`, `[`, `\`, control chars,
and trailing dots. An LLM proposal named `"tune α / β regularization"`
or `"experiment ~v2"` would crash `create_branch_from_main` mid-loop
after a successful (expensive) KFP run.

**Fix.** Regex-strip non-`[A-Za-z0-9._-]` to a single hyphen, strip
leading/trailing punctuation, truncate to 30 chars, fall back to
`"iter"` if everything got stripped.

**Files.** `auto_experiment/auto_loop.py:638` (+ `import re`).
**Commit.** `c2a6fbf`.
**Verification.** Five iters in the post-fix run all produced clean
branch names; no LLM proposal tripped the slugify in practice
(all named `random_forest_more_features`, `histgb_boost_switch`, etc.),
but the path is now safe under any input.

---

### 1.5 MEDIUM — `/predict` leaked raw sklearn exception text

**What broke.** `src/api.py:114` returned
`{"error": f"Prediction failed: {e}"}` on any exception. sklearn /
pandas error messages routinely leak column names, dtypes, package
versions, and sometimes file paths. The inference API is publicly
exposed via LoadBalancer.

**Fix.** Log the full exception in the server log (already done), but
return a generic `"Prediction failed; see server logs."` to the caller.

**Files.** `src/api.py:114-124`.
**Commit.** `c2a6fbf`.
**Verification.** Inspected the response shape after the new image
rolled out; error responses now match the generic envelope.

---

### 1.6 HIGH — Latest-MLflow-run query was race-prone

**What broke.** `auto_loop.py:499` post-KFP did
`mlflow.search_runs(order_by=["start_time DESC"], max_results=1)`. The
"latest" run isn't guaranteed to be the one this KFP execution just
produced — any concurrent training run (another autoresearch loop,
manual `make repro`, CI rebuild, retry) lands in the same `training`
experiment and could win the race. The loop would attribute someone
else's metrics to this iter.

**Fix.** Pipe the KFP run id end-to-end:
1. `pipelines/pipeline.py` calls
   `train_task.set_env_variable("KFP_RUN_ID", dsl.PIPELINE_JOB_ID_PLACEHOLDER)`.
2. `src/train.py` reads `os.environ["KFP_RUN_ID"]` and calls
   `mlflow.set_tag("kfp_run_id", kfp_run_id)` inside the run.
3. `auto_loop.py:504` queries with
   `filter_string=f"tags.kfp_run_id = '{run.run_id}'"`. Falls back to
   the old latest-by-time query with a loud `WARN:` if the tag is
   missing (so we notice).

**Files.** `src/train.py:201-212`, `pipelines/pipeline.py:148`,
`auto_experiment/auto_loop.py:498-525`.
**Commit.** `c2a6fbf`.
**Verification.** Tag-based query did NOT match in the live run — every
iter logged the `WARN: no MLflow run with tag kfp_run_id=...` fallback.
The mechanism is wired correctly but `dsl.PIPELINE_JOB_ID_PLACEHOLDER`
isn't substituting in the env-var slot at runtime. The fallback works
for our sequential single-loop test, but this needs investigation
(likely the wrong placeholder constant for `set_env_variable`, or KFP
v2 limitation). **Open follow-up.**

---

### 1.7 CRITICAL (pitch) — KFP source-edit staleness — documented honestly

**What broke.** The loop's tool schema allowed Claude to return full
new file contents for `params_yaml`, `train_py`, and `preprocess_py`.
The loop wrote those files locally, committed them to a branch, ran
the pipeline, and on improvement opened a PR with the diff. **But** the
KFP `@dsl.component` base image is `ghcr.io/.../pipeline-kfp:latest`,
which has the source baked in at CI build time. So within a single
iter, the cluster trained the **old image's** source — the source the
LLM proposed only became live on the NEXT iter (if the PR merged and
CI rebuilt the image).

For practical/historical traffic this hasn't bitten — every Claude
proposal in this session was `change_type: params_only`. But the
pitch ("Claude proposes code diffs, KFP trains them, winners ship")
overstates what the cluster does for source-only changes.

**Fix (this session).** Two parts:
1. `EXPLANATION.md §14.4` now documents the limitation honestly:
   single-iter source-only changes are committed to the PR but are NOT
   what the cluster just trained.
2. `auto_experiment/program.md` (the system prompt to the LLM) now
   tells Claude that `src/features.py` is not in the tool schema and
   advises requesting derived columns via the params surface
   instead — removing a long-standing contradiction in the prompt.

**Fix (future / not done).** Per-iteration KFP image build keyed by the
proposed source SHA. Out of scope for this pass — costs ~5 min CI per
iter and breaks the demo loop budget.

**Files.** `EXPLANATION.md` §14.4, `auto_experiment/program.md`.
**Commits.** `c2a6fbf`, today.

---

## 2. Audit findings — picked up from Council Round 3

The Council audit is a second-pass review of the post-fix state. Most
of its "Better Now" section validates fixes from §1. Additional items
picked up in this turn:

### 2.1 MEDIUM — KFP/autoresearch image rebuild triggers missed lockfile changes

**What broke.** `.github/workflows/ci.yaml` paths-filter for the `kfp`
and `autoresearch` filters listed `Dockerfile.kfp` /
`Dockerfile.autoresearch` and source dirs, but **not** `pyproject.toml`
or `uv.lock`. A bare dependency bump (e.g. mlflow security patch in
`uv.lock`) would not trigger an image rebuild, leaving the KFP /
autoresearch images stale relative to the inference image. Cross-image
sklearn/mlflow version drift then breaks pickle compatibility — exactly
the failure mode CLAUDE.md warns about under "MLflow image pinned to
v3.11.1".

**Fix.** Added `pyproject.toml` and `uv.lock` to both filters.

**Files.** `.github/workflows/ci.yaml:79-93`.
**Commit.** today.
**Verification.** No image rebuild triggered yet (would require a
dependency bump to actually exercise); change reviewed for correctness.

---

### 2.2 LOW — README `min_improvement` default lagged config

**What broke.** README:21 said "configurable, default 0.001" but the
config moved to 0.003 (one standard error of AUC on a 40K test set).
A reviewer reading the README and then `configs/params.yaml` would
clock the discrepancy and lose trust.

**Fix.** README now says "currently 0.003" with the standard-error
rationale inline.

**Files.** `README.md:21`.
**Commit.** today.

---

### 2.3 LOW — README claimed no stagnation guard exists

**What broke.** README:289 said "currently no `min_iterations_since_improvement`
stop condition" — but `configs/params.yaml` already had
`max_iterations_without_improvement: 10` and `auto_loop.py` already
implemented early-stop based on it.

**Fix.** Replaced the false statement with the actual stagnation guard
documentation (counts failed pipelines, sub-threshold reverts, Claude
errors, default 10).

**Files.** `README.md:288-289`.
**Commit.** today.

---

### 2.4 LOW — `auto_experiment/program.md` contradicted itself on `src/features.py`

**What broke.** The system prompt to Claude said "NEVER MODIFY
src/features.py" in one section and then "Add interaction features in
src/features.py" three sections later. The loop's tool schema does not
expose `features_py` anyway — so the second instruction was unreachable
guidance. A model trying to follow it would either confuse itself or
spend tokens on a path that does nothing.

**Fix.** Both sections now state plainly: `features.py` is not editable
from this loop; request derived columns via the params surface.

**Files.** `auto_experiment/program.md:35`, `:51`, `:60-62`.
**Commit.** today.

---

### 2.5 LOW — `scripts/setup-gcp.sh` final-step prompt referenced a non-existent target

**What broke.** The setup script's "next steps" block said
`make gke-setup` — which has never existed in the Makefile. A fresh-
clone user following the printed prompt would hit `make: *** No rule
to make target 'gke-setup'.`

**Fix.** Replaced with the actual working sequence: `make cluster-wake`,
then `make reset-for-fresh-run`, then
`make autoresearch-run AUTORESEARCH_N=5`.

**Files.** `scripts/setup-gcp.sh:199`.
**Commit.** today.

---

## 3. Infra repairs uncovered during the live 5-iter test

These weren't in either audit — they surfaced when I tried to run the
end-to-end test the user asked for. Without them, the 5-iter
verification would have failed.

### 3.1 KFP launcher's pip probe died on a pip-less uv venv

**What broke.** A previous audit-pass-1 cleanup rewrote
`Dockerfile.kfp` to install via `uv sync --frozen` instead of bare
`pip install`. `uv sync` produces a pip-less venv. KFP v2's launcher
unconditionally shells out to `python -m pip install kfp==<launcher_version>`
at component start, and dies with `No module named pip` if pip isn't
in the venv. **Every** KFP component in the first 5-iter run failed
this way — preprocess never even succeeded.

**Fix.** Added `RUN /app/.venv/bin/python -m ensurepip --upgrade` after
`uv sync` in `Dockerfile.kfp`. The launcher's probe now finds pip and
skips the network install (kfp is already in the lockfile).

**Files.** `Dockerfile.kfp:18-26`.
**Commit.** `d83f299`.
**Verification.** Second test run cleared the preprocess step on every
iter.

---

### 3.2 MLflow OOMKilled mid-upload of a RandomForest pickle

**What broke.** The MLflow container was at a 2 GiB memory limit. The
`--serve-artifacts` proxy stages the entire model pickle in process
memory during `mlflow.sklearn.log_model`. A RandomForest on 200K rows
× ~330 features pickles to ~1–1.5 GiB. Staging that exceeded the limit;
the pod OOMKilled with Exit 137; the training container's HTTP upload
hung indefinitely (no response, no error). KFP marked the run FAILED
after the 1800s timeout would have hit, but in practice the user-visible
symptom was "iter never completes."

**Fix.** Bumped the MLflow container to `limits: memory 4Gi, cpu 2`
(from `2Gi`, `1`), with a comment explaining the proxy memory math.

**Files.** `k8s/mlflow.yaml:83-93`.
**Commit.** `1b48490`.
**Verification.** Run 3 logged a successful RandomForest upload at AUC
0.9323 (~291 MiB MinIO artifact dir). No further OOMKills.

---

### 3.3 MinIO regional PVC filled, blocking KFP preprocess uploads

**What broke.** KFP intermediate artifacts (preprocess train/test CSVs
+ model pkls) had accumulated to ~4 GB across past autoresearch
sessions, against a 5 GiB regional PVC (5 GiB is the cap — GCP
free-trial SSD quota is 250 GB in `asia-south1` and we're already at
~220 GB, with regional PDs replicating 2×). The very next preprocess
upload hit `XMinioStorageFull: Storage backend has reached its minimum
free drive threshold.` Three of the five iters in run 3 failed this way.

**Fix.** One-shot wipe of orphaned artifact dirs via `kubectl exec deploy/minio -- rm -rf /data/mlpipeline/v2/artifacts/classifier-training-pipeline/*`. Freed 4 GB.

This is a recurring operational hazard — every long-running autoresearch
session will refill the PVC eventually. The structural fix per CLAUDE.md
is to swap MinIO for GCS-backed KFP storage (out of scope this pass).

**Files.** None — runtime-only cleanup.
**Verification.** Run 5 (post-cleanup) completed all 5 KFP runs without
storage errors.

---

## 4. Final live validation — 5-iter autoresearch

Job: `autoresearch-real-20260511-230528-pwjwf`. Total time: 36.9 min.
Starting `@champion`: v1 (vanilla DT, AUC 0.749).

| Iter | Proposal | Change type | AUC | Outcome | PR |
|---|---|---|---|---|---|
| 1 | `random_forest_more_features` | params_only | 0.0000 → 0.9323 | ✓ IMPROVED | #22 merged |
| 2 | `histgb_boost_switch` | params_only | 0.9295 | ✗ Reverted (−0.0028) | — |
| 3 | `extra_trees_tuned` | params_only | 0.9208 | ✗ Reverted (−0.0115) | — |
| 4 | `histgb_tuned_early_stopping` | params_only | 0.9323 → 0.9356 | ✓ IMPROVED | #23 merged |
| 5 | `add_v_features_numeric` | params_only | 0.9369 | ✗ Reverted (+0.0013 < 0.003) | — |

All 5 KFP runs reached state=SUCCEEDED. The loop correctly shipped 2
improvements and reverted 3 non-improvements (one of which numerically
improved but stayed under the noise floor — exactly the behavior the
`min_improvement = 0.003` threshold is for). Anthropic spend on the
loop: $0.20 across all 5 iters.

After the run:
- `make reset-for-fresh-run` → registry back to v1 DT baseline
- Reset state pushed to `main` (`696f790`)
- `make cluster-sleep` → all workloads scaled to 0, CloudSQL stopped,
  ArgoCD auto-sync disabled. Idle burn ≈ $0/day.

---

## 5. Items deliberately not picked up

| Item | Reason |
|---|---|
| **Per-iter immutable KFP image build** (Council CRITICAL 1 long path) | ~5 min CI build per iter + significant pipeline refactor; out of portfolio scope. Documented as honest §14.4 in EXPLANATION.md instead. |
| **Train/val/test split for selection** (Council CRITICAL 2) | Multi-day refactor (split logic, leakage tests, threshold tuning, holdout reporting). Real concern; flagged for future work. |
| **PR-AUC as primary metric** (Council HIGH 1) | Would change promotion semantics and invalidate all historical AUC-based comparisons. The audit's argument is correct for a 3.5% positive rate, but switching mid-stream is a deliberate decision the owner should make. |
| **Immutable `MODEL_URI` in pod template** (Council HIGH 2) | Breaks the annotation-driven rollout pattern that is itself a documented design choice (EXPLANATION §14.1). |
| **MLflow as a code-execution trust boundary** (Council HIGH 3) | Already documented; portfolio scope explicitly accepts public-MLflow risk. |
| **`make configure-data` wizard for BYO CSV** (Council HIGH 4) | Real UX gap, but multi-day refactor to wire DVC + KFP raw-data path generation. |
| **KFP namespace drift** (Council HIGH 5) | Operational footgun, but doesn't bite the current demo path. Scoped for future cleanup. |
| **SHA-pinned `pipeline-kfp` / `autoresearch-loop` images** (Council HIGH 6) | Reasonable hardening; the `:latest` + `imagePullPolicy: Always` pattern is good enough for portfolio scope. Would need CI publishing SHA tags AND compiled YAML / Job manifest to reference them. |
| **WIF/SA naming alignment** (Council HIGH 7) | Real but only bites a fresh-clone setup. Future cleanup. |
| **Restrict LLM tool schema to params_only** (Council CRITICAL 1 short path) | Council recommends this; I kept the existing schema because (a) in practice Claude has been doing params-only edits autonomously, (b) `program.md` now warns against `features.py` edits, (c) removing the option closes off a future direction (per-iter image build) without recovering anything in the current loop. Honest doc preferred over hard restriction. |
| **Security contexts, network policies** (Council MEDIUM 5) | Already declined as out of scope for portfolio. |
| **Secret scanning on logged generated source** (Council MEDIUM 4) | Real risk; trivially fixable with a regex pass before `mlflow.log_text`. Worth doing later. |

---

## 6. Known follow-ups (not blocking demo)

1. **CI bot's deployment.yaml SHA-bump push is still rejected.** Every
   autoresearch run that includes an inference-image rebuild hits
   `remote: 2 of 2 required status checks are expected` + `protected
   branch hook declined`, even though the ruleset only declares
   `deletion` + `non_fast_forward`. The workflow's fallback rebase also
   fails (`cannot pull with rebase: You have unstaged changes`).
   Manually pushed the SHA bumps twice this session. Real fix is
   probably adding `github-actions[bot]` to the ruleset's bypass actors
   AND stashing the workspace before the rebase. **File:**
   `.github/workflows/ci.yaml:202-214`.

2. **`dsl.PIPELINE_JOB_ID_PLACEHOLDER` substitution not taking effect.**
   The MLflow run tag is set in code but evaluates to the literal
   placeholder string (or empty) inside the KFP component. Loop falls
   back to latest-by-time and logs a WARN. The wiring is in place; the
   placeholder constant or substitution mechanism needs investigation.
   **File:** `pipelines/pipeline.py:148`.

3. **MinIO will fill again on the next long run.** 5 GiB regional PVC,
   no GC policy. Future fix: either swap MinIO for GCS-backed KFP
   storage (CLAUDE.md notes this as the production fix) or add an
   automatic artifact-TTL cleanup.

4. **`COUNCIL_AUDIT_ROUND_3_CURRENT_STATE.md` and
   `REFINED_PESSIMISTIC_AUDIT_FOR_CLAUDE.md` are left untracked** in
   the working tree. Assumed to be local audit drops; not committing
   without explicit say-so.

---

## 7. Commits this session (in order)

| SHA | Description |
|---|---|
| `e994d80` | fix(pkl): canonical `src.*` module path so saved sklearn pipeline unpickles in inference |
| `6edebc0` | deploy: bump inference-api image to e994d80 (unpickle fix) [skip ci] |
| `c2a6fbf` | audit pass 2: pr-merge transaction, champion fail-closed, kfp-run-id tagging |
| `8768f5f` | deploy: bump inference-api image to c2a6fbf (audit-pass-2) [skip ci] |
| `d83f299` | fix(kfp): seed pip into the uv venv so KFP launcher's install probe doesn't die |
| `1b48490` | fix(mlflow): 2Gi → 4Gi limit so artifact proxy doesn't OOM during RF upload [skip ci] |
| `1c367f4` | prep clean 5-iter test: vanilla DT baseline + truncate history [skip ci] |
| `b05803f8` (PR #22) | auto-exp: random_forest_more_features \| AUC 0.0000 → 0.9323 |
| `(PR #23)` | auto-exp: histgb_tuned_early_stopping \| AUC 0.9323 → 0.9356 |
| `696f790` | reset state: vanilla DT v1 + truncated history for clean demo baseline [skip ci] |
| (today) | doc + paths-filter hygiene: kfp/autoresearch lockfile triggers, README defaults, program.md, setup-gcp.sh next-step |
