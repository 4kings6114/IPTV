"""Run real-emulator tests; publish bounded, independently gated runtime evidence."""
from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from ci_report import publish

EXPECTED_TESTS = {
    'browsingFavoritesAndRecreationSurviveSeventySeconds': 70.0,
    'actualAvcAacPlaybackSurvivesSeventyFiveSecondsRotationAndBackgroundResume': 75.0,
    'localFatalReportCanBeCopiedReopenedAndDeletedWithoutLeakingMessages': 0.0,
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
    with (out / 'gradle.log').open('w') as log:
        process = subprocess.Popen(['./gradlew', '--no-daemon', '--stacktrace', ':app:connectedDebugAndroidTest'], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in process.stdout:
            print(line, end='', flush=True)
            log.write(line)
        status = process.wait()
    counts, cases, failures = collect_results(root / 'app/build/outputs/androidTest-results')
    errors = verification_errors(counts, cases)
    if actual_api != '33' or api.returncode:
        errors.append(f'Expected runtime API 33, got {actual_api!r} (adb status {api.returncode}).')
    if errors:
        status = status or 1
    try:
        logcat = subprocess.run(['adb', 'logcat', '-d', '-s', 'AndroidRuntime:E'], text=True, capture_output=True, timeout=20).stdout[-12000:]
    except subprocess.TimeoutExpired:
        logcat = 'Runtime log collection timed out; rely on the instrumentation results.'
    (out / 'android-runtime.log').write_text(logcat)
    report = {
        'status': status, 'api': actual_api, 'environment': 'Android emulator, not a Poco X3/MIUI',
        'tests': counts, 'cases': cases, 'failures': failures, 'verification_errors': errors,
        'scope': '70s UI; 75s generated loopback AVC/AAC video/audio, actual rotation, stop/new engine on resume; synthetic local-report UI',
    }
    (out / 'summary.json').write_text(json.dumps(report, indent=2))
    tail = (out / 'gradle.log').read_text().splitlines()[-70:]
    publish('Android API33 runtime stability', status, json.dumps(report, indent=2) + '\n' + '\n'.join(tail) + '\n' + logcat)
    return status


if __name__ == '__main__':
    sys.exit(main())
