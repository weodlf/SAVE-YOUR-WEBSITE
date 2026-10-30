# E-SOC 배포

현재 버전은 로그인 없이 모든 실제 운영 기능을 공개합니다. 모든 방문자가 공유 기록을 내보내거나 삭제하고, 승인된 대상을 점검하고, 차단 목록을 변경할 수 있습니다. 세션 CSRF 검증과 요청 횟수 제한은 유지됩니다.

## Render

저장소 루트 `render.yaml`의 Blueprint를 사용합니다. 기존 연결 서비스는 main 브랜치 변경 시 자동 배포됩니다.

- Root Directory: `e-soc`
- Runtime: Docker
- Dockerfile Path: `./Dockerfile`
- Health Check Path: `/healthz`
- 비밀 환경변수: `SECRET_KEY` (32자 이상, Blueprint 자동 생성)
- 홈페이지: `/`
- 전체 기능 콘솔: `/console`

이전 배포에 남은 ADMIN_TOKEN 값은 사용되지 않으며 삭제해도 됩니다. `COOKIE_SECURE=1`, `APP_ENV=production`, `FIREWALL_ENABLED=0`은 유지하세요. Render가 TLS와 WebSocket을 처리합니다. `SCAN_ALLOWLIST`에 허가된 대상 CIDR만 추가하세요. 기본값은 컨테이너 자기 자신입니다.

Free 요금제에서는 유휴 중지와 재시작·재배포 시 기록 손실이 있을 수 있습니다. 보존이 필요하면 영구 디스크를 지원하는 요금제를 선택하고 `/data`에 마운트하세요. 실행 사용자 UID 10001이 쓰기 가능해야 합니다. 인스턴스 수는 1로 유지합니다.

Live 상태가 되면 Render가 표시한 HTTPS 주소를 사용하세요. 사용자 도메인은 Settings → Custom Domains에서 추가하고 표시된 DNS 레코드를 도메인 관리 사이트에 입력해야 합니다. 발급되지 않은 주소를 임의로 가정하지 마세요.

## 개인 서버

Docker Engine과 Compose v2를 사용합니다. 실제 사용할 도메인의 A/AAAA 레코드를 서버에 연결하고 TCP 80/443 도달성을 확인하세요. 기존 웹서버가 포트를 사용하는 경우 먼저 리버스 프록시 구성을 맞춰야 합니다.

```bash
python3 deploy/configure.py soc.example.com
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

도메인을 실제 주소로 바꿉니다. configure.py는 0600 권한의 `.env`에 DOMAIN과 세션 비밀키를 생성하며 기존 파일을 덮어쓰지 않습니다. 운영자 토큰은 만들지 않습니다. `docker compose config`는 비밀키가 출력될 수 있으니 `--quiet`로 검증하세요.

Caddy가 HTTPS 인증서와 WebSocket을 처리합니다. SQLite 데이터와 TLS 인증서는 별도 Docker 볼륨에 보존합니다. 앱은 비관리자 사용자로 실행되고 앱 포트 8080은 호스트에 공개되지 않습니다. Docker 소켓은 앱에 전달하지 않습니다. OS 방화벽 실행은 비활성 상태입니다.

기록을 유지한 중지: `docker compose down`. `down -v`는 영구 볼륨도 삭제하므로 사용하지 마세요. 백업에는 일관된 SQLite 스냅샷과 `.env`, 데이터 볼륨이 포함되어야 합니다. 기존 다른 경로의 DB는 자동 이전되지 않습니다.

## 배포 후 확인

1. `/`와 `/healthz` 응답을 확인합니다.
2. `/console`에서 로그인 없이 실제 데이터와 차단 목록을 읽는지 확인합니다.
3. 모의 검사로 실제 공유 기록 및 실시간 이벤트 갱신을 확인합니다.
4. 허가된 대상만 점검하고 결과를 확인합니다.
5. 영구 디스크를 사용하는 경우 재배포 후 기록 보존을 확인합니다.

Render/프록시 뒤에서는 REMOTE_ADDR가 프록시 주소일 수 있습니다. 실제 방문자 기반 자동 차단을 연동하기 전 신뢰할 수 있는 클라이언트 식별 구성이 필요합니다. 수동 IP 차단 목록과 OS 방화벽 성공 여부는 별개입니다.
