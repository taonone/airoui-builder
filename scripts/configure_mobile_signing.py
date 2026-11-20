"""Create a private Android upload key and optionally register a scoped GitHub Secret.

No password, key bytes or token is printed, placed in argv, or written to the project.
--register is an explicit external write; creation alone only writes the private vault.
"""
import argparse
import contextlib
import hashlib
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import venv
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
    pending = directory / 'creation.pending.secrets.json'
    if private.exists() or keystore.exists() or pending.exists():
        raise RuntimeError("A signing key already exists here. It was not overwritten.")
    tool = shutil.which("keytool")
    if not tool:
        raise RuntimeError("JDK 17 keytool must be on PATH")
    password = secrets.token_urlsafe(36)
    # Persist recovery material before keytool writes the key, including if the
    # process is interrupted between key creation and profile serialization.
    save_private_json(pending, {"profile": profile, "storePassword": password, "keyAlias": "airoui-upload", "keyPassword": password})
    env = dict(os.environ, AIROUI_CREATE_KEY_PASSWORD=password)
    subprocess.run([tool, "-genkeypair", "-storetype", "PKCS12", "-keystore", str(keystore), "-alias", "airoui-upload", "-keyalg", "RSA", "-keysize", "3072", "-validity", "10000", "-dname", "CN=Airoui Android Upload Key", "-storepass:env", "AIROUI_CREATE_KEY_PASSWORD", "-keypass:env", "AIROUI_CREATE_KEY_PASSWORD"], env=env, check=True, capture_output=True)
    cert = subprocess.run([tool, "-J-Duser.language=en", "-J-Duser.country=US", "-list", "-v", "-keystore", str(keystore), "-alias", "airoui-upload", "-storepass:env", "AIROUI_CREATE_KEY_PASSWORD"], env=env, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    match = re.search(r"SHA256:\s*([A-Fa-f0-9:]{95})", cert)
    if not match:
        raise RuntimeError("Cannot verify the newly generated certificate")
    fingerprint = match[1].replace(":", "").lower()
    save_private_json(private, {profile: {"keystoreBase64": base64.b64encode(keystore.read_bytes()).decode(), "storePassword": password, "keyAlias": "airoui-upload", "keyPassword": password, "certificateSha256": fingerprint}})
    public = {"profile": profile, "certificateSha256": fingerprint, "keyAlias": "airoui-upload", "keystorePath": str(keystore)}
    save_private_json(directory / 'signing.public.json', public)
    pending.unlink()
    for path in [private, keystore]:
        if os.name != "nt":
            path.chmod(0o600)
    return public


COMMON_PROFILE = "_common"
COMMON_SECRET = "AIROUI_COMMON_SIGNING_PROFILE"
LEGACY_SECRETS = ["AIROUI_KEYSTORE_BASE64", "AIROUI_KEYSTORE_PASSWORD", "AIROUI_KEY_ALIAS", "AIROUI_KEY_PASSWORD"]


def save_private_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    if os.name != 'nt':
        temporary.chmod(0o600)
    temporary.replace(path)


@contextlib.contextmanager
def setup_lock(directory):
    lock_directory(directory)
    with (directory / '.setup.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('다른 서명키 설정이 진행 중입니다. 완료 후 다시 실행해주세요.') from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def verify_profile(value):
    names = ['keystoreBase64', 'storePassword', 'keyAlias', 'keyPassword']
    if not isinstance(value, dict) or not all(isinstance(value.get(name), str) and value[name] for name in names):
        raise RuntimeError('서명 프로필의 키·비밀번호·별칭 설정을 확인해주세요.')
    tool = shutil.which('keytool')
    if not tool:
        raise RuntimeError('JDK 17 keytool must be on PATH')
    with tempfile.TemporaryDirectory(prefix='airoui-key-check-') as temporary:
        folder = Path(temporary); lock_directory(folder)
        keystore = folder / 'verify.jks'
        keystore.write_bytes(base64.b64decode(value['keystoreBase64'], validate=True))
        if os.name != 'nt': keystore.chmod(0o600)
        env = dict(os.environ, AIROUI_VERIFY_STORE_PASSWORD=value['storePassword'], AIROUI_VERIFY_KEY_PASSWORD=value['keyPassword'])
        args = [tool, '-J-Duser.language=en', '-J-Duser.country=US']
        try:
            result = subprocess.run(args + ['-list', '-v', '-keystore', str(keystore), '-alias', value['keyAlias'], '-storepass:env', 'AIROUI_VERIFY_STORE_PASSWORD'], env=env, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
            # A certificate lookup alone does not verify access to the private key.
            subprocess.run(args + ['-certreq', '-keystore', str(keystore), '-alias', value['keyAlias'], '-storepass:env', 'AIROUI_VERIFY_STORE_PASSWORD', '-keypass:env', 'AIROUI_VERIFY_KEY_PASSWORD'], env=env, check=True, capture_output=True)
        except subprocess.CalledProcessError:
            raise RuntimeError('서명키의 비밀번호 또는 별칭이 일치하지 않습니다.') from None
        match = re.search(r'SHA256:\s*([A-Fa-f0-9:]{95})', result.stdout)
        if not match: raise RuntimeError('서명 인증서를 확인할 수 없습니다.')
        fingerprint = match[1].replace(':', '').lower()
        expected = str(value.get('certificateSha256') or '').replace(':', '').lower()
        if expected and expected != fingerprint:
            raise RuntimeError('보관된 키와 인증서 지문이 다릅니다. 기존 파일을 확인해주세요.')
        return fingerprint


class GitHubSecrets:
    def __init__(self, repo):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise RuntimeError('Invalid GitHub repository')
        result = subprocess.run(['git', '-c', 'credential.interactive=never', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n', capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if not fields.get('password'): raise RuntimeError('GitHub git 로그인이 필요합니다.')
        self.prefix = 'https://api.github.com/repos/' + repo + '/actions/secrets'
        self.headers = {'Authorization': 'Bearer ' + fields['password'], 'User-Agent': 'Airoui-Signing-Setup', 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'}
        self.public_key = None

    def request(self, path='', method='GET', payload=None):
        request = urllib.request.Request(self.prefix + path, data=json.dumps(payload).encode() if payload is not None else None, headers=self.headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b'{}')

    def ensure_access(self):
        self.public_key = self.request('/public-key')

    def get_secret(self, name):
        try: return self.request('/' + name)
        except urllib.error.HTTPError as error:
            if error.code == 404: return None
            raise RuntimeError('GitHub Secret 설정을 읽을 수 없습니다. 권한을 확인해주세요.') from None

    def put_secret(self, name, value):
        from nacl.public import PublicKey, SealedBox
        if self.public_key is None: self.ensure_access()
        if self.get_secret(name) is not None:
            raise RuntimeError('기존 GitHub Secret은 덮어쓰지 않습니다.')
        encrypted = SealedBox(PublicKey(base64.b64decode(self.public_key['key']))).encrypt(value)
        self.request('/' + name, 'PUT', {'encrypted_value': base64.b64encode(encrypted).decode(), 'key_id': self.public_key['key_id']})


def register_secret(repo, directory, common=False, api=None):
    api = api or GitHubSecrets(repo)
    api.ensure_access()
    private = json.loads((directory / 'signing.secrets.json').read_text(encoding='utf-8'))
    value = private[COMMON_PROFILE] if common else private
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(raw).hexdigest()
    name = COMMON_SECRET if common else 'AIROUI_SIGNING_PROFILES'
    receipt_path = directory / 'signing.registration.json'
    existing = api.get_secret(name)
    if existing:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
        if receipt.get('repo') == repo and receipt.get('secret') == name and receipt.get('payloadSha256') == digest and receipt.get('updatedAt') == existing.get('updated_at'):
            return 'already-registered'
        raise RuntimeError('GitHub에 서명 Secret이 이미 있습니다. 값을 보존했으며 덮어쓰지 않았습니다. 기존 키 백업과 등록 기록을 확인해주세요.')
    api.put_secret(name, raw)
    metadata = api.get_secret(name)
    if not metadata: raise RuntimeError('Secret 등록 응답을 확인할 수 없습니다. 키는 보관했으므로 새 키를 만들지 말고 다시 확인해주세요.')
    save_private_json(receipt_path, {'repo': repo, 'secret': name, 'payloadSha256': digest, 'updatedAt': metadata.get('updated_at')})
    return 'registered'


def setup_profile(repo, directory, profile, common=False, api=None):
    api = api or GitHubSecrets(repo)
    api.ensure_access()  # Check repository access before creating any key.
    name = COMMON_SECRET if common else 'AIROUI_SIGNING_PROFILES'
    existing = api.get_secret(name)
    if not existing and common and any(api.get_secret(name) for name in LEGACY_SECRETS):
        raise RuntimeError('기존 공통 서명 설정이 있습니다. 기존 키를 보존했으며 새 키를 만들지 않았습니다.')
    private = directory / 'signing.secrets.json'
    if not private.exists():
        if existing: raise RuntimeError('GitHub 공통 서명키가 이미 있습니다. 새 키를 만들지 않았습니다. 기존 개인 보관 폴더를 복원해주세요.')
        public = create_key(profile, directory)
    else:
        public = json.loads((directory / 'signing.public.json').read_text(encoding='utf-8'))
    values = json.loads(private.read_text(encoding='utf-8'))
    fingerprint = verify_profile(values[profile])
    if fingerprint != public['certificateSha256']: raise RuntimeError('공개 인증서 정보와 개인 키가 일치하지 않습니다.')
    return public, register_secret(repo, directory, common=common, api=api)


def bootstrap_nacl():
    try:
        from nacl.public import PublicKey, SealedBox
        return None
    except ImportError:
        tool_directory = Path.home() / '.codex' / 'tools' / 'airoui-signing'
        environment = tool_directory / 'venv'
        executable = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not executable.exists(): venv.EnvBuilder(with_pip=True).create(environment)
        probe = subprocess.run([str(executable), '-c', 'import nacl.public'], capture_output=True)
        if probe.returncode != 0:
            subprocess.run([str(executable), '-m', 'pip', 'install', '--disable-pip-version-check', 'PyNaCl==1.6.2'], check=True)
        return executable


def main(argv=None):
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--profile', help='Fixed Android applicationId')
    choice.add_argument('--common', action='store_true', help='One common release upload key for apps without individual profiles')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--create', action='store_true')
    action.add_argument('--register', action='store_true')
    action.add_argument('--setup', action='store_true', help='Create or reuse the key and register once; safe to run again')
    action.add_argument('--verify-env', action='store_true', help='CI: verify the configured common key without exposing its value')
    parser.add_argument('--repo', default='taonone/airoui-builder')
    args = parser.parse_args(argv)
    if args.profile and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*){2,}', args.profile): parser.error('Invalid applicationId')
    try:
        if args.verify_env:
            if not args.common: raise RuntimeError('--verify-env requires --common')
            raw = os.environ.get(COMMON_SECRET)
            if not raw: raise RuntimeError('공통 출시 서명 Secret이 등록되지 않았습니다.')
            fingerprint = verify_profile(json.loads(raw))
            print('Common signing key verified. Certificate SHA-256:', fingerprint)
            return 0
        if args.setup or args.register:
            executable = bootstrap_nacl()
            if executable:
                return subprocess.run([str(executable), str(Path(__file__).resolve()), *(sys.argv[1:] if argv is None else argv)], check=False).returncode
        profile = COMMON_PROFILE if args.common else args.profile
        directory = Path.home() / '.codex' / 'secrets' / 'airoui-android' / profile
        with setup_lock(directory):
            if args.setup:
                public, state = setup_profile(args.repo, directory, profile, common=args.common)
            else:
                public = create_key(profile, directory) if args.create else json.loads((directory / 'signing.public.json').read_text(encoding='utf-8'))
                state = register_secret(args.repo, directory, common=args.common) if args.register else 'local-only'
        print('개인 키 보관 폴더:', directory)
        print('인증서 SHA-256:', public['certificateSha256'])
        print({'registered': 'GitHub 서명 Secret 등록 완료.', 'already-registered': '이미 등록되어 있습니다. 기존 키를 그대로 사용합니다.', 'local-only': '개인 키 보관 완료. GitHub Secret은 변경하지 않았습니다.'}[state])
        if args.common: print('개별 키가 없는 앱은 공통 키로 출시 APK/AAB를 빌드합니다.')
        print('개인 보관 폴더 전체를 안전한 별도 저장소에 백업해주세요.')
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else '서명키 설정 실패: ' + type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
