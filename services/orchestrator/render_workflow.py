"""Render Symphony policy from platform runtime config and an exact app commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

try:
    from .manifest import load_project, require_sha
except ImportError:  # Direct script invocation in the worker image.
    from manifest import load_project, require_sha


ROOT = Path(__file__).resolve().parents[2]


def fetch_policy(repository: str, path: str, commit: str, token: str | None = None) -> bytes:
    require_sha(commit)
    url = f"https://raw.githubusercontent.com/{repository}/{commit}/{path}"
    headers = {"Accept": "text/plain", "User-Agent": "launch-operations"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return response.read()


def render(project_id: str, commit: str, policy: bytes) -> tuple[str, dict[str, str]]:
    manifest = load_project(project_id)
    require_sha(commit)
    runtime_path = ROOT / "services" / "orchestrator" / "runtime.yaml"
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    if runtime.pop("schema_version", None) != 1 or runtime["agent"].get("max_concurrent_agents") != 1:
        raise ValueError("unsupported or unsafe orchestrator runtime configuration")
    policy_text = policy.decode("utf-8").strip()
    if not policy_text.startswith("#"):
        raise ValueError("product workflow must be Markdown")
    data = manifest.data
    statuses = data["tracker"]["statuses"]
    runtime["tracker"] = {
        "kind": data["tracker"]["provider"],
        "provider": {
            "base_url": "$JIRA_BASE_URL", "email": "$JIRA_EMAIL",
            "api_token": "$JIRA_API_TOKEN", "project_key": data["tracker"]["delivery_project"],
        },
        "required_labels": [data["tracker"]["eligibility_label"]],
        "active_states": [statuses["ready"], statuses["active"], statuses["approved"].lower()],
        "terminal_states": [statuses["complete"]],
    }
    runtime["hooks"]["after_create"] = (
        f"git clone --branch {data['source']['delivery_branch']} --single-branch "
        f"https://github.com/{manifest.repository}.git .\n"
        "printf '\\n.symphony-review-*.json\\n' >> .git/info/exclude\n"
    )
    recovery = runtime.pop("recovery")
    body = (
        "---\n" + yaml.safe_dump(runtime, sort_keys=False).strip() + "\n---\n"
        "<!-- generated: do not edit; platform runtime + product workflow -->\n"
        f"<!-- project={project_id} product_commit={commit} -->\n\n"
        "You are delivering {{ issue.identifier }}: {{ issue.title }}.\n"
        "{{ issue.description }}\n"
        "{% if attempt %}Resume the existing workspace, workpad, and PR for retry {{ attempt }}; do not repeat completed work.{% endif %}\n\n"
        f"Runtime recovery contract: `{json.dumps(recovery, sort_keys=True)}`\n\n"
        "# Platform handoff contract\n\n"
        "The Jira and GitHub credentials are platform capabilities, not task data. Never inspect credential "
        "stores or attempt to recover excluded environment values. Use authenticated `gh` commands and the "
        "platform evidence handoff only. Create `.symphony-review-request.json` atomically with: issue, PR "
        "number, full tested head SHA, tested base SHA, integer attempt number (zero for the initial run), summary, evidence file paths, UI-change "
        "boolean, synthetic-evidence confirmation, checks, turn count, and duration milliseconds. The platform "
        "verifies the current head/checks, uploads evidence, records policy and run metadata, and performs the "
        "In Review transition. A changed head invalidates the request.\n\n"
        "# Product-owned workflow\n\n" + policy_text + "\n"
    )
    metadata = {
        "project_id": project_id,
        "product_commit": commit,
        "product_workflow_sha256": hashlib.sha256(policy).hexdigest(),
        "rendered_workflow_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "repository": manifest.repository,
        "workflow_path": manifest.workflow_path,
    }
    return body, metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("commit")
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()
    manifest = load_project(args.project)
    policy = args.policy_file.read_bytes() if args.policy_file else fetch_policy(
        manifest.repository, manifest.workflow_path, args.commit, os.environ.get("GITHUB_TOKEN")
    )
    body, metadata = render(args.project, args.commit, policy)
    args.output.write_text(body, encoding="utf-8")
    args.metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
