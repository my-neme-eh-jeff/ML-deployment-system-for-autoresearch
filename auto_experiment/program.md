# AutoResearch Program: Customer Churn Model Improvement

**Inspired by:** [Karpathy's autoresearch](https://github.com/karpathy/autoresearch)
> "Give an AI agent a small but real ML setup and let it experiment autonomously."

This project follows the autoresearch pattern adapted for sklearn + DVC + MLflow:
- Propose ONE focused change per iteration
- Run the full pipeline (`dvc repro`)
- Keep the change if AUC-ROC improves by ≥ 0.001, revert otherwise
- Every attempt (kept or reverted) is logged to MLflow

---

## Project Context

**Dataset:** Telco Customer Churn (Kaggle)
- 7,043 customers, 19 features, binary churn target
- Churn rate: ~26.5% (mild class imbalance)
- Train set: 5,634 rows | Test set: 1,409 rows

**Current baseline:** AUC-ROC = 0.8162 (RandomForest, 100 estimators)

**Primary metric:** AUC-ROC (higher is better). Secondary: F1, Recall.

**Pipeline:** `preprocess.py` → `train.py` → `evaluate.py`
- Run with: `dvc repro`
- Results in: `metrics.json`

**Feature sets:**
- Numeric (4): SeniorCitizen, tenure, MonthlyCharges, TotalCharges
- Categorical (15): gender, Partner, Dependents, PhoneService, MultipleLines, InternetService, OnlineSecurity, OnlineBackup, DeviceProtection, TechSupport, StreamingTV, StreamingMovies, Contract, PaperlessBilling, PaymentMethod

---

## What You Can Modify

**ALLOWED:**
- `configs/params.yaml` — hyperparameters, feature engineering flags, model type
- `src/train.py` — model training logic, `build_pipeline()`, feature engineering
- `src/preprocess.py` — data cleaning, feature creation, split strategy

**NEVER MODIFY (sacred):**
- `src/evaluate.py` — evaluation and champion/challenger logic
- `dvc.yaml` — pipeline DAG definition
- Output file paths: `models/churn_model.pkl`, `models/run_id.txt`, `metrics.json`, `data/processed/train.csv`, `data/processed/test.csv`
- MLflow patterns: `MODEL_NAME = "churn-model"`, `run_id_path.write_text(run.info.run_id)`, sklearn Pipeline wrapper
- The `auto_experiment:` section of params.yaml

---

## Research Directions (ordered by expected impact)

### Tier 1 — High confidence improvements

**1. Switch to HistGradientBoostingClassifier**
- Set `model_type: HistGradientBoostingClassifier` in params.yaml
- Native NaN handling, faster than RF, often 1-3% better AUC on tabular data
- No need to change any code — train.py already supports it
- Expected: +0.01 to +0.02 AUC

**2. Add `charges_per_month` interaction feature**
- Set `add_charges_per_month: true` in params.yaml
- `charges_per_month = TotalCharges / (tenure + 1)` captures "cost per month relative to tenure"
- High-value customers who pay more monthly are less likely to churn
- Expected: +0.005 to +0.01 AUC

### Tier 2 — Medium confidence

**3. Class weight balancing**
- Set `class_weight: balanced` in params.yaml (works for RF and ExtraTrees)
- Addresses the 26.5% churn rate imbalance
- Tends to improve Recall and F1 at slight AUC cost — watch the delta carefully
- Expected: +0.003 to -0.005 AUC (uncertain direction, helps F1 and Recall)

**4. Tune max_depth for RF**
- RandomForest grows fully by default (unlimited depth) — this can overfit
- Try `max_depth: 10` or `max_depth: 15`
- Expected: +0.002 to +0.008 AUC

**5. GradientBoostingClassifier tuned**
- Set `model_type: GradientBoostingClassifier`, `n_estimators: 300`, `learning_rate: 0.05`, `max_depth: 4`
- Slower than HistGBM but well-studied; good baseline for boosting
- Expected: +0.01 to +0.02 AUC

**6. Ordinal encoding for Contract**
- Modify `src/train.py`: replace OneHot for Contract with OrdinalEncoder (Month-to-month=0, One year=1, Two year=2)
- Contract is the single strongest churn predictor; ordinal encoding preserves the ordering signal
- Expected: +0.003 to +0.008 AUC

### Tier 3 — Speculative (try if Tier 1-2 exhausted)

**7. Log transform on charges**
- Set `use_log_transform: true` in params.yaml
- MonthlyCharges/TotalCharges are right-skewed; log1p normalizes them
- Low impact for tree models (they don't need scale normalization) but worth one try
- Expected: +0.000 to +0.005 AUC

**8. n_estimators tuning for RF**
- Try `n_estimators: 300` or `n_estimators: 500`
- More trees rarely hurt but may improve stability
- Expected: +0.001 to +0.003 AUC

---

## Key Business Context (helps with feature ideas)

The **strongest churn signals** in Telco data (from published research):
1. **Contract type** — Month-to-month churns at ~43%, Two-year at ~3%
2. **Tenure** — New customers (1-12 months) churn most
3. **InternetService** — Fiber optic customers churn more (premium, competitive)
4. **Electronic check payment** — Correlates with higher churn
5. **No online security/tech support** — Correlates with churn

Use this domain knowledge when proposing feature engineering ideas.

---

## Output Format

You MUST return ONLY a valid JSON object — no markdown, no explanation outside the JSON.

```json
{
  "rationale": "2-3 sentences explaining WHY this specific change should improve AUC-ROC",
  "change_type": "params_only",
  "experiment_name": "hist_gradient_boost_baseline",
  "params_yaml": "<full new content of configs/params.yaml, or null if unchanged>",
  "train_py": "<full new content of src/train.py, or null if unchanged>",
  "preprocess_py": "<full new content of src/preprocess.py, or null if unchanged>"
}
```

**Rules:**
- `change_type` must be one of: `params_only`, `train_py`, `preprocess_py`, `both_src`
- Always provide FULL file contents (not diffs) for any file you change
- `experiment_name` must be short snake_case (used as a git commit message and MLflow run name)
- Never set all three file fields to null — at least one must contain new content
- Keep the `auto_experiment:` section of params.yaml exactly as-is (never modify it)
- Preserve all invariants listed in "What You Can Modify" section

---

## History

The auto_loop.py script injects the last 10 experiment rows here at runtime.
If you see this placeholder, this is the first experiment — no history yet.
