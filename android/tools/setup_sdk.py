"""Explicit official Android SDK setup with readable, bounded failure diagnostics."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from ci_report import publish

messages: list[str] = []


def log(message: str) -> None:
    print(message, flush=True)
    messages.append(message)


def run(command: list[str], *, input_text: str | None = None) -> None:
    result = subprocess.run(command, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    log(result.stdout[-40000:])
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} returned {result.returncode}")


status = 0
try:
    log("Java executable: " + str(shutil.which("java")))
    log("JAVA_HOME: " + str(os.environ.get("JAVA_HOME")))
    run(["java", "-version"])
    manager = shutil.which("sdkmanager")
    sdk = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or Path.home() / ".android/sdk-trial")
    if manager is None:
        candidates = list(sdk.glob("cmdline-tools/*/bin/sdkmanager"))
        if candidates:
            manager = str(candidates[0])
    if manager is None:
        sdk = Path.home() / ".android/sdk-trial"
        url = "https://dl.google.com/android/repository/commandlinetools-linux-12266719_latest.zip"
        archive = Path(os.environ["RUNNER_TEMP"]) / "android-commandline-tools.zip"
        log("Downloading official command-line tools: " + url)
        with urllib.request.urlopen(url, timeout=90) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        destination = sdk / "cmdline-tools"
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                (destination / info.filename).resolve().relative_to(destination.resolve())
            source.extractall(destination)
        extracted = destination / "cmdline-tools"
        extracted.rename(destination / "16.0")
        for path in (destination / "16.0/bin").iterdir():
            if path.is_file(): path.chmod(0o755)
        manager = str(destination / "16.0/bin/sdkmanager")
        archive.unlink()
    log("Android SDK directory: " + str(sdk))
    run([manager, f"--sdk_root={sdk}", "platforms;android-36", "build-tools;36.0.0"], input_text="y\n" * 100)
    with open(os.environ["GITHUB_ENV"], "a") as output:
        output.write(f"ANDROID_HOME={sdk}\nANDROID_SDK_ROOT={sdk}\n")
    with open(os.environ["GITHUB_PATH"], "a") as output:
        output.write(f"{Path(manager).parent}\n{sdk / 'platform-tools'}\n")
except Exception as error:
    status = 1
    log(type(error).__name__ + ": " + str(error))
publish("Android SDK setup details", status, "\n".join(messages) if status else "Official SDK setup completed.\n" + "\n".join(messages[:3]) + "\n" + messages[-1][-2000:])
sys.exit(status)
