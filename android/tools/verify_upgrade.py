"""Verify real in-place updates from immutable delivered 0.1.2/0.1.3/0.1.4 sources.

The baseline is rebuilt with the SAME verified retained trial key, not claimed to be the
byte-identical previously downloaded APK. No private key is copied or uploaded.
Separate immutable baselines verify legacy/named catalogs and the existing PiP preference.
No uninstall/clear-data is allowed between each seed and its current-version check.
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
PREVIOUS_REVISION = "4332f07e3e6b3fd3c0b324434d28a35245664130"
DELIVERED_013_APK_SHA256 = "fe4a044ebabd55d89b577842f0c15cdcbe4471d7e127285c8df9c83a61fe99a5"
PIP_REVISION = "b48e15512b9a41a8d76c5cec3dcc99e06e2ba8ed"
DELIVERED_014_APK_SHA256 = "55b7a68822217ca94e7b40aa9dc27202a6c75f4709d5275692426da65483b858"
CURRENT_VERSION_CODE = 6
CURRENT_VERSION_NAME = "0.1.5-trial"
PACKAGE = "io.github.fourkings6114.iptv.trial"
TEST_PACKAGE = PACKAGE + ".test/androidx.test.runner.AndroidJUnitRunner"
UPGRADE_TEST_CLASSES = (
    "io.github.fourkings6114.iptv.UpgradeSeedTest",
    "io.github.fourkings6114.iptv.UpgradePreservationTest",
    "io.github.fourkings6114.iptv.CatalogUpgradeSeedTest",
    "io.github.fourkings6114.iptv.CatalogUpgradePreservationTest",
    "io.github.fourkings6114.iptv.PipUpgradeSeedTest",
    "io.github.fourkings6114.iptv.PipUpgradePreservationTest",
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


def extract_baseline(repo: Path, destination: Path, revision: str = BASELINE_REVISION) -> Path:
    # Fetch a single immutable ancestor, without checking out/switching any branch.
    available = subprocess.run(["git", "cat-file", "-e", revision + "^{commit}"],
                               cwd=repo, capture_output=True)
    if available.returncode:
        subprocess.run(["git", "fetch", "--no-tags", "--depth=1", "origin", revision],
                       cwd=repo, check=True, timeout=90)
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / "baseline-source.tar"
    with archive_path.open("wb") as output:
        subprocess.run(["git", "archive", "--format=tar", revision, "android"],
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


def verify_source_upgrade(root: Path, out: Path, *, revision: str, code: int, version: str,
                          reference_sha256: str, test_classes: tuple[str, str], current: dict) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "in_progress", "baseline_kind": f"rebuilt_from_delivered_{version.removesuffix('-trial')}_source_not_original_apk_bytes",
        "baseline_commit": revision, "reference_delivered_apk_sha256": reference_sha256,
        "scope": "Synthetic persisted catalogs, source selection, favorites, history and preferences; adb install -r; no uninstall/clear-data between seed and verification",
        "current": current,
    }
    try:
        baseline = extract_baseline(root.parent, root / f"app/build/upgrade-baseline-{code}-source", revision)
        # Historical 0.1.2 used a bare KSP schema argument without declared outputs.
        # The immutable sources must actually export schemas, not restore only classes.
        run_logged(["./gradlew", "--no-daemon", "--no-build-cache", "--stacktrace", ":app:assembleDebug"],
                   baseline, out / "baseline-build.log")
        old_apk = baseline / "app/build/outputs/apk/debug/app-debug.apk"
        new_apk = root / "app/build/outputs/apk/debug/app-debug.apk"
        test_apk = root / "app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
        report["baseline"] = apk_identity(old_apk, code, version, out, "baseline")
        report["baseline_binary_matches_delivered_sha256"] = report["baseline"]["apk_sha256"] == reference_sha256
        report["same_as_delivered_0_1_2_signer"] = True  # shared by all delivered 0.1.2+ trials
        report["room_v1_identity_hash"] = compare_schemas(
            json.loads((baseline / SCHEMA_PATH).read_text()), json.loads((root / SCHEMA_PATH).read_text()))
        report["room_schema_unchanged"] = True
        # Disposable emulator reset BEFORE this phase's synthetic baseline data is created.
        run_logged(["adb", "install", "-r", "-d", str(old_apk)], root, out / "install-baseline.txt", 120)
        cleared = run_logged(["adb", "shell", "pm", "clear", PACKAGE], root, out / "initialize-test-data.txt", 30)
        assert cleared.strip() == "Success"
        run_logged(["adb", "install", "-r", str(test_apk)], root, out / "install-tests.txt", 120)
        seed = run_logged(["adb", "shell", "am", "instrument", "-w", "-r", "-e", "class", test_classes[0], TEST_PACKAGE],
                          root, out / "seed-instrumentation.txt", 120)
        verify_instrumentation_output(seed)
        report["seed_test_passed"] = True
        update = run_logged(["adb", "install", "-r", str(new_apk)], root, out / "update-in-place.txt", 120)
        assert re.search(r"(?m)^Success\s*$", update), "Package update must succeed without uninstall"
        report["install_r_succeeded"] = True
        verified = run_logged(["adb", "shell", "am", "instrument", "-w", "-r", "-e", "class", test_classes[1], TEST_PACKAGE],
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


def verify_upgrade(root: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    report = {"status": "in_progress"}
    try:
        run_logged(["./gradlew", "--no-daemon", "--stacktrace", ":app:assembleDebug", ":app:assembleDebugAndroidTest"],
                   root, out / "current-build.log")
        current = apk_identity(root / "app/build/outputs/apk/debug/app-debug.apk",
                               CURRENT_VERSION_CODE, CURRENT_VERSION_NAME, out, "current")
        # Retain the existing legacy regression and report contract for signing-owner evidence.
        report.update(verify_source_upgrade(root, out / "from-012", revision=BASELINE_REVISION,
            code=3, version="0.1.2-trial", reference_sha256=DELIVERED_012_APK_SHA256,
            test_classes=UPGRADE_TEST_CLASSES[:2], current=current))
        report["status"] = "in_progress"
        report["from_0_1_3"] = verify_source_upgrade(root, out / "from-013", revision=PREVIOUS_REVISION,
            code=4, version="0.1.3-trial", reference_sha256=DELIVERED_013_APK_SHA256,
            test_classes=UPGRADE_TEST_CLASSES[2:4], current=current)
        report["from_0_1_4"] = verify_source_upgrade(root, out / "from-014", revision=PIP_REVISION,
            code=5, version="0.1.4-trial", reference_sha256=DELIVERED_014_APK_SHA256,
            test_classes=UPGRADE_TEST_CLASSES[4:6], current=current)
        report["status"] = "success"
        return report
    except Exception as error:
        report["status"] = "failure"
        report["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
