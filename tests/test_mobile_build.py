import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mobile_build", root / "scripts/mobile_build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class MobileBuildTests(unittest.TestCase):
    def test_rejects_path_traversal_absolute_symlink_and_credential_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for number, name in enumerate(["../escape", "/tmp/escape", "root/../../escape", "root/.github/workflows/steal.yml", "root\\escape"]):
                archive = base / f"bad-{number}.zip"
                with zipfile.ZipFile(archive, "w") as z:
                    z.writestr(name, "untrusted")
                with self.assertRaises(builder.BuildError):
                    builder.extract_source(archive, base / f"out-{number}")
            archive = base / "link.zip"
            with zipfile.ZipFile(archive, "w") as z:
                item = zipfile.ZipInfo("root/link")
                item.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(item, "../../escape")
            with self.assertRaises(builder.BuildError):
                builder.extract_source(archive, base / "link-output")

    def test_secrets_are_blocked_without_returning_their_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.html"
            path.write_text("<script>const key='sk-" + "A" * 40 + "'</script>")
            with self.assertRaisesRegex(builder.BuildError, "Suspected private credential") as result:
                builder.scan_secrets(Path(tmp))
            self.assertNotIn("A" * 40, str(result.exception))
            path.write_text('<script>const TURNSTILE_SITE_KEY="0x4AAAAAAGITzyCwtkbKSbbG"</script>')
            builder.scan_secrets(Path(tmp))

    def test_gradle_does_not_inherit_r2_git_or_source_capabilities(self):
        with patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "test-private-r2", "GITHUB_TOKEN": "test-private-git", "AIROUI_SIGNING_PROFILES": "test-private-profiles", "JAVA_HOME": "test-jdk"}):
            env = builder.clean_build_env()
            for name in ["AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "AIROUI_SIGNING_PROFILES"]:
                self.assertNotIn(name, env)
            self.assertEqual(env["JAVA_HOME"], "test-jdk")

    def test_release_requires_persistent_signing_profile_no_debug_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(builder.BuildError, "Release signing secrets"):
                builder.signing_values({"signingProfile": "com.airoui.app"})
        profile = {"keystoreBase64": "test-only", "storePassword": "test-only", "keyAlias": "test-only", "keyPassword": "test-only"}
        with patch.dict(os.environ, {"AIROUI_SIGNING_PROFILES": json.dumps({"com.airoui.app": profile})}, clear=True):
            self.assertEqual(builder.signing_values({"signingProfile": "com.airoui.app"}), profile)

    def test_source_redirect_is_rejected_and_payload_is_not_shell_code(self):
        with self.assertRaises(builder.BuildError):
            builder.NoRedirect().redirect_request(None, None, 302, None, None, "https://credential-sink.invalid")
        for value in ["a; touch stolen", "../x", "$(whoami)", "", None]:
            with self.assertRaises(builder.BuildError):
                builder.validate_build_id(value)

    def test_certificate_parsing_supports_apk_and_aab_output(self):
        digest = "ab" * 32
        self.assertEqual(builder.certificate_fingerprint("Signer #1 certificate SHA-256 digest: " + digest), digest)
        self.assertEqual(builder.certificate_fingerprint("SHA256: " + ":".join(["AB"] * 32)), digest)


if __name__ == "__main__":
    unittest.main()
