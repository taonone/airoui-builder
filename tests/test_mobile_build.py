import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import hashlib

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mobile_build", root / "scripts/mobile_build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class MobileBuildTests(unittest.TestCase):
    def test_large_zip_download_is_streamed_and_limits_truncation_are_enforced(self):
        class Reply(io.BytesIO):
            def __init__(self, data, declared):
                super().__init__(data)
                self.headers = {"Content-Length": str(declared)} if declared is not None else {}
            def read(self, n=-1):
                self.assert_bounded(n)
                return super().read(n)
            def assert_bounded(self, n):
                if not 0 < n <= 1024 * 1024:
                    raise AssertionError("Unbounded source read")
        data = b"PK\x03\x04" + b"x" * (12 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "source.zip"
            with patch.object(builder.urllib.request, 'build_opener') as opener:
                opener.return_value.open.return_value = Reply(data, len(data))
                self.assertEqual(builder.download_archive('https://airoui.com/private.zip', target), hashlib.sha256(data).hexdigest())
            self.assertEqual(target.stat().st_size, len(data))
            for declared, maximum in [(None, len(data)-1), (len(data)+1, len(data)+1), (len(data), len(data)-1)]:
                with patch.object(builder.urllib.request, 'build_opener') as opener:
                    opener.return_value.open.return_value = Reply(data, declared)
                    with self.assertRaises(builder.BuildError):
                        builder.download_archive('https://airoui.com/private.zip', target, maximum)
                self.assertFalse(target.exists())

    def test_extraction_above_old_100mb_and_cross_block_secret_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); archive = base / 'large.zip'
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr('project/settings.gradle', '')
                with z.open('project/app/src/main/assets/index.html', 'w') as f:
                    for _ in range(101):
                        f.write(b'x' * 1024 * 1024)
            project = builder.extract_source(archive, base / 'extracted')
            self.assertGreater((project/'app/src/main/assets/index.html').stat().st_size, 100*1024*1024)
            secret = project/'cross-boundary.js'
            secret.write_text('x' * 65520 + "\nconst key='sk-" + 'A'*40 + "';")
            with self.assertRaises(builder.BuildError):
                builder.scan_secrets(project)
            with patch.object(builder, 'MAX_EXTRACTED_BYTES', 10*1024*1024):
                with self.assertRaises(builder.BuildError):
                    builder.extract_source(archive, base / 'limited')

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

    def test_common_profile_is_shared_but_individual_profiles_take_priority(self):
        common = {"keystoreBase64": "shared-key", "storePassword": "shared-password", "keyAlias": "shared-alias", "keyPassword": "shared-password"}
        individual = {"keystoreBase64": "individual-key", "storePassword": "individual-password", "keyAlias": "individual-alias", "keyPassword": "individual-password"}
        with patch.dict(os.environ, {"AIROUI_COMMON_SIGNING_PROFILE": json.dumps(common), "AIROUI_SIGNING_PROFILES": json.dumps({"com.airoui.existing": individual})}, clear=True):
            for name in ["com.airoui.fan", "com.airoui.fridge"]:
                self.assertEqual(builder.signing_values({"signingProfile": name}), common)
            self.assertEqual(builder.signing_values({"signingProfile": "com.airoui.existing"}), individual)

    def test_partial_individual_key_is_not_mixed_with_common_and_certificate_pin_is_preserved(self):
        common = {"keystoreBase64": "shared-key", "storePassword": "shared-password", "keyAlias": "shared-alias", "keyPassword": "shared-password", "certificateSha256": "a" * 64}
        env = {"AIROUI_COMMON_SIGNING_PROFILE": json.dumps(common), "AIROUI_SIGNING_PROFILES": json.dumps({"com.airoui.existing": {"keyAlias": "partial"}})}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(builder.BuildError, "Release signing secrets"):
                builder.signing_values({"signingProfile": "com.airoui.existing"})
            with self.assertRaisesRegex(builder.BuildError, "differs from the installed app"):
                builder.signing_values({"signingProfile": "com.airoui.new", "expectedCertificateSha256": "b" * 64})

    def test_malformed_secret_has_a_controlled_error_and_common_does_not_enter_gradle_env(self):
        with patch.dict(os.environ, {"AIROUI_COMMON_SIGNING_PROFILE": "private-value-not-json"}, clear=True):
            with self.assertRaises(builder.BuildError) as result:
                builder.signing_values({"signingProfile": "com.airoui.new"})
            self.assertNotIn("private-value-not-json", str(result.exception))
            self.assertNotIn("AIROUI_COMMON_SIGNING_PROFILE", builder.clean_build_env())

    def test_structured_secret_values_are_masked_before_github_build_logs(self):
        values = {"keystoreBase64": "fixture-key", "storePassword": "fixture%password\n", "keyPassword": "fixture-password"}
        output = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True), contextlib.redirect_stdout(output):
            builder.mask_signing_values(values)
        self.assertIn("::add-mask::fixture%25password%0A", output.getvalue())
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
            builder.mask_signing_values(values)
        self.assertEqual(output.getvalue(), "")

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
