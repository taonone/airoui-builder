# Mobile Foundry v3 — 요청한 7개 개선과 운영 적용

## 구현 내용

| 요청 | 적용 내용 |
| --- | --- |
| 1. 기능 보존형 모바일 변환 | DOM 파싱, 대문자·속성 있는 head·HTML 조각 지원, 이전 Foundry 주입만 교체, 변환 중복 방지. Canvas·SVG·video 크기와 원본 스크립트·터치 이벤트를 강제로 변경하지 않습니다. 새 viewport는 확대를 금지하지 않습니다. |
| 2. 호환성 검사 | 큰 고정 너비·최소 너비, HTTP, 외부 자원, 키보드 조작, Canvas/WebGL, 비밀키 의심 패턴 검사. 점수를 정적 검사로 표시합니다. 360/390/768px의 격리 화면 검사 버튼은 넘침·작은 조작 영역·스크립트 오류·차단된 요청 수를 보여줍니다. |
| 3. Android 프로젝트 생성 | 소스 누락 시 중단, XML/Gradle 문자열 처리, 유효한 앱 ID, 원본 HTML 패키징. 실제 gradlew/gradlew.bat/JAR 포함 및 SHA-256 검증. JDK 17 / Gradle 8.13 / AGP 8.13.2 / SDK 36 조합을 사용합니다. |
| 4. 앱 ID·버전·서명 관리 | D1에 상품별 앱 ID를 저장합니다. 이름을 바꿔도 ID는 유지하며 versionCode는 원자적으로 증가합니다. 기존 ID·마지막 버전·인증서 지문을 처음 등록할 수 있고, 첫 출시 인증서는 이후 결과와 대조합니다. 출시 키는 앱별 GitHub Secret에서만 읽습니다. |
| 5. WebView 보안 | HTTPS WebViewAssetLoader, 파일·콘텐츠 접근 차단, 혼합 콘텐츠·HTTP 차단. 외부 HTTPS/전화/메일 이동은 사용자 조작으로 별도 앱에서만 열며 임의 스킴은 차단합니다. 디버깅은 디버그 APK에만 허용합니다. |
| 6. 소스 보호 | 관리자 업로드, AES-GCM 암호화 저장, 만료 30분인 다운로드 권한, ZIP 해시 검증, 공개 소스 경로 차단. CI에서는 출처·ZIP 경로·심볼릭 링크·파일 수·해제 크기·비밀키 패턴을 검사합니다. 완료 후 소스를 삭제하고, 미완료 소스는 24시간 후 접근이 만료됩니다. |
| 13. Google Play 모드 | 테스트 APK / 서명된 출시 APK / Google Play AAB 선택. AAB는 bundleRelease와 지속되는 출시 키를 사용하며 서명이 없으면 실패합니다. 실제 파일과 검증된 결과 메타데이터가 있어야 완료로 표시합니다. |

PWA 오프라인 기능 전체 완성, 외부 자산 자동 수집, Play Console 자동 업로드는 이번 7개 항목의 구현 범위가 아닙니다.

## 실제 빌더 저장소에서 바꿀 파일

빌더 변경은 [PR #1](https://github.com/taonone/airoui-builder/pull/1)의 별도 브랜치에 준비했습니다. 해당 브랜치의 GitHub `Builder security tests`도 통과했습니다. 운영 main 병합과 사이트 배포는 별도입니다.

대상 저장소는 `taonone/airoui-builder`입니다. 실행되는 파일은 **`.github/workflows/build-apk.yml`**입니다. 사용자가 참고로 제공한 **`.github/apk_build.yml`은 workflows 폴더 밖에 있어 자동 실행 workflow가 아닙니다.** 그 파일의 Capacitor/src 압축 해제 구조를 현재 Native Android 프로젝트 ZIP에 적용하면 안 됩니다.

- `.github/workflows/build-apk.yml`: 이 작업 폴더의 `builder-integration/build-apk.yml`과 같은 내용
- `scripts/mobile_build.py`: 이 작업 폴더의 동일 파일
- `tests/test_mobile_build.py`: 빌더 보안 회귀 테스트

기존 `repository_dispatch: build_apk`, 표준 `ubuntu-latest`, Java 17, 15분 제한, R2 Secret 3개와 `builds/{빌드ID}` 결과 경로를 유지합니다. Node/Capacitor는 이 Native Android ZIP 빌드에 필요하지 않습니다. 외부 입력은 셸에 삽입하지 않고 GitHub event JSON을 Python으로 읽습니다. 다운로드 권한 URL과 Secret은 로그에 출력하지 않습니다.

`mobile_build.py`는 검토한 Gradle 설정·Wrapper·MainActivity 템플릿의 해시도 확인합니다. 별도 Gradle 코드·buildSrc·임의 네이티브 코드는 거부합니다. HTML은 앱 자산으로 포함되며 빌드 중 실행하지 않습니다. R2/GitHub 자격 증명은 Gradle 자식 프로세스에 전달하지 않고, 서명 파일은 프로젝트 밖 임시 디렉터리에서 사용 후 삭제합니다.

## 적용 순서

서로 맞물리는 v3 변경이므로 한쪽만 운영에 적용하지 마세요.

1. 진행 중인 빌드를 마친 후 잠시 새 빌드 실행을 중지합니다.
2. 빌더 저장소의 위 workflow와 Python runner를 함께 반영합니다. 새 runner는 v3 ZIP을 검사하므로 이전 HTML에서 생성한 프로젝트를 재사용하지 않습니다.
3. 현재 작업 폴더의 **worker.js와 index.html을 함께 배포**합니다. DB·MY_BUCKET·기존 인증 Secret과 전용 빌드 Worker 연결은 유지합니다. 별도 프런트 자산 배포 없이 Wrapper가 index.html에 포함되어 있습니다.
4. 강력 새로고침 후 관리자 계정으로 로그인합니다. Mobile Foundry에서 앱 ID·최종 versionCode·모드를 확인하고 **테스트 APK**로 한 번 검증합니다.
5. 아래 절차로 출시 키를 등록한 뒤 **서명된 출시 APK** 또는 **Google Play 출시 AAB**를 실행합니다.

전용 빌드 Worker가 새 `sourceUrl` 필드를 모르는 경우에도 기존 `fileKey`에 서버가 생성한 만료 쿼리를 붙여 기존 URL 생성 방식으로 전달합니다. 전용 Worker는 쿼리를 제거하면 안 됩니다. 직접 GitHub dispatch 경로는 기존 GITHUB_BUILDER_REPO/GITHUB_PAT가 모두 설정되어 있을 때 사용하며, 암호화된 R2 원본은 R2 공개 주소가 아닌 airoui.com의 소스 전달 API로 내려받습니다.

## 새 출시 키 만들기 — 기존 키가 없다고 답한 경우

현재 GitHub 저장소에는 R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT만 있는 것을 확인했습니다. **운영 키와 GitHub Secret을 이 작업에서 임의로 생성·등록하지는 않았습니다.** 아래 도구는 생성과 등록을 분리합니다.

1. Mobile Foundry에 표시되는 **앱 ID**를 복사합니다. 예: `com.airoui.my_app`. 이미 설치한 앱이 있다면 그 앱의 실제 applicationId와 일치시켜야 합니다.
2. 이 작업 폴더에서 아래 명령을 실행합니다. 예시 ID를 실제 값으로 바꿉니다.

```powershell
python scripts/configure_mobile_signing.py --profile com.airoui.my_app --create
```

JDK 17 keytool로 새 RSA 3072비트 업로드 키와 무작위 비밀번호를 생성합니다. 보관 위치는 프로젝트 밖의 다음 개인 폴더입니다.

```text
%USERPROFILE%\.codex\secrets\airoui-android\com.airoui.my_app\
  upload.jks
  signing.secrets.json
  signing.public.json
```

Windows에서는 현재 사용자와 SYSTEM만 접근하도록 폴더 ACL을 설정합니다. 화면에는 보관 경로·공개 인증서 SHA-256만 표시하며 비밀번호와 키 본문은 출력하지 않습니다. 기존 키를 덮어쓰지 않습니다. **개인 폴더 전체를 안전한 별도 저장소에 백업한 뒤 다음 단계로 진행합니다.** 채팅·Git 저장소·공개 R2에 올리지 마세요.

3. GitHub에 인증된 git 자격 증명이 있는 환경에서 다음 명령으로 암호화 등록합니다. PyNaCl은 GitHub의 공개키 암호화에 사용합니다.

```powershell
python -m pip install PyNaCl
python scripts/configure_mobile_signing.py --profile com.airoui.my_app --register --repo taonone/airoui-builder
```

생성한 앱별 프로필을 **AIROUI_SIGNING_PROFILES**라는 GitHub Actions Secret으로 등록합니다. 토큰·비밀번호·Base64 키를 명령줄 인수에 넣지 않습니다. Secret이 이미 있으면 덮어쓰기를 거부합니다. 여러 앱 프로필은 기존 비공개 프로필 맵을 보존해 검토한 뒤 합쳐야 합니다.

4. 생성 도구가 출력한 공개 인증서 SHA-256을 Foundry의 인증서 입력에 넣으면 첫 출시부터 기대 지문을 확인할 수 있습니다. 입력하지 않아도 첫 검증된 출시 결과의 지문을 DB에 고정합니다.
5. AAB 빌드 후 Google Play Console에 AAB를 업로드하고 Play App Signing을 설정합니다. AAB는 스마트폰에 직접 설치하는 파일이 아닙니다. 테스트하려면 출시 APK 또는 Play 내부 테스트 트랙을 사용합니다.

처음 APK를 만들던 기존 workflow는 assembleDebug를 실행했습니다. 그 디버그 서명과 새 출시 키는 다를 수 있습니다. **기존 테스트 앱에 새 출시 APK를 덮어 설치할 수 없으면 먼저 앱 데이터를 내보내거나 백업해야 합니다.** 이 도구가 기존 앱을 삭제하거나 데이터를 지우지는 않습니다. Google Play의 업로드 키와 실제 배포 앱 서명키도 구분해 보관해야 합니다.

## 소스 보관과 원본 기능 관련 설정

- source capability는 30분 후 만료됩니다. 큐에서 너무 오래 기다려 만료됐다면 원본을 다시 생성해 새 빌드로 실행합니다.
- CI는 완료·실패 상태를 R2에 게시한 뒤 해당 시도의 암호화 소스를 삭제합니다. 미완료 소스는 24시간 후 전달 API가 거부합니다.
- 기존 Worker scheduled 처리에 만료 소스 정리를 추가했습니다. Cron이 없다면 매시간 Cron을 추가하거나 R2 Lifecycle에 `sources/` 접두사 1일 만료 규칙을 설정하세요. 정리 커서를 DB에 보관하므로 매 실행 200개씩 순회합니다. 접근 만료와 물리 삭제 시점은 구분됩니다.
- 암호화 키는 기존 서버 JWT Secret에서 별도 용도로 파생합니다. 서버 키를 교체하면 대기 중 원본은 재생성이 필요합니다.
- HTTP 리소스는 HTTPS로 전환해야 합니다. 외부 API가 WebView 앱의 HTTPS origin을 지원해야 하지만, 이를 이유로 관리자 API의 CORS를 전체 허용하지 마세요.
- 격리 화면 검사는 sandbox/CSP로 외부 리소스·API·폼·팝업을 제한합니다. 자체 프레임 이동 등 모든 네트워크 동작이나 무한 루프를 차단하는 악성코드 분석 환경은 아니므로 본인이 신뢰하는 앱 소스를 검사하는 용도입니다. 외부 라이브러리·API가 필요한 앱은 차단 개수를 보고 별도로 검증해야 합니다. 실기기 GPU·배터리·터치 성능을 인증하지 않습니다. 비밀키 검사도 알려진 의심 패턴 검사이며 모든 형태의 비밀을 찾아낸다고 보장하지 않습니다.

## 검증

전체 JavaScript/브라우저/Workers 테스트 103개와 Python 테스트 10개가 통과했습니다. 실제 workerd에서 D1 버전 예약, R2 암호화 저장, 만료 권한을 통한 복호화 전달도 확인했습니다. 두 workflow는 actionlint 검사를 통과했습니다.

실제 생성한 프로젝트를 Android SDK 36 / JDK 17 환경에서 컴파일했습니다. 테스트 APK, 서명된 출시 APK, AAB 모두 빌드에 성공했고 apksigner/jarsigner 검증을 통과했습니다. 출시 APK와 AAB는 동일한 지속 검증 프로필의 인증서로 확인했으며, APK/AAB 내부 HTML이 제출한 원본과 같은지도 바이트 해시로 대조했습니다. 특수문자·한글 이름도 사용했습니다. 운영 서명키를 사용한 빌드, 실제 휴대폰 설치, Google Play 심사는 이 검증에 포함하지 않습니다.

```powershell
node --test tests/admin-member-delete.test.mjs tests/auth-cache.test.mjs tests/auth-security.test.mjs tests/signup-ui.test.mjs tests/cloud-build-ui.test.mjs tests/cloud-build-runtime.test.mjs tests/mobile-foundry.test.mjs
python -m unittest discover -s tests -p 'test_*.py' -v
```

브라우저/서버 테스트 의존성은 AIROUI_SECURITY_TEST_DEPS 경로의 node_modules에서 읽을 수 있습니다. playwright, jszip, miniflare 및 기존 Worker 의존성이 필요합니다. AIROUI_TEST_CHROMIUM으로 검증용 Chromium 경로를 지정할 수 있습니다.

공식 근거: [AGP 8.13 도구 호환성](https://developer.android.com/build/releases/agp-8-13-0-release-notes), [WebViewAssetLoader](https://developer.android.com/reference/androidx/webkit/WebViewAssetLoader), [앱 서명](https://developer.android.com/studio/publish/app-signing), [Google Play API 요구사항](https://support.google.com/googleplay/android-developer/answer/11926878?hl=en).
