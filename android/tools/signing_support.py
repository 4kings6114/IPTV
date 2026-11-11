"""Shared signing invariants. Never log keystore bytes, passwords or secret bodies."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

REPOSITORY = "4kings6114/IPTV"
BRANCH = "arena/df65ff7a-iptv"
APPLICATION_ID = "io.github.fourkings6114.iptv.trial"
EXPECTED_SIGNER_SHA256 = "eba2f7bdb4cfea267f4c30cedba8b2956f9c86ed3d5363512291fd3d6bd29c48"
SECRET_NAME = "IPTV_SIGNING_KEYSTORE_B64"
CACHE_KEY = "android-trial-signing-v1"
POLICY_PATH = "android/signing/policy.json"
REQUEST_PATH = "android/signing/owner-request.json"
BACKUP_WORKFLOW = ".github/workflows/android-signing-backup.yml"
KEY_ALIAS = "androiddebugkey"
# The existing trial identity is retained, NOT a newly generated production key.
# Storage protection comes from Actions Secrets and an age-encrypted owner backup.
LEGACY_PASSWORD = "android"
MAX_KEYSTORE_BYTES = 32 * 1024
MAX_BACKUP_BYTES = 128 * 1024
AGE_VERSION = "v1.3.2"


class SigningError(RuntimeError):
    """Fixed, non-sensitive messages only; do not embed subprocess stderr or values."""


def decode_keystore(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 48 * 1024:
        raise SigningError("Signing secret is missing or exceeds its size limit.")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise SigningError("Signing secret is not valid base64.") from None
    if not 512 <= len(data) <= MAX_KEYSTORE_BYTES or not data.startswith(b"\xfe\xed\xfe\xed"):
        raise SigningError("Expected the original bounded JKS trial keystore; no replacement key is generated.")
    return data


def read_policy(root: Path) -> dict:
    try:
        policy = json.loads((root / POLICY_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SigningError("Signing policy is missing or invalid.") from None
    if (not isinstance(policy, dict) or policy.get("format") != 1 or policy.get("repository") != REPOSITORY
            or policy.get("branch") != BRANCH or policy.get("application_id") != APPLICATION_ID
            or policy.get("certificate_sha256") != EXPECTED_SIGNER_SHA256
            or policy.get("required_source") not in ("legacy-cache-during-owner-setup", "actions-secret")):
        raise SigningError("Signing policy does not match this repository, branch or installed identity.")
    return policy


def check_request(request: dict) -> dict:
    if (not isinstance(request, dict) or request.get("format") != 1 or request.get("repository") != REPOSITORY
            or request.get("branch") != BRANCH or request.get("certificate_sha256") != EXPECTED_SIGNER_SHA256
            or not re.fullmatch(r"[0-9a-f]{32}", str(request.get("request_id", "")))
            or not re.fullmatch(r"age1[023456789acdefghjklmnpqrstuvwxyz]{58}", str(request.get("recipient", "")))):
        raise SigningError("Owner recovery request is invalid or belongs to another signing identity.")
    return request


def private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)


def checked_tool(command: list[str], *, env: dict | None = None, input_data: bytes | None = None,
                 timeout: int = 60) -> bytes:
    try:
        result = subprocess.run(command, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SigningError("A required signing tool is unavailable or timed out.") from None
    if result.returncode:
        # Tool errors can contain caller input. Keep the process boundary fail-closed and value-free.
        raise SigningError("A signing tool rejected the key or operation; no secret values are logged.")
    return result.stdout


def certificate_digest(path: Path) -> str:
    env = dict(os.environ, IPTV_KEYTOOL_PASSWORD=LEGACY_PASSWORD)
    certificate = checked_tool([
        "keytool", "-exportcert", "-keystore", str(path), "-storetype", "JKS",
        "-storepass:env", "IPTV_KEYTOOL_PASSWORD", "-alias", KEY_ALIAS,
    ], env=env)
    return hashlib.sha256(certificate).hexdigest()


def validate_key(path: Path, expected: str = EXPECTED_SIGNER_SHA256, *, prove_private_key: bool = False) -> str:
    if path.is_symlink() or not path.is_file() or not 512 <= path.stat().st_size <= MAX_KEYSTORE_BYTES:
        raise SigningError("Signing key file is missing, unsafe or oversized.")
    digest = certificate_digest(path)
    if digest != expected:
        raise SigningError("Signing certificate differs from the installed trial; refusing an incompatible update.")
    if prove_private_key:
        # A public certificate alone is not a recoverable signing key. Sign and verify
        # a harmless challenge using the JDK, with this certificate as the trust anchor.
        with tempfile.TemporaryDirectory(prefix="key-proof-", dir=path.parent) as directory:
            work = Path(directory)
            with zipfile.ZipFile(work / "challenge.jar", "w") as archive:
                archive.writestr("challenge.txt", "IPTV signing recovery proof\n" + digest)
            env = dict(os.environ, IPTV_KEYTOOL_PASSWORD=LEGACY_PASSWORD)
            checked_tool([
                "jarsigner", "-keystore", str(path), "-storetype", "JKS",
                "-storepass:env", "IPTV_KEYTOOL_PASSWORD", "-keypass:env", "IPTV_KEYTOOL_PASSWORD",
                "-signedjar", str(work / "signed.jar"), str(work / "challenge.jar"), KEY_ALIAS,
            ], env=env)
            checked_tool([
                "jarsigner", "-verify", "-strict", "-keystore", str(path), "-storetype", "JKS",
                "-storepass:env", "IPTV_KEYTOOL_PASSWORD", str(work / "signed.jar"),
            ], env=env)
    return digest


def validate_backup(payload: dict, request: dict, commit: str) -> bytes:
    check_request(request)
    if (not isinstance(payload, dict) or payload.get("format") != "iptv-signing-backup-v1" or payload.get("repository") != REPOSITORY
            or payload.get("branch") != BRANCH or payload.get("application_id") != APPLICATION_ID
            or payload.get("certificate_sha256") != EXPECTED_SIGNER_SHA256
            or payload.get("request_id") != request["request_id"] or payload.get("recipient") != request["recipient"]
            or payload.get("commit") != commit or not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", commit)):
        raise SigningError("Backup context does not match the owner's request and installed certificate.")
    return decode_keystore(payload.get("keystore_base64"))
