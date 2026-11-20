import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_mobile_signing", root / "scripts/configure_mobile_signing.py")
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


class FakeGitHub:
    def __init__(self, secrets=None):
        self.secrets = secrets or {}
        self.writes = []

    def ensure_access(self):
        pass

    def get_secret(self, name):
        return self.secrets.get(name)

    def put_secret(self, name, value):
        self.writes.append((name, value))
        self.secrets[name] = {"name": name, "updated_at": "fixture-registered-at"}


class SigningSetupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("keytool"), "JDK keytool required")
    def test_private_key_creation_is_persistent_and_never_overwrites_a_key(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "private-vault"
            public = signing.create_key("com.airoui.validation_only", directory)
            self.assertEqual(len(public["certificateSha256"]), 64)
            data = json.loads((directory / "signing.secrets.json").read_text())
            profile = data["com.airoui.validation_only"]
            self.assertEqual(hashlib.sha256(base64.b64decode(profile["keystoreBase64"])).hexdigest(), hashlib.sha256((directory / "upload.jks").read_bytes()).hexdigest())
            self.assertEqual(profile["certificateSha256"], public["certificateSha256"])
            self.assertEqual(profile["storePassword"], profile["keyPassword"])
            self.assertNotIn("storePassword", public)
            before = hashlib.sha256((directory / "upload.jks").read_bytes()).hexdigest()
            with self.assertRaisesRegex(RuntimeError, "not overwritten"):
                signing.create_key("com.airoui.validation_only", directory)
            self.assertEqual(before, hashlib.sha256((directory / "upload.jks").read_bytes()).hexdigest())

    @unittest.skipUnless(shutil.which("keytool"), "JDK keytool required")
    def test_common_setup_registers_once_and_reuses_the_same_key(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "common-vault"; api = FakeGitHub()
            public, state = signing.setup_profile("owner/repo", folder, signing.COMMON_PROFILE, common=True, api=api)
            self.assertEqual(state, "registered")
            before = hashlib.sha256((folder / "upload.jks").read_bytes()).hexdigest()
            again, state = signing.setup_profile("owner/repo", folder, signing.COMMON_PROFILE, common=True, api=api)
            self.assertEqual(state, "already-registered")
            self.assertEqual(public, again)
            self.assertEqual(len(api.writes), 1)
            name, raw = api.writes[0]
            self.assertEqual(name, signing.COMMON_SECRET)
            value = json.loads(raw)
            self.assertIn("keystoreBase64", value)
            self.assertNotIn(signing.COMMON_PROFILE, value)
            self.assertEqual(before, hashlib.sha256((folder / "upload.jks").read_bytes()).hexdigest())
            self.assertFalse((folder / "creation.pending.secrets.json").exists())
            api.secrets[name]["updated_at"] = "changed-externally"
            with self.assertRaisesRegex(RuntimeError, "덮어쓰지"):
                signing.register_secret("owner/repo", folder, common=True, api=api)
            self.assertEqual(len(api.writes), 1)

    def test_existing_remote_or_legacy_keys_are_preserved_before_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "not-created"
            for existing in [{signing.COMMON_SECRET: {"updated_at": "existing"}}, {"AIROUI_KEYSTORE_BASE64": {"updated_at": "legacy"}}]:
                api = FakeGitHub(existing)
                with patch.object(signing, "create_key") as create:
                    with self.assertRaises(RuntimeError):
                        signing.setup_profile("owner/repo", folder, signing.COMMON_PROFILE, common=True, api=api)
                create.assert_not_called()
                self.assertEqual(api.writes, [])


if __name__ == "__main__":
    unittest.main()
