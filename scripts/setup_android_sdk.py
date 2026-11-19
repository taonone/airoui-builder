"""Resolve Android tools independently of PATH, install SDK36 and export CI paths."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


class SdkSetupError(RuntimeError):
    pass


def find_sdk(env):
    roots = []
    for value in [env.get("ANDROID_HOME"), env.get("ANDROID_SDK_ROOT"), "/usr/local/lib/android/sdk"]:
        if value:
            root = Path(value).expanduser().resolve()
            if root not in roots:
                roots.append(root)
    cli = shutil.which("sdkmanager", path=env.get("PATH", "")) or shutil.which("sdkmanager.bat", path=env.get("PATH", ""))
    if cli:
        candidate = Path(cli).resolve()
        for parent in candidate.parents:
            if parent.name == "cmdline-tools":
                if parent.parent not in roots:
                    roots.append(parent.parent)
                break
    for root in roots:
        tools = root / "cmdline-tools"
        versions = list(tools.iterdir()) if tools.is_dir() else []
        versions.sort(key=lambda path: (path.name == "latest", tuple(int(n) for n in re.findall(r"\d+", path.name))), reverse=True)
        for directory in versions + [root / "tools"]:
            for filename in ["sdkmanager", "sdkmanager.bat"]:
                manager = directory / "bin" / filename
                if manager.is_file():
                    return root, manager
    raise SdkSetupError("Android SDK의 sdkmanager를 찾을 수 없습니다. Runner에 Android Command-line Tools가 설치되어 있는지 확인해주세요.")


def required_files(root):
    suffix = ".exe" if os.name == "nt" else ""
    signer_suffix = ".bat" if os.name == "nt" else ""
    return [root / "platforms/android-36/android.jar", root / ("build-tools/36.0.0/aapt" + suffix), root / ("build-tools/36.0.0/apksigner" + signer_suffix)]


def export_paths(root, manager, env):
    for value in [str(root), str(manager.parent)]:
        if "\n" in value or "\r" in value:
            raise SdkSetupError("Invalid Android SDK path")
    if env.get("GITHUB_ENV"):
        with Path(env["GITHUB_ENV"]).open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"ANDROID_HOME={root}\nANDROID_SDK_ROOT={root}\n")
    if env.get("GITHUB_PATH"):
        with Path(env["GITHUB_PATH"]).open("a", encoding="utf-8", newline="\n") as output:
            output.write(str(manager.parent) + "\n" + str(root / "platform-tools") + "\n" + str(root / "build-tools/36.0.0") + "\n")


def setup(env=None):
    env = dict(os.environ if env is None else env)
    root, manager = find_sdk(env)
    sdk_env = {**env, "ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(root)}
    # Newer SDK tools can require a newer JVM. Keep this override local to
    # sdkmanager; Gradle still uses Java 17 configured by setup-java.
    java21 = env.get("JAVA_HOME_21_X64")
    if java21 and (Path(java21) / "bin" / ("java.exe" if os.name == "nt" else "java")).is_file():
        sdk_env["JAVA_HOME"] = java21
    print("Android SDK:", root, flush=True)
    print("SDK manager:", manager, flush=True)
    subprocess.run([str(manager), "--version"], env=sdk_env, check=True, timeout=60)
    if not all(path.is_file() for path in required_files(root)):
        subprocess.run([str(manager), f"--sdk_root={root}", "platforms;android-36", "build-tools;36.0.0"], env=sdk_env,
                       input="y\n" * 100, text=True, encoding="utf-8", errors="replace", check=True, timeout=300)
    if not all(path.is_file() for path in required_files(root)):
        raise SdkSetupError("SDK 설치 명령이 끝났지만 Android 36 또는 Build Tools 36.0.0의 필수 파일이 없습니다. Actions의 SDK 설치 로그를 확인해주세요.")
    export_paths(root, manager, env)
    print("Android SDK 36 and Build Tools 36.0.0 verified.", flush=True)
    return root


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=Path("foundry-work"))
    args = parser.parse_args(argv)
    try:
        setup()
    except Exception as error:
        if isinstance(error, SdkSetupError):
            message = str(error)
        elif isinstance(error, subprocess.TimeoutExpired):
            message = "Android SDK 설치 시간이 초과되었습니다. Actions의 SDK 설치 로그를 확인해주세요."
        else:
            message = "Android SDK 도구 실행 또는 패키지 설치에 실패했습니다. Actions의 SDK 설치 로그를 확인해주세요."
        args.work.mkdir(parents=True, exist_ok=True)
        (args.work / "failure.json").write_text(json.dumps({"status": "failed", "code": "BUILD_ANDROID_SDK_SETUP", "error": message}), encoding="utf-8")
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
