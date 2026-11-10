"""Run Gradle and publish bounded build diagnostics via the GitHub Checks API.

Repository contents remain read-only. This makes compiler/test failures inspectable
without relying on a separate signed log-blob download service. Never dump env/secrets.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]
build = root / "app/build"
build.mkdir(parents=True, exist_ok=True)
log_path = build / "gradle.log"
command = ["./gradlew", "--no-daemon", "--stacktrace", ":app:testDebugUnitTest", ":app:lintDebug", ":app:assembleDebug"]
with log_path.open("w", encoding="utf-8") as log:
    process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        log.write(line)
    status = process.wait()

lines = log_path.read_text().splitlines()
important = [line for line in lines if re.search(r"^e:|^w:|FAILURE:|What went wrong|Execution failed|Could not|^> |Caused by:", line)]
problems = []
for path in (build / "test-results/testDebugUnitTest").glob("TEST-*.xml"):
    suite = ET.parse(path).getroot()
    for case in suite.findall("testcase"):
        for failure in list(case.findall("failure")) + list(case.findall("error")):
            problems.append(f"{case.get('classname')}.{case.get('name')}: {failure.get('message')}\n{(failure.text or '')[:1600]}")
summary = ("Gradle exit status: " + str(status) + "\n\n" + "\n".join(problems + important + lines[-130:]))[-54000:]
# No runtime data is collected. Be defensive about URL query credentials in build-tool diagnostics.
summary = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", summary)
report = {"exit_status": status, "summary": summary}
(build / "verification-summary.json").write_text(json.dumps(report, indent=2))

token = os.environ.get("GH_TOKEN")
if token:
    payload = {
        "name": "Android verification details",
        "head_sha": os.environ["GITHUB_SHA"],
        "status": "completed",
        "conclusion": "success" if status == 0 else "failure",
        "details_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "output": {
            "title": "Android build/tests passed" if status == 0 else "Android build diagnostics",
            "summary": "```text\n" + summary.replace("```", "'''") + "\n```",
        },
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/check-runs",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print("Published bounded Android verification details:", json.load(response)["html_url"])
    except Exception as error:
        # Diagnostics reporting must not hide the original Gradle exit status.
        print("::warning::Could not publish verification details:", type(error).__name__)
sys.exit(status)
