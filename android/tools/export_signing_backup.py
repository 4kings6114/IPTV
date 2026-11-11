"""One owner-requested, age-encrypted backup. No plaintext key artifact or log."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys

if __package__:
    from .signing_support import (APPLICATION_ID, BRANCH, EXPECTED_SIGNER_SHA256, MAX_BACKUP_BYTES,
                                  REPOSITORY, REQUEST_PATH, SigningError, check_request,
                                  checked_tool, private_write, validate_key)
else:
    from signing_support import (APPLICATION_ID, BRANCH, EXPECTED_SIGNER_SHA256, MAX_BACKUP_BYTES,
                                 REPOSITORY, REQUEST_PATH, SigningError, check_request,
                                 checked_tool, private_write, validate_key)


def encrypt_payload(payload: dict, recipient: str) -> bytes:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encrypted = checked_tool(["age", "--encrypt", "--recipient", recipient], input_data=data)
    if not encrypted.startswith(b"age-encryption.org/v1\n") or len(encrypted) > MAX_BACKUP_BYTES:
        raise SigningError("Age did not produce a bounded encrypted backup.")
    return encrypted


def fresh_owner_request(root: Path, commit: str) -> bool:
    # Actions deliberately omits commit file lists from its push payload. Read
    # the actual commit diff instead; checkout must include its parent (depth 2).
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SigningError("Invalid backup source commit.")
    head = checked_tool(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
    parents = checked_tool(["git", "-C", str(root), "rev-list", "--parents", "-n", "1", commit]).decode().split()
    if head != commit or len(parents) != 2:
        raise SigningError("Owner backup needs its exact non-merge request commit and parent; use checkout depth 2.")
    changed = checked_tool(["git", "-C", str(root), "diff-tree", "--no-commit-id", "--name-only",
                            "--diff-filter=AM", "-r", commit, "--", REQUEST_PATH]).decode().splitlines()
    return REQUEST_PATH in changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or os.environ.get("GITHUB_REF") != "refs/heads/" + BRANCH:
        raise SigningError("Owner backup is restricted to the approved repository and working branch.")
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        raise SigningError("Owner backup requires a public request commit, not an untrusted PR or dispatch.")
    root = Path(__file__).resolve().parents[2]
    path = root / REQUEST_PATH
    fresh = fresh_owner_request(root, os.environ.get("GITHUB_SHA", ""))
    if (not path.exists() or not fresh) and args.inspect:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("ready=false\n")
        print("No new owner recovery request in this push; no signing key was restored or exported.")
        return 0
    if not fresh:
        raise SigningError("This push did not create/update an owner request; refusing backup replay.")
    try:
        request = check_request(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError, TypeError):
        raise SigningError("Cannot read the public owner request.") from None
    if args.inspect:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("ready=true\n")
        return 0
    commit = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SigningError("Invalid backup source commit.")
    key = Path(os.environ["IPTV_TRIAL_KEYSTORE"])
    validate_key(key, prove_private_key=True)
    payload = {
        "format": "iptv-signing-backup-v1", "repository": REPOSITORY, "branch": BRANCH,
        "application_id": APPLICATION_ID, "certificate_sha256": EXPECTED_SIGNER_SHA256,
        "request_id": request["request_id"], "recipient": request["recipient"], "commit": commit,
        "keystore_base64": base64.b64encode(key.read_bytes()).decode("ascii"),
    }
    encrypted = encrypt_payload(payload, request["recipient"])
    folder = Path(os.environ["RUNNER_TEMP"]) / "iptv-encrypted-backup"
    folder.mkdir(mode=0o700)
    name = f"iptv-signing-{request['request_id']}.age"
    destination = folder / name
    private_write(destination, encrypted)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"artifact_path={destination}\nartifact_name={name}\n")
    report = {
        "format": "encrypted-signing-backup-report-v1", "commit": commit,
        "run_id": os.environ["GITHUB_RUN_ID"],
        "request_id": request["request_id"], "certificate_sha256": EXPECTED_SIGNER_SHA256,
        "source": os.environ["IPTV_SIGNING_SOURCE"], "ciphertext_sha256": hashlib.sha256(encrypted).hexdigest(),
        "ciphertext_bytes": len(encrypted), "artifact": name,
        "owner_custody": "Only the owner has the passphrase-protected age identity. No plaintext signing key is uploaded.",
    }
    print(json.dumps(report, indent=2))
    if __package__:
        from .ci_report import publish
    else:
        from ci_report import publish
    publish("Android signing encrypted backup", 0, json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SigningError, OSError) as error:
        message = str(error) if isinstance(error, SigningError) else "Encrypted backup filesystem operation failed."
        print("::error::" + message, file=sys.stderr)
        sys.exit(1)
