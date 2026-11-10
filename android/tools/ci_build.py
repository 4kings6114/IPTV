"""Run Gradle and publish bounded build diagnostics via the GitHub Checks API.

Repository contents remain read-only. This makes compiler/test failures inspectable
without relying on a separate signed log-blob download service. Never dump env/secrets.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
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

from ci_report import publish
publish("Android verification details", status, summary)
sys.exit(status)
