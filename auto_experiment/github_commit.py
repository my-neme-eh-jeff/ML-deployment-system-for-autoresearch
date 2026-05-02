"""GitHub App authentication + GraphQL commits for the autoresearch loop.

Why this module exists: the loop runs as a K8s Job and needs to push commits to
GitHub without a deploy-key SSH or a long-lived PAT. Pattern:

  PEM (in GCP Secret Manager) → JWT signed locally → 1-hour install token →
  GraphQL createCommitOnBranch (atomic multi-file, verified-signed by GitHub).

Industry standard (Devin, Sweep, Codegen, Copilot Coding Agent). See BRAINSTORM.md §6.
"""

import base64
import os
import time
from pathlib import Path

import jwt as pyjwt
import requests

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"


def load_pem_from_secret_manager(project: str, secret: str) -> bytes:
    """Fetch the App PEM from GCP Secret Manager via Workload Identity."""
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret}/versions/latest"
    return client.access_secret_version(request={"name": name}).payload.data


def mint_app_jwt(app_id: str, pem: bytes) -> str:
    """Mint a 10-minute JWT signed with the App's private key."""
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": str(app_id)}
    return pyjwt.encode(payload, pem, algorithm="RS256")


def get_installation_token(
    app_id: str, installation_id: str, project: str, secret: str
) -> str:
    """Exchange the App JWT for a 1-hour installation access token."""
    pem = load_pem_from_secret_manager(project, secret)
    app_jwt = mint_app_jwt(app_id, pem)
    r = requests.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


def create_branch_from_main(token: str, owner: str, repo: str, branch: str) -> str:
    """Create a new branch from the current main HEAD. Idempotent — returns the existing branch's SHA if it already exists."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    # Check if branch already exists
    r = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{branch}",
        headers=headers,
        timeout=30,
    )
    if r.status_code == 200:
        return r.json()["object"]["sha"]
    # Create new branch from main
    r = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/main",
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    main_sha = r.json()["object"]["sha"]
    r = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
        headers=headers,
        json={"ref": f"refs/heads/{branch}", "sha": main_sha},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["object"]["sha"]


def commit_files_to_branch(
    token: str,
    owner: str,
    repo: str,
    branch: str,
    message: str,
    files: list[tuple[str, bytes]],
) -> str:
    """Atomic multi-file commit via GraphQL createCommitOnBranch.

    `files` is a list of (relative_path, file_bytes). Returns the new commit OID.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    # Look up the branch's GraphQL node ID + current HEAD oid
    branch_q = """
    query($owner:String!, $repo:String!, $branch:String!) {
      repository(owner:$owner, name:$repo) {
        ref(qualifiedName:$branch) { id target { oid } }
      }
    }
    """
    r = requests.post(
        GITHUB_GRAPHQL,
        headers=headers,
        json={
            "query": branch_q,
            "variables": {
                "owner": owner,
                "repo": repo,
                "branch": f"refs/heads/{branch}",
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    ref = r.json()["data"]["repository"]["ref"]
    if ref is None:
        raise RuntimeError(f"Branch {branch} not found on {owner}/{repo}")
    branch_id = ref["id"]
    head_oid = ref["target"]["oid"]

    additions = [
        {"path": p, "contents": base64.b64encode(c).decode("ascii")} for p, c in files
    ]
    mutation = """
    mutation($branch:ID!, $message:String!, $files:[FileAddition!], $parent:GitObjectID!) {
      createCommitOnBranch(input:{
        branch:{id:$branch},
        message:{headline:$message},
        fileChanges:{additions:$files},
        expectedHeadOid:$parent
      }) {
        commit { oid url }
      }
    }
    """
    r = requests.post(
        GITHUB_GRAPHQL,
        headers=headers,
        json={
            "query": mutation,
            "variables": {
                "branch": branch_id,
                "message": message,
                "files": additions,
                "parent": head_oid,
            },
        },
        timeout=60,
    )
    r.raise_for_status()
    result = r.json()
    if result.get("errors"):
        raise RuntimeError(f"GraphQL errors: {result['errors']}")
    return result["data"]["createCommitOnBranch"]["commit"]["oid"]


def open_pull_request(
    token: str, owner: str, repo: str, branch: str, title: str, body: str
) -> str:
    """Open a PR from `branch` against main. Returns the PR URL."""
    r = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "head": branch, "base": "main", "body": body},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["html_url"]


def github_config_from_env() -> dict | None:
    """Read GitHub App config from env vars. Returns None if not configured (e.g. local dev)."""
    required = [
        "GITHUB_APP_ID",
        "GITHUB_INSTALLATION_ID",
        "GITHUB_OWNER",
        "GITHUB_REPO",
        "GCP_PROJECT",
        "GITHUB_PEM_SECRET",
    ]
    if not all(os.environ.get(k) for k in required):
        return None
    return {
        "app_id": os.environ["GITHUB_APP_ID"],
        "installation_id": os.environ["GITHUB_INSTALLATION_ID"],
        "owner": os.environ["GITHUB_OWNER"],
        "repo": os.environ["GITHUB_REPO"],
        "project": os.environ["GCP_PROJECT"],
        "secret": os.environ["GITHUB_PEM_SECRET"],
    }


def collect_changed_files(
    project_root: Path, proposal: dict, history_path: Path
) -> list[tuple[str, bytes]]:
    """Read the files that should be committed for one autoresearch iteration."""
    files: list[tuple[str, bytes]] = []
    for field, rel_path in [
        ("params_yaml", "configs/params.yaml"),
        ("train_py", "src/train.py"),
        ("preprocess_py", "src/preprocess.py"),
    ]:
        if proposal.get(field):
            p = project_root / rel_path
            files.append((rel_path, p.read_bytes()))
    for rel in ("metrics.json", "dvc.lock"):
        p = project_root / rel
        if p.exists():
            files.append((rel, p.read_bytes()))
    if history_path.exists():
        files.append(
            (str(history_path.relative_to(project_root)), history_path.read_bytes())
        )
    return files
