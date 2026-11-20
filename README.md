# airoui-builder
airoui-builder new


## Mobile Foundry v3

실행 workflow는 `.github/workflows/build-apk.yml`입니다. Native Android ZIP을 검증하고 테스트 APK, 서명된 출시 APK, Google Play AAB를 생성합니다. `.github/apk_build.yml`의 Capacitor 예제는 실행되는 workflow가 아닙니다.

운영에는 메인 사이트의 v3 Worker/HTML과 함께 적용해야 합니다. [적용 및 새 서명키 설정 안내](docs/MOBILE_FOUNDRY_V3.ko.md)를 확인하세요.


## 공통 출시 서명키

`공통서명키설정.cmd`를 한 번 실행하면 개별 키가 없는 앱의 AAB/출시 APK에 공통 업로드 키를 사용합니다. 같은 키를 재사용하며 기존 앱별 프로필을 우선합니다. GitHub에는 `AIROUI_COMMON_SIGNING_PROFILE` Secret 한 개로 암호화 등록합니다. [핵심 사용법](docs/공통서명키사용법.txt)을 확인하세요.
