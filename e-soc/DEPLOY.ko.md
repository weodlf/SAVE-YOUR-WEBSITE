# E-SOC 공개 배포 준비

이 패키지는 배포 준비 파일입니다. 아직 호스팅 서비스에 업로드하거나 공개 URL을 생성하지 않았습니다.

## 홈페이지와 콘솔 함께 배포

Docker를 지원하는 호스팅 서비스에서 이 폴더를 빌드 루트로 사용하세요.

- Dockerfile: `Dockerfile`
- 서비스 포트: 공급자가 지정한 `PORT` 환경변수 (기본 8080)
- 헬스체크: `/healthz`
- 홈페이지: `/`
- 보안 콘솔: `/console`
- 영구 저장 디스크 마운트: `/data` (실행 UID 10001이 쓰기 가능해야 함)
- 인스턴스 수: 1
- HTTPS와 WebSocket 지원 필요

호스팅 서비스의 비밀 환경변수 설정에서 `SECRET_KEY`와 `ADMIN_TOKEN`을 각각 서로 다른 32자 이상의 임의 값으로 지정하세요. 콘솔에서는 ADMIN_TOKEN을 사용합니다. 값은 소스 코드와 채팅에 넣지 마세요. `APP_ENV=production`에서는 누락된 비밀 값이 있으면 시작을 거부합니다.

`DATABASE_URL=sqlite:////data/esoc.db`, `COOKIE_SECURE=1`, `FIREWALL_ENABLED=0`은 Docker 이미지에 기본 설정되어 있습니다. 허가된 점검 대상만 `SCAN_ALLOWLIST` CIDR 목록으로 추가하세요. 컨테이너 호스팅에서는 OS 방화벽 기능을 활성화하지 마세요. 컨테이너는 관리자 권한 없이 실행됩니다.

예를 들어 Render에서는 Docker Web Service로 만들고 위 환경변수, 헬스체크, 영구 디스크를 설정합니다. 영구 디스크가 필요한 요금제와 비용은 계정에서 확인해야 합니다. 파일 기반 DB를 쓰는 이 버전은 디스크 없는 재시작·재배포 시 기록 보존을 보장하지 않습니다.

## 소개 홈페이지 파일만 보기

`homepage-preview.html`은 스타일이 포함된 독립 파일입니다. 인터넷 공개 없이 로컬 브라우저에서 열 수 있습니다. 이 파일에는 실제 보안 API가 포함되지 않으며, 콘솔 연결 안내를 표시합니다.

## 배포 후 확인

1. 발급된 HTTPS 주소의 `/`와 `/healthz` 응답을 확인합니다.
2. `/console`에 운영자 토큰으로 로그인합니다.
3. 모의 검사 한 번으로 실시간 이벤트 수신을 확인합니다.
4. 재배포 후 기존 기록이 유지되는지 확인합니다.

공개 URL은 실제 호스팅 배포가 성공한 뒤에만 제공할 수 있습니다. 작업 환경의 localhost 주소는 외부 접속 주소가 아닙니다.

## 개인 서버: Docker Compose + HTTPS

준비된 `compose.yaml`은 Caddy를 통해 HTTPS와 WebSocket을 처리하고 SQLite 및 TLS 인증서를 Docker 볼륨에 보존합니다. 앱 포트 8080은 호스트에 공개하지 않습니다. OS 방화벽 실행은 비활성화되어 있습니다. Docker Engine과 Compose v2가 설치된 서버를 사용하세요.

1. 사용할 도메인의 A/AAAA 레코드를 해당 서버로 연결하세요. 서버에 연결되지 않는 AAAA 레코드는 제거하세요.
2. 서버 보안그룹과 방화벽에서 TCP 80/443을 허용하세요. 다른 웹서버가 같은 포트를 쓰면 기존 리버스 프록시와 연동해야 하므로 먼저 확인하세요.
3. 프로젝트 파일을 서버에 복사하고 프로젝트 폴더에서 다음을 실행하세요. 도메인은 실제 사용할 이름으로 바꿉니다.

```bash
python3 deploy/configure.py soc.example.com
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

`configure.py`는 0600 권한의 `.env`를 생성하며 기존 파일은 덮어쓰지 않습니다. `ADMIN_TOKEN`은 서버에서 소유자만 접근 가능한 편집기로 확인한 후 콘솔에 입력하세요. `docker compose config`는 비밀값이 출력될 수 있으므로 반드시 `--quiet`를 사용하고 설정 전문을 공유하지 마세요.

정상 배포 후 `https://도메인/`과 `https://도메인/console`에 접속합니다. Caddy가 인증서를 발급하려면 도메인 DNS와 80/443 도달성이 준비되어 있어야 합니다. 도메인이 없으면 먼저 접속 방식과 TLS 구성을 정해야 합니다.

Docker 소켓은 앱에 전달되지 않습니다. 컨테이너 안의 127.0.0.1은 컨테이너 자신이며 호스트 서버가 아닙니다. 실제 점검 대상은 명시적으로 승인한 CIDR만 설정하세요. 리버스 프록시 뒤의 검사 API는 현재 실제 방문자 IP를 구분하지 못하므로, 실제 트래픽 자동 차단에 연동하기 전 신뢰할 수 있는 클라이언트 식별을 구현해야 합니다.

데이터를 유지한 중지: `docker compose down`. **`down -v`는 영구 볼륨도 삭제하므로 사용하지 마세요.** 백업은 일관된 SQLite 스냅샷과 `.env`, 볼륨 보존을 포함해야 합니다. 기존 보안 기록이 있는 프로젝트를 옮길 때는 별도 데이터 이전이 필요하며 이 구성은 빈 새 볼륨에서 시작합니다.

## Render 자동 설정으로 시작하기

저장소 루트 `render.yaml`에 E-SOC Free 체험 배포 설정을 포함했습니다. 다음 주소에서 Render Blueprint 배포를 시작할 수 있습니다.

https://render.com/deploy?repo=https://github.com/weodlf/SAVE-YOUR-WEBSITE

Render 계정에서 저장소 연결 및 Blueprint 생성을 완료해야 실제 서비스가 만들어집니다. 이 링크 자체는 실행 중인 사이트 주소가 아닙니다. 기존에 수동으로 만든 서비스가 있다면 중복 생성하지 않도록 확인하세요.

비밀키 두 개는 Render가 자동으로 생성합니다. 콘솔 로그인용 ADMIN_TOKEN은 배포 후 서비스의 Environment 설정에서 직접 확인하세요. Free 서비스는 유휴 시 중지되고 재시작·재배포 시 SQLite 기록이 사라질 수 있습니다. 영구 기록이 필요하면 요금제와 디스크를 별도로 설정해야 합니다.

서비스가 Live가 되면 Render가 발급한 HTTPS 주소에서 홈페이지를 확인하세요. 사용자 도메인은 해당 서비스의 Settings → Custom Domains에서 추가하고 Render가 표시하는 DNS 레코드를 도메인 관리 사이트에 설정한 뒤 연결 상태를 확인해야 합니다.
