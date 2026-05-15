# Trust-first recording plan

This demo should make one thing obvious: the project is not just "Claude wrote some ML code." It is an accountable release loop where every model promotion has receipts across KFP, MLflow, GitHub, ArgoCD, and the live API.

The video should therefore show fewer surfaces, but tie them together harder.

## Core pushback

Do not record the current script exactly as-is unless the live cluster actually has the state the captions claim.

The existing WebReel configs are valid, but several labels are aspirational:

- `MLflow - all 20 autoresearch runs` is only credible if MLflow really shows 20 relevant runs.
- `AUC trajectory across 20 iters` is only credible if the chart is already configured and visible.
- `train metrics` in KFP is probably the wrong phrasing because metrics are primarily logged by the evaluate step / MLflow, not by clicking the train node in KFP.
- The GitHub PR search for `autoresearch` may miss PRs if the real PR titles are `auto-exp: ...`; use the query that matches the actual merged PR trail.
- The ArgoCD scene currently types a password into a recording workflow. Do not publish that raw clip, and preferably do not record login at all.

The strongest demo is not "look how much infra I know." The strongest demo is "here is the transaction boundary, and here are the IDs proving every handoff."

## Recommended storyline

Target length: 4 to 5 minutes.

1. Hook: 15 seconds

   Show one dense composite frame:

   - MLflow champion version and metric.
   - GitHub merged PR with Verified badge.
   - ArgoCD Synced / Healthy.
   - API `/health` returning `model_version` / `model_run_id`.

   Voiceover:

   > I built an agentic MLOps loop where Claude proposes changes, Kubeflow trains them, MLflow promotes only winners, GitHub records the signed PR, and ArgoCD rolls the live API. The point is not the fraud model. The point is accountable automation.

2. System map: 20 seconds

   Use the README architecture diagram or a clean static slide. Do not linger. The audience should know the chain before seeing the evidence:

   Claude proposal -> KFP run -> MLflow run/version -> GitHub PR -> ArgoCD rollout -> FastAPI health/predict.

3. Live kick-off / terminal: 35 seconds

   Show:

   ```bash
   git rev-parse --short HEAD
   make autoresearch-run AUTORESEARCH_N=...
   ```

   Keep the terminal pane as proof that this is a real run, not a hand-built slide. Speed-ramp dead time. If a full run takes too long, show the start and then cut to completed state.

4. KFP proof: 35 seconds

   Show the latest successful run and the DAG. The caption should be:

   > Kubeflow ran preprocess -> train -> evaluate for this candidate.

   Avoid saying "train metrics" unless the UI truly shows metric cards there. Better evidence is the KFP run ID. The important thing to prove is that the run exists and succeeded.

5. MLflow proof: 50 seconds

   Show:

   - experiment runs filtered to this autoresearch run, not random historical runs;
   - `auc_roc`, `average_precision`, `f1`, and params;
   - `kfp_run_id` tag matching the KFP run;
   - registered model `classifier` and `@champion` alias.

   Voiceover:

   > I do not query "latest run" and hope. The training run is linked by run ID and tagged with the KFP run ID, so the controller can identify the exact model that came out of this pipeline execution.

6. GitHub accountability proof: 55 seconds

   Show the actual merged PR for the winning candidate:

   - PR title with AUC before -> after.
   - PR body containing KFP run ID, MLflow run ID, model version.
   - Files changed tab.
   - `k8s/deployment.yaml` annotation bump.
   - GitHub App / Verified commit indicator.

   Push this hard. This is the most job-market-relevant part because it shows agentic AI plus governance, not just a bot running scripts.

7. ArgoCD + live API proof: 45 seconds

   Show ArgoCD already logged in. Do not record typing the admin password unless it is a throwaway password and you cut or blur the HUD.

   Show:

   - app is Synced / Healthy;
   - deployment pod-template annotations include the same version/run ID from the PR;
   - terminal `curl /health` returns the model version/run ID;
   - optional one real `/predict` call.

8. Limitation slide: 30 seconds

   End with explicit engineering honesty:

   - This is a portfolio-scale single-zone cluster, not HA production.
   - Model serving is FastAPI, not KServe/Seldon by design.
   - The agentic trust boundary is constrained by signed PRs and metric gates, but generated code still needs stronger policy controls before production.
   - If source diffs to training code are shown in PRs, say clearly whether the KFP run trained that source or whether the source lands for a later image rebuild.

   This limitation slide builds more trust than another dashboard shot.

## Preflight checklist

Run these before recording and paste the important IDs into a notes file for narration.

```bash
# main repo
git status --short
git rev-parse --short HEAD

# live API
curl -s http://34.47.242.89/health | jq .

# GitOps manifest proof
rg -n "mlops/classifier-version|mlops/classifier-run-id|image:" k8s/deployment.yaml

# recent PR trail
gh pr list --state merged --search "auto-exp" --limit 10
```

Manual UI checks:

- MLflow has a visible champion version and a run with the expected metric.
- MLflow run tag `kfp_run_id` matches the KFP run being shown.
- KFP latest run is green and is actually from autoresearch, not bootstrap/manual testing.
- GitHub PR query returns real merged autoresearch PRs.
- ArgoCD app is already logged in, Synced, and Healthy.
- No secret, private token, or reusable password appears in any raw clip.

## WebReel config edits to consider before recording

These are not required for schema validity; they are credibility fixes.

1. Change MLflow captions from fixed counts to actual state.

   Prefer:

   > MLflow - autoresearch runs

   Avoid:

   > all 20 autoresearch runs

   unless the UI really shows 20.

2. Change KFP labels away from "train metrics."

   Prefer:

   > pipeline run succeeded
   > evaluate step records metrics to MLflow

3. Change GitHub search query to match the real PR titles.

   If PRs are titled `auto-exp: ...`, use a query for `auto-exp` instead of `autoresearch`.

4. Do not record ArgoCD login in the final cut.

   Start from an already-authenticated tab, use a still, or record with a temporary password and remove the login segment completely.

5. Add one terminal-only receipt clip.

   A short clip with `curl /health`, `git log -1 -- k8s/deployment.yaml`, and `gh pr view <PR> --json ...` may be more convincing than another dashboard pan.

## Recruiter / hiring-manager framing

Current 2026 AI platform roles are asking for three clusters of evidence:

- agentic systems that are observable, controllable, and secure;
- Kubernetes / CI-CD / GitOps ownership, not just notebook training;
- reliability signals: traces, logs, alerts, SLOs, rollback, cost, and governance.

This project is strongest on:

- agentic workflow with a real tool-use loop;
- KFP + MLflow + GitHub + ArgoCD integration;
- signed PR audit trail;
- model registry and rollout mechanics;
- practical Kubernetes packaging.

It is weaker on:

- observability and alerting;
- IaC / Terraform;
- production serving frameworks such as KServe, Seldon, Ray Serve, Triton, vLLM;
- online model monitoring, drift detection, and feedback loops;
- multi-tenant security.

Do not try to hide those gaps. Say:

> I intentionally built the control plane and accountability chain first. The next production-hardening layer would be OpenTelemetry/Prometheus alerts, policy checks for LLM-generated diffs, and traffic-aware serving.

That answer sounds senior because it separates portfolio scope from production obligations.

## What to emphasize in the final edit

Use repeated ID matching as the visual motif:

- KFP run ID appears in KFP.
- Same KFP run ID appears as an MLflow tag.
- Same MLflow run ID appears in PR body.
- Same champion version/run ID appears in `k8s/deployment.yaml`.
- Same version/run ID appears from `/health`.

That makes the viewer think: this person understands lineage.

## What not to say

Avoid:

- "production-grade" without qualifiers;
- "the live model can never get worse";
- "any binary CSV plugs in";
- "zero risk";
- "Claude improves the model 30x while I sleep";
- "20 runs" if the UI does not show 20;
- "source diffs trained immediately" unless that is actually true for the KFP image/run being shown.

Use instead:

- "portfolio-scale production pattern";
- "only metric-winning candidates trigger rollout";
- "the release path is auditable";
- "the model can still be scientifically weak, but the promotion chain is inspectable";
- "this is an agentic release-control demo, not a fraud-detection benchmark."

## Recording-day command flow

From the demo worktree:

```bash
cd ~/Desktop/code/experiment/customer_churn-webreel-demo/demo/webreel
cp .env.example .env
$EDITOR .env
pnpm run validate:all
pnpm run record:smoke
pnpm run preview:mlflow
pnpm run preview:kfp
pnpm run preview:github
pnpm run preview:argocd
```

Start terminal capture only when the cluster is ready:

```bash
./scripts/capture-terminal.sh "make autoresearch-run AUTORESEARCH_N=20 AUTORESEARCH_HOURS=4.0"
```

Record browser scenes after the run has created evidence:

```bash
set -a; source .env; set +a
pnpm run record:all
```

After recording:

- rotate any ArgoCD/admin password that appeared anywhere in raw footage;
- review clips frame-by-frame around typed secrets;
- cut dead UI loading time;
- keep the final video focused on IDs and handoffs.

## Sources checked for job-market framing

- PepsiCo Senior Principal AI Observability Architect posting, May 2026: emphasizes agentic observability, traces, safety/security, SLOs, runbooks, cost telemetry, and governance.
- HPE Agentic AI/ML Engineer posting, April 2026: emphasizes production-grade agentic orchestration, MCP/tool infrastructure, reliability, observability, Kubernetes, CI/CD, autoscaling, and resource management.
- MLOps Community Senior MLOps Engineer posting: emphasizes Kubernetes, Terraform/Helm, KServe/Kubeflow, ArgoCD/FluxCD, Prometheus/Grafana/Loki, model-serving stacks, and feedback loops.
