"""Run bounded real-emulator tests and expose failures via authenticated Checks API."""
from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from ci_report import publish

root = Path(__file__).resolve().parents[1]
out = root / 'app/build/device-smoke'
out.mkdir(parents=True, exist_ok=True)
with (out/'gradle.log').open('w') as log:
    process = subprocess.Popen(['./gradlew', '--no-daemon', '--stacktrace', ':app:connectedDebugAndroidTest'], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in process.stdout:
        print(line, end='', flush=True)
        log.write(line)
    status = process.wait()
counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
failures = []
for path in (root/'app/build/outputs/androidTest-results').rglob('*.xml'):
    try:
        tree = ET.parse(path).getroot()
    except ET.ParseError:
        continue
    suites = [tree] if tree.tag == 'testsuite' else tree.findall('testsuite')
    for suite in suites:
        for key in counts: counts[key] += int(suite.get(key, '0'))
        for case in suite.findall('testcase'):
            for failure in case.findall('failure') + case.findall('error'):
                failures.append(f"{case.get('name')}: {failure.get('message')}\n{(failure.text or '')[:4000]}")
if counts['tests'] < 2 or counts['failures'] or counts['errors'] or counts['skipped']:
    status = status or 1
logcat = subprocess.run(['adb', 'logcat', '-d', '-s', 'AndroidRuntime:E'], text=True, capture_output=True, timeout=20).stdout[-12000:]
(out/'android-runtime.log').write_text(logcat)
report = {'status': status, 'api': 33, 'environment': 'Android emulator, not a Poco X3/MIUI', 'tests': counts, 'scope': '70s UI; 75s generated localhost AVC/AAC video, rotation, stop/restart', 'failures': failures}
(out/'summary.json').write_text(json.dumps(report,indent=2))
tail = (out/'gradle.log').read_text().splitlines()[-70:]
publish('Android API33 runtime stability', status, json.dumps(report,indent=2)+'\n'+'\n'.join(tail)+'\n'+logcat)
sys.exit(status)
