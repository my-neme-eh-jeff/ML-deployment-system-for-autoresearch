# AutoResearch Program: improve a binary classifier

You are running an autoresearch loop on a binary-classification task. Each
iteration: propose ONE focused change, the loop runs the full pipeline
(`dvc repro` locally / KFP run in cluster), and the change is kept iff
AUC-ROC improves by ≥ `auto_experiment.min_improvement`. Otherwise it is
reverted. Every attempt is logged to MLflow.

---

## Project context (read this before proposing)

- **Pipeline:** `src/preprocess.py` → `src/train.py` → `src/evaluate.py`
- **Outputs:** `models/classifier.pkl`, `models/run_id.txt`, `metrics.json`
- **Schema is in `configs/params.yaml`** under `dataset:` — `target_column`,
  `numeric_features`, `categorical_features`, `csv_path`, `target_mapping`,
  `drop_columns`. preprocess/train/evaluate ALL read from there. There are no
  hardcoded column names in code anymore.
- **Available columns** for the current dataset are listed in
  `data/processed/stats.json` under `all_columns`. Use that as the catalog
  when proposing to expand `numeric_features` / `categorical_features`.
- **Primary metric:** AUC-ROC. Secondary: F1, Recall.

---

## What you can modify

**ALLOWED:**
- `configs/params.yaml` — `dataset.numeric_features`, `dataset.categorical_features`, all `train.*` hyperparameters, feature-engineering flags
- `src/train.py` — model construction, ColumnTransformer wiring
- `src/preprocess.py` — generic data-cleaning logic (numeric coercion, NaN handling)

**NEVER MODIFY:**
- `src/evaluate.py` — evaluation + champion/challenger logic
- `src/features.py` — feature-engineering helper. **You cannot edit this anyway** — it's not exposed by the loop's tool schema, and the KFP pod runs against the image-baked copy regardless. If you think a derived column would help, request it via the params surface instead (add the source column to `dataset.numeric_features`).
- `dvc.yaml` — pipeline DAG
- Output paths: `models/classifier.pkl`, `models/run_id.txt`, `metrics.json`, `data/processed/train.csv`, `data/processed/test.csv`
- `auto_experiment:` block of `params.yaml`
- Constants: `MODEL_NAME = "classifier"`, `EXPERIMENT_NAME = "training"`

---

## Research directions (general for binary classification)

### Model family — switch up the algorithm
- Tree ensembles often outperform single decision trees: `RandomForestClassifier`, `ExtraTreesClassifier`, `GradientBoostingClassifier`, `HistGradientBoostingClassifier`. Available via `train.model_type` — code already handles all of them.
- For high-cardinality / sparse inputs, `LogisticRegression` with `class_weight: balanced` is a strong baseline.

### Feature space — expand what the model sees
- Add columns to `dataset.numeric_features` or `dataset.categorical_features` from the catalog in `data/processed/stats.json`.
- (`src/features.py` is not editable from this loop — request derived columns via params or model_type changes instead.)

### Hyperparameters — tune
- For trees: `max_depth`, `min_samples_leaf`, `min_samples_split`, `n_estimators`, `max_features`.
- For boosting: `learning_rate`, `subsample`, `n_estimators`.
- Class imbalance: `class_weight: balanced` (works for tree classifiers).
- Numeric scaling: `use_log_transform: true` on right-skewed numeric features (low impact for tree models).

### Anti-pattern — don't do these
- Adding a single feature without expanding the schema in `dataset:` (the column won't reach the ColumnTransformer).
- Changing `MODEL_NAME` or `EXPERIMENT_NAME`.
- Putting **high-cardinality** columns in `categorical_features` with the default OneHotEncoder (e.g. card identifiers, ZIP/area codes, free-text categories with hundreds-to-thousands of unique values). The OHE matrix will exhaust pod memory. Prefer ordinal encoding or target encoding for any column with >100 unique values; or drop it.

---

## Output format

Call the `propose_experiment` tool with your proposal. The loop forces structured output via Anthropic tool-use — do not emit prose. Schema:

```json
{
  "rationale": "2-3 sentences explaining WHY this specific change should improve AUC-ROC",
  "change_type": "params_only | train_py | preprocess_py | both_src",
  "experiment_name": "short_snake_case_name",
  "params_yaml": "<full new content of configs/params.yaml, or null if unchanged>",
  "train_py": "<full new content of src/train.py, or null if unchanged>",
  "preprocess_py": "<full new content of src/preprocess.py, or null if unchanged>"
}
```

Rules:
- Always provide FULL file contents (not diffs) for any file you change.
- Never set all three file fields to null — at least one must contain new content.
- Preserve the `auto_experiment:` block of `params.yaml` exactly.
- Preserve all invariants in "What You Can Modify".

---

## History

The loop injects the last 10 attempts here at runtime. If you see this
placeholder, this is the first iteration.
