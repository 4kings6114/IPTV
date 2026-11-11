"""Publish bounded synthetic-only owner-tool diagnostics without log-blob downloads."""
from __future__ import annotations

import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from android.tools.ci_report import publish

if __name__ == "__main__":
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_android_signing")
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    status = 0 if result.wasSuccessful() else 1
    text = output.getvalue()
    print(text)
    publish("Android Windows owner-tool details", status, text)
    sys.exit(status)
