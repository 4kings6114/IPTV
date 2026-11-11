"""Run real-emulator tests; publish bounded, independently gated runtime evidence."""
from pathlib import Path
import json
import os
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET
from ci_report import publish
from verify_upgrade import UPGRADE_TEST_CLASSES, verify_upgrade

EXPECTED_TESTS = {
    'browsingFavoritesAndRecreationSurviveSeventySeconds': 70.0,
    'actualAvcAacPlaybackSurvivesSeventyFiveSecondsRotationAndBackgroundResume': 75.0,
    'localFatalReportCanBeCopiedReopenedAndDeletedWithoutLeakingMessages': 0.0,
    'httpsPoolEvictionOnMainReproducesReportedCrash': 0.0,
    'httpsPooledSocketsSurvivePlayerStopResumeAndDestroyWithStrictMode': 0.0,
    'independentLinksCanBeNamedRefreshedRenamedDeletedAndReopened': 0.0,
    'fileReimportKeepsItsTargetAcrossRecreationAndRejectsDeletedTargets': 0.0,
    'missingSelectionRecoversToOfflineSampleWithoutErasingSavedChannels': 0.0,
}


def collect_results(directory: Path):
    counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    cases = []
    failures = []
    for path in directory.rglob('*.xml'):
        try:
            tree = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        suites = [tree] if tree.tag == 'testsuite' else tree.findall('testsuite')
        for suite in suites:
            for key in counts:
                counts[key] += int(suite.get(key, '0'))
            for case in suite.findall('testcase'):
                cases.append({'name': case.get('name'), 'class': case.get('classname'), 'seconds': float(case.get('time', '0'))})
                for failure in case.findall('failure') + case.findall('error'):
                    failures.append(f"{case.get('name')}: {failure.get('message')}\n{(failure.text or '')[:4000]}")
    return counts, cases, failures


def verification_errors(counts, cases):
    errors = []
    if counts['tests'] < len(EXPECTED_TESTS) or any(counts[key] for key in ('failures', 'errors', 'skipped')):
        errors.append('All required device tests must execute and pass without skips.')
    by_name = {case['name']: case for case in cases}
    for name, minimum in EXPECTED_TESTS.items():
        case = by_name.get(name)
        if case is None:
            errors.append(f'Missing test: {name}')
        elif case['seconds'] < minimum:
            errors.append(f'{name} ran for {case["seconds"]} seconds; minimum is {minimum}.')
    return errors


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / 'app/build/device-smoke'
    out.mkdir(parents=True, exist_ok=True)
    api = subprocess.run(['adb', 'shell', 'getprop', 'ro.build.version.sdk'], text=True, capture_output=True, timeout=20)
    actual_api = api.stdout.strip()
    expected_api = os.environ.get('EXPECTED_ANDROID_API', '31')
    status = 1
    phase_error = None
    upgrade = None
    try:
        assert expected_api in ('31', '33'), 'Unsupported runtime target'
        assert actual_api == expected_api and api.returncode == 0, f'Expected runtime API {expected_api}, got {actual_api!r}'
        upgrade = verify_upgrade(root, out / 'upgrade')
        # Seed/verify are REQUIRED in the two separate version-specific phases above,
        # not ignored/skipped in a mixed-version suite on the current application.
        excluded = ','.join(UPGRADE_TEST_CLASSES)
        with (out / 'gradle.log').open('w') as log:
            process = subprocess.Popen([
                './gradlew', '--no-daemon', '--stacktrace',
                '-Pandroid.testInstrumentationRunnerArguments.notClass=' + excluded,
                ':app:connectedDebugAndroidTest',
            ], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line)
            status = process.wait()
    except Exception:
        phase_error = traceback.format_exc()[-12000:]
        print(phase_error, flush=True)
    counts, cases, failures = collect_results(root / 'app/build/outputs/androidTest-results')
    errors = verification_errors(counts, cases)
    if phase_error:
        errors.append(phase_error)
    if upgrade is None:
        saved = out / 'upgrade/summary.json'
        if saved.exists():
            upgrade = json.loads(saved.read_text())
    if not upgrade or upgrade.get('status') != 'success':
        errors.append('The source-rebuilt 0.1.2 -> current install -r preservation test must pass.')
    if actual_api != expected_api or api.returncode:
        errors.append(f'Expected runtime API {expected_api}, got {actual_api!r} (adb status {api.returncode}).')
    if errors:
        status = status or 1
    try:
        logcat = subprocess.run(['adb', 'logcat', '-d', '-s', 'AndroidRuntime:E'], text=True, capture_output=True, timeout=20).stdout[-12000:]
    except subprocess.TimeoutExpired:
        logcat = 'Runtime log collection timed out; rely on the instrumentation results.'
    (out / 'android-runtime.log').write_text(logcat)
    report = {
        'status': status, 'api': actual_api, 'expected_api': expected_api, 'environment': 'Android emulator, not a Poco X3/MIUI',
        'upgrade': upgrade, 'tests': counts, 'cases': cases, 'failures': failures, 'verification_errors': errors,
        'scope': 'Source-rebuilt 0.1.2 signed in-place upgrade, legacy data/selection preservation; independent URL/file catalogs, naming, rename, refresh, cancel, delete and recreation; 70s UI; 75s generated loopback AVC/AAC, rotation, stop/resume; private reports; old TLS main-thread negative control and strict-mode real player TLS-pool teardown',
    }
    (out / 'summary.json').write_text(json.dumps(report, indent=2))
    gradle_log = out / 'gradle.log'
    tail = gradle_log.read_text().splitlines()[-70:] if gradle_log.exists() else []
    publish(f'Android API{expected_api} runtime stability', status, json.dumps(report, indent=2) + '\n' + '\n'.join(tail) + '\n' + logcat)
    return status


if __name__ == '__main__':
    sys.exit(main())
