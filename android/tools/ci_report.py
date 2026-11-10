"""Bounded CI status reporting. Never prints credentials or mutates repository content."""
from __future__ import annotations

import json
import os
import urllib.request


def publish(name: str, status: int, summary: str) -> None:
    token = os.environ.get("GH_TOKEN")
    if not token:
        return
    payload = {
        "name": name,
        "head_sha": os.environ["GITHUB_SHA"],
        "status": "completed",
        "conclusion": "success" if status == 0 else "failure",
        "details_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "output": {"title": name, "summary": "```text\n" + summary[-54000:].replace("```", "'''") + "\n```"},
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/check-runs",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print("Published bounded CI details:", json.load(response)["html_url"])
    except Exception as error:
        print("::warning::Could not publish CI details:", type(error).__name__)
