"""Offline signing-policy/owner-tool tests. Never use the live signing key."""
from __future__ import annotations

import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from android.tools import export_signing_backup as exporter
from android.tools import prepare_trial_signing as prepare
from android.tools import signing_owner as owner
from android.tools import signing_support as support

PUBLIC_RECIPIENT = "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"
COMMIT = "a" * 40
# Structural fixture only, NOT a usable keystore/private key.
FAKE_JKS = b"\xfe\xed\xfe\xed" + b"not-a-signing-key" * 40


def request():
    return dict(format=1, repository=support.REPOSITORY, branch=support.BRANCH,
                certificate_sha256=support.EXPECTED_SIGNER_SHA256,
                request_id="b" * 32, recipient=PUBLIC_RECIPIENT)


def policy(source="legacy-cache-during-owner-setup"):
    return dict(format=1, repository=support.REPOSITORY, branch=support.BRANCH,
                application_id=support.APPLICATION_ID,
                certificate_sha256=support.EXPECTED_SIGNER_SHA256, required_source=source)


class AndroidSigningUnitTests(unittest.TestCase):
    def test_secret_only_policy_cannot_silently_fall_back_or_generate_a_key(self):
        with self.assertRaises(support.SigningError):
            prepare.signing_source(policy("actions-secret"), "")
        self.assertEqual(prepare.signing_source(policy(), ""), "legacy-cache-transitional")
        self.assertEqual(prepare.signing_source(policy(), "present"), "actions-secret")
        self.assertEqual(prepare.signing_source(policy("actions-secret"), "present"), "actions-secret")
        source = Path(prepare.__file__).read_text()
        self.assertNotIn("-genkeypair", source)
        self.assertNotIn("-genkey", source)

    def test_invalid_secret_values_are_rejected_without_echoing_input(self):
        for value in (None, "", "DO_NOT_ECHO_PRIVATE_INPUT!", "x" * 50000,
                      base64.b64encode(b"wrong-format" * 80).decode()):
            with self.subTest(kind=type(value).__name__), self.assertRaises(support.SigningError) as caught:
                support.decode_keystore(value)
            self.assertNotIn("DO_NOT_ECHO", str(caught.exception))
        self.assertEqual(support.decode_keystore(base64.b64encode(FAKE_JKS).decode()), FAKE_JKS)

    def test_policy_requires_exact_identity_and_explicit_storage_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / support.POLICY_PATH
            path.parent.mkdir(parents=True)
            for value in ([], {}, policy("unknown"), dict(policy(), certificate_sha256="0" * 64), dict(policy(), branch="main")):
                path.write_text(json.dumps(value))
                with self.subTest(value=value), self.assertRaises(support.SigningError):
                    support.read_policy(root)
            path.write_text(json.dumps(policy()))
            self.assertEqual(support.read_policy(root), policy())

    def test_recipient_requests_cannot_inject_arguments_paths_or_another_identity(self):
        self.assertEqual(support.check_request(request()), request())
        for value in ([], {}, dict(request(), recipient="--output=/tmp/private"),
                      dict(request(), request_id="../../secret"), dict(request(), branch="main"),
                      dict(request(), certificate_sha256="0" * 64)):
            with self.subTest(value=value), self.assertRaises(support.SigningError):
                support.check_request(value)

    def test_backup_context_is_bound_to_owner_request_commit_and_certificate(self):
        payload = dict(format="iptv-signing-backup-v1", repository=support.REPOSITORY,
                       branch=support.BRANCH, application_id=support.APPLICATION_ID,
                       certificate_sha256=support.EXPECTED_SIGNER_SHA256,
                       request_id=request()["request_id"], recipient=PUBLIC_RECIPIENT,
                       commit=COMMIT, keystore_base64=base64.b64encode(FAKE_JKS).decode())
        self.assertEqual(support.validate_backup(payload, request(), COMMIT), FAKE_JKS)
        for field in ("repository", "branch", "application_id", "certificate_sha256", "request_id", "recipient", "commit"):
            changed = dict(payload, **{field: "wrong"})
            with self.subTest(field=field), self.assertRaises(support.SigningError):
                support.validate_backup(changed, request(), COMMIT)
        with self.assertRaises(support.SigningError):
            support.validate_backup([], request(), COMMIT)

    def test_backup_export_reads_real_commit_diff_not_omitted_push_payload_file_lists(self):
        for changed, expected in ((support.REQUEST_PATH + "\n", True), ("", False)):
            outputs = [(COMMIT + "\n").encode(), (COMMIT + " " + "c" * 40 + "\n").encode(), changed.encode()]
            with patch.object(exporter, "checked_tool", side_effect=outputs) as call:
                self.assertEqual(exporter.fresh_owner_request(Path("repository"), COMMIT), expected)
                command = call.call_args.args[0]
                self.assertIn("diff-tree", command)
                self.assertEqual(command[-2:], ["--", support.REQUEST_PATH])
        with patch.object(exporter, "checked_tool", side_effect=[COMMIT.encode(), COMMIT.encode()]):
            with self.assertRaises(support.SigningError):
                exporter.fresh_owner_request(Path("shallow-checkout"), COMMIT)
        with patch.object(exporter, "checked_tool") as call:
            with self.assertRaises(support.SigningError):
                exporter.fresh_owner_request(Path("repository"), "not-a-commit")
            call.assert_not_called()

    def test_plaintext_and_unexpected_artifact_archives_are_refused(self):
        cipher = b"age-encryption.org/v1\nsynthetic ciphertext only"
        name = "iptv-signing-" + "b" * 32 + ".age"
        self.assertEqual(owner.extract_age_bytes(cipher, name), cipher)
        for data in (FAKE_JKS, b"plaintext", b"x" * (support.MAX_BACKUP_BYTES + 1)):
            with self.assertRaises(support.SigningError):
                owner.extract_age_bytes(data, name)
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr(name, cipher)
        self.assertEqual(owner.extract_age_bytes(archive_bytes.getvalue(), name), cipher)
        malicious = io.BytesIO()
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("../private.jks", FAKE_JKS)
        with self.assertRaises(support.SigningError):
            owner.extract_age_bytes(malicious.getvalue(), name)

    def test_secret_install_uses_stdin_not_command_line_or_a_cleartext_file(self):
        with patch.object(owner, "api", side_effect=owner.GitHubFailure(404)), \
                patch.object(owner, "gh_call", return_value=b"") as call, redirect_stdout(io.StringIO()):
            owner.install_secret(FAKE_JKS)
        args, data = call.call_args.args
        self.assertEqual(args, ["secret", "set", support.SECRET_NAME, "--repo", "github.com/" + support.REPOSITORY])
        self.assertEqual(data, base64.b64encode(FAKE_JKS))
        self.assertNotIn(data.decode(), " ".join(args))

    def test_existing_secret_is_not_overwritten_without_explicit_replace(self):
        with patch.object(owner, "api", return_value={"name": support.SECRET_NAME}), patch.object(owner, "gh_call") as call:
            with self.assertRaises(support.SigningError):
                owner.install_secret(FAKE_JKS)
            call.assert_not_called()

    def test_public_git_commits_and_pushes_are_limited_to_one_file_and_active_branch(self):
        def git_output(args):
            if args == ["branch", "--show-current"]:
                return support.BRANCH.encode()
            if args[:2] == ["remote", "get-url"]:
                return ("https://github.com/" + support.REPOSITORY + ".git").encode()
            if args == ["diff", "--cached", "--name-only"]:
                return (support.REQUEST_PATH + "\n").encode()
            if args == ["rev-parse", "HEAD"]:
                return COMMIT.encode()
            return b""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(owner, "__file__", str(root / "android/tools/signing_owner.py")), \
                    patch.object(owner, "git_call", side_effect=git_output) as call, patch.object(owner, "api") as api:
                self.assertEqual(owner.commit_public_file(support.REQUEST_PATH, request(), "Public recovery request"), COMMIT)
                commands = [entry.args[0] for entry in call.call_args_list]
                self.assertIn(["commit", "-m", "Public recovery request", "--", support.REQUEST_PATH], commands)
                self.assertEqual(commands[-1], ["push", "origin", support.BRANCH])
                self.assertIn(["merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"], commands)
                self.assertNotIn("keystore", (root / support.REQUEST_PATH).read_text())
                self.assertFalse(any(command[0] in {"checkout", "switch"} for command in commands))
                api.assert_not_called()
        with self.assertRaises(support.SigningError):
            owner.commit_public_file("main.yml", {}, "not allowed")

    def test_public_git_commit_refuses_other_branch_dirty_tree_or_wrong_remote(self):
        cases = [[b"main"], [support.BRANCH.encode(), b" M unrelated-file"],
                 [support.BRANCH.encode(), b"", b"https://github.com/unrelated/repo.git"]]
        for outputs in cases:
            with self.subTest(outputs=outputs), patch.object(owner, "git_call", side_effect=outputs) as call:
                with self.assertRaises(support.SigningError):
                    owner.commit_public_file(support.REQUEST_PATH, request(), "Public request")
                self.assertFalse(any(entry.args[0][0] in {"add", "commit", "push"} for entry in call.call_args_list))

    def test_no_cache_delete_before_offline_copy_and_secret_only_ci_proof(self):
        with patch.object(owner, "api") as call:
            with self.assertRaises(support.SigningError):
                owner.purge_cache(Path("unused"), offline_copy_confirmed=False)
            call.assert_not_called()
        with patch.object(owner, "load_state", return_value={"activation_commit": COMMIT}), \
                patch.object(owner, "recovered_keystore", return_value=FAKE_JKS), \
                patch.object(owner, "verify_activation", side_effect=support.SigningError("not verified")), \
                patch.object(owner, "api") as call:
            with self.assertRaises(support.SigningError):
                owner.purge_cache(Path("unused"), offline_copy_confirmed=True)
            call.assert_not_called()

    def test_cache_delete_is_exact_key_and_branch_and_uses_fetched_ids(self):
        responses = [
            {"object": {"sha": COMMIT}},
            {"actions_caches": [
                {"id": 11, "key": support.CACHE_KEY, "ref": "refs/heads/" + support.BRANCH},
                {"id": 12, "key": "android-gradle", "ref": "refs/heads/" + support.BRANCH},
                {"id": 13, "key": support.CACHE_KEY, "ref": "refs/heads/main"},
            ]}, None,
        ]
        with patch.object(owner, "load_state", return_value={"activation_commit": COMMIT}), \
                patch.object(owner, "recovered_keystore", return_value=FAKE_JKS), \
                patch.object(owner, "verify_activation"), \
                patch.object(owner, "api", side_effect=responses) as call, redirect_stdout(io.StringIO()):
            owner.purge_cache(Path("unused"), offline_copy_confirmed=True)
        deletes = [entry for entry in call.call_args_list if entry.kwargs.get("method") == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertTrue(deletes[0].args[0].endswith("/actions/caches/11"))

    def test_bad_backup_never_reaches_secret_install_or_policy_activation(self):
        with patch.object(owner, "load_state", return_value={}), patch.object(owner, "collect_backup"), \
                patch.object(owner, "recovered_keystore", side_effect=support.SigningError("wrong key")), \
                patch.object(owner, "install_secret") as install, patch.object(owner, "activate") as activate:
            with self.assertRaises(support.SigningError):
                owner.finish(Path("unused"))
            install.assert_not_called()
            activate.assert_not_called()

    def test_owner_private_directory_cannot_be_inside_a_git_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git").mkdir()
            with self.assertRaises(support.SigningError):
                owner.private_directory(root / "private")

    def test_signing_file_writes_are_exclusive_and_posix_private(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "key.jks"
            support.private_write(file, FAKE_JKS)
            if os.name != "nt":
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                support.private_write(file, b"must not overwrite")
            self.assertEqual(file.read_bytes(), FAKE_JKS)

    def test_collect_backup_checks_raw_and_zip_transport_against_both_digests(self):
        cipher = b"age-encryption.org/v1\nsynthetic ciphertext only"
        name = "iptv-signing-" + request()["request_id"] + ".age"
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr(name, cipher)
        zipped = archive_bytes.getvalue()
        run = {"id": 44, "status": "completed", "conclusion": "success"}
        report = dict(format="encrypted-signing-backup-report-v1", commit=COMMIT, run_id="44",
                      request_id=request()["request_id"], artifact=name, certificate_sha256=support.EXPECTED_SIGNER_SHA256,
                      ciphertext_sha256=hashlib.sha256(cipher).hexdigest(), ciphertext_bytes=len(cipher))
        for raw, digest_data in ((cipher, cipher), (zipped, zipped), (zipped, cipher)):
            with self.subTest(transport=len(raw), digest=len(digest_data)), tempfile.TemporaryDirectory() as temp:
                state = dict(format=1, repository=support.REPOSITORY, branch=support.BRANCH,
                             request=request(), request_commit=COMMIT)
                artifact = dict(id=55, name=name, expired=False, size_in_bytes=len(raw),
                                digest="sha256:" + hashlib.sha256(digest_data).hexdigest())
                with patch.object(owner, "find_run", return_value=run), patch.object(owner, "wait_run", return_value=run), \
                        patch.object(owner, "check_report", return_value=report), \
                        patch.object(owner, "api", return_value={"artifacts": [artifact]}), \
                        patch.object(owner, "gh_call", return_value=raw):
                    owner.collect_backup(Path(temp), state)
                self.assertEqual((Path(temp) / name).read_bytes(), cipher)
                self.assertEqual(state["backup_sha256"], hashlib.sha256(cipher).hexdigest())

    def test_secret_activation_requires_both_matching_real_upgrade_runtime_reports(self):
        info = dict(commit=COMMIT, run_id="44", application_id=support.APPLICATION_ID, signing_source="actions-secret",
                    signer_sha256=support.EXPECTED_SIGNER_SHA256, signing_key_path_verified=True,
                    private_signing_material_written_to_cache=False, legacy_signing_cache_restored=False, sha256="d" * 64)
        def reports():
            return {api: dict(status=0, signing_source="actions-secret", commit=COMMIT, run_id="45", api=str(api),
                              expected_api=str(api), verification_errors=[], failures=[],
                              tests=dict(tests=8, failures=0, errors=0, skipped=0),
                              upgrade=dict(status="success", same_as_delivered_0_1_2_signer=True,
                                           room_schema_unchanged=True, seed_test_passed=True, install_r_succeeded=True,
                                           preservation_test_passed=True,
                                           current=dict(apk_sha256=info["sha256"], signer_sha256=support.EXPECTED_SIGNER_SHA256),
                                           baseline=dict(signer_sha256=support.EXPECTED_SIGNER_SHA256))) for api in (31, 33)}
        owner.validate_activation_reports(info, reports(), COMMIT, "44", "45")
        for key, value in (("signing_source", "legacy-cache-transitional"), ("signer_sha256", "0" * 64),
                           ("signing_key_path_verified", False), ("legacy_signing_cache_restored", True),
                           ("private_signing_material_written_to_cache", True), ("run_id", "old-run")):
            with self.subTest(key=key), self.assertRaises(support.SigningError):
                owner.validate_activation_reports(dict(info, **{key: value}), reports(), COMMIT, "44", "45")
        for api in (31, 33):
            for field, value in (("status", 1), ("signing_source", "legacy-cache-transitional"),
                                 ("run_id", "other-run"), ("failures", ["failure"]), ("upgrade", {})):
                invalid = reports()
                invalid[api][field] = value
                with self.subTest(api=api, field=field), self.assertRaises(support.SigningError):
                    owner.validate_activation_reports(info, invalid, COMMIT, "44", "45")
            invalid = reports()
            invalid[api]["upgrade"]["preservation_test_passed"] = False
            with self.assertRaises(support.SigningError):
                owner.validate_activation_reports(info, invalid, COMMIT, "44", "45")
            invalid = reports()
            invalid[api]["tests"]["skipped"] = 1
            with self.assertRaises(support.SigningError):
                owner.validate_activation_reports(info, invalid, COMMIT, "44", "45")

    def test_workflow_contract_never_saves_signing_keys_and_always_cleans_up(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("android.yml", "android-smoke.yml", "android-signing-backup.yml"):
            text = (root / ".github/workflows" / name).read_text()
            self.assertIn("secrets.IPTV_SIGNING_KEYSTORE_B64", text)
            self.assertIn("steps.signing-source.outputs.use_legacy_cache == 'true'", text)
            self.assertIn("if: always()\n        run: python3", text)
            self.assertIn("prepare_trial_signing.py --cleanup", text)
            for chunk in text.split("      - "):
                if "actions/cache/save@" in chunk or "actions/upload-artifact@" in chunk:
                    self.assertNotIn("debug.keystore", chunk)
                    self.assertNotIn("iptv-private-signing", chunk)
        backup = (root / ".github/workflows/android-signing-backup.yml").read_text()
        self.assertNotIn("pull_request", backup)
        self.assertIn("fetch-depth: 2", backup)
        self.assertIn("needs: tooling-tests", backup)
        self.assertIn("archive: false", backup)
        gradle = (root / "android/app/build.gradle.kts").read_text()
        self.assertIn('environmentVariable("GITHUB_ACTIONS")', gradle)
        self.assertIn("require(!trialKeystore.isNullOrBlank())", gradle)

    def test_external_tool_failure_does_not_include_stderr_or_secret_input(self):
        result = type("Result", (), {"returncode": 1, "stdout": b"", "stderr": b"PRIVATE_SHOULD_NOT_BE_LOGGED"})()
        with patch.object(support.subprocess, "run", return_value=result):
            with self.assertRaises(support.SigningError) as error:
                support.checked_tool(["mock-tool"], input_data=b"PRIVATE_SHOULD_NOT_BE_LOGGED")
        self.assertNotIn("PRIVATE_SHOULD_NOT_BE_LOGGED", str(error.exception))


if __name__ == "__main__":
    unittest.main()
