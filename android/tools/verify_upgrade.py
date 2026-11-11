"""Verify a real in-place package update from the immutable delivered 0.1.2 source.

The baseline is rebuilt with the SAME cached trial key, not claimed to be the
byte-identical previously downloaded APK. No private key is copied or uploaded.
No uninstall/clear-data is allowed between seeding v3 and validating v4.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

BASELINE_REVISION = "93b2152ceefc9ffb228bc0ace5ad8014e114d5d6"
DELIVERED_012_SIGNER = "eba2f7bdb4cfea267f4c30cedba8b2956f9c86ed3d5363512291fd3d6bd29c48"
DELIVERED_012_APK_SHA256 = "97c14c706c1288f2a47ef2da0be4fbbc2aac442ed56091e44c9cdcd611b86d5a"
PACKAGE = "io.github.fourkings6114.iptv.trial"
TEST_PACKAGE = PACKAGE + ".test/androidx.test.runner.AndroidJUnitRunner"
UPGRADE_TEST_CLASSES = (
    "io.github.fourkings6114.iptv.UpgradeSeedTest",
    "io.github.fourkings6114.iptv.UpgradePreservationTest",
)
SCHEMA_PATH = "app/schemas/io.github.fourkings6114.iptv.data.AppDatabase/1.json"


def run_logged(command, cwd: Path, log: Path, timeout: int = 600) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.stdout)
    print(result.stdout[-16000:], flush=True)
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed with status {result.returncode}; see {log.name}")
    return result.stdout


def verify_instrumentation_output(text: str) -> None:
    if not re.search(r"(?m)^OK \(1 test\)\s*$", text):
        raise AssertionError("Upgrade phase must pass exactly one non-skipped instrumentation test")
    if not re.search(r"(?m)^INSTRUMENTATION_CODE: -1\s*$", text):
        raise AssertionError("Upgrade instrumentation did not finish normally")
    if "FAILURES!!!" in text or "INSTRUMENTATION_FAILED" in text:
        raise AssertionError("Upgrade instrumentation reported a failure")


def compare_schemas(old: dict, new: dict) -> str:
    assert old["database"]["version"] == new["database"]["version"] == 1
    assert old["database"] == new["database"], "Room v1 changed without a migration"
    return old["database"]["identityHash"]


def extract_baseline(repo: Path, destination: Path) -> Path:
    # Fetch a single immutable ancestor, without checking out/switching any branch.
    available = subprocess.run(["git", "cat-file", "-e", BASELINE_REVISION + "^{commit}"],
                               cwd=repo, capture_output=True)
    if available.returncode:
        subprocess.run(["git", "fetch", "--no-tags", "--depth=1", "origin", BASELINE_REVISION],
                       cwd=repo, check=True, timeout=90)
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / "baseline-source.tar"
    with archive_path.open("wb") as output:
        subprocess.run(["git", "archive", "--format=tar", BASELINE_REVISION, "android"],
                       cwd=repo, stdout=output, check=True, timeout=60)
    with tarfile.open(archive_path) as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            assert parts and parts[0] == "android" and ".." not in parts and not member.name.startswith("/")
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                assert member.isfile(), "No symlinks or special files in the upgrade source snapshot"
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                assert stream is not None
                with stream:
                    target.write_bytes(stream.read())
                target.chmod(member.mode & 0o777)
    return destination / "android"


def apk_identity(apk: Path, code: int, version: str, out: Path, label: str) -> dict:
    tools = Path(os.environ["ANDROID_HOME"]) / "build-tools/36.0.0"
    signature = run_logged([str(tools / "apksigner"), "verify", "--verbose", "--print-certs", str(apk)],
                           out, out / (label + "-signature.txt"), 30)
    signer = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})", signature)
    assert signer and signer.group(1) == DELIVERED_012_SIGNER, "Upgrade must use the delivered 0.1.2 signer"
    badging = run_logged([str(tools / "aapt"), "dump", "badging", str(apk)],
                         out, out / (label + "-badging.txt"), 30)
    package = badging.splitlines()[0]
    assert f"name='{PACKAGE}'" in package
    assert f"versionCode='{code}'" in package and f"versionName='{version}'" in package
    return {"version_code": code, "version_name": version, "signer_sha256": signer.group(1),
            "apk_sha256": hashlib.sha256(apk.read_bytes()).hexdigest(), "bytes": apk.stat().st_size}


def verify_upgrade(root: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "in_progress", "baseline_kind": "rebuilt_from_delivered_0.1.2_source_not_original_apk_bytes",
        "baseline_commit": BASELINE_REVISION, "reference_delivered_apk_sha256": DELIVERED_012_APK_SHA256,
        "scope": "Synthetic legacy custom/file sources, source selection, favorites, history and preferences; adb install -r; no uninstall/clear-data after seed",
    }
    try:
        run_logged(["./gradlew", "--no-daemon", "--stacktrace", ":app:assembleDebug", ":app:assembleDebugAndroidTest"],
                   root, out / "current-build.log")
        baseline = extract_baseline(root.parent, root / "app/build/upgrade-baseline-source")
        run_logged(["./gradlew", "--no-daemon", "--stacktrace", ":app:assembleDebug"],
                   baseline, out / "baseline-build.log")
        old_apk = baseline / "app/build/outputs/apk/debug/app-debug.apk"
        new_apk = root / "app/build/outputs/apk/debug/app-debug.apk"
        test_apk = root / "app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
        report["baseline"] = apk_identity(old_apk, 3, "0.1.2-trial", out, "baseline")
        report["current"] = apk_identity(new_apk, 4, "0.1.3-trial", out, "current")
        report["same_as_delivered_0_1_2_signer"] = True
        report["room_v1_identity_hash"] = compare_schemas(
            json.loads((baseline / SCHEMA_PATH).read_text()), json.loads((root / SCHEMA_PATH).read_text()))
        report["room_schema_unchanged"] = True
        # This reset is BEFORE creating synthetic baseline data, on a disposable emulator.
        run_logged(["adb", "install", "-r", str(old_apk)], root, out / "install-baseline.txt", 120)
        cleared = run_logged(["adb", "shell", "pm", "clear", PACKAGE], root, out / "initialize-test-data.txt", 30)
        assert cleared.strip() == "Success"
        run_logged(["adb", "install", "-r", str(test_apk)], root, out / "install-tests.txt", 120)
        seed = run_logged(["adb", "shell", "am", "instrument", "-w", "-r", "-e", "class", UPGRADE_TEST_CLASSES[0], TEST_PACKAGE],
                          root, out / "seed-instrumentation.txt", 120)
        verify_instrumentation_output(seed)
        report["seed_test_passed"] = True
        update = run_logged(["adb", "install", "-r", str(new_apk)], root, out / "update-in-place.txt", 120)
        assert re.search(r"(?m)^Success\s*$", update), "Package update must succeed without uninstall"
        report["install_r_succeeded"] = True
        verified = run_logged(["adb", "shell", "am", "instrument", "-w", "-r", "-e", "class", UPGRADE_TEST_CLASSES[1], TEST_PACKAGE],
                              root, out / "verify-instrumentation.txt", 120)
        verify_instrumentation_output(verified)
        report["preservation_test_passed"] = True
        report["status"] = "success"
        return report
    except Exception as error:
        report["status"] = "failure"
        report["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
