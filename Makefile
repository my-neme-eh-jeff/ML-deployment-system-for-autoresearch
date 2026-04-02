.PHONY: repro train serve clean mlflow promote test lint compile-kfp \
       argocd-ui argocd-password deploy-argocd k8s-status demo demo-stop

# ── Local development ──────────────────────────────────────────────

repro:
	uv run dvc repro

train:
	uv run python src/train.py

serve:
	uv run uvicorn src.api:app --reload --port 8000

mlflow:
	uv run mlflow ui --port 5000

promote:
	uv run python src/promote.py

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/ pipelines/
	uv run ruff format --check src/ tests/ pipelines/

compile-kfp:
	uv run python pipelines/churn_pipeline.py

clean:
	rm -rf data/processed models metrics.json mlruns/

# ── Docker ─────────────────────────────────────────────────────────

docker-build:
	docker build -t churn-api:latest .

docker-run:
	docker run --rm -p 8000:8000 churn-api:latest

# ── Kubernetes (vind cluster) ──────────────────────────────────────

argocd-ui:
	@echo "ArgoCD UI at http://localhost:8090"
	kubectl port-forward -n argocd svc/argocd-server 8090:443

argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo

deploy-argocd:
	kubectl apply -f argocd/application.yaml

k8s-status:
	@echo "── ArgoCD pods ──"
	@kubectl get pods -n argocd --no-headers 2>/dev/null || echo "  namespace not found"
	@echo "── Churn serving ──"
	@kubectl get pods -n churn-serving --no-headers 2>/dev/null || echo "  namespace not found"

demo:
	@echo "Starting all services..."
	@echo "  MLflow UI:    http://localhost:5000"
	@echo "  ArgoCD UI:    https://localhost:8090  (admin / $$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d))"
	@echo "  Churn API:    http://localhost:8001"
	@echo ""
	@uv run mlflow ui --port 5000 &
	@kubectl port-forward -n argocd svc/argocd-server 8090:443 &
	@kubectl port-forward -n churn-serving svc/churn-api 8001:80 &
	@echo "All services running. Use 'make demo-stop' to stop."
	@wait

demo-stop:
	@echo "Stopping demo services..."
	@-pkill -f "mlflow ui" 2>/dev/null
	@-pkill -f "kubectl port-forward" 2>/dev/null
	@echo "Done."
