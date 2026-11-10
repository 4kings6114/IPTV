"""Optional one-file handoff to the current Arena workspace, never a code publish.

The workspace holds the upload untrusted until it compares the bytes against the
APK digest obtained independently from the authenticated GitHub package check.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.request

url = os.environ.get("WORKSPACE_DELIVERY_URL", "")
if not url:
    print("No optional workspace handoff requested; APK remains available in Actions artifacts.")
    sys.exit(0)
if not re.fullmatch(r"https://[0-9]+-[a-z0-9]+\.e2b\.app/ci-upload/[0-9a-f]{64}", url):
    raise ValueError("Delivery is restricted to a one-time HTTPS upload endpoint on an Arena workspace")
root = Path(__file__).resolve().parents[1] / "app/build/deliverables"
info = json.loads((root / "BUILD-INFO.json").read_text())
assert info["application_id"] == "io.github.fourkings6114.iptv.trial"
assert info["commit"] == os.environ["GITHUB_SHA"]
apk = (root / info["apk"]).read_bytes()
assert len(apk) == info["bytes"] and len(apk) < 60 * 1024 * 1024
assert hashlib.sha256(apk).hexdigest() == info["sha256"]
request = urllib.request.Request(url, data=apk, method="POST", headers={
    "Content-Type": "application/vnd.android.package-archive",
    "X-Content-SHA256": info["sha256"],
})
with urllib.request.urlopen(request, timeout=120) as response:
    received = json.load(response)
    assert response.status == 201
    assert received["sha256"] == info["sha256"] and received["bytes"] == len(apk)
print("Trial APK delivered to the requested workspace, pending independent hash verification.")
