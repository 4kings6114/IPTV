"""Offline gates for required emulator evidence; these do not claim to run Android."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from android.tools.run_device_smoke import EXPECTED_TESTS, collect_results, verification_errors


class AndroidRuntimeGateTests(unittest.TestCase):
    def setUp(self):
        self.counts = dict(tests=len(EXPECTED_TESTS), failures=0, errors=0, skipped=0)
        self.cases = [dict(name=name, seconds=max(1.0, minimum)) for name, minimum in EXPECTED_TESTS.items()]

    def test_all_previous_and_new_native_pip_tests_are_required(self):
        self.assertEqual(13, len(EXPECTED_TESTS))
        self.assertEqual([], verification_errors(self.counts, self.cases))
        for name in EXPECTED_TESTS:
            with self.subTest(name=name):
                self.assertTrue(verification_errors(self.counts, [case for case in self.cases if case['name'] != name]))

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
