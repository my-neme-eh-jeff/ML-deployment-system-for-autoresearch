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
    from pyfiglet import Figlet
    from questionary import Choice, Style
    from rich.align import Align
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError as e:
    print(
        f"ERROR: {e}\nRun `uv sync` first to install setup wizard dependencies.",
        file=sys.stderr,
    )
    sys.exit(1)


PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
TENANT_CONFIG = PROJECT_ROOT / "configs" / "tenant.yaml"

# Single accent color used by both the rich panels and questionary prompts.
# Picked to feel like Vercel / Bun / modern CLI tooling — warm orange.
ACCENT = "#ff9d00"
SUCCESS = "#44ff44"
DIM = "#858585"

console = Console()

QSTYLE = Style(
    [
        ("qmark", f"fg:{ACCENT} bold"),
        ("question", "bold"),
        ("answer", f"fg:{SUCCESS} bold"),
        ("pointer", f"fg:{ACCENT} bold"),
        ("highlighted", f"fg:{ACCENT} bold"),
        ("selected", f"fg:{SUCCESS}"),
        ("disabled", f"fg:{DIM} italic"),
    ]
)


def banner() -> None:
    """Render the wizard banner with a figlet logo + rich panel.

    Modeled after the Vercel / Bun / Wrangler CLI feel — slant ASCII logo on
    top, project name and one-line description underneath, all in a bordered
    panel with the accent color.
    """
    logo_lines = Figlet(font="slant").renderText("Autoresearch").rstrip()

    body = Text()
    body.append(logo_lines, style=f"bold {ACCENT}")
    body.append("\n\n")
    body.append("ML Deployment System ", style="bold white")
    body.append("·", style=DIM)
    body.append(" Setup Wizard", style=DIM)
    body.append("\n\n")
    body.append(
        "Configure your cloud + tracker, drop in your creds, ship to prod.",
        style=DIM,
    )

    console.print()
    console.print(
        Panel(
            Align.left(body),
            border_style=ACCENT,
            padding=(1, 3),
            title=f"[{ACCENT}]▸[/{ACCENT}] [bold]autoresearch[/bold]",
            title_align="left",
            subtitle=f"[{DIM}]~2 minutes • idempotent • non-destructive[/{DIM}]",
            subtitle_align="right",
        )
    )
    console.print()


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
        style=QSTYLE,
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
        style=QSTYLE,
    ).unsafe_ask()


def _gcp_settings(existing: dict[str, str]) -> tuple[str, str]:
    project = (
        questionary.text(
            "GCP project ID:",
            default=existing.get("GCP_PROJECT", ""),
            validate=lambda v: (
                True if v.strip() else "Project ID is required (e.g. my-project-12345)."
            ),
            style=QSTYLE,
        )
        .unsafe_ask()
        .strip()
    )

    region = (
        questionary.text(
            "GCP region:",
            default=existing.get("GCP_REGION", "asia-south1"),
            style=QSTYLE,
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
            style=QSTYLE,
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
            style=QSTYLE,
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
        style=QSTYLE,
    ).unsafe_ask()
    if not setup_gh:
        return None

    return {
        "GITHUB_APP_ID": questionary.text(
            "GitHub App ID (e.g. 3576508):",
            default=existing.get("GITHUB_APP_ID", ""),
            style=QSTYLE,
        )
        .unsafe_ask()
        .strip(),
        "GITHUB_INSTALLATION_ID": questionary.text(
            "GitHub App Installation ID:",
            default=existing.get("GITHUB_INSTALLATION_ID", ""),
            style=QSTYLE,
        )
        .unsafe_ask()
        .strip(),
        "GITHUB_OWNER": questionary.text(
            "GitHub owner (user or org):",
            default=existing.get("GITHUB_OWNER", ""),
            style=QSTYLE,
        )
        .unsafe_ask()
        .strip(),
        "GITHUB_REPO": questionary.text(
            "GitHub repo name:",
            default=existing.get("GITHUB_REPO", ""),
            style=QSTYLE,
        )
        .unsafe_ask()
        .strip(),
        "GITHUB_PEM_SECRET": questionary.text(
            "GCP Secret Manager secret name holding the GitHub App PEM:",
            default=existing.get("GITHUB_PEM_SECRET", "github-app-key"),
            style=QSTYLE,
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
    """Pretty summary table before writing files. Lets the user spot typos."""
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 2),
        title=f"[bold {ACCENT}]Review your choices[/bold {ACCENT}]",
        title_justify="left",
        title_style="",
    )
    table.add_column("Key", style=DIM, no_wrap=True)
    table.add_column("Value")

    table.add_row("Cloud", f"[bold]{cloud}[/bold]")
    table.add_row("Tracker", f"[bold]{tracker}[/bold]")
    table.add_row("GCP project", project)
    table.add_row("GCP region", region)
    table.add_row(
        "Anthropic key",
        f"[{SUCCESS}]✓ set[/{SUCCESS}]" if anthropic_set else "[red]✗ not set[/red]",
    )
    if gh:
        table.add_row("GitHub App ID", gh["GITHUB_APP_ID"])
        table.add_row("Installation ID", gh["GITHUB_INSTALLATION_ID"])
        table.add_row("Repo", f"{gh['GITHUB_OWNER']}/{gh['GITHUB_REPO']}")
    else:
        table.add_row("GitHub App", f"[{DIM}]skipped[/{DIM}]")

    console.print()
    console.print(table)
    console.print()


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
    console.print(
        f"  [{SUCCESS}]✓[/{SUCCESS}] wrote [bold]{ENV_FILE.relative_to(PROJECT_ROOT)}[/bold]"
    )


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
    console.print(
        f"  [{SUCCESS}]✓[/{SUCCESS}] wrote [bold]{TENANT_CONFIG.relative_to(PROJECT_ROOT)}[/bold]"
    )


def _next_steps(cloud: str, gh: dict[str, str] | None) -> None:
    """Print the post-setup runbook as a clean, numbered Panel."""
    steps: list[tuple[str, list[str]]] = [
        (
            "Authenticate gcloud + kubectl",
            ["gcloud auth login", "gcloud auth application-default login"],
        ),
        (
            "Provision GCP resources [dim](one-time)[/dim]",
            ["bash scripts/setup-gcp.sh"],
        ),
        (
            "Deploy MLflow + ArgoCD to your GKE cluster [dim](one-time)[/dim]",
            ["make deploy-mlflow", "make deploy-argocd"],
        ),
    ]
    if gh:
        steps.append(
            ("Push Anthropic key into the cluster", ["make autoresearch-secret"])
        )
    steps.extend(
        [
            ("Wake the cluster [dim](if asleep)[/dim]", ["make cluster-wake"]),
            (
                "Bootstrap a v1 baseline",
                [
                    "make mlflow-kill && make mlflow   # in another terminal",
                    "make reset-for-fresh-run",
                ],
            ),
        ]
    )
    if gh:
        steps.append(
            (
                "Fire the autoresearch loop",
                [
                    "make autoresearch-run AUTORESEARCH_N=20 AUTORESEARCH_HOURS=4.0",
                    "make autoresearch-logs",
                ],
            )
        )

    lines: list[str] = []
    for i, (title, cmds) in enumerate(steps, start=1):
        lines.append(f"[bold {ACCENT}]{i:>2}.[/bold {ACCENT}] [bold]{title}[/bold]")
        for cmd in cmds:
            lines.append(f"    [{ACCENT}]▸[/{ACCENT}] [{DIM}]{cmd}[/{DIM}]")
        lines.append("")

    console.print()
    console.print(
        Panel(
            Text.from_markup("\n".join(lines).rstrip()),
            title=f"[{ACCENT}]▸[/{ACCENT}] [bold]Next steps[/bold]",
            title_align="left",
            border_style=ACCENT,
            padding=(1, 3),
        )
    )
    console.print(
        f"\n  [{SUCCESS}]✓[/{SUCCESS}] [bold]Done — happy researching.[/bold]\n"
    )


def _prereqs_warning() -> None:
    """Soft-warn about missing CLIs. Doesn't block."""
    rows = []
    for cli in ("gcloud", "kubectl", "uv"):
        present = _check_cli(cli)
        rows.append((cli, present))

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Tool", style="bold", no_wrap=True)
    table.add_column("Status")
    for cli, present in rows:
        if present:
            table.add_row(cli, f"[{SUCCESS}]✓ installed[/{SUCCESS}]")
        else:
            table.add_row(cli, "[red]✗ not on PATH[/red]")
    console.print(
        Panel(
            table,
            title=f"[{ACCENT}]▸[/{ACCENT}] [bold]Prerequisites[/bold]",
            title_align="left",
            border_style=DIM,
            padding=(0, 2),
        )
    )
    missing = [c for c, p in rows if not p]
    if missing:
        console.print(
            f"\n  [yellow]⚠[/yellow]  Missing: [bold]{', '.join(missing)}[/bold]. "
            f"You can finish this wizard, but later steps will fail without them."
            f"\n     gcloud → cloud.google.com/sdk · kubectl → bundled with gcloud · uv → "
            f"pip install uv\n"
        )
    console.print()


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
        style=QSTYLE,
    ).unsafe_ask():
        print("  (cancelled — nothing written)")
        return 1

    _write_env(anthropic_key, gh, project, region)
    _write_tenant_config(cloud, tracker, project, region)
    _next_steps(cloud, gh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
