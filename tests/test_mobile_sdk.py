import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_android_sdk", root / "scripts/setup_android_sdk.py")
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


class AndroidSdkTests(unittest.TestCase):
    def fixture(self, base, version="12.0", installed=True):
        directory = base / "Android SDK with spaces"
        manager = directory / "cmdline-tools" / version / "bin" / ("sdkmanager.bat" if os.name == "nt" else "sdkmanager")
        manager.parent.mkdir(parents=True, exist_ok=True)
        manager.write_text("test fixture")
        if installed:
            for path in sdk.required_files(directory):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
        env = {"ANDROID_HOME": str(directory), "PATH": "", "GITHUB_ENV": str(base / "github-env"), "GITHUB_PATH": str(base / "github-path")}
        return directory, manager, env

    def test_numbered_sdk_tools_are_found_without_path_and_exported(self):
        with tempfile.TemporaryDirectory() as temp:
            directory, manager, env = self.fixture(Path(temp))
            with patch.object(sdk.subprocess, "run") as run:
                self.assertEqual(sdk.setup(env), directory)
            self.assertEqual(run.call_args.args[0], [str(manager), "--version"])
            self.assertEqual(run.call_count, 1)
            self.assertIn(str(manager.parent), Path(env["GITHUB_PATH"]).read_text())
            self.assertIn("ANDROID_HOME=" + str(directory), Path(env["GITHUB_ENV"]).read_text())

    def test_latest_alias_is_preferred_and_numeric_versions_sort_correctly(self):
        with tempfile.TemporaryDirectory() as temp:
            directory, manager, env = self.fixture(Path(temp), version="9.0")
            manager12 = directory / "cmdline-tools/12.0/bin" / manager.name
            manager12.parent.mkdir(parents=True); manager12.write_text("fixture")
            self.assertEqual(sdk.find_sdk(env)[1], manager12)
            latest = directory / "cmdline-tools/latest/bin" / manager.name
            latest.parent.mkdir(parents=True); latest.write_text("fixture")
            self.assertEqual(sdk.find_sdk(env)[1], latest)

    def test_install_uses_absolute_cli_and_does_not_report_success_for_missing_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            directory, manager, env = self.fixture(Path(temp), installed=False)
            with patch.object(sdk.subprocess, "run") as run:
                with self.assertRaises(sdk.SdkSetupError):
                    sdk.setup(env)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0], [str(manager), "--sdk_root=" + str(directory), "platforms;android-36", "build-tools;36.0.0"])
            self.assertFalse(Path(env["GITHUB_ENV"]).exists())

    def test_sdk_java_override_does_not_change_the_build_java_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); directory, manager, env = self.fixture(base)
            java21 = base / "jdk21"; java = java21 / "bin" / ("java.exe" if os.name == "nt" else "java")
            java.parent.mkdir(parents=True); java.write_text("fixture")
            env.update(JAVA_HOME="jdk17-build", JAVA_HOME_21_X64=str(java21))
            with patch.object(sdk.subprocess, "run") as run:
                sdk.setup(env)
            self.assertEqual(run.call_args.kwargs["env"]["JAVA_HOME"], str(java21))
            self.assertEqual(env["JAVA_HOME"], "jdk17-build")
            self.assertNotIn("JAVA_HOME", Path(env["GITHUB_ENV"]).read_text())

    def test_sdk_failure_is_recorded_before_project_download_or_signing(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(sdk, "setup", side_effect=sdk.SdkSetupError("SDK fixture failure")):
                self.assertEqual(sdk.main(["--work", temp]), 1)
            failure = json.loads((Path(temp) / "failure.json").read_text())
            self.assertEqual(failure["code"], "BUILD_ANDROID_SDK_SETUP")
            self.assertEqual(failure["error"], "SDK fixture failure")


if __name__ == "__main__":
    unittest.main()
