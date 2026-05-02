"""
AutoResearch-inspired experiment loop for customer churn model improvement.

Inspired by: https://github.com/karpathy/autoresearch

Loop per iteration:
  1. Read current state (params.yaml, train.py, preprocess.py, metrics.json, history.tsv)
  2. Call Claude API with research directions + history → get ONE proposed change
  3. Apply changes (write full file contents)
  4. Run ruff --fix (lint before commit so pre-commit hooks pass)
  5. Run dvc repro (preprocess → train → evaluate)
  6. Compare AUC-ROC: if improved by >= min_improvement → git commit
  7. Otherwise → git checkout -- (surgical revert)
  8. Log to MLflow auto-experiment + local history.tsv
  9. Repeat
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# PROJECT_ROOT must be defined before any third-party imports so that
# load_dotenv() can find .env before anthropic/mlflow read os.environ.
PROJECT_ROOT = Path(__file__).parent.parent

from dotenv import load_dotenv  # noqa: E402

# Load .env from project root — no need to export vars manually.
# Variables already set in the environment take precedence over .env values.
load_dotenv(PROJECT_ROOT / ".env")

import anthropic  # noqa: E402
import mlflow  # noqa: E402
import yaml  # noqa: E402

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
    """Fail fast with clear messages if the environment isn't ready."""
    import urllib.request

    # 1. ANTHROPIC_API_KEY
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ERROR: ANTHROPIC_API_KEY is not set.\nExport it: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    # 2. MLflow reachable
    mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    try:
        urllib.request.urlopen(f"{mlflow_uri}/health", timeout=5)
    except Exception:
        sys.exit(
            f"ERROR: MLflow not reachable at {mlflow_uri}\n"
            "Run 'make mlflow-kill && make mlflow' in another terminal first."
        )

    print("✓ ANTHROPIC_API_KEY set")
    print(f"✓ MLflow reachable at {mlflow_uri}")

    # 3. Git working tree clean for editable files — only when running locally.
    # In-cluster (Dockerfile sets IN_CLUSTER=true) the container starts from a fresh
    # image so the tree is always clean, and PROJECT_ROOT is not a git repo anyway.
    if os.environ.get("IN_CLUSTER") == "true":
        print("✓ Skipping working-tree check (IN_CLUSTER=true)")
        return

    result = subprocess.run(
        ["git", "diff", "--name-only"] + EDITABLE_FILES,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if result.stdout.strip():
        sys.exit(
            f"ERROR: Working tree has uncommitted changes to editable files:\n"
            f"{result.stdout.strip()}\n"
            "Commit or stash them before running the loop."
        )
    print("✓ Working tree clean for editable files")


# ── State collection ─────────────────────────────────────────────────────────


def read_file(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text()


def read_metrics() -> dict:
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
    return {
        "exp_num": exp_num,
        "best_auc": best_auc,
        "current_auc": read_metrics()["auc_roc"],
        "params_yaml": read_file("configs/params.yaml"),
        "train_py": read_file("src/train.py"),
        "preprocess_py": read_file("src/preprocess.py"),
        "program_md": PROGRAM_MD_PATH.read_text(),
        "history": read_history(),
    }


# ── Claude API call ──────────────────────────────────────────────────────────


def call_claude(state: dict, model: str = "claude-sonnet-4-6") -> dict:
    client = anthropic.Anthropic()

    system_prompt = f"""{state["program_md"]}

CRITICAL: Return ONLY a valid JSON object. No markdown fences, no explanation outside the JSON.
If you accidentally wrap it in ```json ... ```, the parser will strip the fences — but prefer clean JSON directly."""

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
Propose ONE specific change to improve AUC-ROC. Choose something not yet tried or something that failed for a different reason than what you'd try now. Return ONLY the JSON object."""

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text.strip()
            # Strip markdown fences if Claude added them
            if text.startswith("```"):
                text = text.split("```", 2)[-1] if text.count("```") >= 2 else text
                text = text.removeprefix("json").strip()
                if text.endswith("```"):
                    text = text[:-3].strip()
            return json.loads(text)
        except json.JSONDecodeError as e:
            if attempt == 2:
                raise RuntimeError(
                    f"Claude returned invalid JSON after 3 attempts: {e}\nResponse: {text[:500]}"
                )
            print(f"  [retry {attempt + 1}/3] JSON parse error, retrying...")
            time.sleep(2)


# ── Apply / revert ────────────────────────────────────────────────────────────


def snapshot_files(proposal: dict) -> dict:
    """Save in-memory copies of files that will be changed."""
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
    """Restore files to their pre-experiment state."""
    if originals:
        # Use git checkout for a clean revert (handles any edge cases)
        files = list(originals.keys())
        subprocess.run(
            ["git", "checkout", "--"] + files,
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
        )
    else:
        # Fallback: revert all editable files
        subprocess.run(
            ["git", "checkout", "--"] + EDITABLE_FILES,
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
        )


def ruff_fix(proposal: dict):
    """Lint and format any changed Python files so pre-commit hooks pass."""
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


def run_pipeline(timeout: int = 180) -> dict:
    """Run dvc repro and return {'success': bool, 'auc': float, 'metrics': dict, 'stderr': str}."""
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
            "stderr": f"Failed to read metrics.json: {e}",
        }


# ── Git commit ────────────────────────────────────────────────────────────────


def commit_improvement(proposal: dict, old_auc: float, new_auc: float):
    name = proposal.get("experiment_name", "unnamed_experiment")
    # Stage changed source files
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
    # Also stage generated outputs
    generated = ["metrics.json", "dvc.lock"]

    to_stage = changed + [g for g in generated if (PROJECT_ROOT / g).exists()]
    subprocess.run(["git", "add"] + to_stage, cwd=PROJECT_ROOT, check=True)

    msg = f"auto-exp: {name} | AUC {old_auc:.4f} → {new_auc:.4f}"
    subprocess.run(
        ["git", "commit", "-m", msg, "--no-verify"],
        cwd=PROJECT_ROOT,
        check=True,
    )


# ── MLflow logging ────────────────────────────────────────────────────────────


def log_to_mlflow(
    exp_num: int,
    proposal: dict,
    pipeline_result: dict,
    auc_before: float,
    improved: bool,
    error_msg: str = "",
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

        # Log attempted files as artifacts (even reverted ones — full audit trail)
        if proposal.get("params_yaml"):
            mlflow.log_text(proposal["params_yaml"], "attempted/params.yaml")
        if proposal.get("train_py"):
            mlflow.log_text(proposal["train_py"], "attempted/train.py")
        if proposal.get("preprocess_py"):
            mlflow.log_text(proposal["preprocess_py"], "attempted/preprocess.py")

        # Link to the actual training run if pipeline succeeded
        if pipeline_result["success"]:
            run_id_file = PROJECT_ROOT / "models" / "run_id.txt"
            if run_id_file.exists():
                mlflow.set_tag("linked_train_run_id", run_id_file.read_text().strip())

        if error_msg:
            mlflow.set_tag("error", error_msg[:500])

        for metric_name, val in pipeline_result.get("metrics", {}).items():
            mlflow.log_metric(f"pipeline_{metric_name}", val)


# ── TSV logging ───────────────────────────────────────────────────────────────


def log_to_tsv(
    exp_num: int, proposal: dict, pipeline_result: dict, auc_before: float, outcome: str
):
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        "exp_num": exp_num,
        "experiment_name": proposal.get("experiment_name", "unnamed"),
        "change_type": proposal.get("change_type", "unknown"),
        "auc_before": f"{auc_before:.4f}",
        "auc_after": f"{pipeline_result['auc']:.4f}",
        "delta": f"{pipeline_result['auc'] - auc_before:+.4f}",
        "outcome": outcome,
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

    # Load baseline from params.yaml
    with open(PROJECT_ROOT / "configs/params.yaml") as f:
        auto_cfg = yaml.safe_load(f).get("auto_experiment", {})
    baseline_auc = auto_cfg.get("baseline_auc", 0.8162)
    if min_improvement == 0.001:  # use params.yaml value if not overridden
        min_improvement = auto_cfg.get("min_improvement", 0.001)

    best_auc = read_metrics()["auc_roc"]
    start_time = time.time()

    print(f"\n{'=' * 60}")
    print(f"AutoResearch Loop — {n_experiments} experiments, {hours}h budget")
    print(f"Baseline AUC: {baseline_auc:.4f} | Current best: {best_auc:.4f}")
    print(f"Min improvement threshold: {min_improvement}")
    print(f"Claude model: {claude_model}")
    print(f"Dry run: {dry_run}")
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
            proposal = call_claude(state, model=claude_model)
        except Exception as e:
            print(f"  ERROR calling Claude: {e}")
            continue

        print(
            f"[{i}/{n_experiments}] Proposal: {proposal.get('experiment_name', 'unnamed')}"
        )
        print(f"  Rationale: {proposal.get('rationale', '')[:200]}")
        print(f"  Change type: {proposal.get('change_type', 'unknown')}")

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

        # Apply changes
        originals = snapshot_files(proposal)
        apply_changes(proposal)
        ruff_fix(proposal)

        # Run pipeline
        print(f"[{i}/{n_experiments}] Running dvc repro...")
        pipeline_result = run_pipeline(timeout=300)

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
            )
            log_to_tsv(i, proposal, pipeline_result, best_auc, "failed")
            continue

        new_auc = pipeline_result["auc"]
        delta = new_auc - best_auc
        improved = delta >= min_improvement

        if improved:
            print(f"  ✓ IMPROVED: {best_auc:.4f} → {new_auc:.4f} (+{delta:.4f})")
            commit_improvement(proposal, best_auc, new_auc)
            prev_best = best_auc
            best_auc = new_auc
            log_to_mlflow(i, proposal, pipeline_result, prev_best, True)
            log_to_tsv(i, proposal, pipeline_result, prev_best, "improved")
        else:
            print(
                f"  ✗ REVERTED: {new_auc:.4f} did not beat {best_auc:.4f} (delta={delta:+.4f})"
            )
            revert_files(originals)
            log_to_mlflow(i, proposal, pipeline_result, best_auc, False)
            log_to_tsv(i, proposal, pipeline_result, best_auc, "reverted")

    total_elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"Loop complete. {i} experiments in {total_elapsed / 60:.1f} minutes.")
    print(
        f"Final best AUC: {best_auc:.4f} (started at {baseline_auc:.4f}, delta={best_auc - baseline_auc:+.4f})"
    )
    print(f"{'=' * 60}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AutoResearch-inspired experiment loop for churn model"
    )
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
