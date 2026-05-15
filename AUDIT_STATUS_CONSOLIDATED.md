# Audit Status — Consolidated View

Single source of truth across the two pessimistic-audit passes spawned this
weekend (`REFINED_PESSIMISTIC_AUDIT_FOR_CLAUDE.md` + the council follow-up
`COUNCIL_AUDIT_ROUND_3_CURRENT_STATE.md`).

For each finding: **status** (✅ done / 🔜 pick-up next / 🟰 redundant —
already declined / 📝 doc-only honest scope), **severity**, where it lives,
why it matters in plain English, and what was done about it.

A "redundant" tag means the item was raised again by a later pass after we
had already explicitly decided not to do it (or had documented around it).
Listed once with the rationale, not re-litigated.

---

## TL;DR

| Bucket | Count |
|---|---|
| ✅ Done this session | 17 |
| 🔜 Pick up next | 12 |
| 🟰 Redundant / already declined | 9 |
| 📝 Doc-honest-scope (no code fix) | 3 |

5-iter live validation: **5/5 KFP runs SUCCEEDED**
(`autoresearch-real-20260511-230528-pwjwf`, 36.9 min, 2 PRs shipped, 3
clean reverts). Cluster currently asleep, registry reset to v1 baseline.

---

## ✅ DONE this session

| # | Severity | Audit | What broke (plain English) | Fix | Files |
|---|---|---|---|---|---|
| 1 | CRITICAL | Both | Inference pod couldn't unpickle the model. `FunctionTransformer` referenced bare `features` module; image only had `src/api.py`. Every new pod 503'd forever. | `src` is now a proper package; pipeline runs as `python -m src.*`; Dockerfile ships `src/__init__.py` + `src/features.py` + `PYTHONPATH=/app`. | `Dockerfile`, `dvc.yaml`, `pipelines/pipeline.py`, `Makefile`, `src/train.py` (`e994d80`) |
| 2 | CRITICAL | Both | PR-merge wasn't transactional: loop bumped `best_auc` even when the PR failed to merge. MLflow `@champion` moved but `k8s/deployment.yaml` didn't, so pods kept serving the old version. | Snapshot `@champion` before iter. On PR-not-merged → revert local files AND revert `@champion`. Iter logged as failed; counts toward stagnation guard. | `auto_experiment/auto_loop.py` (helpers `_get_champion_version`, `_revert_mlflow_champion`) (`c2a6fbf`) |
| 3 | HIGH | Both | `get_champion_metric` returned `None` when champion existed but lacked the primary metric → next mediocre model overwrote the real champion unconditionally. | Fail closed: raise `RuntimeError` if champion exists but `auc_roc` is missing. Bootstrap (no champion) still returns `None`. | `src/evaluate.py:49` (`c2a6fbf`) |
| 4 | HIGH | Both | LLM-supplied branch names spliced raw into a git ref. One proposal with spaces / `α/β` / `~v2` would crash `create_branch_from_main` mid-loop after an expensive KFP run. | Regex slugify to `[A-Za-z0-9._-]`, strip leading/trailing punctuation, truncate to 30 chars, fallback `"iter"`. | `auto_experiment/auto_loop.py:638` (`c2a6fbf`) |
| 5 | MEDIUM | Both | `/predict` echoed raw sklearn/pandas exception text to callers — leaks column names, dtypes, package versions, file paths through a public LB. | Generic error envelope `"Prediction failed; see server logs."`. Full detail still logged server-side. | `src/api.py:114` (`c2a6fbf`) |
| 6 | HIGH | Both | Loop queried "latest MLflow run by start_time" after KFP completion — concurrent training (other loops, manual repro, CI, retries) would get misattributed. | KFP injects `KFP_RUN_ID`, `train.py` tags the MLflow run with it, loop queries `tags.kfp_run_id = '<this run>'`. Falls back to latest-by-time with loud WARN. | `src/train.py`, `pipelines/pipeline.py`, `auto_experiment/auto_loop.py` (`c2a6fbf`) — see also #29 below for placeholder bug. |
| 7 | LOW | Both | README `min_improvement` default lagged real config (0.001 vs actual 0.003). | README says "currently 0.003" with standard-error rationale. | `README.md:21` (`57a3141`) |
| 8 | LOW | Round 3 | README claimed no stagnation guard exists. It does (`max_iterations_without_improvement: 10`). | README documents the real guard semantics. | `README.md:288-289` (`57a3141`) |
| 9 | LOW | Round 3 | `auto_experiment/program.md` contradicted itself: "NEVER MODIFY src/features.py" then "Add interaction features in src/features.py". Tool schema can't return `features_py` anyway. | Both spots now say plainly: features.py is not editable from this loop. Request derived columns via params instead. | `auto_experiment/program.md:35, :51, :60-62` (`57a3141`) |
| 10 | LOW | Round 3 | `scripts/setup-gcp.sh` final-step printed `make gke-setup` — target has never existed. Fresh-clone user breaks here. | Printed sequence now: `cluster-wake` → `reset-for-fresh-run` → `autoresearch-run`. | `scripts/setup-gcp.sh:199` (`57a3141`) |
| 11 | MEDIUM | Round 3 | `ci.yaml` paths-filter for `kfp`/`autoresearch` didn't include `pyproject.toml` + `uv.lock`. A bare dependency bump would leave those images stale relative to inference. | Added both files to both filters. | `.github/workflows/ci.yaml:79-93` (`57a3141`) |
| 12 | CRITICAL→📝 | Both | KFP doesn't execute per-iter source edits: components run baked image source. Source-only proposals show up in the PR but aren't what the cluster trained. | `EXPLANATION.md §14.4` documents this honestly. Per-iter image build deferred (out of portfolio scope). | `EXPLANATION.md §14.4` (`c2a6fbf`) |
| 13 | infra | (live test) | First 5-iter run: every KFP component died with `No module named pip`. KFP v2 launcher shells `python -m pip install kfp==<v>` regardless; `uv sync` builds pip-less venv. | `ensurepip --upgrade` after `uv sync` in `Dockerfile.kfp`. | `Dockerfile.kfp:18` (`d83f299`) |
| 14 | infra | (live test) | MLflow OOMKilled mid-pickle-upload during RandomForest training. `--serve-artifacts` proxy stages full ~1.3 GiB pickle in process memory; 2Gi limit too tight. | Bumped MLflow container limits to `cpu 2, memory 4Gi`. | `k8s/mlflow.yaml:83-93` (`1b48490`) |
| 15 | infra | (live test) | MinIO PVC (5Gi cap from GCP free-trial SSD quota) had filled with ~4 GB of orphaned KFP artifacts across past sessions. Preprocess uploads hit `XMinioStorageFull`. | One-shot `kubectl exec deploy/minio -- rm -rf …/classifier-training-pipeline/*`. | Runtime-only (no code change) |
| 16 | infra | (today) | Loop's existing retry/backoff wasn't documented in tabular form for review. | Audit summary in `AUDIT_FIXES_SUMMARY.md` documents the Anthropic + KFP retry semantics added during audit pass 1 (5x exponential w/ jitter, classified retryable vs fail-fast). | `auto_experiment/auto_loop.py` (audit pass 1, pre-this-session) |
| 17 | 📝 | Round 3 | Council reframed the pitch question: "Claude proposes code diffs, KFP trains them, winners ship" overstates what happens for source-only changes. | Pitch language softened in EXPLANATION §14.4 to honest-scope. | `EXPLANATION.md` (`c2a6fbf`) |

---

## 🔜 PICK UP NEXT

Ordered roughly by impact-per-hour. Top three are the highest-leverage.

### 1. **HIGH — `dsl.PIPELINE_JOB_ID_PLACEHOLDER` not substituting at runtime**
*(Council §1.3 residual)*

The kfp_run_id tag mechanism is wired correctly in source, but every iter
in the live test logged the WARN fallback:
`no MLflow run with tag kfp_run_id='<uuid>' — falling back to latest run`.
Means the placeholder constant we used in `set_env_variable` isn't being
substituted by the KFP runtime. Loop still works because of the fallback,
but the race-prone path is what's actually getting exercised.

**Files:** `pipelines/pipeline.py:148`.
**Fix sketch:** verify the right placeholder for env vars in KFP v2 — may
be a literal `{{$.pipeline_job_id}}` string interpolation rather than a
`dsl.*` constant. Test locally with a tiny pipeline and assert the env var
arrives non-empty inside the component.

### 2. **HIGH — CI bot's `k8s/deployment.yaml` SHA-bump push is rejected**

Every CI run with an inference rebuild fails the bot's push with
`remote: 2 of 2 required status checks are expected` + `protected branch
hook declined`, even though the repo ruleset only declares `deletion` +
`non_fast_forward`. Workflow's fallback rebase also fails:
`cannot pull with rebase: You have unstaged changes`. Manually pushed
twice this session; demo can't depend on me doing this every time.

**Files:** `.github/workflows/ci.yaml:202-214`.
**Fix sketch:** (a) add `github-actions[bot]` to the ruleset's bypass
actors via the GitHub UI, AND (b) `git stash` the workspace before
`git pull --rebase`.

### 3. **HIGH — Fallback-to-latest in #6 should fail closed, not silently succeed**
*(Council §1.3 residual)*

Right now when the tag query is empty, we log a WARN and grab whatever's
latest. That's exactly the race the fix was supposed to close. Should
either (a) fail the iter outright unless an explicit
`AUTORESEARCH_ALLOW_LATEST_FALLBACK=1` env is set, or (b) only fall back
if no other in-flight runs exist in the experiment.

**Files:** `auto_experiment/auto_loop.py:510-518`.

### 4. **HIGH — `history.tsv` "improved" row written before merge confirmation**
*(Council §1.4 residual)*

`log_to_tsv(... "improved" ...)` runs before `commit_improvement` and the
PR merge wait. If the PR fails and we roll back `@champion` + local files
(fix #2), the `history.tsv` row stays as `improved`. Subsequent iters see
a row that says we shipped something we didn't.

**Files:** `auto_experiment/auto_loop.py:995, :1047, :1061`.
**Fix sketch:** delay the TSV write until after the merge has confirmed,
or rewrite the row to `outcome=failed` on rollback.

### 5. **HIGH — BYO CSV claim is overbroad for the cluster path**
*(Council HIGH 4, Persona 6 CRITICAL 1)*

README/EXPLANATION present this as "plug-and-play any binary CSV." Reality:
local DVC mode is schema-driven, but the cluster path still defaults to
`gs://customer-churn-dvc-remote/raw/ieee_cis.parquet`. A real user
following `make setup` has no guided path to upload their CSV to GCS, set
the schema, and re-point KFP at it.

**Files:** `README.md:3, :174`, `pipelines/pipeline.py:123-125`,
`dvc.yaml:5`, `scripts/setup.py`.
**Fix sketch:** add `make configure-data` that prompts for CSV path,
uploads to GCS, rewrites `dvc.yaml` + KFP default arg, and validates the
schema. OR narrow the README claim to "schema-driven code; cluster wired
to IEEE-CIS for the demo" if that's the honest version.

### 6. **HIGH — Generated source logged raw to MLflow with no redaction**
*(REFINED Persona 10 MEDIUM, Round 3 MEDIUM 4)*

Attempted iter files are logged verbatim to MLflow params/artifacts. With
public MLflow UI, an accidental secret in generated code or error text
becomes a public artifact. Trivial regex pass before
`mlflow.log_text`/`log_params` would catch `^(AWS|GH|ANTHROPIC|GCP)_(TOKEN|KEY|SECRET)`,
`pem`/`p12`/`json` blobs that look like credentials, env-var-style lines.

**Files:** `auto_experiment/auto_loop.py:793-798`.

### 7. **HIGH — Live source refresh from `raw.githubusercontent.com/main` bypasses image provenance**
*(REFINED Persona 10 HIGH)*

`scripts/run-autoresearch.sh` curls 7 files from `…/main` at pod start.
"What was on main when the pod started" can differ from "what was in the
image that was reviewed." A bad merge to main (or compromised account) is
arbitrary code execution inside autoresearch with WI access to Anthropic
key + GitHub App PEM.

**Files:** `scripts/run-autoresearch.sh:14-32`.
**Fix sketch:** fetch by commit SHA passed via env (`REFRESH_FROM_SHA`)
and verify checksum, or remove the runtime refresh entirely (rebuild image
on every relevant change — which paths-filter now does for autoresearch
since fix #11).

### 8. **HIGH — Autoresearch Job has no Kubernetes-enforced active deadline**
*(REFINED Persona 9 CRITICAL 2)*

`activeDeadlineSeconds` is unset. The loop has a `--hours` budget in code,
but a hung KFP submit or stuck Anthropic call could keep the pod alive
indefinitely. Combined with Anthropic spend, this is a runaway risk.

**Files:** `jobs/autoresearch-job.yaml:8`.
**Fix:** `spec.activeDeadlineSeconds: 7200` (2h cap, matches the existing
default).

### 9. **MEDIUM — `cluster-wake` swallows real failures**
*(REFINED Persona 9 HIGH 1)*

Every `kubectl scale` is `|| true`; the URL print at the end runs even if
MLflow / CloudSQL never came up. Operator thinks the cluster is awake; the
first real call 503s.

**Files:** `Makefile cluster-wake target`.
**Fix sketch:** capture exit codes of each scale; require readiness probes
on the critical workloads before printing URLs; print a clear FAILED line
on any non-recovery.

### 10. **MEDIUM — `make demo` prints inconsistent ArgoCD access path**
*(Round 3 Meta — demo command reliability)*

Prints localhost HTTPS URL, then immediately says "access ArgoCD via
public HTTP LoadBalancer with no port-forward." One coherent path per
service.

**Files:** `Makefile:296-303`.

### 11. **MEDIUM — Test strategy gap: nothing covers the API contract or rollback path**
*(Round 3 Meta — test strategy gap, Hiring Manager HIGH 2)*

Tests cover preprocess/train/evaluate only. No FastAPI test for `/predict`
response shape, no test for `commit_improvement` rollback branch (fix #2),
no test for branch slugify (fix #4). The differentiator code paths have
zero CI coverage.

**Files:** `tests/`.

### 12. **LOW — Public docs still contain demo-coaching language + live IPs**
*(Round 3 Meta — public-doc hygiene)*

`EXPLANATION.md` has "don't react on camera," live LoadBalancer IPs, and
private prep notes. Readable as private notes leaking into public docs.

**Files:** `EXPLANATION.md` §17 demo plan area.

---

## 🟰 REDUNDANT — already explicitly declined, raised again

The audits keep relitigating these. Listed once with the reason; not
moving on them this pass.

| Finding | Audit refs | Why declined |
|---|---|---|
| **Per-iteration immutable KFP image build** (so the LLM's source diff is what KFP actually trains) | REFINED #2, Round 3 CRITICAL 1 long path | ~5 min extra CI per iter destroys the demo loop budget. Documented honestly in EXPLANATION §14.4 instead (the LLM has been doing params-only edits in practice anyway). |
| **Train/val/test 3-way split + holdout reporting** | REFINED Persona 7 CRITICAL 1, Round 3 CRITICAL 2 | Real concern, but multi-day refactor (split logic, leakage tests, threshold tuning, holdout reporting). Flagged for future work; doesn't block demo. |
| **PR-AUC as the primary promotion metric (instead of AUC-ROC)** | REFINED Persona 7 HIGH 1, Round 3 HIGH 1 | Changes promotion semantics and invalidates all historical AUC comparisons. Owner's call to switch mid-stream; not a defect. |
| **Immutable `MODEL_URI=models:/classifier/<version>` in pod template (no more mutable `@champion`)** | REFINED #5 + Persona 3 CRITICAL 1, Round 3 HIGH 2 | Breaks the annotation-driven rollout pattern documented as a design choice in `EXPLANATION §14.1`. The annotation IS the version contract for the demo. |
| **MLflow registry write as a code-execution trust boundary** | REFINED Persona 10 CRITICAL 1, Round 3 HIGH 3 | Portfolio scope explicitly accepts public-MLflow risk. Will not be running prod inference on this MLflow. |
| **Pod `securityContext` / non-root / network policies** | REFINED Persona 1 HIGH 2, Round 3 MEDIUM 5 | On the user's explicit "out of scope for portfolio" list. |
| **Repo-scoped WIF / aligned `github-cicd` vs `churn-cicd` SA naming** | REFINED Persona 5 MEDIUM 1, Round 3 HIGH 7 | Operational footgun only on fresh-clone setup. Will be cleaned when setup script is overhauled for #5. |
| **Single-zone GKE / no CloudSQL backups / single MLflow replica DR** | REFINED scope-ack list, Round 3 Meta — DR caveat | Portfolio scope. The DR caveat doc-line is worth adding (low-effort), but the underlying fix isn't. |
| **Hardcoded GCP project ID / bucket names in manifests** | REFINED Persona 3 HIGH 2, Round 3 HIGH (multiple) | Same as above — portfolio scope; setup overhaul (#5) will template these. Not pulling them out as a standalone fix. |

---

## 📝 DOC-HONEST-SCOPE — no code change

| Finding | Audit refs | What was done |
|---|---|---|
| Source-edit boundary in KFP | REFINED #2, Round 3 CRITICAL 1 | EXPLANATION §14.4 added (`c2a6fbf`). |
| "Production-safe" pitch overclaim | REFINED Persona 8 CRITICAL 1, Round 3 hiring lens | EXPLANATION §16 (pitching) and the README "Current scope" both already frame as portfolio. §14.4 closes the remaining gap. |
| CI pushing image bumps directly to `main` (not via the autoresearch PR flow) | REFINED Persona 5 CRITICAL 2, Round 3 MEDIUM 3 | The claim is "autoresearch experiment changes are PRs" — not "every change is a PR." The CI image-bump push is currently broken anyway (pick-up #2); when that's fixed, the README qualifier should also stay accurate. |

---

## Operational follow-ups (not strictly audit items)

| Item | Severity | Notes |
|---|---|---|
| **MinIO will refill on the next long run** | known op | 5Gi regional PVC + no GC. Periodic `kubectl exec rm -rf` works; structural fix is GCS-backed KFP store (out of scope). |
| **`COUNCIL_AUDIT_ROUND_3_CURRENT_STATE.md` and `REFINED_PESSIMISTIC_AUDIT_FOR_CLAUDE.md` left untracked** | trivia | Assumed to be local audit drops; not committing without your say-so. |
| **`AUDIT_FIXES_SUMMARY.md`** | trivia | Already committed at repo root (`57a3141`). This file (`AUDIT_STATUS_CONSOLIDATED.md`) is the prioritized successor. |

---

## Suggested next-session order

If you want a tight 1-2 hour session that buys the most credibility:

1. Pick up **#1** (placeholder fix) — closes the kfp_run_id race for real instead of via fallback.
2. **#3** (fail closed on missing tag) — pair with #1; the two together turn the WARN line green.
3. **#4** (history.tsv rewrite on rollback) — closes the last gap in the transactional-PR fix; small diff.
4. **#8** (`activeDeadlineSeconds`) — one-line manifest change; closes runaway loop risk.
5. **#2** (CI bot push) — only if you want to demo a full autoresearch run without me babysitting the SHA bump. The UI bypass-actors change takes 30s.

That's ~3 hours of work for the top five and leaves the BYO-CSV path and
tests for a separate session. None of the items in **🟰 REDUNDANT** need
to be discussed again — if a future audit re-raises them, point at this
file.
