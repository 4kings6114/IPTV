"""Load the retained trial identity from a Secret, or explicitly transitional cache.

Never generate a replacement key. The working copy lives in RUNNER_TEMP and is
removed after use. The transitional restored local cache copy is removed after
validation; the REMOTE cache is untouched. No source bytes/decoded values are logged.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys

if __package__:
    from .signing_support import SigningError, decode_keystore, private_write, read_policy, validate_key
else:
    from signing_support import SigningError, decode_keystore, private_write, read_policy, validate_key


def signing_source(policy: dict, secret: str) -> str:
    if secret:
        return "actions-secret"
    if policy["required_source"] == "actions-secret":
        raise SigningError("The signing Secret is required but unavailable. Restore the owner's backup; no cache fallback or new key is allowed.")
    return "legacy-cache-transitional"


def append_environment(values: dict[str, str]) -> None:
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise SigningError("Invalid signing environment value.")
            output.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args(argv)
    private_dir = Path(os.environ["RUNNER_TEMP"]) / "iptv-private-signing"
    if args.cleanup:
        if private_dir.is_symlink():
            raise SigningError("Refusing unsafe signing cleanup path.")
        if private_dir.exists():
            shutil.rmtree(private_dir)
        return 0
    root = Path(__file__).resolve().parents[2]
    policy = read_policy(root)
    # The secret belongs only to this preparation process, not JDK subprocesses.
    secret = os.environ.pop("IPTV_SIGNING_KEYSTORE_B64", "")
    source = signing_source(policy, secret)
    if args.inspect:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"use_legacy_cache={'true' if source == 'legacy-cache-transitional' else 'false'}\n")
        print("Signing source:", source, "; no secret value was printed.")
        return 0
    if private_dir.exists():
        raise SigningError("Private signing directory already exists; refusing to overwrite a key.")
    private_dir.mkdir(mode=0o700)
    key = private_dir / "trial.jks"
    legacy = None
    try:
        if source == "actions-secret":
            data = decode_keystore(secret)
        else:
            legacy = Path.home() / ".android/debug.keystore"
            validate_key(legacy)
            data = legacy.read_bytes()
        private_write(key, data)
        digest = validate_key(key, prove_private_key=True)
        if legacy is not None:
            legacy.unlink()  # runner-local restored copy ONLY, never the remote cache
        append_environment({
            "IPTV_TRIAL_KEYSTORE": str(key), "IPTV_SIGNING_SOURCE": source,
            "IPTV_SIGNING_CERTIFICATE_SHA256": digest,
        })
        print("Retained signing certificate verified:", digest)
        print("Signing source:", source)
        if source != "actions-secret":
            print("::warning::Owner migration is pending. The old remote cache has NOT been deleted.")
        return 0
    except Exception:
        shutil.rmtree(private_dir)
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SigningError, OSError) as error:
        message = str(error) if isinstance(error, SigningError) else "Signing filesystem operation failed."
        print("::error::" + message, file=sys.stderr)
        sys.exit(1)
