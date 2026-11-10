"""Keep the expendable DEBUG key at the exact cached path; never export private material."""
import os
from pathlib import Path
import subprocess

key = Path.home() / '.android/debug.keystore'
key.parent.mkdir(parents=True, exist_ok=True)
restored = key.is_file()
if not restored:
    # Standard Android debug credentials, NOT a production distribution identity.
    subprocess.run([
        'keytool', '-genkeypair', '-noprompt', '-storetype', 'JKS',
        '-keystore', str(key), '-storepass', 'android', '-keypass', 'android',
        '-alias', 'androiddebugkey', '-keyalg', 'RSA', '-keysize', '2048',
        '-validity', '10000', '-dname', 'CN=Android Debug,O=Android,C=US',
    ], check=True, capture_output=True, timeout=45)
key.chmod(0o600)
assert key.stat().st_size > 0
with open(os.environ['GITHUB_ENV'], 'a') as output:
    output.write(f'IPTV_TRIAL_KEYSTORE={key}\n')
print('Trial key restored from cache:', restored)
print('CI signing now explicitly uses the cached key path. No key is uploaded as an artifact.')
