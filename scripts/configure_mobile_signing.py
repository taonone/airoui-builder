"""Create a private Android upload key and optionally register a scoped GitHub Secret.

No password, key bytes or token is printed, placed in argv, or written to the project.
--register is an explicit external write; creation alone only writes the private vault.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request


def lock_directory(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        sid = re.search(r"S-1-\d+(?:-\d+)+", identity)
        if not sid:
            raise RuntimeError("Cannot resolve current Windows user SID")
        subprocess.run(["icacls", str(directory), "/inheritance:r", "/grant:r", "*" + sid[0] + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], check=True, capture_output=True)
    else:
        directory.chmod(0o700)


def create_key(profile, directory):
    lock_directory(directory)
    private = directory / "signing.secrets.json"
    keystore = directory / "upload.jks"
    if private.exists() or keystore.exists():
        raise RuntimeError("A signing key already exists here. It was not overwritten.")
    tool = shutil.which("keytool")
    if not tool:
        raise RuntimeError("JDK 17 keytool must be on PATH")
    password = secrets.token_urlsafe(36)
    env = dict(os.environ, AIROUI_CREATE_KEY_PASSWORD=password)
    subprocess.run([tool, "-genkeypair", "-storetype", "PKCS12", "-keystore", str(keystore), "-alias", "airoui-upload", "-keyalg", "RSA", "-keysize", "3072", "-validity", "10000", "-dname", "CN=Airoui Android Upload Key", "-storepass:env", "AIROUI_CREATE_KEY_PASSWORD", "-keypass:env", "AIROUI_CREATE_KEY_PASSWORD"], env=env, check=True, capture_output=True)
    cert = subprocess.run([tool, "-J-Duser.language=en", "-J-Duser.country=US", "-list", "-v", "-keystore", str(keystore), "-alias", "airoui-upload", "-storepass:env", "AIROUI_CREATE_KEY_PASSWORD"], env=env, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    match = re.search(r"SHA256:\s*([A-Fa-f0-9:]{95})", cert)
    if not match:
        raise RuntimeError("Cannot verify the newly generated certificate")
    fingerprint = match[1].replace(":", "").lower()
    private.write_text(json.dumps({profile: {"keystoreBase64": base64.b64encode(keystore.read_bytes()).decode(), "storePassword": password, "keyAlias": "airoui-upload", "keyPassword": password, "certificateSha256": fingerprint}}), encoding="utf-8")
    public = {"profile": profile, "certificateSha256": fingerprint, "keyAlias": "airoui-upload", "keystorePath": str(keystore)}
    (directory / "signing.public.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
    for path in [private, keystore]:
        if os.name != "nt":
            path.chmod(0o600)
    return public


def register_secret(repo, directory):
    # The profile map is kept locally so adding profiles can be reviewed together.
    # GitHub does not allow reading existing Secret values; never blindly replace one.
    from nacl.public import PublicKey, SealedBox
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("Invalid GitHub repository")
    result = subprocess.run(["git", "-c", "credential.interactive=never", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    token = fields.get("password")
    if not token:
        raise RuntimeError("Authenticated GitHub git credential required")
    prefix = "https://api.github.com/repos/" + repo + "/actions/secrets"
    headers = {"Authorization": "Bearer " + token, "User-Agent": "Airoui-Signing-Setup", "Accept": "application/vnd.github+json"}
    def call(url, method="GET", payload=None):
        request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload else None, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    name = "AIROUI_SIGNING_PROFILES"
    try:
        call(prefix + "/" + name)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise RuntimeError("Cannot check GitHub Secret metadata") from None
    else:
        raise RuntimeError("AIROUI_SIGNING_PROFILES already exists. Merge profiles through a separately reviewed update; it was not overwritten.")
    public = call(prefix + "/public-key")
    value = (directory / "signing.secrets.json").read_bytes()
    encrypted = SealedBox(PublicKey(base64.b64decode(public["key"]))).encrypt(value)
    call(prefix + "/" + name, "PUT", {"encrypted_value": base64.b64encode(encrypted).decode(), "key_id": public["key_id"]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, help="The app's fixed Android applicationId")
    parser.add_argument("--create", action="store_true", help="Create a new key; never overwrites an existing key")
    parser.add_argument("--register", action="store_true", help="Explicitly write the encrypted profile map to GitHub Actions Secrets")
    parser.add_argument("--repo", default="taonone/airoui-builder")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*){2,}", args.profile):
        parser.error("Invalid applicationId")
    directory = Path.home() / ".codex" / "secrets" / "airoui-android" / args.profile
    try:
        public = create_key(args.profile, directory) if args.create else json.loads((directory / "signing.public.json").read_text())
        if args.register:
            register_secret(args.repo, directory)
        print("Private signing vault:", directory)
        print("Application ID:", args.profile)
        print("Certificate SHA-256:", public["certificateSha256"])
        print("GitHub Secret registered." if args.register else "No GitHub Secret was changed. Back up the private vault before registration.")
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else "Signing setup failed: " + type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
