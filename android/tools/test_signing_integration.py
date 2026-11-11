"""CI-only integration using a disposable SYNTHETIC key, never the live signer.

Requires Linux, JDK 17, official age and IPTV_SIGNING_INTEGRATION=1. No private
fixture, passphrase, decrypted JSON or subprocess output is logged/uploaded.
"""
from __future__ import annotations

import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import sys
import tempfile
import time
from unittest.mock import patch

import export_signing_backup as exporter
import prepare_trial_signing as prepare
import signing_owner as owner
import signing_support as support

# Disposable test input only. This is not a real recovery passphrase/example to reuse.
TEST_PASSPHRASE = b"synthetic-ci-identity-ONLY-do-not-reuse"
COMMIT = "a" * 40


def interactive_worker(arguments: list[str], passphrase: bytes) -> int:
    """Give age a real controlling terminal; exercise its LOCAL prompt semantics."""
    pid, descriptor = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", *arguments])
    pending = b""
    prompts = 0
    deadline = time.monotonic() + 120
    result = None
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([descriptor], [], [], 0.2)
            if readable:
                try:
                    chunk = os.read(descriptor, 4096)
                except OSError:
                    chunk = b""
                pending = (pending + chunk)[-16000:]
                if re.search(rb"(?:enter|confirm) passphrase[^\r\n]*:", pending, re.IGNORECASE):
                    prompts += 1
                    if prompts > 3:
                        raise AssertionError("Synthetic age prompt unexpectedly repeated.")
                    os.write(descriptor, passphrase + b"\n")
                    pending = b""
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                result = status
                break
        if result is None:
            raise AssertionError("Synthetic age prompt test timed out.")
        if os.waitstatus_to_exitcode(result) != 0:
            # Deliberately do not dump worker output: decryption was exercised.
            raise AssertionError("Synthetic local-terminal worker failed: " + arguments[0])
        return prompts
    finally:
        os.close(descriptor)
        if result is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)


def validate_fixture_key(path: Path, expected: str, **kwargs) -> str:
    return support.validate_key(path, expected=expected, **kwargs)


def worker(arguments: list[str]) -> None:
    command, directory = arguments[:2]
    directory = Path(directory)
    if command == "init":
        owner.init_owner(directory)
        return
    expected = arguments[2]
    with patch.object(owner, "validate_key", side_effect=lambda path, **kw: validate_fixture_key(path, expected, **kw)):
        if command == "recover":
            data = owner.recovered_keystore(directory, owner.load_state(directory))
            assert hashlib.sha256(data).hexdigest() == arguments[3]
        elif command == "reject":
            try:
                owner.recovered_keystore(directory, owner.load_state(directory))
            except support.SigningError:
                return
            raise AssertionError("Invalid synthetic backup was accepted.")
        else:
            raise AssertionError("Unknown synthetic worker operation.")


def set_backup(directory: Path, encrypted: bytes, state: dict) -> None:
    name = "iptv-signing-" + state["request"]["request_id"] + ".age"
    (directory / name).write_bytes(encrypted)
    state.update(backup_file=name, backup_sha256=hashlib.sha256(encrypted).hexdigest())
    owner.save_state(directory, state)


def main() -> int:
    if os.environ.get("IPTV_SIGNING_INTEGRATION") != "1":
        raise RuntimeError("This synthetic integration test requires explicit CI opt-in.")
    if sys.argv[1:2] == ["--worker"]:
        worker(sys.argv[2:])
        return 0
    with tempfile.TemporaryDirectory(prefix="iptv-synthetic-signing-", dir=os.environ["RUNNER_TEMP"]) as temp:
        root = Path(temp)
        print("Synthetic integration: JKS signing proof and certificate-only rejection", flush=True)
        key = root / "synthetic.jks"
        env = dict(os.environ, IPTV_KEYTOOL_PASSWORD=support.LEGACY_PASSWORD)
        support.checked_tool([
            "keytool", "-genkeypair", "-noprompt", "-storetype", "JKS", "-keystore", str(key),
            "-storepass:env", "IPTV_KEYTOOL_PASSWORD", "-keypass:env", "IPTV_KEYTOOL_PASSWORD",
            "-alias", support.KEY_ALIAS, "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650",
            "-dname", "CN=IPTV Synthetic CI Only,O=NOT the delivered signer,C=ZZ",
        ], env=env)
        key.chmod(0o600)
        data = key.read_bytes()
        digest = support.certificate_digest(key)
        assert digest != support.EXPECTED_SIGNER_SHA256
        support.validate_key(key, expected=digest, prove_private_key=True)
        try:
            support.validate_key(key)  # Fixed live certificate gate MUST reject our fixture.
        except support.SigningError:
            pass
        else:
            raise AssertionError("Synthetic fixture bypassed the installed-certificate gate.")
        certificate = root / "synthetic.cer"
        certificate.write_bytes(support.checked_tool([
            "keytool", "-exportcert", "-keystore", str(key), "-storetype", "JKS",
            "-storepass:env", "IPTV_KEYTOOL_PASSWORD", "-alias", support.KEY_ALIAS,
        ], env=env))
        public_only = root / "public-only.jks"
        support.checked_tool([
            "keytool", "-importcert", "-noprompt", "-keystore", str(public_only), "-storetype", "JKS",
            "-storepass:env", "IPTV_KEYTOOL_PASSWORD", "-alias", support.KEY_ALIAS, "-file", str(certificate),
        ], env=env)
        support.validate_key(public_only, expected=digest)
        try:
            support.validate_key(public_only, expected=digest, prove_private_key=True)
        except support.SigningError:
            pass
        else:
            raise AssertionError("A public certificate was mistaken for a recoverable private key.")

        print("Synthetic integration: real local-terminal encrypted identity and age roundtrip", flush=True)
        directory = root / "owner"
        directory.mkdir(mode=0o700)
        assert interactive_worker(["init", str(directory)], TEST_PASSPHRASE) == 2
        state = owner.load_state(directory)
        assert (directory / owner.IDENTITY_FILE).read_bytes().startswith(b"age-encryption.org/v1\n")
        request = owner.public_request(state["recipient"])
        payload = dict(format="iptv-signing-backup-v1", repository=support.REPOSITORY, branch=support.BRANCH,
                       application_id=support.APPLICATION_ID, certificate_sha256=support.EXPECTED_SIGNER_SHA256,
                       request_id=request["request_id"], recipient=request["recipient"], commit=COMMIT,
                       keystore_base64=base64.b64encode(data).decode())
        encrypted = exporter.encrypt_payload(payload, request["recipient"])
        assert data not in encrypted and b"keystore_base64" not in encrypted
        state.update(request=request, request_commit=COMMIT)
        set_backup(directory, encrypted, state)
        assert interactive_worker(["recover", str(directory), digest, hashlib.sha256(data).hexdigest()], TEST_PASSPHRASE) >= 1
        assert interactive_worker(["reject", str(directory), digest], b"deliberately-wrong-synthetic-passphrase") >= 1
        damaged = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
        set_backup(directory, damaged, state)
        interactive_worker(["reject", str(directory), digest], TEST_PASSPHRASE)
        other_identity = support.checked_tool(["age-keygen"])
        other_recipient = support.checked_tool(["age-keygen", "-y"], input_data=other_identity).decode().strip()
        set_backup(directory, exporter.encrypt_payload(payload, other_recipient), state)
        interactive_worker(["reject", str(directory), digest], TEST_PASSPHRASE)
        set_backup(directory, encrypted, state)
        assert not list(directory.glob("private-verify-*")), "Temporary recovered keystore was not removed"

        # Exercise Secret-first preparation/cleanup with a fixture-specific
        # certificate validator. The production fixed certificate gate was tested above.
        print("Synthetic integration: Secret-first preparation, fail-closed missing Secret and cleanup", flush=True)
        home = root / "home"
        runner = root / "runner"
        runner.mkdir()
        github_env = root / "github-env"
        test_env = dict(HOME=str(home), RUNNER_TEMP=str(runner), GITHUB_ENV=str(github_env),
                        IPTV_SIGNING_KEYSTORE_B64=base64.b64encode(data).decode())
        policy = dict(required_source="actions-secret")
        with patch.dict(os.environ, test_env), patch.object(prepare, "read_policy", return_value=policy), \
                patch.object(prepare, "validate_key", side_effect=lambda path, **kw: validate_fixture_key(path, digest, **kw)), \
                redirect_stdout(io.StringIO()):
            assert prepare.main([]) == 0
            assert (runner / "iptv-private-signing/trial.jks").read_bytes() == data
            assert "IPTV_SIGNING_SOURCE=actions-secret\n" in github_env.read_text()
            assert not (home / ".android/debug.keystore").exists()
            assert prepare.main(["--cleanup"]) == 0
            assert not (runner / "iptv-private-signing").exists()
            os.environ["IPTV_SIGNING_KEYSTORE_B64"] = ""
            try:
                prepare.main([])
            except support.SigningError:
                pass
            else:
                raise AssertionError("Secret-only preparation accepted a missing Secret.")
            assert not (runner / "iptv-private-signing").exists()
            legacy = home / ".android/debug.keystore"
            support.private_write(legacy, data)
            policy["required_source"] = "legacy-cache-during-owner-setup"
            assert prepare.main([]) == 0
            assert not legacy.exists(), "The runner-local restored copy was not cleaned"
            assert (runner / "iptv-private-signing/trial.jks").read_bytes() == data
            prepare.main(["--cleanup"])
        summary = {"synthetic_only": True, "status": "success", "live_signer_accessed": False,
                   "verified": ["real JKS private-key sign/verify proof", "wrong certificate rejection",
                                "public-certificate-only rejection", "local-terminal passphrase-protected identity",
                                "age recovery roundtrip", "wrong passphrase/recipient and damaged ciphertext rejection",
                                "Secret-first and transitional preparation", "no generation on missing Secret", "temporary-key cleanup"]}
        print(json.dumps(summary, indent=2))
        from ci_report import publish
        publish("Android synthetic signing recovery", 0, json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Test failures must not spill subprocess captures or decrypted fixture data.
        print("::error::Synthetic signing integration failed; no live key was used.", file=sys.stderr)
        sys.exit(1)
