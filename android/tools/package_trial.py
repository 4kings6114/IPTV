"""Package only the verified test APK, never keys, tokens, or runtime state."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import traceback
from ci_report import publish
import xml.etree.ElementTree as ET
import zipfile

def report_failure(kind, error, trace):
    publish("Android APK package details", 1, "".join(traceback.format_exception(kind, error, trace)))
    sys.__excepthook__(kind, error, trace)


sys.excepthook = report_failure

root = Path(__file__).resolve().parents[1]
app = root / "app"
build = app / "build"
apk = build / "outputs/apk/debug/app-debug.apk"
metadata = json.loads((apk.parent / "output-metadata.json").read_text())
element = metadata["elements"][0]
assert metadata["applicationId"] == "io.github.fourkings6114.iptv.trial"
assert element["versionName"].endswith("-trial")
assert element["versionCode"] == 1
badging = (build / "badging.txt").read_text()
assert "sdkVersion:'26'" in badging
assert "targetSdkVersion:'36'" in badging
permissions = set(re.findall(r"uses-permission: name='([^']+)'", badging))
# The only optional merged permission is AndroidX's signature-protected receiver permission.
assert all(name in {"android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE"}
           or name == metadata["applicationId"] + ".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"
           for name in permissions), permissions
assert "android.permission.INTERNET" in permissions
with zipfile.ZipFile(apk) as archive:
    assert archive.testzip() is None
    assert "AndroidManifest.xml" in archive.namelist()
    assert "classes.dex" in archive.namelist()
    assert len([line for line in archive.read("assets/trial.m3u").decode().splitlines()
                if line.startswith("#EXTINF:")]) == 20
    assert not any(name.endswith((".keystore", ".jks", ".env")) for name in archive.namelist())
    native_libraries = sorted(name for name in archive.namelist() if name.endswith(".so"))

counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
for path in (build / "test-results/testDebugUnitTest").glob("TEST-*.xml"):
    suite = ET.parse(path).getroot()
    for name in counts:
        counts[name] += int(suite.get(name, "0"))
assert counts["tests"] >= 30, counts
assert counts["failures"] == counts["errors"] == 0, counts
lint = ET.parse(build / "reports/lint-results-debug.xml").getroot()
assert not any(issue.get("severity") in {"Fatal", "Error"} for issue in lint.findall("issue"))

output = build / "deliverables"
output.mkdir(parents=True, exist_ok=True)
name = f"IPTV-Hamrah-{element['versionName']}.apk"
destination = output / name
shutil.copyfile(apk, destination)
digest = hashlib.sha256(destination.read_bytes()).hexdigest()
(output / (name + ".sha256")).write_text(f"{digest}  {name}\n")
info = {
    "application_id": metadata["applicationId"],
    "version_name": element["versionName"],
    "version_code": element["versionCode"],
    "min_sdk": 26,
    "target_sdk": 36,
    "commit": os.environ["BUILD_SHA"],
    "run_id": os.environ["BUILD_RUN_ID"],
    "apk": name,
    "bytes": destination.stat().st_size,
    "sha256": digest,
    "unit_tests": counts,
    "permissions": sorted(permissions),
    "native_libraries": native_libraries,
    "signing": "DEBUG/TRIAL ONLY; branch cache identity; not a production release key",
    "physical_device_test": "NOT PERFORMED; intended first test: Poco X3 / Android 13",
    "stream_playback_tests": "NOT PERFORMED",
}
(output / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n")
shutil.copyfile(root / "INSTALL-fa.md", output / "INSTALL-fa.md")
info["signature_verification"] = (build / "signature.txt").read_text()
(output / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
publish("Android APK package details", 0, json.dumps(info, indent=2))
