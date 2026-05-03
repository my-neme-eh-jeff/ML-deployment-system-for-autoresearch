.PHONY: repro train serve clean mlflow mlflow-kill promote test lint compile-kfp \
       argocd-ui argocd-password deploy-argocd deploy-mlflow k8s-status bootstrap demo demo-stop \
       gke-connect cluster-sleep cluster-wake gke-status gke-urls kfp-run \
       autoresearch-secret autoresearch-submit autoresearch-logs reset-for-fresh-run

# ── Local development ──────────────────────────────────────────────

# Requires cluster MLflow to be running (make deploy-mlflow) and port-forwarded (make mlflow).
# If port-forward silently fails (another process owns :5000), use 'make mlflow-kill' first.
repro:
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run dvc repro

train:
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run python src/train.py

serve:
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run uvicorn src.api:app --reload --port 8000

# Kill any process on port 5000 (e.g. a stray 'mlflow ui') then port-forward the cluster MLflow.
# WARNING: if another app is using :5000 legitimately, this will kill it.
mlflow-kill:
	@echo "Killing any process on port 5000..."
	@-lsof -ti :5000 | xargs kill -9 2>/dev/null || true
	@-pkill -f "kubectl port-forward -n mlflow" 2>/dev/null || true
	@echo "Port 5000 is now free."

# Port-forward the cluster MLflow to localhost:5000.
# Run 'make mlflow-kill' first if something else is already on :5000.
mlflow:
	@echo "MLflow UI at http://localhost:5000 (cluster)"
	@echo "Tip: if this silently fails, run 'make mlflow-kill' first."
	kubectl port-forward -n mlflow svc/mlflow 5000:5000

promote:
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run python src/promote.py

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/ pipelines/
	uv run ruff format --check src/ tests/ pipelines/

compile-kfp:
	uv run python pipelines/pipeline.py

clean:
	rm -rf data/processed models metrics.json

# ── Auto-experiment (AI-driven loop, autoresearch-style) ───────────
# Prereqs:
#   1. export ANTHROPIC_API_KEY=sk-ant-...
#   2. Run 'make mlflow-kill && make mlflow' in a separate terminal

# Dry run: show what Claude proposes, no pipeline execution
auto-experiment-dry-run:
	uv run python auto_experiment/auto_loop.py --n-experiments 1 --dry-run

# Run the full loop: 20 experiments, up to 2 hours
auto-experiment:
	MLFLOW_TRACKING_URI=http://localhost:5000 \
	uv run python auto_experiment/auto_loop.py --n-experiments 20 --hours 2.0

# ── Cluster bootstrap (first-time or after MLflow PVC data loss) ───
# Run this after 'make deploy-mlflow' to populate the model registry.
# Prerequisite: 'make mlflow' port-forward must be running in another terminal.
bootstrap:
	@echo "Step 1/2: Training model and registering in cluster MLflow..."
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run python src/train.py
	@echo "Step 2/2: Evaluating and setting @champion alias..."
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run python src/evaluate.py
	@echo ""
	@echo "Bootstrap complete. inference-api pods will load @champion on next restart."
	@echo "Run 'kubectl rollout restart deployment/inference-api -n inference' to trigger now."

# ── Docker ─────────────────────────────────────────────────────────

docker-build:
	docker buildx build \
		--platform linux/amd64,linux/arm64 \
		-t ghcr.io/my-neme-eh-jeff/inference-api:latest \
		--push \
		.

docker-run:
	docker run --rm -p 8000:8000 \
		-e MLFLOW_TRACKING_URI=http://host.docker.internal:5000 \
		ghcr.io/my-neme-eh-jeff/inference-api:latest

# ── GKE cluster ────────────────────────────────────────────────────

gke-connect:
	gcloud container clusters get-credentials mlops-cluster \
		--region=asia-south1 \
		--project=project-8018ed81-1dfe-470e-aad

GCP_PROJECT := project-8018ed81-1dfe-470e-aad
SQL_INSTANCE := churn-mlflow

# Components permanently disabled on this cluster (Autopilot incompatibility or unused features):
#   kubeflow/cache-deployer-deployment      — fails GKE Warden (CSR with system: prefix)
#   kubeflow/cache-server                    — needs the webhook the cache-deployer never created
#   kubeflow/ml-pipeline-viewer-crd          — visualization extra, not used
#   kubeflow/ml-pipeline-visualizationserver — visualization extra, not used
#   argocd/argocd-applicationset-controller  — ApplicationSet CRs not used in this project
#   argocd/argocd-notifications-controller   — Slack/email notifications not used
#
# KFP wake-safety: minio-pvc and mysql-pv-claim use storageClassName: standard-rwo-regional
# (replicates across 2 zones in asia-south1). Zonal PD locks the disk to one zone — Autopilot
# may bring up nodes in a different zone after sleep, and zonal PVCs can't follow.

ARGOCD_DEPLOYS := argocd-server argocd-repo-server argocd-redis argocd-dex-server
KFP_DEPLOYS    := mysql minio ml-pipeline ml-pipeline-ui ml-pipeline-persistenceagent \
                  ml-pipeline-scheduledworkflow workflow-controller \
                  metadata-grpc-deployment metadata-writer metadata-envoy-deployment

# Scale all workloads to 0 + stop CloudSQL to minimize compute billing.
# Disables ArgoCD auto-sync first so it can't revert the scale-down.
# Remaining: 4 LB forwarding rules (under 5/project free tier = $0), GCS, Artifact Registry,
# CloudSQL storage (~few GB), 2 regional 5Gi PVCs (KFP). Idle burn ≈ $0/day.
cluster-sleep:
	@echo "Disabling ArgoCD auto-sync (so scale-down isn't reverted on next wake)..."
	@kubectl patch applications.argoproj.io inference-api -n argocd --type=json \
		-p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]' 2>/dev/null || true
	@echo "Scaling all workloads to 0..."
	@kubectl scale deployment --all -n mlflow --replicas=0 2>/dev/null || true
	@kubectl scale deployment --all -n inference --replicas=0 2>/dev/null || true
	@kubectl scale deployment --all -n argocd --replicas=0 2>/dev/null || true
	@kubectl scale statefulset --all -n argocd --replicas=0 2>/dev/null || true
	@kubectl scale deployment --all -n kubeflow --replicas=0 2>/dev/null || true
	@echo "Stopping CloudSQL instance $(SQL_INSTANCE) (if not already stopped)..."
	@state=$$(gcloud sql instances describe $(SQL_INSTANCE) --project=$(GCP_PROJECT) --format='value(state)' 2>/dev/null); \
	if [ "$$state" = "STOPPED" ]; then \
		echo "  CloudSQL already STOPPED — skipping."; \
	else \
		gcloud sql instances patch $(SQL_INSTANCE) --activation-policy=NEVER \
			--project=$(GCP_PROJECT) --quiet 2>&1 | tail -2; \
	fi
	@echo "Cluster sleeping. CloudSQL stopped. Idle burn ≈ \$$0/day."
	@echo "Wake up with: make cluster-wake"

# Scale workloads back up + start CloudSQL after cluster-sleep.
# Only scales the components we actually use — see the disabled list above.
cluster-wake:
	@echo "Starting CloudSQL instance $(SQL_INSTANCE)..."
	@gcloud sql instances patch $(SQL_INSTANCE) --activation-policy=ALWAYS \
		--project=$(GCP_PROJECT) --quiet 2>&1 | tail -2 || true
	@echo "Waiting for CloudSQL to be RUNNABLE..."
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		state=$$(gcloud sql instances describe $(SQL_INSTANCE) --project=$(GCP_PROJECT) --format='value(state)' 2>/dev/null); \
		if [ "$$state" = "RUNNABLE" ]; then echo "  CloudSQL is RUNNABLE."; break; fi; \
		echo "  state=$$state, retrying in 15s..."; sleep 15; \
	done
	@echo "Waking mlflow + inference-api..."
	@kubectl scale deployment --all -n mlflow --replicas=1 2>/dev/null || true
	@kubectl scale deployment --all -n inference --replicas=2 2>/dev/null || true
	@echo "Waking ArgoCD core..."
	@for d in $(ARGOCD_DEPLOYS); do kubectl scale deployment $$d -n argocd --replicas=1 2>/dev/null || true; done
	@kubectl scale statefulset argocd-application-controller -n argocd --replicas=1 2>/dev/null || true
	@echo "Waking KFP core..."
	@for d in $(KFP_DEPLOYS); do kubectl scale deployment $$d -n kubeflow --replicas=1 2>/dev/null || true; done
	@echo "Re-enabling ArgoCD auto-sync..."
	@kubectl patch applications.argoproj.io inference-api -n argocd --type merge \
		-p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}' 2>/dev/null || true
	@echo "Waiting for MLflow to be ready (~2 min)..."
	@kubectl wait --for=condition=available --timeout=180s deployment/mlflow -n mlflow 2>/dev/null || true
	@echo ""
	@echo "URLs:"
	@make gke-urls
	@echo ""
	@echo "If inference-api returns 503, run: make bootstrap"

gke-status:
	@echo "=== Nodes ==="
	@kubectl get nodes
	@echo "=== MLflow ===" && kubectl get pods -n mlflow --no-headers 2>/dev/null
	@echo "=== KFP ===" && kubectl get pods -n kubeflow --no-headers 2>/dev/null | grep "ui\|pipeline" | head -5
	@echo "=== ArgoCD ===" && kubectl get pods -n argocd --no-headers 2>/dev/null | grep Running | head -3
	@echo "=== inference ===" && kubectl get pods -n inference --no-headers 2>/dev/null

gke-urls:
	@echo "MLflow UI:   http://$$(kubectl get svc mlflow -n mlflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}'):5000"
	@echo "ArgoCD UI:   http://$$(kubectl get svc argocd-server -n argocd -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
	@echo "KFP UI:      http://$$(kubectl get svc ml-pipeline-ui -n kubeflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
	@echo "Inference API: http://$$(kubectl get svc inference-api -n inference -o jsonpath='{.status.loadBalancer.ingress[0].ip}')/predict"

kfp-run:
	MLFLOW_TRACKING_URI=http://$$(kubectl get svc mlflow -n mlflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}'):5000 \
	uv run python pipelines/pipeline.py \
		--run \
		--host http://$$(kubectl get svc ml-pipeline-ui -n kubeflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# ── Autoresearch (in-cluster K8s Job) ──────────────────────────────

# Create / update the ANTHROPIC_API_KEY Secret in inference from the local .env file.
# Idempotent — safe to re-run.
autoresearch-secret:
	@if [ ! -f .env ]; then echo "ERROR: .env not found. Create it with ANTHROPIC_API_KEY=..."; exit 1; fi
	@kubectl create secret generic anthropic --namespace=inference \
		--from-env-file=.env \
		--dry-run=client -o yaml | kubectl apply -f - 2>&1 | tail -1

# Submit one autoresearch Job with a unique timestamp-based name.
# The Job manifest lives in jobs/ (NOT k8s/) so ArgoCD doesn't try to
# GitOps-manage it — Jobs are one-shot, not declarative state.
# Default uses Dockerfile CMD ["--n-experiments", "1", "--dry-run"] — smoke only.
autoresearch-submit:
	@ts=$$(date +%Y%m%d-%H%M%S); \
	sed "s/name: autoresearch-smoke/name: autoresearch-$$ts/" jobs/autoresearch-job.yaml | kubectl create -f - 2>&1
	@echo "Watch with: make autoresearch-logs"

# Submit a REAL autoresearch run (no --dry-run). Override iters + hours via env vars.
# Each kept improvement → its own per-iter PR with auto-merge enabled.
# Each PR's merge bumps the deployment.yaml model-version annotation, ArgoCD
# rolls inference-api, and the new pods serve the latest @champion.
#
#   make autoresearch-run                                  # 1 iter, 2h budget
#   make autoresearch-run AUTORESEARCH_N=5                 # 5 iters, 2h budget
#   make autoresearch-run AUTORESEARCH_N=10 AUTORESEARCH_HOURS=4.0
AUTORESEARCH_N ?= 1
AUTORESEARCH_HOURS ?= 2.0
autoresearch-run:
	@ts=$$(date +%Y%m%d-%H%M%S); \
	sed -e "s/name: autoresearch-smoke/name: autoresearch-real-$$ts/" \
	    -e "s|# args: \[.*\]|args: [\"--n-experiments\", \"$(AUTORESEARCH_N)\", \"--hours\", \"$(AUTORESEARCH_HOURS)\"]|" \
	    jobs/autoresearch-job.yaml | kubectl create -f - 2>&1
	@echo "Watch with: make autoresearch-logs"

# Reset autoresearch state for a clean fresh run.
# Run BEFORE a new dataset / new bad-baseline experiment so trajectory plots
# and `classifier` versions start from v1 again. NOT idempotent — destructive.
#   1. Empty auto_experiment/history.tsv to header-only (Claude's memory wipes)
#   2. Delete MLflow `classifier` registered model (drops every version + alias)
#   3. dvc repro --force against cluster MLflow → registers fresh v1 from
#      whatever configs/params.yaml currently says
#   4. Force-set classifier@champion to the new v1
#   5. Restart inference-api pods so they load v1
# Prerequisite: `make mlflow-kill && make mlflow` port-forward in another terminal.
reset-for-fresh-run:
	@echo "── 1/5: emptying history.tsv ──"
	@printf "timestamp\texp_num\texperiment_name\tchange_type\tauc_before\tauc_after\tdelta\toutcome\tinput_tokens\toutput_tokens\tcost_usd\trationale\n" > auto_experiment/history.tsv
	@echo "── 2/5: deleting MLflow classifier registered model ──"
	@MLFLOW_TRACKING_URI=http://localhost:5000 uv run python -c "import mlflow; c = mlflow.MlflowClient(); \
		[c.delete_registered_model('classifier')] if any(m.name == 'classifier' for m in c.search_registered_models()) else print('  (classifier not registered, skipping)')"
	@echo "── 3/5: dvc repro --force to register fresh v1 from current params.yaml ──"
	@rm -rf data/processed/test.csv data/processed/train.csv models metrics.json
	@MLFLOW_TRACKING_URI=http://localhost:5000 uv run dvc repro --force 2>&1 | tail -10
	@echo "── 4/5: force-set classifier@champion to v1 ──"
	@MLFLOW_TRACKING_URI=http://localhost:5000 uv run python -c "import mlflow; \
		c = mlflow.MlflowClient(); c.set_registered_model_alias('classifier', 'champion', '1'); \
		v = c.get_model_version_by_alias('classifier', 'champion'); \
		print(f'  @champion → v{v.version}')"
	@echo "── 5/5: restart inference-api pods to pick up v1 ──"
	@kubectl rollout restart deployment/inference-api -n inference 2>&1 | tail -1
	@echo
	@echo "Reset complete. Run 'make autoresearch-run AUTORESEARCH_N=5' when ready."

# Tail the most recent autoresearch Job's logs.
autoresearch-logs:
	@latest=$$(kubectl get pods -n inference -l app=autoresearch --sort-by='.metadata.creationTimestamp' --no-headers 2>/dev/null | tail -1 | awk '{print $$1}'); \
	if [ -z "$$latest" ]; then echo "No autoresearch pod found yet."; exit 1; fi; \
	echo "Tailing $$latest..."; kubectl logs -n inference $$latest -f

# ── Kubernetes (vind cluster) ──────────────────────────────────────

deploy-mlflow:
	kubectl apply -f k8s/mlflow.yaml
	@echo "Waiting for MLflow to be ready..."
	kubectl wait --for=condition=available --timeout=120s deployment/mlflow -n mlflow
	@echo "MLflow deployed. Run 'make mlflow' to port-forward."

deploy-argocd:
	kubectl apply -f argocd/application.yaml

argocd-ui:
	@echo "ArgoCD UI:  http://$$(kubectl get svc argocd-server -n argocd -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
	@echo "Inference API: http://$$(kubectl get svc inference-api -n inference -o jsonpath='{.status.loadBalancer.ingress[0].ip}')/health"

argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo

k8s-status:
	@echo "── MLflow pods ──"
	@kubectl get pods -n mlflow --no-headers 2>/dev/null || echo "  namespace not found"
	@echo "── ArgoCD pods ──"
	@kubectl get pods -n argocd --no-headers 2>/dev/null || echo "  namespace not found"
	@echo "── Churn serving ──"
	@kubectl get pods -n inference --no-headers 2>/dev/null || echo "  namespace not found"

demo:
	@echo "Starting all services (port-forwarding from cluster)..."
	@echo "  MLflow UI:    http://localhost:5000"
	@echo "  ArgoCD UI:    https://localhost:8090  (admin / $$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d))"
	@echo "  Inference API:    http://localhost:8001"
	@echo ""
	@kubectl port-forward -n mlflow svc/mlflow 5000:5000 &
	@echo "  ArgoCD:       http://$$(kubectl get svc argocd-server -n argocd -o jsonpath='{.status.loadBalancer.ingress[0].ip}') (no port-forward needed)"
	@kubectl port-forward -n inference svc/inference-api 8001:80 &
	@echo "All services running. Use 'make demo-stop' to stop."
	@wait

demo-stop:
	@echo "Stopping demo services..."
	@-pkill -f "kubectl port-forward" 2>/dev/null
	@echo "Done."
