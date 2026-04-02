.PHONY: repro train serve clean mlflow promote test lint compile-kfp \
       argocd-ui argocd-password deploy-argocd deploy-mlflow k8s-status demo demo-stop

# ── Local development ──────────────────────────────────────────────

# Requires cluster MLflow to be running (make deploy-mlflow) and port-forwarded (make mlflow)
repro:
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run dvc repro

train:
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run python src/train.py

serve:
	MLFLOW_TRACKING_URI=http://localhost:5000 uv run uvicorn src.api:app --reload --port 8000

# Port-forward the cluster MLflow to localhost:5000
mlflow:
	@echo "MLflow UI at http://localhost:5000 (cluster)"
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

# ── Docker ─────────────────────────────────────────────────────────

docker-build:
	docker build -t ghcr.io/my-neme-eh-jeff/churn-api:latest .

docker-push:
	docker push ghcr.io/my-neme-eh-jeff/churn-api:latest

docker-run:
	docker run --rm -p 8000:8000 \
		-e MLFLOW_TRACKING_URI=http://host.docker.internal:5000 \
		ghcr.io/my-neme-eh-jeff/churn-api:latest

# ── Kubernetes (vind cluster) ──────────────────────────────────────

deploy-mlflow:
	kubectl apply -f k8s/mlflow.yaml
	@echo "Waiting for MLflow to be ready..."
	kubectl wait --for=condition=available --timeout=120s deployment/mlflow -n mlflow
	@echo "MLflow deployed. Run 'make mlflow' to port-forward."

deploy-argocd:
	kubectl apply -f argocd/application.yaml

argocd-ui:
	@echo "ArgoCD UI at http://$$(kubectl get svc argocd-server -n argocd -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"

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
