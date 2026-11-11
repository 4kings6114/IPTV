"""Offline tests for the Android CI upgrade verifier, no SDK/emulator required."""
from copy import deepcopy
import unittest

from android.tools.verify_upgrade import compare_schemas, verify_instrumentation_output


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


if __name__ == "__main__":
    unittest.main()
