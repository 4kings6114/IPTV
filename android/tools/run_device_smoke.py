"""Run real-emulator tests; publish bounded, independently gated runtime evidence."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET
if __package__:
    from .ci_report import publish
    from .verify_upgrade import UPGRADE_TEST_CLASSES, verify_upgrade
else:
    from ci_report import publish
    from verify_upgrade import UPGRADE_TEST_CLASSES, verify_upgrade

PIP_TEST_CLASS = 'io.github.fourkings6114.iptv.PictureInPictureTest'
EPG_TEST_CLASS = 'io.github.fourkings6114.iptv.EpgGuideTest'

EXPECTED_TESTS = {
    'browsingFavoritesAndRecreationSurviveSeventySeconds': 70.0,
    'actualAvcAacPlaybackSurvivesSeventyFiveSecondsRotationAndBackgroundResume': 75.0,
    'localFatalReportCanBeCopiedReopenedAndDeletedWithoutLeakingMessages': 0.0,
    'httpsPoolEvictionOnMainReproducesReportedCrash': 0.0,
    'httpsPooledSocketsSurvivePlayerStopResumeAndDestroyWithStrictMode': 0.0,
    'independentLinksCanBeNamedRefreshedRenamedDeletedAndReopened': 0.0,
    'fileReimportKeepsItsTargetAcrossRecreationAndRejectsDeletedTargets': 0.0,
    'missingSelectionRecoversToOfflineSampleWithoutErasingSavedChannels': 0.0,
    'xmltvSettingsShowExactNowNextTimesAndReopenOffline': 0.0,
    'failedAndCancelledGuideRefreshKeepGoodDataAndHidePrivateTokens': 0.0,
    'manualPictureInPictureKeepsDecodedPlaybackAndRestoresFullscreen': 8.0,
    'automaticPictureInPictureRequiresOptInAndDismissalReleasesThePlayer': 6.0,
    'pictureInPictureActionsPauseResumeAndNewChannelReusesTheActivity': 2.0,
    'deniedPictureInPictureKeepsFullscreenUsableAndStopsOnHome': 0.0,
    'explicitBackNeverAutoEntersPictureInPictureEvenWithHomeOptIn': 0.0,
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


def upgrade_verification_errors(upgrade):
    errors = []
    if not upgrade or upgrade.get('status') != 'success':
        errors.append('The source-rebuilt 0.1.2, 0.1.3 and 0.1.4 -> current install -r preservation tests must pass.')
    if not upgrade or (upgrade.get('from_0_1_3') or {}).get('status') != 'success':
        errors.append('Named multi-catalog 0.1.3 upgrade preservation must pass independently.')
    if not upgrade or (upgrade.get('from_0_1_4') or {}).get('status') != 'success':
        errors.append('Delivered PiP 0.1.4 catalog/settings/identity preservation must pass independently.')
    return errors


def run_runtime_phase(root: Path, out: Path, label: str, runner_argument: str) -> int:
    """Keep each real instrumentation phase's XML, even when a later run replaces outputs."""
    assert label in ('pip', 'epg', 'ordinary')
    log_path = out / ('gradle.log' if label == 'ordinary' else label + '-gradle.log')
    with log_path.open('w') as log:
        process = subprocess.Popen([
            './gradlew', '--no-daemon', '--stacktrace', runner_argument,
            ':app:connectedDebugAndroidTest',
        ], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in process.stdout:
            print(line, end='', flush=True)
            log.write(line)
        status = process.wait()
    source = root / 'app/build/outputs/androidTest-results'
    destination = out / 'runtime-results' / label
    for path in source.rglob('*.xml'):
        copied = destination / path.relative_to(source)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copied)
    return status


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / 'app/build/device-smoke'
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out / 'runtime-results', ignore_errors=True) # generated results, never app/user data
    api = subprocess.run(['adb', 'shell', 'getprop', 'ro.build.version.sdk'], text=True, capture_output=True, timeout=20)
    actual_api = api.stdout.strip()
    expected_api = os.environ.get('EXPECTED_ANDROID_API', '31')
    status = 1
    phase_error = None
    upgrade = None
    try:
        assert expected_api in ('31', '33'), 'Unsupported runtime target'
        assert actual_api == expected_api and api.returncode == 0, f'Expected runtime API {expected_api}, got {actual_api!r}'
        # Run system-window automation in its own instrumentation process, before
        # the slower upgrade phases. No Compose/Espresso rule owns its accessibility
        # connection. A failure is still fatal; all previous regressions remain required.
        status = run_runtime_phase(root, out, 'pip',
            '-Pandroid.testInstrumentationRunnerArguments.class=' + PIP_TEST_CLASS)
        if status:
            raise RuntimeError('Native PiP instrumentation failed; see pip-gradle.log and runtime-results/pip.')
        status = run_runtime_phase(root, out, 'epg',
            '-Pandroid.testInstrumentationRunnerArguments.class=' + EPG_TEST_CLASS)
        if status:
            raise RuntimeError('EPG instrumentation failed; see epg-gradle.log and runtime-results/epg.')
        upgrade = verify_upgrade(root, out / 'upgrade')
        # Seeds/verifiers ran mandatorily against their own app versions above;
        # five PiP and two EPG cases already ran on current. All eight older cases remain required.
        excluded = ','.join((*UPGRADE_TEST_CLASSES, PIP_TEST_CLASS, EPG_TEST_CLASS))
        status = run_runtime_phase(root, out, 'ordinary',
            '-Pandroid.testInstrumentationRunnerArguments.notClass=' + excluded)
    except Exception:
        phase_error = traceback.format_exc()[-12000:]
        print(phase_error, flush=True)
    counts, cases, failures = collect_results(out / 'runtime-results')
    errors = verification_errors(counts, cases)
    if phase_error:
        errors.append(phase_error)
    if upgrade is None:
        saved = out / 'upgrade/summary.json'
        if saved.exists():
            upgrade = json.loads(saved.read_text())
    errors.extend(upgrade_verification_errors(upgrade))
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
        'signing_source': os.environ.get('IPTV_SIGNING_SOURCE', 'unknown'),
        'commit': os.environ.get('GITHUB_SHA'), 'run_id': os.environ.get('GITHUB_RUN_ID'),
        'upgrade': upgrade, 'tests': counts, 'cases': cases, 'failures': failures, 'verification_errors': errors,
        'scope': 'Source-rebuilt 0.1.2, 0.1.3 and 0.1.4 signed in-place upgrades, legacy/named-catalog data, stable channel identity and existing Home-PiP preference preservation; real EPG settings/exact-ID now-next/local times/XMLTV/gzip/offline reopen/failure/cancellation/HTTP policy and token-free errors; independent URL/file catalogs, naming, rename, refresh, cancel, delete and recreation; 70s UI; 75s generated loopback AVC/AAC, rotation, stop/resume; real native PiP manual/opted-in Home, decoded playback, expand, dismiss, pause/play system actions, channel reuse, denied permission and explicit Back; private reports; old TLS main-thread negative control and strict-mode real player TLS-pool teardown',
    }
    (out / 'summary.json').write_text(json.dumps(report, indent=2))
    gradle_log = out / 'gradle.log'
    if not gradle_log.exists():
        gradle_log = out / 'epg-gradle.log'
    if not gradle_log.exists():
        gradle_log = out / 'pip-gradle.log'
    tail = gradle_log.read_text().splitlines()[-70:] if gradle_log.exists() else []
    publish(f'Android API{expected_api} runtime stability', status, json.dumps(report, indent=2) + '\n' + '\n'.join(tail) + '\n' + logcat)
    return status


if __name__ == '__main__':
    sys.exit(main())
