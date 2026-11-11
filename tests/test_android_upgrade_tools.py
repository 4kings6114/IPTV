"""Offline tests for the Android CI upgrade verifier, no SDK/emulator required."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from android.tools.verify_upgrade import compare_schemas, verify_instrumentation_output
from android.tools import verify_upgrade as upgrade


class AndroidUpgradeVerifierTests(unittest.TestCase):
    def test_accepts_exactly_one_successful_instrumentation_case(self):
        verify_instrumentation_output(
            "INSTRUMENTATION_STATUS: numtests=1\n\nOK (1 test)\n\nINSTRUMENTATION_CODE: -1\n"
        )

    def test_rejects_failure_even_when_adb_process_exit_would_be_zero(self):
        for output in (
            "FAILURES!!!\nTests run: 1, Failures: 1\nINSTRUMENTATION_CODE: -1\n",
            "INSTRUMENTATION_FAILED: io.github.example.test\n",
            "OK (1 test)\nINSTRUMENTATION_CODE: 0\n",
            "OK (2 tests)\nINSTRUMENTATION_CODE: -1\n",
            "OK (1 test)\nINSTRUMENTATION_FAILED: failed\nINSTRUMENTATION_CODE: -1\n",
        ):
            with self.subTest(output=output), self.assertRaises(AssertionError):
                verify_instrumentation_output(output)

    def test_schema_gate_compares_the_actual_room_database_not_only_version(self):
        old = {"formatVersion": 1, "database": {"version": 1, "identityHash": "same", "entities": [{"tableName": "sources"}]}}
        self.assertEqual(compare_schemas(old, deepcopy(old)), "same")
        for field, value in (("version", 2), ("identityHash", "different"), ("entities", [])):
            changed = deepcopy(old)
            changed["database"][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                compare_schemas(old, changed)

    def test_each_source_upgrade_performs_install_r_without_erasing_seeded_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "android"
            baseline = Path(temp) / "baseline/android"
            schema = {"database": {"version": 1, "identityHash": "preserved", "entities": []}}
            for location in (root, baseline):
                path = location / upgrade.SCHEMA_PATH
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(schema))
            calls = []
            def fake_run(command, *args):
                calls.append(command)
                if "instrument" in command:
                    return "OK (1 test)\nINSTRUMENTATION_CODE: -1\n"
                return "Success\n"
            with patch.object(upgrade, "extract_baseline", return_value=baseline), \
                    patch.object(upgrade, "run_logged", side_effect=fake_run), \
                    patch.object(upgrade, "apk_identity", return_value={"apk_sha256": upgrade.DELIVERED_013_APK_SHA256}):
                result = upgrade.verify_source_upgrade(root, root / "evidence", revision=upgrade.PREVIOUS_REVISION,
                    code=4, version="0.1.3-trial", reference_sha256=upgrade.DELIVERED_013_APK_SHA256,
                    test_classes=upgrade.UPGRADE_TEST_CLASSES[2:4], current={"version_code": 6})
            seed = next(index for index, command in enumerate(calls) if upgrade.UPGRADE_TEST_CLASSES[2] in command)
            verify = next(index for index, command in enumerate(calls) if upgrade.UPGRADE_TEST_CLASSES[3] in command)
            self.assertEqual(calls[seed + 1:verify], [["adb", "install", "-r", str(root / "app/build/outputs/apk/debug/app-debug.apk")]])
            self.assertEqual(sum(command[:4] == ["adb", "shell", "pm", "clear"] for command in calls), 1)
            self.assertFalse(any("uninstall" in command for command in calls))
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["room_schema_unchanged"])
            self.assertTrue(result["baseline_binary_matches_delivered_sha256"])

    def test_multi_catalog_upgrade_is_required_in_addition_to_the_legacy_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "android"
            out = root / "evidence"
            with patch.object(upgrade, "run_logged"), patch.object(upgrade, "apk_identity", return_value={}), \
                    patch.object(upgrade, "verify_source_upgrade", side_effect=[{"status": "success"}, RuntimeError("named catalogs not preserved")]) as phases:
                with self.assertRaisesRegex(RuntimeError, "named catalogs"):
                    upgrade.verify_upgrade(root, out)
            self.assertEqual(phases.call_count, 2)
            self.assertEqual(phases.call_args_list[0].kwargs["revision"], upgrade.BASELINE_REVISION)
            self.assertEqual(phases.call_args_list[1].kwargs["revision"], upgrade.PREVIOUS_REVISION)
            self.assertEqual(phases.call_args_list[1].kwargs["test_classes"], upgrade.UPGRADE_TEST_CLASSES[2:4])
            self.assertEqual(json.loads((out / "summary.json").read_text())["status"], "failure")


    def test_delivered_014_pip_upgrade_is_required_after_both_older_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "android"
            out = root / "evidence"
            with patch.object(upgrade, "run_logged"), patch.object(upgrade, "apk_identity", return_value={}), \
                    patch.object(upgrade, "verify_source_upgrade", side_effect=[{"status": "success"}, {"status": "success"}, RuntimeError("PiP preference not preserved")]) as phases:
                with self.assertRaisesRegex(RuntimeError, "PiP preference"):
                    upgrade.verify_upgrade(root, out)
            self.assertEqual(phases.call_count, 3)
            latest = phases.call_args_list[2].kwargs
            self.assertEqual(latest["revision"], "b48e15512b9a41a8d76c5cec3dcc99e06e2ba8ed")
            self.assertEqual(latest["reference_sha256"], upgrade.DELIVERED_014_APK_SHA256)
            self.assertEqual(latest["code"], 5)
            self.assertEqual(latest["version"], "0.1.4-trial")
            self.assertEqual(latest["test_classes"], upgrade.UPGRADE_TEST_CLASSES[4:6])
            report = json.loads((out / "summary.json").read_text())
            self.assertEqual(report["status"], "failure")
            self.assertEqual(report["from_0_1_3"]["status"], "success")

    def test_success_report_contains_all_three_independent_immutable_baselines(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "android"
            with patch.object(upgrade, "run_logged"), patch.object(upgrade, "apk_identity", return_value={}), \
                    patch.object(upgrade, "verify_source_upgrade", return_value={"status": "success"}) as phases:
                report = upgrade.verify_upgrade(root, root / "evidence")
            self.assertEqual("success", report["status"])
            self.assertEqual("success", report["from_0_1_3"]["status"])
            self.assertEqual("success", report["from_0_1_4"]["status"])
            self.assertEqual([upgrade.BASELINE_REVISION, upgrade.PREVIOUS_REVISION, upgrade.PIP_REVISION],
                             [call.kwargs["revision"] for call in phases.call_args_list])
            self.assertEqual((6, "0.1.5-trial"), (upgrade.CURRENT_VERSION_CODE, upgrade.CURRENT_VERSION_NAME))


if __name__ == "__main__":
    unittest.main()
