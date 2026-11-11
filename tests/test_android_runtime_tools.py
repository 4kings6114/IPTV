"""Offline gates for required emulator evidence; these do not claim to run Android."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import io
import tempfile
import unittest
from unittest.mock import patch

from android.tools.run_device_smoke import EXPECTED_TESTS, collect_results, verification_errors, run_runtime_phase, upgrade_verification_errors


class AndroidRuntimeGateTests(unittest.TestCase):
    def setUp(self):
        self.counts = dict(tests=len(EXPECTED_TESTS), failures=0, errors=0, skipped=0)
        self.cases = [dict(name=name, seconds=max(1.0, minimum)) for name, minimum in EXPECTED_TESTS.items()]

    def test_all_previous_pip_and_new_epg_tests_are_required(self):
        self.assertEqual(15, len(EXPECTED_TESTS))
        self.assertEqual([], verification_errors(self.counts, self.cases))
        for name in EXPECTED_TESTS:
            with self.subTest(name=name):
                self.assertTrue(verification_errors(self.counts, [case for case in self.cases if case['name'] != name]))

    def test_all_three_upgrade_paths_are_independently_required(self):
        report = dict(status='success', from_0_1_3=dict(status='success'), from_0_1_4=dict(status='success'))
        self.assertEqual([], upgrade_verification_errors(report))
        self.assertTrue(upgrade_verification_errors(None))
        for key in report:
            missing = deepcopy(report)
            del missing[key]
            with self.subTest(key=key):
                self.assertTrue(upgrade_verification_errors(missing))
            failed = deepcopy(report)
            failed[key] = 'failure' if key == 'status' else dict(status='failure')
            with self.subTest(key=key, failed=True):
                self.assertTrue(upgrade_verification_errors(failed))

    def test_no_failure_error_or_skipped_case_is_accepted(self):
        for key in ('failures', 'errors', 'skipped'):
            counts = dict(self.counts, **{key: 1})
            with self.subTest(key=key):
                self.assertTrue(verification_errors(counts, self.cases))

    def test_prolonged_ui_and_playback_are_not_weakened_by_the_pip_feature(self):
        self.assertGreaterEqual(EXPECTED_TESTS['browsingFavoritesAndRecreationSurviveSeventySeconds'], 70)
        self.assertGreaterEqual(EXPECTED_TESTS['actualAvcAacPlaybackSurvivesSeventyFiveSecondsRotationAndBackgroundResume'], 75)
        for name, minimum in EXPECTED_TESTS.items():
            if not minimum:
                continue
            cases = deepcopy(self.cases)
            next(case for case in cases if case['name'] == name)['seconds'] = minimum - 0.01
            with self.subTest(name=name):
                self.assertTrue(verification_errors(self.counts, cases))

    def test_separate_real_instrumentation_results_are_preserved_and_aggregated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'android'
            out = root / 'app/build/device-smoke'
            out.mkdir(parents=True)
            xml = root / 'app/build/outputs/androidTest-results/TEST-device.xml'
            xml.parent.mkdir(parents=True)
            for phase in ('pip', 'epg', 'ordinary'):
                xml.write_text(f'<testsuite tests="1"><testcase name="{phase}" time="1" /></testsuite>')
                process = SimpleNamespace(stdout=io.StringIO(), wait=lambda: 0)
                argument = '-Pandroid.testInstrumentationRunnerArguments.class=synthetic.' + phase
                with patch('android.tools.run_device_smoke.subprocess.Popen', return_value=process) as start:
                    self.assertEqual(0, run_runtime_phase(root, out, phase, argument))
                self.assertIn(argument, start.call_args.args[0])
                self.assertIn(':app:connectedDebugAndroidTest', start.call_args.args[0])
            counts, cases, failures = collect_results(out / 'runtime-results')
            self.assertEqual(3, counts['tests'])
            self.assertEqual({'pip', 'epg', 'ordinary'}, {case['name'] for case in cases})
            self.assertEqual([], failures)

    def test_xml_collection_reports_real_cases_failures_and_skips(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'TEST-local.xml').write_text('''<testsuite tests="3" failures="1" errors="0" skipped="1">
                <testcase name="rendered" classname="NativePip" time="8.5" />
                <testcase name="failed" classname="NativePip" time="0.1"><failure message="not minimized">details</failure></testcase>
                <testcase name="skipped" classname="NativePip" time="0"><skipped /></testcase>
                </testsuite>''')
            counts, cases, failures = collect_results(directory)
            self.assertEqual(dict(tests=3, failures=1, errors=0, skipped=1), counts)
            self.assertEqual(['rendered', 'failed', 'skipped'], [case['name'] for case in cases])
            self.assertEqual(8.5, cases[0]['seconds'])
            self.assertIn('not minimized', failures[0])


if __name__ == '__main__':
    unittest.main()
