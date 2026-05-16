"""LLM-driven experiment loop for the classifier."""

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Defined before third-party imports so load_dotenv runs before they read env.
PROJECT_ROOT = Path(__file__).parent.parent

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

import anthropic  # noqa: E402
import mlflow  # noqa: E402
import yaml  # noqa: E402

from auto_experiment import github_commit  # noqa: E402

EDITABLE_FILES = [
    "configs/params.yaml",
    "src/train.py",
    "src/preprocess.py",
]
HISTORY_PATH = PROJECT_ROOT / "auto_experiment" / "history.tsv"
PROGRAM_MD_PATH = PROJECT_ROOT / "auto_experiment" / "program.md"
METRICS_PATH = PROJECT_ROOT / "metrics.json"

# Per-iteration KFP run ceiling. Heavy proposals on IEEE-CIS (200K × 339) can
# push past 15 min on bigger HGB / boosting trees; 30 min keeps them in scope.
KFP_TIMEOUT_SECONDS = 1800


# ── Startup checks ──────────────────────────────────────────────────────────


def check_prerequisites():
    import urllib.request

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set.")

    mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    try:
        urllib.request.urlopen(f"{mlflow_uri}/health", timeout=5)
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: MLflow not reachable at {mlflow_uri}: {e}")

    print(f"✓ ANTHROPIC_API_KEY set; MLflow reachable at {mlflow_uri}")

    # Skip the working-tree check in-cluster: the container is a fresh image
    # and PROJECT_ROOT isn't a git repo there.
    if os.environ.get("IN_CLUSTER") == "true":
        return

    result = subprocess.run(
        ["git", "diff", "--name-only", *EDITABLE_FILES],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if result.stdout.strip():
        sys.exit(
            f"ERROR: Working tree has uncommitted changes to editable files:\n"
            f"{result.stdout.strip()}"
        )


# ── State collection ─────────────────────────────────────────────────────────


def read_file(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text()


def read_metrics() -> dict | None:
    if not METRICS_PATH.exists():
        return None
    return json.loads(METRICS_PATH.read_text())


def read_history(n: int = 10) -> str:
    if not HISTORY_PATH.exists():
        return "(No history yet — this is the first experiment.)"
    rows = HISTORY_PATH.read_text().strip().splitlines()
    if len(rows) <= 1:
        return "(No history yet — this is the first experiment.)"
    header = rows[0]
    recent = rows[max(1, len(rows) - n) :]
    return "\n".join([header] + recent)


STATS_PATH = PROJECT_ROOT / "data" / "processed" / "stats.json"


def read_dataset_stats() -> dict | None:
    if not STATS_PATH.exists():
        return None
    return json.loads(STATS_PATH.read_text())


def collect_state(exp_num: int, best_auc: float) -> dict:
    metrics = read_metrics()
    current_auc = metrics["auc_roc"] if metrics else best_auc
    stats = read_dataset_stats() or {}
    return {
        "exp_num": exp_num,
        "best_auc": best_auc,
        "current_auc": current_auc,
        "params_yaml": read_file("configs/params.yaml"),
        "train_py": read_file("src/train.py"),
        "preprocess_py": read_file("src/preprocess.py"),
        "program_md": PROGRAM_MD_PATH.read_text(),
        "history": read_history(),
        "dataset_stats": stats,
    }


# ── Claude API call ──────────────────────────────────────────────────────────

# USD per 1M tokens. Bump when Anthropic pricing changes.
CLAUDE_PRICING = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = CLAUDE_PRICING.get(model)
    if not price:
        for known, p in CLAUDE_PRICING.items():
            if model.startswith(known):
                price = p
                break
    if not price:
        return 0.0
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


# Anthropic tool-use schema. We force the model to call this tool, which
# guarantees the output is a structured object matching the schema (no JSON
# parsing fragility, no prose-before-JSON issues). Sonnet 4.6 explicitly
# rejects assistant-prefill, so this is the production-correct approach.
PROPOSAL_TOOL = {
    "name": "propose_experiment",
    "description": "Propose ONE focused change to improve AUC-ROC.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": "2-3 sentences explaining why this change should improve AUC-ROC.",
            },
            "change_type": {
                "type": "string",
                "enum": ["params_only", "train_py", "preprocess_py", "both_src"],
            },
            "experiment_name": {
                "type": "string",
                "description": "Short snake_case name; used as a commit message and MLflow run name.",
            },
            "params_yaml": {
                "type": ["string", "null"],
                "description": "Full new content of configs/params.yaml, or null if unchanged.",
            },
            "train_py": {
                "type": ["string", "null"],
                "description": "Full new content of src/train.py, or null if unchanged.",
            },
            "preprocess_py": {
                "type": ["string", "null"],
                "description": "Full new content of src/preprocess.py, or null if unchanged.",
            },
        },
        "required": ["rationale", "change_type", "experiment_name"],
    },
}


def call_claude(state: dict, model: str = "claude-sonnet-4-6") -> dict:
    client = anthropic.Anthropic()

    system_prompt = state["program_md"]

    stats = state.get("dataset_stats") or {}
    catalog = stats.get("all_columns") or []
    if catalog:
        catalog_block = (
            f"### Available columns in this dataset ({len(catalog)} total)\n"
            f"Pick from this list when expanding `dataset.numeric_features` / "
            f"`dataset.categorical_features`. Anything outside this list will be "
            f"silently dropped by preprocess and your iteration will fail to add "
            f"signal.\n\n"
            f"```\n{', '.join(catalog)}\n```\n\n"
            f"Currently in use: {len(stats.get('numeric_features', []))} numeric, "
            f"{len(stats.get('categorical_features', []))} categorical. "
            f"Positive rate: {stats.get('positive_rate', 0.0):.3%}.\n"
        )
    else:
        catalog_block = ""

    user_prompt = f"""## Current State (Experiment #{state["exp_num"]})

Best AUC-ROC achieved so far in this session: {state["best_auc"]:.4f}
Current AUC-ROC in metrics.json: {state["current_auc"]:.4f}

{catalog_block}### configs/params.yaml
```yaml
{state["params_yaml"]}
```

### src/train.py
```python
{state["train_py"]}
```

### src/preprocess.py
```python
{state["preprocess_py"]}
```

### Experiment History (last 10 attempts)
{state["history"]}

## Task
Propose ONE specific change to improve AUC-ROC. Choose something not yet tried, or something that failed for a different reason than what you'd try now. Call the `propose_experiment` tool with your proposal."""

    total_in = 0
    total_out = 0
    last_err: Exception | None = None
    # Exponential backoff with jitter for transient Anthropic errors:
    # 529 = "their servers overloaded" (e.g. just after a model launch);
    # 429 = your account's per-minute token bucket; both clear within seconds.
    # Schema/tool errors are NOT retryable — fail fast on those.
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                # program.md is ~3–4K stable tokens across every iter of a
                # session. Marking it `cache_control: ephemeral` lets
                # Anthropic charge cache-read rates (≈0.1×) from iter 2
                # onward — ~75% input-cost reduction over a 20-iter run.
                # The user prompt is NOT cached: experiment history,
                # current AUC, params.yaml, train.py all mutate each iter.
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[PROPOSAL_TOOL],
                tool_choice={"type": "tool", "name": "propose_experiment"},
                messages=[{"role": "user", "content": user_prompt}],
            )
            total_in += getattr(response.usage, "input_tokens", 0) or 0
            total_out += getattr(response.usage, "output_tokens", 0) or 0
            # Cache stats — not billed at standard rate. Log so we can verify
            # cache-hit behavior on the first real run.
            usage = response.usage
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            if cache_read or cache_write:
                print(
                    f"[claude] cache_read={cache_read} cache_write={cache_write} "
                    f"billed_in={total_in} out={total_out}"
                )

            tool_use = next(
                (b for b in response.content if getattr(b, "type", None) == "tool_use"),
                None,
            )
            if tool_use is None:
                raise RuntimeError(
                    f"Model did not call the tool. stop_reason={response.stop_reason}, "
                    f"content_types={[getattr(b, 'type', '?') for b in response.content]}"
                )
            return {
                "proposal": dict(tool_use.input),
                "input_tokens": total_in,
                "output_tokens": total_out,
                "cost_usd": estimate_cost_usd(model, total_in, total_out),
            }
        except (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIStatusError,
        ) as e:
            last_err = e
            # Don't retry on 4xx client errors (bad request, auth) — those
            # won't fix themselves and burning more retries just wastes time.
            status = getattr(e, "status_code", None)
            if (
                isinstance(e, anthropic.APIStatusError)
                and status
                and 400 <= status < 500
                and status not in (408, 429)
            ):
                raise RuntimeError(
                    f"Claude returned non-retryable {status}: {e}"
                ) from e
            if attempt == max_attempts - 1:
                raise RuntimeError(
                    f"Claude failed after {max_attempts} attempts: {e}"
                ) from e
            # Exponential backoff with jitter: 2s, 4s, 8s, 16s + 0-1s jitter.
            backoff = (2 ** (attempt + 1)) + random.uniform(0, 1)
            print(
                f"  [retry {attempt + 1}/{max_attempts}] {type(e).__name__} — "
                f"sleeping {backoff:.1f}s"
            )
            time.sleep(backoff)
        except Exception as e:
            # Schema-shape errors and similar — fail fast.
            last_err = e
            raise RuntimeError(f"Claude tool-use failed (non-retryable): {e}") from e
    raise RuntimeError(f"Unreachable; last_err={last_err}")


# ── Apply / revert ────────────────────────────────────────────────────────────


def snapshot_files(proposal: dict) -> dict:
    originals = {}
    for field, rel_path in [
        ("params_yaml", "configs/params.yaml"),
        ("train_py", "src/train.py"),
        ("preprocess_py", "src/preprocess.py"),
    ]:
        if proposal.get(field):
            originals[rel_path] = (PROJECT_ROOT / rel_path).read_text()
    return originals


def apply_changes(proposal: dict):
    mapping = {
        "params_yaml": "configs/params.yaml",
        "train_py": "src/train.py",
        "preprocess_py": "src/preprocess.py",
    }
    for field, rel_path in mapping.items():
        content = proposal.get(field)
        if content:
            (PROJECT_ROOT / rel_path).write_text(content)


def revert_files(originals: dict):
    """Restore the files we mutated to their pre-iter bytes.

    `originals` is produced by `snapshot_files` BEFORE `apply_changes` runs,
    so it holds the exact bytes the loop saw at the start of this iter. We
    write those bytes back directly — never shell out to `git checkout`,
    because inside the cluster Job the git repo is a freshly-init'd one-commit
    thing whose HEAD is the LLM's bad write (see scripts/run-autoresearch.sh).
    `git checkout` against that HEAD silently "reverts" to the bad write.
    """
    if not originals:
        return
    for rel_path, content in originals.items():
        (PROJECT_ROOT / rel_path).write_text(content)


def ruff_fix(proposal: dict):
    py_files = []
    if proposal.get("train_py"):
        py_files.append("src/train.py")
    if proposal.get("preprocess_py"):
        py_files.append("src/preprocess.py")
    if not py_files:
        return
    subprocess.run(
        ["uv", "run", "ruff", "check", "--fix"] + py_files,
        cwd=PROJECT_ROOT,
        capture_output=True,
    )
    subprocess.run(
        ["uv", "run", "ruff", "format"] + py_files,
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


# ── Pipeline execution ────────────────────────────────────────────────────────


def run_pipeline_local_dvc(timeout: int = 180) -> dict:
    env = os.environ.copy()
    if "MLFLOW_TRACKING_URI" not in env:
        env["MLFLOW_TRACKING_URI"] = "http://localhost:5000"

    try:
        result = subprocess.run(
            ["uv", "run", "dvc", "repro"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "auc": 0.0,
            "metrics": {},
            "stderr": "Pipeline timed out",
        }
    if result.returncode != 0:
        return {
            "success": False,
            "auc": 0.0,
            "metrics": {},
            "stderr": result.stderr[-2000:],
        }
    try:
        metrics = json.loads(METRICS_PATH.read_text())
        return {
            "success": True,
            "auc": metrics["auc_roc"],
            "metrics": metrics,
            "stderr": "",
        }
    except Exception as e:
        return {
            "success": False,
            "auc": 0.0,
            "metrics": {},
            "stderr": f"read metrics.json: {e}",
        }


def run_pipeline_kfp(timeout: int = 900) -> dict:
    from kfp.client import Client

    kfp_host = os.environ.get(
        "KFP_HOST", "http://ml-pipeline.kubeflow.svc.cluster.local:8888"
    )
    pipeline_yaml = PROJECT_ROOT / "pipelines" / "pipeline.yaml"
    if not pipeline_yaml.exists():
        return {
            "success": False,
            "auc": 0.0,
            "metrics": {},
            "stderr": f"compiled pipeline missing at {pipeline_yaml}",
        }
    params_yaml_content = (PROJECT_ROOT / "configs" / "params.yaml").read_text()

    print(f"  → submitting KFP run to {kfp_host}")
    kfp = Client(host=kfp_host)
    # Retry KFP submit — KFP API server can return 500 when its MySQL backend
    # is briefly unreachable (e.g. right after cluster wake before the
    # CloudSQL Auth Proxy sidecar is ready, or a transient connection blip
    # during sustained load). One transient failure shouldn't burn a whole iter
    # (Claude tokens already spent). 3 attempts with backoff is enough — if
    # KFP is really down for >30s, the iter should fail honestly.
    submit_attempts = 3
    run = None
    last_submit_err: Exception | None = None
    # Generate a UUID client-side and pass it to the train component as the
    # MLflow tag value. We don't use dsl.PIPELINE_JOB_ID_PLACEHOLDER because
    # KFP v2 only substitutes that placeholder in command/arg slots, not in
    # component parameter values — the literal `{{$.pipeline_job_uuid}}`
    # string ended up as the tag during smoke-testing. Going client-side
    # decouples us from that quirk; the trade-off is the tag isn't equal to
    # KFP's run.run_id, but it's still unique and locatable.
    import uuid as _uuid

    iter_tag = _uuid.uuid4().hex
    for attempt in range(submit_attempts):
        try:
            run = kfp.create_run_from_pipeline_package(
                str(pipeline_yaml),
                arguments={
                    "params_yaml": params_yaml_content,
                    "kfp_run_id": iter_tag,
                },
                run_name=f"autoresearch-{int(time.time())}",
            )
            break
        except Exception as e:
            last_submit_err = e
            if attempt == submit_attempts - 1:
                return {
                    "success": False,
                    "auc": 0.0,
                    "metrics": {},
                    "stderr": f"KFP submit (after {submit_attempts} retries): {e}",
                }
            backoff = (2 ** (attempt + 1)) + random.uniform(0, 1)
            print(
                f"  [retry {attempt + 1}/{submit_attempts}] KFP submit failed "
                f"({type(e).__name__}: {str(e)[:100]}) — sleeping {backoff:.1f}s"
            )
            time.sleep(backoff)
    if run is None:
        return {
            "success": False,
            "auc": 0.0,
            "metrics": {},
            "stderr": f"KFP submit (impossible): {last_submit_err}",
        }

    print(f"  → KFP run id: {run.run_id} — waiting up to {timeout}s")
    try:
        result = kfp.wait_for_run_completion(run.run_id, timeout=timeout)
    except Exception as e:
        return {"success": False, "auc": 0.0, "metrics": {}, "stderr": f"KFP wait: {e}"}
    # kfp v2 returns the V2beta1Run directly; v1 wrapped it in `.run`.
    run_obj = getattr(result, "run", result)
    state = (
        getattr(run_obj, "state", None) or getattr(run_obj, "status", None) or "UNKNOWN"
    )
    if str(state).upper() not in ("SUCCEEDED", "COMPLETE"):
        return {
            "success": False,
            "auc": 0.0,
            "metrics": {},
            "stderr": f"KFP run state={state}, id={run.run_id}",
        }

    try:
        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        # Query by the client-side iter_tag we just passed in. Pinpoints
        # THIS submission's MLflow run, even if a concurrent training run
        # (another autoresearch loop, manual `make repro`, CI build, retry)
        # lands in the same experiment moments later.
        runs = mlflow.search_runs(
            experiment_names=["training"],
            filter_string=f"tags.kfp_run_id = '{iter_tag}'",
            order_by=["start_time DESC"],
            max_results=1,
        )
        if runs.empty:
            # Fail closed by default. The whole point of tagging the MLflow
            # run with the KFP run id is to NOT race on "latest by start_time"
            # — falling back to that silently turns this safety mechanism into
            # decorative comments. If we genuinely need the fallback (e.g. an
            # old pipeline-kfp image is in flight that doesn't tag), opt in
            # explicitly via env.
            allow_fallback = os.environ.get("AUTORESEARCH_ALLOW_LATEST_FALLBACK") == "1"
            if not allow_fallback:
                return {
                    "success": False,
                    "auc": 0.0,
                    "metrics": {},
                    "stderr": (
                        f"no MLflow run with tag kfp_run_id='{iter_tag}'. "
                        f"Pipeline-kfp image may not be reading KFP_RUN_ID "
                        f"env (rebuild via CI), or the train component "
                        f"didn't propagate the submission arg. Set "
                        f"AUTORESEARCH_ALLOW_LATEST_FALLBACK=1 to opt into "
                        f"the race-prone latest-run lookup."
                    ),
                }
            print(
                "  WARN: no MLflow run with tag kfp_run_id="
                f"'{iter_tag}' — AUTORESEARCH_ALLOW_LATEST_FALLBACK=1, "
                f"falling back to latest run (race-prone)."
            )
            runs = mlflow.search_runs(
                experiment_names=["training"],
                order_by=["start_time DESC"],
                max_results=1,
            )
        if runs.empty:
            return {
                "success": False,
                "auc": 0.0,
                "metrics": {},
                "stderr": "no MLflow run found after KFP completion",
            }
        row = runs.iloc[0]
        auc = float(row.get("metrics.auc_roc", 0.0))
        metrics = {
            "auc_roc": auc,
            "f1": float(row.get("metrics.f1", 0.0)),
            "accuracy": float(row.get("metrics.accuracy", 0.0)),
            "precision": float(row.get("metrics.precision", 0.0)),
            "recall": float(row.get("metrics.recall", 0.0)),
            "mlflow_run_id": row["run_id"],
            "kfp_run_id": run.run_id,
        }
        return {"success": True, "auc": auc, "metrics": metrics, "stderr": ""}
    except Exception as e:
        return {
            "success": False,
            "auc": 0.0,
            "metrics": {},
            "stderr": f"read MLflow: {e}",
        }


def run_pipeline(timeout: int = 900) -> dict:
    if os.environ.get("IN_CLUSTER") == "true":
        return run_pipeline_kfp(timeout=timeout)
    return run_pipeline_local_dvc(timeout=timeout)


# ── Git commit ────────────────────────────────────────────────────────────────


def commit_improvement_local_git(proposal: dict, old_auc: float, new_auc: float):
    name = proposal.get("experiment_name", "unnamed_experiment")
    changed = [
        f
        for f in ["configs/params.yaml", "src/train.py", "src/preprocess.py"]
        if proposal.get(
            {
                "configs/params.yaml": "params_yaml",
                "src/train.py": "train_py",
                "src/preprocess.py": "preprocess_py",
            }[f]
        )
    ]
    generated = ["metrics.json", "dvc.lock"]
    to_stage = changed + [g for g in generated if (PROJECT_ROOT / g).exists()]
    subprocess.run(["git", "add"] + to_stage, cwd=PROJECT_ROOT, check=True)
    msg = f"auto-exp: {name} | AUC {old_auc:.4f} → {new_auc:.4f}"
    subprocess.run(
        ["git", "commit", "-m", msg, "--no-verify"], cwd=PROJECT_ROOT, check=True
    )


def _read_champion_version(mlflow_run_id: str) -> tuple[str, str]:
    """Return (version_int_as_str, run_id) of the @champion classifier.

    Called right after the KFP pipeline finishes — `evaluate.py` has just
    promoted the new champion (or kept the old one). We trust @champion as
    the source of truth for what the loop should bump in deployment.yaml.
    """
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    client = mlflow.MlflowClient()
    champ = client.get_model_version_by_alias("classifier", "champion")
    return str(champ.version), str(champ.run_id)


def _get_champion_version() -> str | None:
    """Return the version-string of the current @champion, or None if unset."""
    try:
        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        client = mlflow.MlflowClient()
        v = client.get_model_version_by_alias("classifier", "champion")
        return str(v.version)
    except Exception:
        return None


def _revert_mlflow_champion(prev_version: str | None) -> None:
    """Roll @champion back to `prev_version`.

    Used when an iter trained a new version + promoted it via evaluate.py,
    but the PR carrying the deployment.yaml annotation never merged. Without
    this, MLflow records the new version as @champion while the deployed
    pods still serve the previous one — the loop's view of "what's live"
    diverges from the cluster.
    """
    if not prev_version:
        print("  WARN: no previous @champion to revert to (registry was empty)")
        return
    try:
        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        client = mlflow.MlflowClient()
        client.set_registered_model_alias("classifier", "champion", prev_version)
        print(f"  ↩ Reverted @champion → v{prev_version}")
    except Exception as e:
        print(f"  WARN: failed to revert @champion to v{prev_version}: {e}")


def _slugify_branch_segment(name: str) -> str:
    """Reduce an LLM-supplied experiment name to a git-ref-safe segment.

    Git ref-format rejects spaces, `..`, `~`, `^`, `:`, `?`, `*`, `[`, `\\`,
    control chars, and trailing dots. Anything outside `[A-Za-z0-9._-]` is
    collapsed to a single hyphen; leading/trailing punctuation is stripped;
    the result is capped at 30 chars. Empty after sanitization falls back
    to `"iter"` so we always return a usable segment.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "")
    slug = slug.strip("-._")[:30]
    return slug or "iter"


def _rewrite_last_history_row_to_failed(reason: str) -> None:
    """Mutate the last history.tsv row in place: outcome `improved` → `failed`.

    The loop writes the `improved` row BEFORE the commit / PR-merge wait so
    history.tsv goes into the PR. If the PR doesn't merge, we roll back
    files + @champion but the local in-memory state would still show
    `improved` in history.tsv, which the next iter's Claude prompt then
    inherits as if the change actually shipped. This rewrites the last
    row so the loop's memory matches what actually landed on main.
    """
    try:
        if not HISTORY_PATH.exists():
            return
        lines = HISTORY_PATH.read_text().splitlines()
        # First line is the header; need at least one data row to rewrite.
        if len(lines) < 2:
            return
        last = lines[-1]
        cols = last.split("\t")
        # Schema: ts, exp_num, name, change_type, auc_before, auc_after,
        # delta, outcome, in_tok, out_tok, cost, rationale (11+).
        if len(cols) < 8 or cols[7] != "improved":
            return
        cols[7] = "failed"
        # Append the rollback reason to the rationale (last column).
        if cols[-1]:
            cols[-1] = f"{cols[-1]} [rolled back: {reason}]"
        else:
            cols[-1] = f"[rolled back: {reason}]"
        lines[-1] = "\t".join(cols)
        HISTORY_PATH.write_text("\n".join(lines) + "\n")
        print("  ↩ Rewrote last history.tsv row improved → failed")
    except Exception as e:
        print(f"  WARN: failed to rewrite history.tsv last row: {e}")


def open_iter_pr_with_auto_merge(
    proposal: dict,
    old_auc: float,
    new_auc: float,
    iter_num: int,
    run_id: str,
    pipeline_result: dict,
    gh_config: dict,
    usage: dict,
) -> tuple[str, str]:
    """Per-iteration: create a fresh branch from main HEAD, commit the
    experiment files PLUS a `k8s/deployment.yaml` annotation bump (so ArgoCD
    rolls inference-api on this PR's merge), open a PR, enable auto-merge.

    Returns (pr_url, commit_sha). Does NOT wait for the merge — the loop
    proceeds to the next iter immediately. Auto-merge fires when required
    branch protection checks pass (lint-and-test + compile-kfp, ~1 min).
    """
    name = proposal.get("experiment_name", "unnamed")
    msg = f"auto-exp: {name} | AUC {old_auc:.4f} → {new_auc:.4f}"

    token = github_commit.get_installation_token(
        gh_config["app_id"],
        gh_config["installation_id"],
        gh_config["project"],
        gh_config["secret"],
    )

    # Per-iter branch off the *current* main HEAD. Picks up any [skip ci]
    # commits CI pushed back from the previous iter's merge.
    iter_branch = (
        f"auto/run-{run_id}-iter-{iter_num:02d}-{_slugify_branch_segment(name)}"
    )
    github_commit.create_branch_from_main(
        token, gh_config["owner"], gh_config["repo"], iter_branch
    )

    # Read main's deployment.yaml fresh and bump the two model annotations
    # in place. Other fields (image SHA, replicas) stay untouched, so this
    # change won't conflict with whatever CI may concurrently push.
    deploy_content, _ = github_commit.fetch_file_from_main(
        token, gh_config["owner"], gh_config["repo"], "k8s/deployment.yaml"
    )
    try:
        version_str, mlflow_run_id = _read_champion_version(
            pipeline_result["metrics"].get("mlflow_run_id", "")
        )
    except Exception as e:
        print(f"  WARN: couldn't read @champion for annotation bump: {e}")
        version_str, mlflow_run_id = "?", ""
    deploy_content = github_commit.bump_deployment_annotations(
        deploy_content, version_str, mlflow_run_id
    )

    files = github_commit.collect_changed_files(PROJECT_ROOT, proposal, HISTORY_PATH)
    files.append(("k8s/deployment.yaml", deploy_content.encode("utf-8")))

    sha = github_commit.commit_files_to_branch(
        token, gh_config["owner"], gh_config["repo"], iter_branch, msg, files
    )

    title = msg
    body = (
        f"Autoresearch iteration {iter_num} of run {run_id}\n\n"
        f"- Experiment: `{name}`\n"
        f"- Change type: `{proposal.get('change_type', 'unknown')}`\n"
        f"- AUC: {old_auc:.4f} → **{new_auc:.4f}** (Δ {new_auc - old_auc:+.4f})\n"
        f"- MLflow `classifier@champion` version: **v{version_str}**\n"
        f"- MLflow run id: `{mlflow_run_id}`\n"
        f"- Tokens (in/out): {usage['input_tokens']:,} / {usage['output_tokens']:,}\n"
        f"- Estimated cost: ${usage['cost_usd']:.4f}\n\n"
        f"### Rationale\n{proposal.get('rationale', '')}\n\n"
        f"---\n"
        f"This PR also bumps `k8s/deployment.yaml`'s pod-template annotations "
        f"(`mlops/classifier-version`, `mlops/classifier-run-id`) — when this "
        f"PR merges, ArgoCD will roll `inference-api` and the new pods will "
        f"load `classifier@champion` v{version_str} from MLflow at startup."
    )
    url, _ = github_commit.open_pull_request(
        token,
        gh_config["owner"],
        gh_config["repo"],
        iter_branch,
        title,
        body,
        auto_merge=True,
    )
    return url, sha


def commit_improvement(
    proposal: dict,
    old_auc: float,
    new_auc: float,
    iter_num: int = 0,
    run_id: str = "local",
    pipeline_result: dict | None = None,
    gh_config: dict | None = None,
    usage: dict | None = None,
) -> str | None:
    """Returns the per-iter PR URL when running in cluster, None for local."""
    if gh_config and pipeline_result is not None:
        url, sha = open_iter_pr_with_auto_merge(
            proposal,
            old_auc,
            new_auc,
            iter_num,
            run_id,
            pipeline_result,
            gh_config,
            usage or {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        print(f"  ↑ {sha[:8]} → PR {url} (auto-merge enabled)")
        return url
    commit_improvement_local_git(proposal, old_auc, new_auc)
    return None


# ── MLflow logging ────────────────────────────────────────────────────────────


# Patterns for redacting anything that looks like a secret before we log it
# to MLflow. The MLflow UI is public (audit-acked portfolio scope), so any
# generated source, error text, or rationale that happens to contain an env
# var assignment, API key, or PEM body would become a public artifact. The
# regexes target the obvious-shape leaks; sophisticated leaks need a real
# secret scanner.
_SECRET_PATTERNS = [
    # AWS/GCP/GitHub/Anthropic-style key=value with quoted or bare values.
    (
        re.compile(
            r"(?i)\b("
            r"AWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)"
            r"|GH(?:E)?_TOKEN|GITHUB_TOKEN|GITHUB_PAT_TOKEN"
            r"|ANTHROPIC_API_KEY|OPENAI_API_KEY"
            r"|GCP_SERVICE_ACCOUNT_KEY|GOOGLE_API_KEY"
            r"|MLFLOW_TRACKING_PASSWORD|DATABASE_URL|DB_PASSWORD"
            r")\s*[=:]\s*\S+"
        ),
        r"\1=[REDACTED]",
    ),
    # PEM blocks (private keys).
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED-PEM]",
    ),
    # Bearer tokens in HTTP headers / error text.
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
        "Bearer [REDACTED]",
    ),
    # Anthropic key shape (sk-ant-...).
    (
        re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
        "[REDACTED-ANTHROPIC-KEY]",
    ),
    # GitHub App PEM may not match the block regex if newlines are stripped;
    # catch the begin/end markers separately.
    (
        re.compile(r"-----(?:BEGIN|END)[ A-Z]*PRIVATE KEY-----"),
        "[REDACTED-PEM-MARKER]",
    ),
]


def _redact_secrets(text: str | None) -> str:
    """Return `text` with obvious-shape secret patterns replaced.

    Always returns a string (treats None as empty) so callers can pass it
    straight to mlflow.log_text / set_tag without further guards.
    """
    if not text:
        return ""
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def log_to_mlflow(
    exp_num: int,
    proposal: dict,
    pipeline_result: dict,
    auc_before: float,
    improved: bool,
    error_msg: str = "",
    usage: dict | None = None,
):
    mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("auto-experiment")

    outcome = (
        "improved"
        if improved
        else ("failed" if not pipeline_result["success"] else "reverted")
    )
    name = proposal.get("experiment_name", f"experiment_{exp_num}")

    with mlflow.start_run(run_name=name):
        mlflow.log_params(
            {
                "experiment_number": exp_num,
                "change_type": proposal.get("change_type", "unknown"),
                "outcome": outcome,
            }
        )
        mlflow.log_text(_redact_secrets(proposal.get("rationale", "")), "rationale.txt")

        auc_after = pipeline_result["auc"]
        mlflow.log_metric("auc_roc_before", auc_before)
        mlflow.log_metric("auc_roc_after", auc_after)
        mlflow.log_metric("auc_roc_delta", auc_after - auc_before)

        if usage:
            mlflow.log_metric("claude_input_tokens", usage.get("input_tokens", 0))
            mlflow.log_metric("claude_output_tokens", usage.get("output_tokens", 0))
            mlflow.log_metric("claude_cost_usd", usage.get("cost_usd", 0.0))
            if usage.get("model"):
                mlflow.set_tag("claude_model", usage["model"])

        # Log every attempted file, even reverted ones, for the audit trail.
        # Redact obvious secrets first — the LLM has the project context but
        # may regenerate sample values that look like keys, or echo error
        # text containing tokens. MLflow UI is public per portfolio scope.
        if proposal.get("params_yaml"):
            mlflow.log_text(
                _redact_secrets(proposal["params_yaml"]), "attempted/params.yaml"
            )
        if proposal.get("train_py"):
            mlflow.log_text(_redact_secrets(proposal["train_py"]), "attempted/train.py")
        if proposal.get("preprocess_py"):
            mlflow.log_text(
                _redact_secrets(proposal["preprocess_py"]), "attempted/preprocess.py"
            )

        if pipeline_result["success"]:
            run_id_file = PROJECT_ROOT / "models" / "run_id.txt"
            if run_id_file.exists():
                mlflow.set_tag("linked_train_run_id", run_id_file.read_text().strip())

        if error_msg:
            mlflow.set_tag("error", _redact_secrets(error_msg)[:500])

        for metric_name, val in pipeline_result.get("metrics", {}).items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                mlflow.log_metric(f"pipeline_{metric_name}", val)
            else:
                mlflow.set_tag(f"pipeline_{metric_name}", str(val))


# ── TSV logging ───────────────────────────────────────────────────────────────


def log_to_tsv(
    exp_num: int,
    proposal: dict,
    pipeline_result: dict,
    auc_before: float,
    outcome: str,
    usage: dict | None = None,
):
    usage = usage or {}
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        "exp_num": exp_num,
        "experiment_name": proposal.get("experiment_name", "unnamed"),
        "change_type": proposal.get("change_type", "unknown"),
        "auc_before": f"{auc_before:.4f}",
        "auc_after": f"{pipeline_result['auc']:.4f}",
        "delta": f"{pipeline_result['auc'] - auc_before:+.4f}",
        "outcome": outcome,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": f"{usage.get('cost_usd', 0.0):.4f}",
        "rationale": proposal.get("rationale", "").replace("\n", " ")[:120],
    }
    headers = list(row.keys())
    write_header = not HISTORY_PATH.exists()
    with open(HISTORY_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter="\t")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── Main loop ─────────────────────────────────────────────────────────────────


def run_loop(
    n_experiments: int,
    hours: float,
    dry_run: bool,
    claude_model: str,
    min_improvement: float,
    max_cost_usd: float | None = None,
):
    check_prerequisites()

    with open(PROJECT_ROOT / "configs/params.yaml") as f:
        auto_cfg = yaml.safe_load(f).get("auto_experiment", {})
    baseline_auc = auto_cfg.get("baseline_auc", 0.8162)
    if min_improvement == 0.001:
        min_improvement = auto_cfg.get("min_improvement", 0.001)

    metrics = read_metrics()
    best_auc = metrics["auc_roc"] if metrics else baseline_auc
    start_time = time.time()
    total_cost_usd = 0.0
    total_input_tokens = 0
    total_output_tokens = 0

    max_no_improvement = int(auto_cfg.get("max_iterations_without_improvement", 0))
    iters_since_improvement = 0

    gh_config = github_commit.github_config_from_env() if not dry_run else None
    run_id = os.environ.get("AUTORESEARCH_RUN_ID") or datetime.now(
        timezone.utc
    ).strftime("%Y%m%d-%H%M%S")

    print(f"\n{'=' * 60}")
    print(f"AutoResearch Loop — {n_experiments} experiments, {hours}h budget")
    print(f"Baseline AUC: {baseline_auc:.4f} | Current best: {best_auc:.4f}")
    print(f"Min improvement threshold: {min_improvement}")
    print(f"Claude model: {claude_model}")
    print(f"Dry run: {dry_run}")
    print(
        f"Commit target: {'one PR per kept iter (auto-merge)' if gh_config else 'local git'}"
    )
    print(f"Run id: {run_id}")
    print(f"{'=' * 60}\n")
    pr_urls: list[str] = []

    for i in range(1, n_experiments + 1):
        elapsed = time.time() - start_time
        if elapsed > hours * 3600:
            print(f"\nTime budget ({hours}h) exhausted after {i - 1} experiments.")
            break
        # Cost circuit breaker: stop before the NEXT iter if total has already
        # crossed the cap. Per-iter cost is added below; this check runs at
        # the top of each loop so the cap is honoured even if iter N pushed
        # us over (we don't try to half-run an iteration).
        if max_cost_usd is not None and total_cost_usd >= max_cost_usd:
            print(
                f"\n💰 Cost cap ${max_cost_usd:.2f} reached after iter {i - 1} "
                f"(total ≈ ${total_cost_usd:.4f}). Stopping."
            )
            break

        print(f"\n[{i}/{n_experiments}] Collecting state...")
        state = collect_state(i, best_auc)

        print(f"[{i}/{n_experiments}] Calling Claude ({claude_model})...")
        try:
            claude_result = call_claude(state, model=claude_model)
        except Exception as e:
            print(f"  ERROR calling Claude: {e}")
            iters_since_improvement += 1
            if max_no_improvement > 0 and iters_since_improvement >= max_no_improvement:
                print(
                    f"\n⏸ Early stop after iter {i}: "
                    f"{iters_since_improvement} consecutive iters without improvement "
                    f"(threshold={max_no_improvement})."
                )
                break
            continue

        proposal = claude_result["proposal"]
        usage = {
            "input_tokens": claude_result["input_tokens"],
            "output_tokens": claude_result["output_tokens"],
            "cost_usd": claude_result["cost_usd"],
            "model": claude_model,
        }
        total_input_tokens += usage["input_tokens"]
        total_output_tokens += usage["output_tokens"]
        total_cost_usd += usage["cost_usd"]

        print(
            f"[{i}/{n_experiments}] Proposal: {proposal.get('experiment_name', 'unnamed')}"
        )
        print(f"  Rationale: {proposal.get('rationale', '')[:200]}")
        print(f"  Change type: {proposal.get('change_type', 'unknown')}")
        print(
            f"  Tokens: in={usage['input_tokens']:,} out={usage['output_tokens']:,} "
            f"cost≈${usage['cost_usd']:.4f}"
        )

        if dry_run:
            print("\n  [DRY RUN] Would apply the following changes:")
            if proposal.get("params_yaml"):
                print("  - configs/params.yaml (modified)")
            if proposal.get("train_py"):
                print("  - src/train.py (modified)")
            if proposal.get("preprocess_py"):
                print("  - src/preprocess.py (modified)")
            print("\n  Skipping pipeline run (dry-run mode).")
            continue

        originals = snapshot_files(proposal)
        apply_changes(proposal)
        ruff_fix(proposal)

        # Snapshot the registry's current @champion version BEFORE this iter
        # trains anything. If evaluate.py promotes a new version but the PR
        # carrying the deployment.yaml annotation never merges, we use this
        # to roll @champion back so MLflow and the deployed pod stay coherent.
        prev_champion_version = _get_champion_version()

        print(f"[{i}/{n_experiments}] Running pipeline...")
        pipeline_result = run_pipeline(timeout=KFP_TIMEOUT_SECONDS)

        if not pipeline_result["success"]:
            print(f"  PIPELINE FAILED: {pipeline_result['stderr'][:300]}")
            revert_files(originals)
            log_to_mlflow(
                i,
                proposal,
                pipeline_result,
                best_auc,
                False,
                error_msg=pipeline_result["stderr"][:300],
                usage=usage,
            )
            log_to_tsv(i, proposal, pipeline_result, best_auc, "failed", usage=usage)
            iters_since_improvement += 1
            if max_no_improvement > 0 and iters_since_improvement >= max_no_improvement:
                print(
                    f"\n⏸ Early stop after iter {i}: "
                    f"{iters_since_improvement} consecutive iters without improvement "
                    f"(threshold={max_no_improvement})."
                )
                break
            continue

        new_auc = pipeline_result["auc"]
        delta = new_auc - best_auc
        improved = delta >= min_improvement

        if improved:
            print(f"  ✓ IMPROVED: {best_auc:.4f} → {new_auc:.4f} (+{delta:.4f})")
            # log_to_tsv must run before commit so history.tsv is in the commit.
            log_to_tsv(i, proposal, pipeline_result, best_auc, "improved", usage=usage)
            pr_merged = False
            pr_err: str | None = None
            try:
                pr_url = commit_improvement(
                    proposal,
                    best_auc,
                    new_auc,
                    iter_num=i,
                    run_id=run_id,
                    pipeline_result=pipeline_result,
                    gh_config=gh_config,
                    usage=usage,
                )
                if pr_url:
                    pr_urls.append(pr_url)
                # Block until this iter's PR has actually merged into main
                # before starting the next iter. Without this, iter N+1
                # branches off stale main (missing iter N's history.tsv row
                # and dvc.lock update), and either GraphQL rejects on parent
                # mismatch or iter N+1 overwrites iter N's history row.
                if pr_url and gh_config:
                    pr_num = github_commit.pr_number_from_url(pr_url)
                    if pr_num is not None:
                        print(
                            f"  ⏳ Waiting for PR #{pr_num} to merge before next iter..."
                        )
                        token = github_commit.get_installation_token(
                            gh_config["app_id"],
                            gh_config["installation_id"],
                            gh_config["project"],
                            gh_config["secret"],
                        )
                        pr_merged = github_commit.wait_for_pr_merge(
                            token,
                            gh_config["owner"],
                            gh_config["repo"],
                            pr_num,
                            poll_interval_s=15,
                            timeout_s=600,
                        )
                        if pr_merged:
                            print(f"  ✓ PR #{pr_num} merged.")
                else:
                    # No gh_config → the loop is running in a mode where PR
                    # gating is not applicable (e.g. local dev). Treat as
                    # merged so we don't roll back local file mutations.
                    pr_merged = True
            except Exception as e:
                pr_err = str(e)
                print(f"  WARN: commit/merge-wait failed ({e})")
            if not pr_merged:
                # PR creation or merge did not complete. Roll back: the model
                # was promoted to @champion inside evaluate.py before this
                # branched off, but k8s/deployment.yaml on main never got the
                # annotation bump (it lives in the unmerged PR). Without a
                # rollback, MLflow says version vN is @champion but ArgoCD
                # never rolls pods → pods continue serving the previous
                # version forever. Mark iter as failed, restore files, and
                # revert @champion so the cluster state stays coherent.
                reason = pr_err or "PR did not merge within timeout"
                print(
                    f"  ✗ FAILED: improvement detected but PR not merged "
                    f"({reason}). Reverting local files + MLflow champion."
                )
                revert_files(originals)
                _revert_mlflow_champion(prev_champion_version)
                _rewrite_last_history_row_to_failed(reason)
                log_to_mlflow(
                    i, proposal, pipeline_result, best_auc, False, usage=usage
                )
                iters_since_improvement += 1
                continue
            prev_best = best_auc
            best_auc = new_auc
            log_to_mlflow(i, proposal, pipeline_result, prev_best, True, usage=usage)
            iters_since_improvement = 0
        else:
            print(
                f"  ✗ REVERTED: {new_auc:.4f} did not beat {best_auc:.4f} (delta={delta:+.4f})"
            )
            revert_files(originals)
            log_to_mlflow(i, proposal, pipeline_result, best_auc, False, usage=usage)
            log_to_tsv(i, proposal, pipeline_result, best_auc, "reverted", usage=usage)
            iters_since_improvement += 1
            if max_no_improvement > 0 and iters_since_improvement >= max_no_improvement:
                print(
                    f"\n⏸ Early stop after iter {i}: "
                    f"{iters_since_improvement} consecutive iters without improvement "
                    f"(threshold={max_no_improvement})."
                )
                break

    total_elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"Loop complete. {i} experiments in {total_elapsed / 60:.1f} minutes.")
    print(
        f"Final best AUC: {best_auc:.4f} (started at {baseline_auc:.4f}, delta={best_auc - baseline_auc:+.4f})"
    )
    print(
        f"Claude usage: in={total_input_tokens:,} out={total_output_tokens:,} "
        f"≈ ${total_cost_usd:.4f} ({claude_model})"
    )

    if pr_urls:
        print("\nPRs opened (one per kept iter, all auto-merge enabled):")
        for u in pr_urls:
            print(f"  {u}")
    print(f"{'=' * 60}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM-driven experiment loop")
    parser.add_argument(
        "--n-experiments", type=int, default=20, help="Max number of experiments to run"
    )
    parser.add_argument("--hours", type=float, default=2.0, help="Max wall-clock hours")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show Claude's proposal but don't run the pipeline",
    )
    parser.add_argument(
        "--model", default="claude-sonnet-4-6", help="Claude model to use"
    )
    parser.add_argument(
        "--min-improvement",
        type=float,
        default=0.001,
        help="Minimum AUC-ROC delta to accept a change",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help=(
            "Stop the loop once the cumulative Anthropic cost crosses this "
            "USD value. Default: no cap. Recommended ~$3 for a 20-iter run "
            "with prompt caching enabled."
        ),
    )
    args = parser.parse_args()

    run_loop(
        n_experiments=args.n_experiments,
        hours=args.hours,
        dry_run=args.dry_run,
        claude_model=args.model,
        min_improvement=args.min_improvement,
        max_cost_usd=args.max_cost_usd,
    )
