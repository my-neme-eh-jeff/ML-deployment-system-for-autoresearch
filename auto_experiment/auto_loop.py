"""LLM-driven experiment loop for the churn model."""

import argparse
import csv
import json
import os
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


def collect_state(exp_num: int, best_auc: float) -> dict:
    metrics = read_metrics()
    current_auc = metrics["auc_roc"] if metrics else best_auc
    return {
        "exp_num": exp_num,
        "best_auc": best_auc,
        "current_auc": current_auc,
        "params_yaml": read_file("configs/params.yaml"),
        "train_py": read_file("src/train.py"),
        "preprocess_py": read_file("src/preprocess.py"),
        "program_md": PROGRAM_MD_PATH.read_text(),
        "history": read_history(),
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

    user_prompt = f"""## Current State (Experiment #{state["exp_num"]})

Best AUC-ROC achieved so far in this session: {state["best_auc"]:.4f}
Current AUC-ROC in metrics.json: {state["current_auc"]:.4f}

### configs/params.yaml
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
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                tools=[PROPOSAL_TOOL],
                tool_choice={"type": "tool", "name": "propose_experiment"},
                messages=[{"role": "user", "content": user_prompt}],
            )
            total_in += getattr(response.usage, "input_tokens", 0) or 0
            total_out += getattr(response.usage, "output_tokens", 0) or 0

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
        except Exception as e:
            last_err = e
            if attempt == 2:
                raise RuntimeError(
                    f"Claude tool-use failed after 3 attempts: {e}"
                ) from e
            print(f"  [retry {attempt + 1}/3] {type(e).__name__}: {e}")
            time.sleep(2)
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
    files = list(originals.keys()) if originals else EDITABLE_FILES
    subprocess.run(
        ["git", "checkout", "--", *files],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
    )


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
    try:
        run = kfp.create_run_from_pipeline_package(
            str(pipeline_yaml),
            arguments={"params_yaml": params_yaml_content},
            run_name=f"autoresearch-{int(time.time())}",
        )
    except Exception as e:
        return {
            "success": False,
            "auc": 0.0,
            "metrics": {},
            "stderr": f"KFP submit: {e}",
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


def commit_improvement_github_app(
    proposal: dict,
    old_auc: float,
    new_auc: float,
    branch: str,
    gh_config: dict,
) -> str:
    name = proposal.get("experiment_name", "unnamed_experiment")
    msg = f"auto-exp: {name} | AUC {old_auc:.4f} → {new_auc:.4f}"
    files = github_commit.collect_changed_files(PROJECT_ROOT, proposal, HISTORY_PATH)
    token = github_commit.get_installation_token(
        gh_config["app_id"],
        gh_config["installation_id"],
        gh_config["project"],
        gh_config["secret"],
    )
    sha = github_commit.commit_files_to_branch(
        token, gh_config["owner"], gh_config["repo"], branch, msg, files
    )
    return sha


def commit_improvement(
    proposal: dict,
    old_auc: float,
    new_auc: float,
    branch: str | None = None,
    gh_config: dict | None = None,
):
    if branch and gh_config:
        sha = commit_improvement_github_app(
            proposal, old_auc, new_auc, branch, gh_config
        )
        print(f"  ↑ committed {sha[:8]} to branch {branch} via GitHub App")
    else:
        commit_improvement_local_git(proposal, old_auc, new_auc)


# ── MLflow logging ────────────────────────────────────────────────────────────


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
        mlflow.log_text(proposal.get("rationale", ""), "rationale.txt")

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
        if proposal.get("params_yaml"):
            mlflow.log_text(proposal["params_yaml"], "attempted/params.yaml")
        if proposal.get("train_py"):
            mlflow.log_text(proposal["train_py"], "attempted/train.py")
        if proposal.get("preprocess_py"):
            mlflow.log_text(proposal["preprocess_py"], "attempted/preprocess.py")

        if pipeline_result["success"]:
            run_id_file = PROJECT_ROOT / "models" / "run_id.txt"
            if run_id_file.exists():
                mlflow.set_tag("linked_train_run_id", run_id_file.read_text().strip())

        if error_msg:
            mlflow.set_tag("error", error_msg[:500])

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

    gh_config = github_commit.github_config_from_env() if not dry_run else None
    branch_name: str | None = None
    if gh_config:
        run_id = os.environ.get("AUTORESEARCH_RUN_ID") or datetime.now(
            timezone.utc
        ).strftime("%Y%m%d-%H%M%S")
        branch_name = f"auto/run-{run_id}"
        print(f"GitHub App configured. Creating feature branch: {branch_name}")
        try:
            token = github_commit.get_installation_token(
                gh_config["app_id"],
                gh_config["installation_id"],
                gh_config["project"],
                gh_config["secret"],
            )
            github_commit.create_branch_from_main(
                token, gh_config["owner"], gh_config["repo"], branch_name
            )
            print(
                f"  ✓ branch ready at {gh_config['owner']}/{gh_config['repo']}@{branch_name}"
            )
        except Exception as e:
            print(f"  ERROR creating branch: {e} — falling back to local-only run.")
            branch_name = None
            gh_config = None

    print(f"\n{'=' * 60}")
    print(f"AutoResearch Loop — {n_experiments} experiments, {hours}h budget")
    print(f"Baseline AUC: {baseline_auc:.4f} | Current best: {best_auc:.4f}")
    print(f"Min improvement threshold: {min_improvement}")
    print(f"Claude model: {claude_model}")
    print(f"Dry run: {dry_run}")
    print(
        f"Commit target: {'GitHub branch ' + branch_name if branch_name else 'local git'}"
    )
    print(f"{'=' * 60}\n")

    for i in range(1, n_experiments + 1):
        elapsed = time.time() - start_time
        if elapsed > hours * 3600:
            print(f"\nTime budget ({hours}h) exhausted after {i - 1} experiments.")
            break

        print(f"\n[{i}/{n_experiments}] Collecting state...")
        state = collect_state(i, best_auc)

        print(f"[{i}/{n_experiments}] Calling Claude ({claude_model})...")
        try:
            claude_result = call_claude(state, model=claude_model)
        except Exception as e:
            print(f"  ERROR calling Claude: {e}")
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

        print(f"[{i}/{n_experiments}] Running pipeline...")
        pipeline_result = run_pipeline(timeout=900)

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
            continue

        new_auc = pipeline_result["auc"]
        delta = new_auc - best_auc
        improved = delta >= min_improvement

        if improved:
            print(f"  ✓ IMPROVED: {best_auc:.4f} → {new_auc:.4f} (+{delta:.4f})")
            # log_to_tsv must run before commit so history.tsv is in the commit.
            log_to_tsv(i, proposal, pipeline_result, best_auc, "improved", usage=usage)
            try:
                commit_improvement(
                    proposal, best_auc, new_auc, branch=branch_name, gh_config=gh_config
                )
            except Exception as e:
                print(f"  WARN: commit failed ({e}) — continuing loop")
            prev_best = best_auc
            best_auc = new_auc
            log_to_mlflow(i, proposal, pipeline_result, prev_best, True, usage=usage)
        else:
            print(
                f"  ✗ REVERTED: {new_auc:.4f} did not beat {best_auc:.4f} (delta={delta:+.4f})"
            )
            revert_files(originals)
            log_to_mlflow(i, proposal, pipeline_result, best_auc, False, usage=usage)
            log_to_tsv(i, proposal, pipeline_result, best_auc, "reverted", usage=usage)

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

    if branch_name and gh_config:
        try:
            token = github_commit.get_installation_token(
                gh_config["app_id"],
                gh_config["installation_id"],
                gh_config["project"],
                gh_config["secret"],
            )
            title = f"auto-exp run {branch_name.split('/')[-1]} — AUC {baseline_auc:.4f} → {best_auc:.4f}"
            body = (
                f"Autoresearch run summary\n\n"
                f"- Iterations: {i}\n"
                f"- Wall-clock: {total_elapsed / 60:.1f} min\n"
                f"- Baseline AUC: {baseline_auc:.4f}\n"
                f"- Final AUC: {best_auc:.4f}\n"
                f"- Delta: {best_auc - baseline_auc:+.4f}\n"
                f"- Claude model: `{claude_model}`\n"
                f"- Tokens (in/out): {total_input_tokens:,} / {total_output_tokens:,}\n"
                f"- Estimated cost: ${total_cost_usd:.4f}\n\n"
                f"Generated by ML-deployment-for-autoresearch GitHub App.\n"
                f"Review the per-iteration commits on `{branch_name}` before merging."
            )
            url = github_commit.open_pull_request(
                token, gh_config["owner"], gh_config["repo"], branch_name, title, body
            )
            print(f"PR opened: {url}")
        except Exception as e:
            print(f"WARN: failed to open PR: {e}")

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
    args = parser.parse_args()

    run_loop(
        n_experiments=args.n_experiments,
        hours=args.hours,
        dry_run=args.dry_run,
        claude_model=args.model,
        min_improvement=args.min_improvement,
    )
