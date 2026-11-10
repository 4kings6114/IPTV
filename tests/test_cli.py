import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import build_playlist
from iptv_builder.builder import BuildError
from iptv_builder.config import load_settings
from tests.helpers import ROOT


class CLITests(unittest.TestCase):
    def test_config_relative_inputs_and_output_dir_are_forwarded(self):
        with patch("build_playlist.run_build") as run, tempfile.TemporaryDirectory() as tmp:
            result = build_playlist.main(
                ["--config", str(ROOT / "config.json"), "--output-dir", tmp, "--dry-run"]
            )
            self.assertEqual(result, 0)
            args = run.call_args.args
            self.assertEqual(args[1:], (Path(tmp), ROOT, True))

    def test_allow_large_drop_only_overrides_percentage_guard(self):
        with patch("build_playlist.run_build") as run, redirect_stderr(io.StringIO()) as output:
            self.assertEqual(build_playlist.main(["--allow-large-drop"]), 0)
            actual = run.call_args.args[0]
            expected = replace(load_settings(ROOT / "config.json"), max_drop_ratio=1)
            self.assertEqual(actual, expected)
            self.assertIn("WARNING", output.getvalue())

    def test_config_failure_and_guard_failure_return_nonzero(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(build_playlist.main(["--config", "/nonexistent-iptv-config.json"]), 1)
            with patch("build_playlist.run_build", side_effect=BuildError("blocked")):
                self.assertEqual(build_playlist.main([]), 1)

    def test_help_does_not_start_a_build(self):
        with patch("build_playlist.run_build") as run, redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as context:
                build_playlist.main(["--help"])
            self.assertEqual(context.exception.code, 0)
            run.assert_not_called()
