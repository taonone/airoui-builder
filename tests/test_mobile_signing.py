import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_mobile_signing", root / "scripts/configure_mobile_signing.py")
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


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


if __name__ == "__main__":
    unittest.main()
