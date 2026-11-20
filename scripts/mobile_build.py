"""Airoui Foundry v3 CI runner. Payloads are data, never shell source.

Phases keep R2 credentials out of Gradle and signing keys out of source archives.
The same runner supports local extracted-project builds for verification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

WRAPPER_HASHES = {
    "gradlew": "c76f16038b4e70b84ed0b3b9e08e82a8ecaf66ec0efdfdecdece92520fa3cecb",
    "gradlew.bat": "0f3ed8f03b50934cb8c48b15a470d5c20a30a5385825e48b55bcc8ea3d8f8e18",
    "gradle/wrapper/gradle-wrapper.jar": "81a82aaea5abcc8ff68b3dfcb58b3c3c429378efd98e7433460610fecd7ae45f",
}
GRADLE_SHA256 = "20f1b1176237254a6fc204d8434196fa11a4cfb387567519c61556e8710aed78"
TEMPLATE_HASHES = {'settings.gradle': '5f4189a9dbcbe1e96460676d1819cd0383070700666b467c97323e71a3c7c28d', 'build.gradle': '96a33edb21d173da2c36f5312f50cdfb9f71d9f5078c081c81f2d029547f6cb9', 'gradle.properties': 'e6b436ff6a08da8b8c05e0e2fc1a7f40f41701bb506cd80fc2ddfd7b6e3b3b2b', 'app/build.gradle': '71c3d9c8038a29b8518bc1332cc12aec2912fed19ad753cd0017d2236d117e3b', 'MainActivity.java': '7c45af454a734cbd60fb1749a6f6244de581dc0ad36337e76c71dfbfdceaf2b0'}
SECRET_RULES = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    re.compile(r"\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"(?:TURNSTILE_SECRET_KEY|AIROUI_JWT_SECRET|RESEND_API_KEY|GITHUB_PAT|client_secret)\s*[\"']?\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']", re.I),
]


class BuildError(Exception):
    pass


def save_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_build_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise BuildError("Invalid build ID")
    return value


def event_payload():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    payload = event.get("client_payload", {})
    validate_build_id(payload.get("app_id"))
    return payload


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BuildError("Source redirect refused")


def download_bytes(url, maximum):
    request = urllib.request.Request(url, headers={"User-Agent": "Airoui-Foundry-v3"})
    # Capability URL is deliberately never printed or forwarded to another host.
    with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
        content = response.read(maximum + 1)
    if len(content) > maximum:
        raise BuildError("Source download exceeds size limit")
    return content


def extract_source(archive: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    seen = set()
    with zipfile.ZipFile(archive) as zip_file:
        if len(zip_file.infolist()) > 2000:
            raise BuildError("Too many source files")
        for item in zip_file.infolist():
            name = item.filename
            path = PurePosixPath(name)
            mode = item.external_attr >> 16
            if (not name or "\\" in name or ":" in name or path.is_absolute() or
                    any(part in {"..", ".git", ".github", ".gradle", "buildSrc", "build-logic"} for part in path.parts) or
                    stat.S_ISLNK(mode) or name in seen):
                raise BuildError("Unsafe or duplicate ZIP entry")
            seen.add(name)
            total += item.file_size
            if total > 100 * 1024 * 1024:
                raise BuildError("Extracted source exceeds 100MB")
            target = destination.joinpath(*path.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    roots = list(destination.rglob("settings.gradle"))
    if len(roots) != 1:
        raise BuildError("Expected exactly one Android project root")
    return roots[0].parent


def scan_secrets(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".jks", ".keystore", ".p12", ".pfx", ".pem", ".key"} or path.name.startswith(".env"):
            raise BuildError("Private credential file in source archive")
        if path.suffix.lower() in {".html", ".js", ".json", ".txt", ".css"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(rule.search(text) for rule in SECRET_RULES):
                # Values and excerpts never enter CI logs.
                raise BuildError("Suspected private credential in source archive")


def verify_templates(root, config):
    def quoted(value):
        return json.dumps(str(value), ensure_ascii=False).replace('$', chr(92) + '$').replace(chr(0x2028), chr(92) + 'u2028').replace(chr(0x2029), chr(92) + 'u2029')
    java = root / 'app/src/main/java' / config['applicationId'].replace('.', '/') / 'MainActivity.java'
    for name, expected in TEMPLATE_HASHES.items():
        text = (java if name == 'MainActivity.java' else root / name).read_text(encoding='utf-8')
        if name == 'app/build.gradle':
            text = text.replace('namespace ' + quoted(config['applicationId']), 'namespace "__APPLICATION_ID__"').replace('applicationId ' + quoted(config['applicationId']), 'applicationId "__APPLICATION_ID__"').replace('versionCode ' + str(config['versionCode']), 'versionCode __VERSION_CODE__').replace('versionName ' + quoted(config['versionName']), 'versionName "__VERSION_NAME__"')
        if name == 'MainActivity.java':
            text = text.replace('package ' + config['applicationId'] + ';', 'package __APPLICATION_ID__;')
        if hashlib.sha256(text.encode()).hexdigest() != expected:
            raise BuildError('Unreviewed Android build template refused')
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if any(part in {'.gradle', 'build'} for part in relative.parts):
            continue  # Only local subsequent builds have these generated directories.
        if 'buildSrc' in relative.parts or 'build-logic' in relative.parts:
            raise BuildError('Custom build logic is not allowed')
        if path.is_file() and (path.suffix in {'.gradle', '.kts', '.java', '.kt'} or path.name == 'gradle.properties'):
            allowed = {root / name for name in TEMPLATE_HASHES if name != 'MainActivity.java'} | {java}
            if path not in allowed:
                raise BuildError('Unexpected executable Android build source')


def verify_project(root: Path, expected=None):
    config = json.loads((root / "foundry-build.json").read_text(encoding="utf-8"))
    if config.get("schema") != 3 or config.get("mode") not in {"apk", "release", "play"}:
        raise BuildError("Refresh Foundry and regenerate a v3 Android project")
    if config.get("signingProfile") != config.get("applicationId"):
        raise BuildError("Signing profile must match the reserved application ID")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*){2,}", config.get("applicationId", "")):
        raise BuildError("Invalid application ID")
    if type(config.get("versionCode")) is not int or not 1 <= config["versionCode"] <= 2100000000:
        raise BuildError("Invalid versionCode")
    if expected:
        for field in ["buildId", "applicationId", "versionCode", "versionName", "mode", "expectedCertificateSha256"]:
            if expected.get(field) is not None and config.get(field) != expected[field]:
                raise BuildError("Project metadata disagrees with server reservation")
    for name, digest in WRAPPER_HASHES.items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise BuildError("Gradle Wrapper integrity check failed")
    properties = (root / "gradle/wrapper/gradle-wrapper.properties").read_text(encoding="utf-8")
    if f"distributionSha256Sum={GRADLE_SHA256}" not in properties or "gradle-8.13-bin.zip" not in properties:
        raise BuildError("Untrusted Gradle distribution")
    gradle = (root / "app/build.gradle").read_text(encoding="utf-8")
    if not re.search(r"\btargetSdk\s+36\b", gradle) or not re.search(r"\bcompileSdk\s+36\b", gradle):
        raise BuildError("Android SDK 36 required")
    manifest = ET.parse(root / "app/src/main/AndroidManifest.xml")
    android = "{http://schemas.android.com/apk/res/android}"
    application = manifest.getroot().find("application")
    if application is None or application.get(android + "usesCleartextTraffic") != "false":
        raise BuildError("Secure WebView manifest required")
    ET.parse(root / "app/src/main/res/values/strings.xml")
    verify_templates(root, config)
    scan_secrets(root)
    (root / "gradlew").chmod(0o755)
    return config


def prepare(work: Path):
    payload = event_payload()
    parsed = urllib.parse.urlsplit(payload.get("source_url", ""))
    origin = os.environ.get("AIROUI_SOURCE_ORIGIN", "https://airoui.com").rstrip("/")
    if (parsed.scheme != "https" or f"{parsed.scheme}://{parsed.netloc}" != origin or parsed.username or parsed.password or parsed.fragment or
            parsed.path != f"/files/sources/{payload['app_id']}.zip"):
        raise BuildError("Untrusted build source URL")
    query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    if set(query) != {"expires", "signature"}:
        raise BuildError("Private source capability required")
    manifest_url = urllib.parse.urlunsplit(parsed._replace(query=parsed.query + "&format=manifest"))
    manifest = json.loads(download_bytes(manifest_url, 16384))
    if manifest.get("buildId") != payload["app_id"] or not re.fullmatch(r"[a-f0-9]{64}", manifest.get("sourceSha256", "")):
        raise BuildError("Invalid source manifest")
    archive = download_bytes(payload["source_url"], 10 * 1024 * 1024)
    if hashlib.sha256(archive).hexdigest() != manifest["sourceSha256"]:
        raise BuildError("Source SHA-256 mismatch")
    archive_path = work / "source.zip"
    archive_path.write_bytes(archive)
    root = extract_source(archive_path, work / "project")
    verify_project(root, manifest)
    save_json(work / "reservation.json", manifest)
    save_json(work / "project.json", {"root": str(root.resolve())})


def clean_build_env():
    # Checkout tokens and R2 credentials must not reach project-controlled Gradle code.
    allowed = {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "GRADLE_USER_HOME", "CI"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def certificate_fingerprint(output):
    match = re.search(r"(?:certificate SHA-256 digest:|SHA256:)\s*([A-Fa-f0-9:]{64,95})", output)
    if not match:
        raise BuildError("Signing certificate fingerprint not found")
    return match[1].replace(":", "").lower()


def signing_values(config):
    try:
        profiles = json.loads(os.environ.get("AIROUI_SIGNING_PROFILES") or "{}")
    except (ValueError, TypeError):
        raise BuildError("App signing profiles Secret is not valid JSON") from None
    if not isinstance(profiles, dict):
        raise BuildError("App signing profiles Secret must be a JSON object")
    if config["signingProfile"] in profiles:
        profile = profiles[config["signingProfile"]]
    elif os.environ.get("AIROUI_COMMON_SIGNING_PROFILE"):
        try:
            profile = json.loads(os.environ["AIROUI_COMMON_SIGNING_PROFILE"])
        except (ValueError, TypeError):
            raise BuildError("Common release signing Secret is not valid JSON") from None
    else:
        profile = {
            "keystoreBase64": os.environ.get("AIROUI_KEYSTORE_BASE64"),
            "storePassword": os.environ.get("AIROUI_KEYSTORE_PASSWORD"),
            "keyAlias": os.environ.get("AIROUI_KEY_ALIAS"),
            "keyPassword": os.environ.get("AIROUI_KEY_PASSWORD"),
        }
    if not isinstance(profile, dict):
        raise BuildError("Selected signing profile must be a JSON object")
    # Select an entire profile. Never combine passwords/keys from different profiles.
    values = {name: profile.get(name) for name in ["keystoreBase64", "storePassword", "keyAlias", "keyPassword"]}
    if not all(isinstance(value, str) and value for value in values.values()):
        raise BuildError("Release signing secrets are not configured for this app")
    expected = str(config.get("expectedCertificateSha256") or "").lower().replace(":", "")
    declared = str(profile.get("certificateSha256") or "").lower().replace(":", "")
    if expected and declared and expected != declared:
        raise BuildError("Signing certificate differs from the installed app")
    return values


def mask_signing_values(values):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for name in ["keystoreBase64", "storePassword", "keyPassword"]:
            value = values[name].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::add-mask::" + value, flush=True)


def build_project(root: Path, output: Path, expected=None):
    config = verify_project(root, expected)
    env = clean_build_env()
    task = {"apk": "assembleDebug", "release": "assembleRelease", "play": "bundleRelease"}[config["mode"]]
    # Temporary signing material lives outside both the source project and output.
    with tempfile.TemporaryDirectory(prefix="airoui-signing-") as private:
        if config["mode"] != "apk":
            values = signing_values(config)
            mask_signing_values(values)
            keyfile = Path(private) / "upload.jks"
            keyfile.write_bytes(base64.b64decode(values["keystoreBase64"], validate=True))
            keyfile.chmod(0o600)
            env.update(AIROUI_KEYSTORE_PATH=str(keyfile), AIROUI_KEYSTORE_PASSWORD=values["storePassword"], AIROUI_KEY_ALIAS=values["keyAlias"], AIROUI_KEY_PASSWORD=values["keyPassword"])
        command = [str(root / ("gradlew.bat" if os.name == "nt" else "gradlew")), task, "--no-daemon", "--console=plain"]
        subprocess.run(command, cwd=root, env=env, check=True, timeout=600)
    extension = "aab" if config["mode"] == "play" else "apk"
    artifact = root / ("app/build/outputs/bundle/release/app-release.aab" if extension == "aab" else f"app/build/outputs/apk/{'debug' if config['mode'] == 'apk' else 'release'}/app-{'debug' if config['mode'] == 'apk' else 'release'}.apk")
    if not artifact.is_file() or artifact.stat().st_size < 1024:
        raise BuildError("Expected Android artifact is missing")
    with zipfile.ZipFile(artifact) as package:
        packaged_html = package.read(("base/" if extension == "aab" else "") + "assets/www/index.html")
        original_html = (root / "app/src/main/assets/www/index.html").read_bytes()
        if hashlib.sha256(packaged_html).digest() != hashlib.sha256(original_html).digest():
            raise BuildError("Built app does not contain the submitted HTML")
    if extension == "apk":
        sdk = Path(env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT", ""))
        verifier = sdk / "build-tools/36.0.0" / ("apksigner.bat" if os.name == "nt" else "apksigner")
        verified = subprocess.run([str(verifier), "verify", "--verbose", "--print-certs", str(artifact)], env=env, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        fingerprint = certificate_fingerprint(verified.stdout)
    else:
        subprocess.run(["jarsigner", "-verify", str(artifact)], env=env, check=True, capture_output=True)
        cert = subprocess.run(["keytool", "-printcert", "-jarfile", str(artifact)], env=env, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        fingerprint = certificate_fingerprint(cert.stdout)
        with zipfile.ZipFile(artifact) as bundle:
            if not {"BundleConfig.pb", "base/manifest/AndroidManifest.xml"}.issubset(bundle.namelist()):
                raise BuildError("Invalid Android App Bundle structure")
    expected_cert = config.get("expectedCertificateSha256")
    if config["mode"] != "apk" and expected_cert and fingerprint != expected_cert.lower().replace(":", ""):
        raise BuildError("Signing certificate differs from the installed app")
    output.mkdir(parents=True, exist_ok=True)
    target = output / ("artifact." + extension)
    shutil.copyfile(artifact, target)
    save_json(output / "artifact.json", {"status": "ready", "artifactType": extension, "mode": config["mode"], "applicationId": config["applicationId"], "versionCode": config["versionCode"], "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "certificateSha256": fingerprint, "signatureVerified": True})


def publish(work: Path, output: Path):
    payload = event_payload()
    build_id = payload["app_id"]
    bucket = os.environ.get("R2_BUCKET", "airoui-files")
    endpoint = os.environ.get("R2_ENDPOINT", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,62}", bucket) or not re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::443)?", endpoint):
        raise BuildError("Invalid R2 configuration")
    artifact_info = output / "artifact.json"
    ready = os.environ.get("BUILD_STEP_STATUS") == "success" and artifact_info.is_file()
    failure_file = work / "failure.json"
    failure = json.loads(failure_file.read_text()) if failure_file.is_file() else {"error": "Android build or signing verification failed. Check the GitHub Actions run log."}
    status = json.loads(artifact_info.read_text()) if ready else {"status": "failed", **failure}
    aws = shutil.which("aws")
    if not aws:
        raise BuildError("AWS CLI required for R2 upload")
    def transfer(*args):
        subprocess.run([aws, "s3", *args, "--endpoint-url", endpoint, "--only-show-errors"], check=True, timeout=120)
    if ready:
        ext = status["artifactType"]
        transfer("cp", str(output / ("artifact." + ext)), f"s3://{bucket}/builds/{build_id}.{ext}", "--content-type", "application/vnd.android.package-archive" if ext == "apk" else "application/octet-stream")
    report = work / "status.json"
    save_json(report, status)
    transfer("cp", str(report), f"s3://{bucket}/builds/{build_id}.status.json", "--content-type", "application/json", "--cache-control", "private,no-store")
    # Source is not needed after this attempt. Unfinished attempts also expire server-side.
    transfer("rm", f"s3://{bucket}/sources/{build_id}.zip")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "build", "publish"])
    parser.add_argument("--work", type=Path, default=Path("foundry-work"))
    parser.add_argument("--project", type=Path)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    try:
        if args.phase == "prepare":
            prepare(args.work)
        elif args.phase == "build":
            root = args.project or Path(json.loads((args.work / "project.json").read_text())["root"])
            expected = json.loads((args.work / "reservation.json").read_text()) if (args.work / "reservation.json").exists() else None
            build_project(root.resolve(), args.work / "output", expected)
        else:
            publish(args.work, args.work / "output")
    except Exception as error:
        # urllib exceptions may contain capability URLs; subprocess output may contain
        # signing errors. Return controlled messages, never repr(error) or argv/env.
        message = str(error) if isinstance(error, BuildError) else "Foundry phase failed: " + type(error).__name__
        if args.phase != "publish":
            save_json(args.work / "failure.json", {"error": message, "code": "BUILD_SIGNING_REQUIRED" if message.startswith("Release signing secrets") else "BUILD_COMPILE_FAILED"})
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
