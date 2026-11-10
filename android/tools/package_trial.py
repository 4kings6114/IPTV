"""Package only the verified test APK, never keys, tokens, or runtime state."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
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
assert element["versionCode"] == 3
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
    assert "assets/stability.mp4" not in archive.namelist(), "Instrumentation fixture must not ship in app APK"
    assert len([line for line in archive.read("assets/trial.m3u").decode().splitlines()
                if line.startswith("#EXTINF:")]) == 20
    assert not any(name.endswith((".keystore", ".jks", ".env")) for name in archive.namelist())
    native_libraries = sorted(name for name in archive.namelist() if name.endswith(".so"))

counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
for path in (build / "test-results/testDebugUnitTest").glob("TEST-*.xml"):
    suite = ET.parse(path).getroot()
    for name in counts:
        counts[name] += int(suite.get(name, "0"))
assert counts["tests"] >= 43, counts
assert counts["failures"] == counts["errors"] == counts["skipped"] == 0, counts
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
    "physical_device_test": "0.1.2 NOT YET RETESTED; user crash report for 0.1.1 is Xiaomi / Android API 31",
    "stream_playback_tests": "Provider streams NOT TESTED; separate API31/API33 synthetic runtime workflow",
}
(output / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n")
shutil.copyfile(root / "INSTALL-fa.md", output / "INSTALL-fa.md")
info["signature_verification"] = (build / "signature.txt").read_text()
# Public certificate digest from the APK actually supplied to the user, not a key.
previous_signer = "aa088f89aacd9f4e1e9642088ed7fa82dfeedc302c0ecb1e089effc34a365cb9"
current_signer = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})", info["signature_verification"])
assert current_signer, "Missing verified signer fingerprint"
info["signer_sha256"] = current_signer.group(1)
# Check that the APK really used the file that Actions is caching, not AGP's alternate default.
key_path = Path(os.environ["IPTV_TRIAL_KEYSTORE"])
certificate = subprocess.check_output([
    "keytool", "-exportcert", "-keystore", str(key_path), "-storepass", "android", "-alias", "androiddebugkey",
], stderr=subprocess.PIPE, timeout=20)
assert hashlib.sha256(certificate).hexdigest() == current_signer.group(1), "APK signer differs from cached trial key"
info["signing_cache_path_verified"] = True
info["updates_original_0_1_0_trial"] = current_signer.group(1) == previous_signer
previous_0_1_1_signer = "f73be34704f68399ada9b3a8d3d8ce82b585cd508beb68929e6a93063250da2a"
info["updates_delivered_0_1_1_trial"] = current_signer.group(1) == previous_0_1_1_signer
info["installation_warning"] = "If signer differs, Android cannot update the previously delivered trial in place. Uninstalling it erases local playlists, favorites, history and preferences. Save needed source information and the MIUI crash summary first."
(output / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
publish("Android APK package details", 0, json.dumps(info, indent=2))
