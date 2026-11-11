"""Owner-operated signing recovery. Python stdlib + gh + age + JDK 17.

Run on the owner's computer, NOT in chat or a shared CI shell. Only a public
recipient/request and a public signing-policy change are committed, always to
this session's branch. Key bytes go to gh secret set over stdin, never argv.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
import uuid
import zipfile

if __package__:
    from .signing_support import (AGE_VERSION, APPLICATION_ID, BACKUP_WORKFLOW, BRANCH, CACHE_KEY,
                                  EXPECTED_SIGNER_SHA256, MAX_BACKUP_BYTES, POLICY_PATH, REPOSITORY,
                                  REQUEST_PATH, SECRET_NAME, SigningError, check_request, checked_tool,
                                  private_write, validate_backup, validate_key)
else:
    from signing_support import (AGE_VERSION, APPLICATION_ID, BACKUP_WORKFLOW, BRANCH, CACHE_KEY,
                                 EXPECTED_SIGNER_SHA256, MAX_BACKUP_BYTES, POLICY_PATH, REPOSITORY,
                                 REQUEST_PATH, SECRET_NAME, SigningError, check_request, checked_tool,
                                 private_write, validate_backup, validate_key)

GH_REPO = "github.com/" + REPOSITORY
API_ROOT = "repos/" + REPOSITORY
STATE_FILE = "owner-state.json"
IDENTITY_FILE = "owner-identity.age"


class GitHubFailure(SigningError):
    def __init__(self, status: int = 0):
        super().__init__(f"GitHub operation failed (HTTP {status or 'unknown'}). Check the local gh login and repository permissions; never paste a token into chat.")
        self.status = status


def github_environment() -> dict:
    env = dict(os.environ)
    env.pop("GH_DEBUG", None)
    env["GH_PROMPT_DISABLED"] = "1"
    return env


def gh_call(arguments: list[str], data: bytes | None = None) -> bytes:
    env = github_environment()
    try:
        result = subprocess.run(["gh", *arguments], input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise SigningError("GitHub CLI is unavailable or timed out.") from None
    if result.returncode:
        match = re.search(rb"HTTP (\d{3})", result.stderr + result.stdout)
        raise GitHubFailure(int(match.group(1)) if match else 0)
    return result.stdout


def api(endpoint: str, *, method: str = "GET", body: dict | None = None):
    args = ["api", "--hostname", "github.com", "--method", method, endpoint]
    encoded = None
    if body is not None:
        args += ["--input", "-"]
        encoded = json.dumps(body).encode("utf-8")
    output = gh_call(args, encoded)
    return json.loads(output) if output.strip() else None


def powershell_environment(**values: str) -> dict:
    # A parent PowerShell 7 session exports its own module search path. Passing
    # it to Windows PowerShell 5.1 can break Microsoft.PowerShell.Security
    # autoload (Set-Acl/Get-Acl). Let powershell.exe construct its native path.
    env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    env.update(values)
    return env


def private_directory(path: Path) -> Path:
    path = path.expanduser()
    if path.is_symlink():
        raise SigningError("The owner directory must not be a symlink.")
    path = path.resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise SigningError("Keep the owner directory outside every Git checkout.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        # Restrict the NTFS DACL without Set-Acl: Windows PowerShell 5.1 may
        # request SeSecurityPrivilege for a new DirectorySecurity object's SACL,
        # which ordinary owner accounts do not have. icacls edits only the DACL.
        # SID syntax is language-independent; no localized account names or shell
        # interpolation are used. Set the directory owner to the current user,
        # reset inherited/explicit ACEs, disable inheritance, and grant only the
        # owner and SYSTEM before writing any private material.
        env = powershell_environment()
        sid = checked_tool(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value"],
                           env=env).decode("ascii").strip()
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise SigningError("Cannot determine the Windows owner SID safely.")
        checked_tool(["icacls.exe", str(path), "/setowner", f"*{sid}"])
        checked_tool(["icacls.exe", str(path), "/reset"])
        checked_tool(["icacls.exe", str(path), "/inheritance:r"])
        checked_tool(["icacls.exe", str(path), "/grant:r", f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"])
        # Verify the resulting owner and exact protected DACL through read-only
        # Get-Acl; this inspection does not request the auditing SACL privilege.
        script = """
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:IPTV_OWNER_DIRECTORY
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid) { [Console]::Error.WriteLine('ACL owner mismatch'); exit 1 }
if (-not $acl.AreAccessRulesProtected) { [Console]::Error.WriteLine('ACL inheritance is enabled'); exit 2 }
$rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
if ($rules.Count -ne 2) { [Console]::Error.WriteLine('Unexpected ACL entry count'); exit 3 }
foreach ($rule in $rules) {
    if ($rule.IdentityReference.Value -notin @($sid, 'S-1-5-18') -or $rule.AccessControlType -ne 'Allow' -or $rule.IsInherited) { [Console]::Error.WriteLine('Unexpected ACL entry'); exit 4 }
}
"""
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        env = powershell_environment(IPTV_OWNER_DIRECTORY=str(path))
        checked_tool(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=env)
    else:
        path.chmod(0o700)
    return path


def load_state(directory: Path) -> dict:
    try:
        path = directory / STATE_FILE
        if path.is_symlink() or path.stat().st_size > 16 * 1024:
            raise OSError()
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["repository"] != REPOSITORY or state["branch"] != BRANCH or state["format"] != 1:
            raise ValueError()
        return state
    except (OSError, ValueError, KeyError, TypeError):
        raise SigningError("Owner state is missing/invalid. Run init, or restore the complete encrypted offline backup folder.") from None


def save_state(directory: Path, state: dict) -> None:
    temp = directory / ("state-" + uuid.uuid4().hex + ".tmp")
    private_write(temp, (json.dumps(state, indent=2) + "\n").encode("utf-8"))
    os.replace(temp, directory / STATE_FILE)


def doctor() -> None:
    missing = [name for name in ("git", "gh", "age", "age-keygen", "keytool", "jarsigner") if not shutil.which(name)]
    if missing:
        raise SigningError("Install the official prerequisites and reopen the terminal: " + ", ".join(missing))
    version = checked_tool(["age", "--version"]).decode().strip().removeprefix("v")
    parts = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not parts or tuple(map(int, parts.groups())) < (1, 3, 2):
        raise SigningError("Use official age " + AGE_VERSION + " or newer.")
    # This returns a PUBLIC encryption key, never existing secret values.
    api(API_ROOT + "/actions/secrets/public-key")
    print("Tools and local GitHub Actions Secrets access are ready. No secret values were read.")


def init_owner(directory: Path) -> None:
    if (directory / IDENTITY_FILE).exists() or (directory / STATE_FILE).exists():
        raise SigningError("Owner recovery identity already exists; it will never be overwritten automatically.")
    identity = checked_tool(["age-keygen"])
    recipient = checked_tool(["age-keygen", "-y"], input_data=identity).decode().strip()
    check_request(public_request(recipient))
    temp = directory / ("identity-" + uuid.uuid4().hex + ".age")
    print("Choose a strong recovery passphrase in age's LOCAL terminal prompt. Store it separately in your password manager.")
    try:
        # age reads the passphrase from the controlling terminal, not stdin/argv/env.
        result = subprocess.run(["age", "--passphrase", "--output", str(temp)], input=identity,
                                stdout=subprocess.DEVNULL, timeout=300)
        if result.returncode or not temp.read_bytes().startswith(b"age-encryption.org/v1\n"):
            raise SigningError("The encrypted recovery identity was not created.")
        temp.chmod(0o600)
        os.replace(temp, directory / IDENTITY_FILE)
        save_state(directory, {"format": 1, "repository": REPOSITORY, "branch": BRANCH, "recipient": recipient})
    finally:
        if temp.exists():
            temp.unlink()
    print("Encrypted owner identity created. Only its public recipient will leave this computer.")


def public_request(recipient: str) -> dict:
    return {"format": 1, "repository": REPOSITORY, "branch": BRANCH,
            "certificate_sha256": EXPECTED_SIGNER_SHA256, "request_id": uuid.uuid4().hex,
            "recipient": recipient}


def git_call(arguments: list[str]) -> bytes:
    root = Path(__file__).resolve().parents[2]
    return checked_tool(["git", "-C", str(root), *arguments], timeout=120)


def commit_public_file(path: str, value: dict, message: str) -> str:
    if path not in (REQUEST_PATH, POLICY_PATH):
        raise SigningError("Owner tool is restricted to the public request/policy files.")
    if git_call(["branch", "--show-current"]).decode().strip() != BRANCH:
        raise SigningError("Use a checkout of the approved working branch; this tool never switches or creates branches.")
    if git_call(["status", "--porcelain"]).strip():
        raise SigningError("The owner checkout must be clean before a public request/policy commit. Keep private backups outside Git.")
    permitted = {"https://github.com/" + REPOSITORY, "https://github.com/" + REPOSITORY + ".git",
                 "git@github.com:" + REPOSITORY, "git@github.com:" + REPOSITORY + ".git"}
    for args in (["remote", "get-url", "origin"], ["remote", "get-url", "--push", "origin"]):
        if git_call(args).decode().strip() not in permitted:
            raise SigningError("The owner checkout origin/push URL must identify the approved GitHub repository.")
    # Fast-forward this branch only; never force-push, switch branches or merge main.
    git_call(["fetch", "--no-tags", "origin", BRANCH])
    git_call(["merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"])
    git_call(["merge", "--ff-only", "FETCH_HEAD"])
    destination = Path(__file__).resolve().parents[2] / path
    if destination.is_symlink():
        raise SigningError("The public request/policy file must not be a symlink.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    git_call(["add", "--", path])
    if git_call(["diff", "--cached", "--name-only"]).decode().splitlines() != [path]:
        raise SigningError("Refusing a commit containing anything except the intended public request/policy file.")
    git_call(["commit", "-m", message, "--", path])
    commit = git_call(["rev-parse", "HEAD"]).decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SigningError("Git did not return a valid public request/policy commit.")
    git_call(["push", "origin", BRANCH])
    return commit


def request_backup(directory: Path, *, new_request: bool = False) -> None:
    state = load_state(directory)
    if state.get("request_commit") and not new_request:
        print("An owner request already exists. Run finish to resume it; use --new-request only to replace an expired request.")
        return
    request = check_request(public_request(state["recipient"]))
    commit = commit_public_file(REQUEST_PATH, request, "Request an owner-encrypted signing backup")
    state.update(request=request, request_commit=commit)
    state.pop("activation_commit", None)
    state.pop("verified_ci", None)
    state.pop("backup_file", None)
    save_state(directory, state)
    print("Public recovery request committed ONLY to", BRANCH)
    print("Run finish after the backup job starts. No private recovery key was sent to CI.")


def find_run(commit: str, workflow: str, timeout: int = 180) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        result = api(API_ROOT + "/actions/runs?head_sha=" + commit + "&per_page=100")
        candidates = [run for run in result["workflow_runs"]
                      if run.get("path") == workflow and run.get("head_sha") == commit
                      and run.get("head_branch") == BRANCH and run.get("event") == "push"]
        if candidates:
            return max(candidates, key=lambda run: (run.get("run_attempt", 1), run["id"]))
        if time.monotonic() >= deadline:
            raise SigningError("The expected branch workflow has not started. Keep the cache; check Actions and rerun finish/status later.")
        time.sleep(5)


def wait_run(run: dict) -> dict:
    if run["status"] != "completed":
        result = subprocess.run(["gh", "run", "watch", str(run["id"]), "--repo", GH_REPO,
                                 "--interval", "20", "--exit-status"], env=github_environment(), timeout=45 * 60)
        if result.returncode:
            raise SigningError("The required workflow failed; keep the old cache and inspect its public CI report.")
        run = api(API_ROOT + "/actions/runs/" + str(run["id"]))
    if run["conclusion"] != "success":
        raise SigningError("The required workflow did not succeed. The old cache must not be deleted.")
    return run


def check_report(commit: str, name: str) -> dict:
    result = api(API_ROOT + "/commits/" + commit + "/check-runs?per_page=100")
    matches = [check for check in result["check_runs"]
               if check["name"] == name and check["conclusion"] == "success" and check["head_sha"] == commit
               and check.get("app", {}).get("slug") == "github-actions"]
    if not matches:
        raise SigningError("The required successful public signing verification report is missing.")
    raw = max(matches, key=lambda check: check["id"])["output"]["summary"]
    try:
        report = json.JSONDecoder().raw_decode(raw.removeprefix("```text\n"))[0]
        if not isinstance(report, dict):
            raise ValueError()
        return report
    except (ValueError, TypeError):
        raise SigningError("The public signing verification report is invalid.") from None


def extract_age_bytes(raw: bytes, name: str) -> bytes:
    if len(raw) > MAX_BACKUP_BYTES:
        raise SigningError("Encrypted backup download exceeds its bound.")
    if raw.startswith(b"PK"):
        # Older artifact clients can return a ZIP. Never extract paths to disk.
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) != 1 or entries[0].filename != name or entries[0].file_size > MAX_BACKUP_BYTES:
                    raise SigningError("Unexpected backup archive contents.")
                raw = archive.read(entries[0])
        except (ValueError, zipfile.BadZipFile):
            raise SigningError("Encrypted backup archive is invalid.") from None
    if not raw.startswith(b"age-encryption.org/v1\n"):
        raise SigningError("Refusing a plaintext or invalid signing backup.")
    return raw


def collect_backup(directory: Path, state: dict) -> None:
    request = check_request(state["request"])
    run = wait_run(find_run(state["request_commit"], BACKUP_WORKFLOW))
    report = check_report(state["request_commit"], "Android signing encrypted backup")
    name = f"iptv-signing-{request['request_id']}.age"
    if (report.get("format") != "encrypted-signing-backup-report-v1"
            or report.get("commit") != state["request_commit"] or str(report.get("run_id")) != str(run["id"])
            or report.get("request_id") != request["request_id"] or report.get("artifact") != name
            or report.get("certificate_sha256") != EXPECTED_SIGNER_SHA256
            or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("ciphertext_sha256", "")))):
        raise SigningError("Encrypted backup report does not match this owner request and workflow run.")
    result = api(API_ROOT + "/actions/runs/" + str(run["id"]) + "/artifacts?per_page=100")
    matches = [item for item in result["artifacts"] if item["name"] == name and not item["expired"]]
    if len(matches) != 1 or matches[0]["size_in_bytes"] > MAX_BACKUP_BYTES:
        raise SigningError("The encrypted backup is missing/expired. Use request --new-request; do not delete the key cache.")
    artifact = matches[0]
    digest = artifact.get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SigningError("The artifact's verified SHA-256 metadata is unavailable.")
    raw = gh_call(["api", "--hostname", "github.com", API_ROOT + "/actions/artifacts/" + str(artifact["id"]) + "/zip"])
    encrypted = extract_age_bytes(raw, name)
    # archive:false normally downloads raw bytes. Some clients/endpoints can wrap
    # them in a ZIP; validate either transport representation AND the CI ciphertext.
    if digest not in {"sha256:" + hashlib.sha256(raw).hexdigest(),
                      "sha256:" + hashlib.sha256(encrypted).hexdigest()}:
        raise SigningError("Encrypted artifact download checksum differs from GitHub's digest.")
    if (hashlib.sha256(encrypted).hexdigest() != report["ciphertext_sha256"]
            or len(encrypted) != report.get("ciphertext_bytes")):
        raise SigningError("Encrypted backup checksum differs from the request's CI report.")
    path = directory / name
    if path.exists():
        if path.is_symlink() or path.read_bytes() != encrypted:
            raise SigningError("An existing owner backup differs; it will not be overwritten.")
    else:
        private_write(path, encrypted)
    state.update(backup_file=name, backup_sha256=report["ciphertext_sha256"])
    save_state(directory, state)


def recovered_keystore(directory: Path, state: dict) -> bytes:
    name = state.get("backup_file", "")
    if not re.fullmatch(r"iptv-signing-[0-9a-f]{32}\.age", name):
        raise SigningError("No validated encrypted owner backup is available.")
    backup = directory / name
    identity = directory / IDENTITY_FILE
    if (backup.is_symlink() or identity.is_symlink() or not backup.is_file() or not identity.is_file()
            or backup.stat().st_size > MAX_BACKUP_BYTES or identity.stat().st_size > 16 * 1024):
        raise SigningError("Unsafe or missing encrypted owner backup/identity.")
    if hashlib.sha256(backup.read_bytes()).hexdigest() != state["backup_sha256"]:
        raise SigningError("Local encrypted backup checksum changed.")
    print("Enter the recovery passphrase only at age's LOCAL terminal prompt.")
    result = subprocess.run(["age", "--decrypt", "--identity", str(identity), str(backup)],
                            stdout=subprocess.PIPE, timeout=300)
    if result.returncode or len(result.stdout) > MAX_BACKUP_BYTES:
        raise SigningError("Cannot decrypt the owner backup; no Secret was written.")
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise SigningError("Decrypted backup is not valid; no Secret was written.") from None
    data = validate_backup(payload, state["request"], state["request_commit"])
    # Keytool/Jarsigner need a file. It exists briefly in the owner-only folder,
    # outside Git; the encrypted backup is the only long-lived private-key copy.
    with tempfile.TemporaryDirectory(prefix="private-verify-", dir=directory) as temp:
        key = Path(temp) / "verify.jks"
        private_write(key, data)
        validate_key(key, prove_private_key=True)
    return data


def install_secret(data: bytes, *, replace: bool = False) -> None:
    try:
        api(API_ROOT + "/actions/secrets/" + SECRET_NAME)
        exists = True
    except GitHubFailure as error:
        if error.status != 404:
            raise
        exists = False
    if exists and not replace:
        raise SigningError("The signing Secret already exists. Use status, or explicitly --replace after verifying your backup.")
    # gh performs GitHub's public-key encryption locally; body never appears in argv.
    gh_call(["secret", "set", SECRET_NAME, "--repo", GH_REPO], base64.b64encode(data))
    print("Signing Secret saved using local gh authentication. No key/token value was printed.")


def activate(directory: Path, state: dict) -> None:
    policy = {"format": 1, "repository": REPOSITORY, "branch": BRANCH,
              "application_id": APPLICATION_ID, "certificate_sha256": EXPECTED_SIGNER_SHA256,
              "required_source": "actions-secret", "owner_verified_backup_sha256": state["backup_sha256"],
              "owner_request_id": state["request"]["request_id"], "verification_nonce": uuid.uuid4().hex}
    state["activation_commit"] = commit_public_file(POLICY_PATH, policy, "Require the retained signing identity from Actions Secrets")
    state.pop("verified_ci", None)
    save_state(directory, state)


def validate_activation_reports(info: dict, reports: dict[int, dict], commit: str,
                                build_id: str, runtime_id: str) -> None:
    if (info.get("commit") != commit or str(info.get("run_id")) != build_id
            or info.get("application_id") != APPLICATION_ID or info.get("signing_source") != "actions-secret"
            or info.get("signer_sha256") != EXPECTED_SIGNER_SHA256 or info.get("signing_key_path_verified") is not True
            or info.get("private_signing_material_written_to_cache") is not False
            or info.get("legacy_signing_cache_restored") is not False
            or not re.fullmatch(r"[0-9a-f]{64}", str(info.get("sha256", "")))):
        raise SigningError("A successful Secret-only build with the installed signer has not been proven.")
    for api_level in (31, 33):
        report = reports.get(api_level, {})
        upgrade = report.get("upgrade") or {}
        counts = report.get("tests") or {}
        current = upgrade.get("current") or {}
        baseline = upgrade.get("baseline") or {}
        if (report.get("status") != 0 or report.get("signing_source") != "actions-secret"
                or report.get("commit") != commit or str(report.get("run_id")) != runtime_id
                or str(report.get("api")) != str(api_level) or str(report.get("expected_api")) != str(api_level)
                or report.get("verification_errors") != [] or report.get("failures") != []
                or counts.get("tests", 0) < 8 or any(counts.get(key) != 0 for key in ("failures", "errors", "skipped"))
                or upgrade.get("status") != "success"
                or any(upgrade.get(key) is not True for key in (
                    "same_as_delivered_0_1_2_signer", "room_schema_unchanged", "seed_test_passed",
                    "install_r_succeeded", "preservation_test_passed"))
                or current.get("apk_sha256") != info["sha256"]
                or current.get("signer_sha256") != EXPECTED_SIGNER_SHA256
                or baseline.get("signer_sha256") != EXPECTED_SIGNER_SHA256):
            raise SigningError("Both API31/API33 Secret-only upgrade/playback regressions must pass before cache deletion.")


def verify_activation(directory: Path, state: dict) -> None:
    commit = state.get("activation_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SigningError("Secret-only signing has not been activated by this owner tool.")
    build = wait_run(find_run(commit, ".github/workflows/android.yml"))
    runtime = wait_run(find_run(commit, ".github/workflows/android-smoke.yml"))
    info = check_report(commit, "Android APK package details")
    reports = {api_level: check_report(commit, f"Android API{api_level} runtime stability") for api_level in (31, 33)}
    validate_activation_reports(info, reports, commit, str(build["id"]), str(runtime["id"]))
    state["verified_ci"] = {"commit": commit, "build_run": build["id"], "runtime_run": runtime["id"], "apk_sha256": info["sha256"]}
    save_state(directory, state)
    print("Verified: Secret-only build, retained signer and API31/API33 in-place upgrade/runtime checks.")


def finish(directory: Path, *, replace: bool = False, restore: bool = False) -> None:
    state = load_state(directory)
    if not restore:
        collect_backup(directory, state)
    data = recovered_keystore(directory, state)
    state["backup_recovery_verified"] = True
    save_state(directory, state)
    install_secret(data, replace=replace)
    del data
    activate(directory, state)
    verify_activation(directory, state)
    print("Keep an OFFLINE copy of this complete encrypted owner folder and store the passphrase separately.")
    print("The legacy remote cache is still present. Purge is a separate, guarded owner action.")


def purge_cache(directory: Path, *, offline_copy_confirmed: bool) -> None:
    if not offline_copy_confirmed:
        raise SigningError("First copy the complete encrypted owner folder offline and keep its passphrase separately; then explicitly confirm --offline-copy-confirmed.")
    state = load_state(directory)
    recovered_keystore(directory, state)  # Prove recovery again, without any live cache/artifact dependency.
    verify_activation(directory, state)
    branch = api(API_ROOT + "/git/ref/heads/" + quote(BRANCH, safe=""))
    if branch["object"]["sha"] != state["activation_commit"]:
        raise SigningError("The branch advanced after verification; re-activate/verify before deleting its old cache.")
    response = api(API_ROOT + "/actions/caches?key=" + CACHE_KEY + "&per_page=100")
    selected = [entry for entry in response["actions_caches"]
                if entry["key"] == CACHE_KEY and entry["ref"] == "refs/heads/" + BRANCH]
    for entry in selected:
        api(API_ROOT + "/actions/caches/" + str(int(entry["id"])), method="DELETE")
    print("Removed", len(selected), "legacy signing cache entry/entries for this branch only. No other caches were touched.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".iptv-hamrah-signing")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("init")
    request = commands.add_parser("request")
    request.add_argument("--new-request", action="store_true")
    for name in ("finish", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--replace", action="store_true")
    commands.add_parser("status")
    commands.add_parser("verify-backup", help="Offline recovery/private-key proof; no GitHub request or Secret write")
    purge = commands.add_parser("purge-cache")
    purge.add_argument("--offline-copy-confirmed", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        doctor()
        return 0
    directory = private_directory(args.state_dir)
    if args.command == "init":
        init_owner(directory)
    elif args.command == "request":
        doctor()
        request_backup(directory, new_request=args.new_request)
    elif args.command in ("finish", "restore"):
        doctor()
        finish(directory, replace=args.replace, restore=args.command == "restore")
    elif args.command == "verify-backup":
        recovered_keystore(directory, load_state(directory))
        print("Offline encrypted backup and private-key proof verified. No GitHub operation was performed.")
    elif args.command == "purge-cache":
        purge_cache(directory, offline_copy_confirmed=args.offline_copy_confirmed)
    elif args.command == "status":
        state = load_state(directory)
        if state.get("activation_commit"):
            verify_activation(directory, state)
        else:
            print("Owner migration has not been activated. The old cache must be kept.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SigningError, OSError, subprocess.TimeoutExpired, KeyboardInterrupt, ValueError, TypeError, KeyError):
        # Fixed boundary: do not dump process results, decrypted JSON or stack locals.
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, SigningError) else "Operation stopped or a required local tool/filesystem operation failed. Keep the old cache and inspect status before retrying; no automatic cache cleanup was attempted."
        print("ERROR:", message, file=sys.stderr)
        sys.exit(1)
