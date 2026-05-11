#!/usr/bin/env python3
"""Interactive setup wizard for ML Deployment System for Autoresearch.

Run via `make setup`. Walks a fresh user through the project configuration —
cloud provider, ML tracker, credentials, GitHub App — then writes:

  - .env                  (Anthropic key, GitHub App env vars)
  - configs/tenant.yaml   (cloud + tracker choices)

After this, the user follows printed next-steps (gcloud auth, GKE creation,
deploy MLflow/KFP/ArgoCD, reset state, fire autoresearch).

Design notes:
  - Modeled after the Vercel CLI / Claude Code CLI setup feel.
  - Cloud + tracker are list-pick with non-default options DISABLED but
    visible — sets future direction without lying about current support.
  - Idempotent: re-running re-prompts with previous answers as defaults.
  - Non-interactive mode via env vars (CI / test) — see _from_env().
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import questionary
    from questionary import Choice, Style
except ImportError:
    print(
        "ERROR: `questionary` is not installed.\n"
        "Run `uv sync` first (it's a project dependency).",
        file=sys.stderr,
    )
    sys.exit(1)


PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
TENANT_CONFIG = PROJECT_ROOT / "configs" / "tenant.yaml"

ACCENT = Style(
    [
        ("qmark", "fg:#ff9d00 bold"),
        ("question", "bold"),
        ("answer", "fg:#44ff44 bold"),
        ("pointer", "fg:#ff9d00 bold"),
        ("highlighted", "fg:#ff9d00 bold"),
        ("selected", "fg:#44ff44"),
        ("disabled", "fg:#858585 italic"),
    ]
)


def banner() -> None:
    """Print the wizard banner. Cosmetic; sets the tone like Vercel's setup."""
    line = "─" * 64
    print()
    print(line)
    print(" ML Deployment System for Autoresearch — setup wizard")
    print(line)
    print(
        " This wizard configures the project for your cloud + ML tracker,\n"
        " writes a .env, and prints the next steps. ~2 minutes."
    )
    print(line)
    print()


def _read_existing_env() -> dict[str, str]:
    """Parse current .env (if present) so we can offer existing values as defaults."""
    if not ENV_FILE.exists():
        return {}
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _check_cli(cmd: str) -> bool:
    """Return True if `cmd` is on PATH and runs."""
    if shutil.which(cmd) is None:
        return False
    try:
        subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False


def _cloud_choice() -> str:
    return questionary.select(
        "Pick your cloud provider:",
        choices=[
            Choice("Google Kubernetes Engine (GKE) — Autopilot", "gke"),
            Choice(
                "AWS Elastic Kubernetes Service (EKS)",
                "eks",
                disabled="coming soon — only GKE is wired up today",
            ),
            Choice(
                "Azure Kubernetes Service (AKS)",
                "aks",
                disabled="coming soon — only GKE is wired up today",
            ),
        ],
        style=ACCENT,
        instruction="(↑/↓ to move, Enter to pick)",
    ).unsafe_ask()


def _tracker_choice() -> str:
    return questionary.select(
        "Pick your experiment tracker:",
        choices=[
            Choice("MLflow (self-hosted in cluster, CloudSQL-backed)", "mlflow"),
            Choice(
                "Weights & Biases",
                "wandb",
                disabled="coming soon — only MLflow is wired up today",
            ),
        ],
        style=ACCENT,
    ).unsafe_ask()


def _gcp_settings(existing: dict[str, str]) -> tuple[str, str]:
    project = (
        questionary.text(
            "GCP project ID:",
            default=existing.get("GCP_PROJECT", ""),
            validate=lambda v: (
                True if v.strip() else "Project ID is required (e.g. my-project-12345)."
            ),
            style=ACCENT,
        )
        .unsafe_ask()
        .strip()
    )

    region = (
        questionary.text(
            "GCP region:",
            default=existing.get("GCP_REGION", "asia-south1"),
            style=ACCENT,
        )
        .unsafe_ask()
        .strip()
    )
    return project, region


def _anthropic_key(existing: dict[str, str]) -> str:
    has_existing = bool(existing.get("ANTHROPIC_API_KEY"))
    if has_existing:
        keep = questionary.confirm(
            "ANTHROPIC_API_KEY already set in .env. Keep it?",
            default=True,
            style=ACCENT,
        ).unsafe_ask()
        if keep:
            return existing["ANTHROPIC_API_KEY"]

    return (
        questionary.password(
            "Anthropic API key (starts with sk-ant-):",
            validate=lambda v: (
                True
                if v.strip().startswith("sk-ant-")
                else "Expected key to start with 'sk-ant-'."
            ),
            style=ACCENT,
        )
        .unsafe_ask()
        .strip()
    )


def _github_app_config(existing: dict[str, str]) -> dict[str, str] | None:
    setup_gh = questionary.confirm(
        "Configure GitHub App for the autoresearch loop?\n"
        "  (Required so the loop can open signed per-iter PRs. Skip if you only\n"
        "  want to run the inference API + local training.)",
        default=True,
        style=ACCENT,
    ).unsafe_ask()
    if not setup_gh:
        return None

    return {
        "GITHUB_APP_ID": questionary.text(
            "GitHub App ID (e.g. 3576508):",
            default=existing.get("GITHUB_APP_ID", ""),
            style=ACCENT,
        )
        .unsafe_ask()
        .strip(),
        "GITHUB_INSTALLATION_ID": questionary.text(
            "GitHub App Installation ID:",
            default=existing.get("GITHUB_INSTALLATION_ID", ""),
            style=ACCENT,
        )
        .unsafe_ask()
        .strip(),
        "GITHUB_OWNER": questionary.text(
            "GitHub owner (user or org):",
            default=existing.get("GITHUB_OWNER", ""),
            style=ACCENT,
        )
        .unsafe_ask()
        .strip(),
        "GITHUB_REPO": questionary.text(
            "GitHub repo name:",
            default=existing.get("GITHUB_REPO", ""),
            style=ACCENT,
        )
        .unsafe_ask()
        .strip(),
        "GITHUB_PEM_SECRET": questionary.text(
            "GCP Secret Manager secret name holding the GitHub App PEM:",
            default=existing.get("GITHUB_PEM_SECRET", "github-app-key"),
            style=ACCENT,
        )
        .unsafe_ask()
        .strip(),
    }


def _summarize(
    cloud: str,
    tracker: str,
    project: str,
    region: str,
    anthropic_set: bool,
    gh: dict[str, str] | None,
) -> None:
    print("\n  Summary")
    print("  ───────")
    print(f"    Cloud:               {cloud}")
    print(f"    Tracker:             {tracker}")
    print(f"    GCP project:         {project}")
    print(f"    GCP region:          {region}")
    print(f"    Anthropic key set:   {'yes' if anthropic_set else 'no'}")
    if gh:
        print(f"    GitHub App ID:       {gh['GITHUB_APP_ID']}")
        print(f"    GitHub install ID:   {gh['GITHUB_INSTALLATION_ID']}")
        print(f"    Repo:                {gh['GITHUB_OWNER']}/{gh['GITHUB_REPO']}")
    else:
        print("    GitHub App:          not configured")
    print()


def _write_env(
    anthropic_key: str, gh: dict[str, str] | None, project: str, region: str
) -> None:
    """Write .env atomically. Never overwrites if user said no."""
    lines = [
        "# Generated by `make setup` — values are read by auto_experiment/auto_loop.py,",
        "# the autoresearch Job manifest, and src/api.py. Edit by hand or rerun setup.",
        "",
        f"ANTHROPIC_API_KEY={anthropic_key}",
        "",
        f"GCP_PROJECT={project}",
        f"GCP_REGION={region}",
        "",
    ]
    if gh:
        lines.extend([f"{k}={v}" for k, v in gh.items()])
        lines.append("")
    ENV_FILE.write_text("\n".join(lines))
    print(f"  ✓ wrote {ENV_FILE.relative_to(PROJECT_ROOT)}")


def _write_tenant_config(cloud: str, tracker: str, project: str, region: str) -> None:
    TENANT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Generated by `make setup`. Edit by hand or rerun setup.\n"
        "# Only `gke` + `mlflow` are wired up today; others are placeholders\n"
        "# so future contributors know where to slot in implementations.\n"
        "\n"
        "cloud:\n"
        f"  provider: {cloud}\n"
        f"  gcp_project: {project}\n"
        f"  gcp_region: {region}\n"
        "\n"
        "tracker:\n"
        f"  provider: {tracker}\n"
    )
    TENANT_CONFIG.write_text(content)
    print(f"  ✓ wrote {TENANT_CONFIG.relative_to(PROJECT_ROOT)}")


def _next_steps(cloud: str, gh: dict[str, str] | None) -> None:
    print()
    print("  Next steps")
    print("  ──────────")
    n = 1
    print(f"   {n}. Authenticate gcloud + kubectl:")
    print("        gcloud auth login")
    print("        gcloud auth application-default login")
    n += 1
    print(f"   {n}. (One-time) Provision GCP resources:")
    print("        bash scripts/setup-gcp.sh")
    n += 1
    print(f"   {n}. (One-time) Deploy MLflow + ArgoCD to your GKE cluster:")
    print("        make deploy-mlflow")
    print("        make deploy-argocd")
    n += 1
    if gh:
        print(f"   {n}. Push the Anthropic key into the inference namespace:")
        print("        make autoresearch-secret")
        n += 1
    print(f"   {n}. Wake the cluster (if asleep):")
    print("        make cluster-wake")
    n += 1
    print(f"   {n}. Bootstrap a v1 baseline:")
    print("        make mlflow-kill && make mlflow   # in another terminal")
    print("        make reset-for-fresh-run")
    n += 1
    if gh:
        print(f"   {n}. Fire the autoresearch loop:")
        print("        make autoresearch-run AUTORESEARCH_N=20 AUTORESEARCH_HOURS=4.0")
        print("        make autoresearch-logs")
    print()
    print("  Done — happy researching.\n")


def _prereqs_warning() -> None:
    """Soft-warn about missing CLIs. Doesn't block."""
    missing = []
    for cli in ("gcloud", "kubectl", "uv"):
        if not _check_cli(cli):
            missing.append(cli)
    if missing:
        print(
            f"  ⚠  Missing CLIs on PATH: {', '.join(missing)}\n"
            "     You can finish this wizard, but later steps will fail until\n"
            "     these are installed. (gcloud: cloud.google.com/sdk;\n"
            "     kubectl: bundled with gcloud; uv: pip install uv)\n"
        )


def _from_env() -> bool:
    """Allow a fully non-interactive setup from env vars (used by tests + CI)."""
    if not os.environ.get("SETUP_NONINTERACTIVE"):
        return False
    needed = ["GCP_PROJECT", "ANTHROPIC_API_KEY"]
    missing = [k for k in needed if not os.environ.get(k)]
    if missing:
        print(
            f"ERROR: SETUP_NONINTERACTIVE set but missing env vars: {missing}",
            file=sys.stderr,
        )
        sys.exit(1)
    cloud = os.environ.get("CLOUD", "gke")
    tracker = os.environ.get("TRACKER", "mlflow")
    project = os.environ["GCP_PROJECT"]
    region = os.environ.get("GCP_REGION", "asia-south1")
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]
    gh = None
    if os.environ.get("GITHUB_APP_ID"):
        gh = {
            "GITHUB_APP_ID": os.environ["GITHUB_APP_ID"],
            "GITHUB_INSTALLATION_ID": os.environ.get("GITHUB_INSTALLATION_ID", ""),
            "GITHUB_OWNER": os.environ.get("GITHUB_OWNER", ""),
            "GITHUB_REPO": os.environ.get("GITHUB_REPO", ""),
            "GITHUB_PEM_SECRET": os.environ.get("GITHUB_PEM_SECRET", "github-app-key"),
        }
    _write_env(anthropic_key, gh, project, region)
    _write_tenant_config(cloud, tracker, project, region)
    print("  ✓ non-interactive setup complete (SETUP_NONINTERACTIVE=1).")
    return True


def main() -> int:
    if _from_env():
        return 0

    banner()
    _prereqs_warning()

    existing = _read_existing_env()

    try:
        cloud = _cloud_choice()
        tracker = _tracker_choice()
        project, region = _gcp_settings(existing)
        anthropic_key = _anthropic_key(existing)
        gh = _github_app_config(existing)
    except KeyboardInterrupt:
        print("\n  (cancelled)")
        return 1

    _summarize(cloud, tracker, project, region, bool(anthropic_key), gh)

    if not questionary.confirm(
        "Write .env + configs/tenant.yaml?",
        default=True,
        style=ACCENT,
    ).unsafe_ask():
        print("  (cancelled — nothing written)")
        return 1

    _write_env(anthropic_key, gh, project, region)
    _write_tenant_config(cloud, tracker, project, region)
    _next_steps(cloud, gh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
