"""Publish bounded synthetic-only owner-tool diagnostics without log-blob downloads.

Only non-secret ACL commands are inspected; this job never has a signing Secret,
keystore, recovery identity or passphrase. Never reuse this diagnostic wrapper
around the real owner setup or cryptographic subprocesses.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from android.tools.ci_report import publish
from android.tools import signing_owner, signing_support

if __name__ == "__main__":
    output = io.StringIO()
    original = signing_support.checked_tool

    def public_acl_tool(command, *, env=None, input_data=None, timeout=60):
        if os.name != "nt" or command[0] != "powershell.exe":
            return original(command, env=env, input_data=input_data, timeout=timeout)
        result = subprocess.run(command, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, timeout=timeout)
        if result.returncode:
            # These commands reference only a disposable test directory/SID and
            # static ACL code, never private data or token/environment listings.
            phase = "ACL verification" if "-EncodedCommand" in command else "SID lookup"
            output.write(f"Synthetic-only Windows {phase} failed with exit code {result.returncode}.\n")
            output.write("Synthetic-only Windows ACL diagnostic:\n" + result.stderr.decode("utf-8", errors="replace")[:6000] + "\n")
            raise signing_support.SigningError("Synthetic Windows ACL command failed.")
        return result.stdout

    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_android_signing")
    with patch.object(signing_owner, "checked_tool", side_effect=public_acl_tool):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    status = 0 if result.wasSuccessful() else 1
    text = output.getvalue()
    print(text)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        publish("Android Windows owner-tool details", status, text)
    sys.exit(status)
