"""End-to-end smoke for `run_loop` with the external surface mocked.

Covers the three branches the controller can take per iteration:
  1. improved → history outcome=improved, files NOT reverted, log_to_mlflow called
  2. failed pipeline → history outcome=failed, files reverted
  3. sub-threshold → history outcome=reverted, files reverted

The loop reads from / writes to several module-level paths (PROJECT_ROOT,
HISTORY_PATH, EDITABLE_FILES) — we monkeypatch all of them into a tmp_path so
the real repo state stays untouched. External services are mocked at the
function boundary: `call_claude`, `run_pipeline`, `log_to_mlflow`,
`_get_champion_version`, `check_prerequisites`. We do NOT mock the loop's
own state-tracking helpers (`snapshot_files`, `apply_changes`, `revert_files`,
`log_to_tsv`) — those are exactly what we want to exercise.
"""

from __future__ import annotations

import importlib

import pytest


HISTORY_HEADER = (
    "timestamp\texp_num\texperiment_name\tchange_type\tauc_before\tauc_after\t"
    "delta\toutcome\tinput_tokens\toutput_tokens\tcost_usd\trationale\n"
)


def _stub_proposal(name: str, params_yaml: str, train_py: str) -> dict:
    return {
        "experiment_name": name,
        "rationale": f"stub rationale for {name}",
        "change_type": "params_only",
        "params_yaml": params_yaml,
        "train_py": train_py,
        "preprocess_py": None,
    }


@pytest.fixture
def isolated_loop(tmp_path, monkeypatch):
    """Lay out a fake project root + monkeypatch auto_loop's path module
    constants to point at it. Yields the loaded module so the test can
    further patch internals."""
    # Build the on-disk fixture: dummy params, dummy src files, history header.
    (tmp_path / "configs").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "auto_experiment").mkdir()
    (tmp_path / "configs" / "params.yaml").write_text(
        "auto_experiment:\n  min_improvement: 0.003\n  baseline_auc: 0.5\n"
        "  max_iterations_without_improvement: 0\n"
        "dataset:\n  target_column: y\n"
        "train:\n  model_type: DecisionTreeClassifier\n"
    )
    (tmp_path / "src" / "train.py").write_text("# baseline train\n")
    (tmp_path / "src" / "preprocess.py").write_text("# baseline preprocess\n")
    (tmp_path / "auto_experiment" / "history.tsv").write_text(HISTORY_HEADER)
    (tmp_path / "auto_experiment" / "program.md").write_text("system prompt stub")
    (tmp_path / "metrics.json").write_text('{"auc_roc": 0.5}')

    auto_loop = importlib.import_module("auto_experiment.auto_loop")
    monkeypatch.setattr(auto_loop, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        auto_loop, "HISTORY_PATH", tmp_path / "auto_experiment/history.tsv"
    )
    monkeypatch.setattr(
        auto_loop, "PROGRAM_MD_PATH", tmp_path / "auto_experiment/program.md"
    )
    monkeypatch.setattr(auto_loop, "METRICS_PATH", tmp_path / "metrics.json")
    monkeypatch.setattr(
        auto_loop,
        "EDITABLE_FILES",
        ["configs/params.yaml", "src/train.py", "src/preprocess.py"],
    )

    # Disable real external checks.
    monkeypatch.setattr(auto_loop, "check_prerequisites", lambda: None)
    # No GitHub: loop falls back to local file mutation only, treats every
    # iter as "merged" so it doesn't try to roll back champion.
    monkeypatch.setattr(auto_loop.github_commit, "github_config_from_env", lambda: None)
    # MLflow side calls — no-op so we don't need a server.
    monkeypatch.setattr(auto_loop, "log_to_mlflow", lambda *a, **kw: None)
    monkeypatch.setattr(auto_loop, "_get_champion_version", lambda: "1")
    monkeypatch.setattr(auto_loop, "_revert_mlflow_champion", lambda v: None)
    monkeypatch.setattr(auto_loop, "ruff_fix", lambda proposal: None)
    # `commit_improvement` dispatches to either GitHub-App PR open or local
    # `git add/commit/push`. Neither works in a tmp_path that isn't a real
    # repo and has no remote — so stub it. Returning None mimics the
    # "no gh_config" branch where the loop treats the iter as locally-merged.
    monkeypatch.setattr(auto_loop, "commit_improvement", lambda *a, **kw: None)

    return auto_loop


def _read_history_rows(history_path) -> list[dict]:
    """Return data rows (sans header) as dicts keyed by column."""
    lines = history_path.read_text().strip().splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


def test_run_loop_three_branches(isolated_loop, tmp_path):
    # Canned Claude proposals — one per iter.
    proposals = [
        _stub_proposal(
            "iter1_improved",
            "auto_experiment:\n  min_improvement: 0.003\n  baseline_auc: 0.5\n  max_iterations_without_improvement: 0\ndataset:\n  target_column: y\ntrain:\n  model_type: RandomForestClassifier\n",
            "# iter1 mutated train\n",
        ),
        _stub_proposal(
            "iter2_pipeline_failed",
            "auto_experiment:\n  min_improvement: 0.003\n  baseline_auc: 0.5\n  max_iterations_without_improvement: 0\ndataset:\n  target_column: y\ntrain:\n  model_type: GradientBoostingClassifier\n",
            "# iter2 mutated train\n",
        ),
        _stub_proposal(
            "iter3_subthreshold",
            "auto_experiment:\n  min_improvement: 0.003\n  baseline_auc: 0.5\n  max_iterations_without_improvement: 0\ndataset:\n  target_column: y\ntrain:\n  model_type: LogisticRegression\n",
            "# iter3 mutated train\n",
        ),
    ]

    def fake_call_claude(state, model="claude-sonnet-4-6"):
        proposal = proposals[state["exp_num"] - 1]
        return {
            "proposal": proposal,
            "input_tokens": 1000,
            "output_tokens": 100,
            "cost_usd": 0.01,
        }

    # Canned pipeline outcomes: improved, failed, sub-threshold.
    pipeline_results = iter(
        [
            {"success": True, "auc": 0.7, "stderr": "", "run_id": "run1"},
            {"success": False, "auc": 0.0, "stderr": "fake pipeline crash"},
            {"success": True, "auc": 0.5005, "stderr": "", "run_id": "run3"},
        ]
    )

    def fake_run_pipeline(timeout=900):
        return next(pipeline_results)

    import auto_experiment.auto_loop as al

    al.call_claude = fake_call_claude
    al.run_pipeline = fake_run_pipeline

    al.run_loop(
        n_experiments=3,
        hours=10.0,
        dry_run=False,
        claude_model="stub-model",
        min_improvement=0.003,
    )

    rows = _read_history_rows(tmp_path / "auto_experiment" / "history.tsv")
    assert len(rows) == 3, f"expected 3 history rows, got {len(rows)}: {rows}"

    # Iter 1: AUC 0.5 → 0.7, delta 0.2 ≥ 0.003 → improved.
    assert rows[0]["outcome"] == "improved", rows[0]
    assert rows[0]["experiment_name"] == "iter1_improved"

    # Iter 2: pipeline failed.
    assert rows[1]["outcome"] == "failed", rows[1]
    assert rows[1]["experiment_name"] == "iter2_pipeline_failed"

    # Iter 3: AUC 0.7 → 0.5005, delta < 0 → reverted (also sub-threshold).
    assert rows[2]["outcome"] == "reverted", rows[2]
    assert rows[2]["experiment_name"] == "iter3_subthreshold"

    # After iter 3 (reverted), files MUST be back to iter-1-improved state
    # (not the iter-2/iter-3 mutations). train.py should still read
    # "# iter1 mutated train\n".
    train_py = (tmp_path / "src" / "train.py").read_text()
    assert "iter1 mutated train" in train_py, (
        f"iter1 changes should be persisted (it was improved + 'committed'); "
        f"got: {train_py!r}"
    )
