.PHONY: repro train serve clean mlflow mlflow-kill promote test lint compile-kfp \
       argocd-ui argocd-password deploy-argocd deploy-mlflow k8s-status bootstrap demo demo-stop

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
	uv run python pipelines/churn_pipeline.py

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
	@echo "Bootstrap complete. churn-api pods will load @champion on next restart."
	@echo "Run 'kubectl rollout restart deployment/churn-api -n churn-serving' to trigger now."

# ── Docker ─────────────────────────────────────────────────────────

docker-build:
	docker buildx build \
		--platform linux/amd64,linux/arm64 \
		-t ghcr.io/my-neme-eh-jeff/churn-api:latest \
		--push \
		.

docker-run:
	docker run --rm -p 8000:8000 \
		-e MLFLOW_TRACKING_URI=http://host.docker.internal:5000 \
		ghcr.io/my-neme-eh-jeff/churn-api:latest

# ── GKE cluster ────────────────────────────────────────────────────

gke-connect:
	gcloud container clusters get-credentials mlops-cluster \
		--region=asia-south1 \
		--project=project-8018ed81-1dfe-470e-aad

# Scale down all workloads to 0 replicas to stop compute billing.
# CloudSQL + Load Balancer IPs + PVCs still bill (~$25/month).
cluster-sleep:
	@echo "Scaling all workloads to 0..."
	@kubectl scale deployment --all -n mlflow --replicas=0 2>/dev/null || true
	@kubectl scale deployment --all -n churn-serving --replicas=0 2>/dev/null || true
	@kubectl scale deployment --all -n argocd --replicas=0 2>/dev/null || true
	@kubectl scale statefulset --all -n argocd --replicas=0 2>/dev/null || true
	@kubectl scale deployment --all -n kubeflow --replicas=0 2>/dev/null || true
	@echo "Cluster sleeping. Still billing: CloudSQL + LB IPs + PVCs (~$$25/month)"
	@echo "Wake up with: make cluster-wake"

# Scale workloads back up after cluster-sleep.
cluster-wake:
	@echo "Waking cluster..."
	@kubectl scale deployment --all -n mlflow --replicas=1 2>/dev/null || true
	@kubectl scale deployment --all -n churn-serving --replicas=2 2>/dev/null || true
	@kubectl scale deployment --all -n argocd --replicas=1 2>/dev/null || true
	@kubectl scale statefulset --all -n argocd --replicas=1 2>/dev/null || true
	@kubectl scale deployment --all -n kubeflow --replicas=1 2>/dev/null || true
	@echo "Waiting for MLflow to be ready (~2 min)..."
	@kubectl wait --for=condition=available --timeout=180s deployment/mlflow -n mlflow 2>/dev/null || true
	@echo ""
	@echo "URLs:"
	@make gke-urls
	@echo ""
	@echo "If churn-api returns 503, run: make bootstrap"

gke-status:
	@echo "=== Nodes ==="
	@kubectl get nodes
	@echo "=== MLflow ===" && kubectl get pods -n mlflow --no-headers 2>/dev/null
	@echo "=== KFP ===" && kubectl get pods -n kubeflow --no-headers 2>/dev/null | grep "ui\|pipeline" | head -5
	@echo "=== ArgoCD ===" && kubectl get pods -n argocd --no-headers 2>/dev/null | grep Running | head -3
	@echo "=== churn-serving ===" && kubectl get pods -n churn-serving --no-headers 2>/dev/null

gke-urls:
	@echo "MLflow UI:   http://$$(kubectl get svc mlflow -n mlflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}'):5000"
	@echo "ArgoCD UI:   http://$$(kubectl get svc argocd-server -n argocd -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
	@echo "KFP UI:      http://$$(kubectl get svc ml-pipeline-ui -n kubeflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
	@echo "Churn API:   http://$$(kubectl get svc churn-api -n churn-serving -o jsonpath='{.status.loadBalancer.ingress[0].ip}')/predict"

kfp-run:
	MLFLOW_TRACKING_URI=http://$$(kubectl get svc mlflow -n mlflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}'):5000 \
	uv run python pipelines/churn_pipeline.py \
		--run \
		--host http://$$(kubectl get svc ml-pipeline-ui -n kubeflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

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
	@echo "Churn API:  http://$$(kubectl get svc churn-api -n churn-serving -o jsonpath='{.status.loadBalancer.ingress[0].ip}')/health"

argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo

k8s-status:
	@echo "── MLflow pods ──"
	@kubectl get pods -n mlflow --no-headers 2>/dev/null || echo "  namespace not found"
	@echo "── ArgoCD pods ──"
	@kubectl get pods -n argocd --no-headers 2>/dev/null || echo "  namespace not found"
	@echo "── Churn serving ──"
	@kubectl get pods -n churn-serving --no-headers 2>/dev/null || echo "  namespace not found"

demo:
	@echo "Starting all services (port-forwarding from cluster)..."
	@echo "  MLflow UI:    http://localhost:5000"
	@echo "  ArgoCD UI:    https://localhost:8090  (admin / $$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d))"
	@echo "  Churn API:    http://localhost:8001"
	@echo ""
	@kubectl port-forward -n mlflow svc/mlflow 5000:5000 &
	@echo "  ArgoCD:       http://$$(kubectl get svc argocd-server -n argocd -o jsonpath='{.status.loadBalancer.ingress[0].ip}') (no port-forward needed)"
	@kubectl port-forward -n churn-serving svc/churn-api 8001:80 &
	@echo "All services running. Use 'make demo-stop' to stop."
	@wait

demo-stop:
	@echo "Stopping demo services..."
	@-pkill -f "kubectl port-forward" 2>/dev/null
	@echo "Done."
