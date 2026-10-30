# Enterprise Security Operations Center (E-SOC)

Flask / Socket.IO 기반의 한국어 보안 점검·관제 웹사이트입니다. 홈페이지, 초보자 모드, 동시 TCP/HTTP 점검, 입력 패턴 검사, 공유 차단 목록, SQLite 기록과 JSON 내보내기를 제공합니다.

## 로그인 없이 사용

- `/`: 소개 홈페이지
- `/my-ip`: 접속한 본인만 보는 공개 IP·추정 위치 조회 페이지 (조회 버튼을 누르면 외부 IP 서비스에 요청)
- `/console`: 실제 점검·차단 관리·모의 검사·공유 기록 삭제를 포함하는 통합 콘솔
- `/operator`: 기존 주소 호환용 `/console` 리디렉션
- `/healthz`: 상태 확인

로그인, 운영자 토큰, 방문자별 권한 구분은 없습니다. 모든 방문자가 동일한 실제 기록과 차단 목록을 보고 수정할 수 있습니다. 삭제 확인창은 모든 방문자의 공유 로그가 삭제된다는 사실을 표시합니다. 모의 검사도 서버 기록에 저장되며, 브라우저 새로고침 후 다시 표시됩니다. 실제 점검과 모의 기록은 `simulated` 필드와 화면의 `[모의]` 표시로 구분합니다.

## 실행

```bash
cd e-soc
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python app.py
```

http://127.0.0.1:5000/console 에서 바로 사용할 수 있습니다. `.env.example`은 설정 참고 파일이며 Python 앱 자체는 .env를 자동으로 로드하지 않습니다. Docker Compose는 .env를 읽습니다.

## API

먼저 `GET /api/state`로 세션 쿠키와 `csrf` 값을 받습니다. 같은 쿠키를 유지하고 변경 요청에 `X-CSRF-Token` 헤더로 값을 전달하세요. 이는 자동으로 준비되는 요청 검증 값이며 로그인이나 운영자 비밀키가 아닙니다. 다른 사이트가 방문자의 브라우저를 통해 변경 요청을 보내는 것을 방지합니다. 같은 출처 Socket.IO 연결은 인증 없이 실제 이벤트를 수신합니다.

| 메서드 / 경로 | 동작 |
| --- | --- |
| GET /api/state | 최신 로그 100건, 점검 20건, 차단 목록, 누적 지표, CSRF |
| POST /api/scans | JSON `target`; 비동기 실제 점검 접수, 202 반환 |
| POST /api/simulate | JSON `payload`; 서버에 저장되는 모의 검사 |
| POST /api/inspect | JSON `payload`; 실제 요청 검사, 탐지 시 IP 등록 및 403 |
| POST /api/blocks | JSON `ip`; 실제 공유 차단 목록에 IP 등록 |
| DELETE /api/blocks/ID | 차단 해제; 성공한 OS 규칙도 제거 |
| DELETE /api/logs | 모든 방문자의 저장 로그 영구 삭제; 점검/차단은 유지 |
| GET /api/export | 전체 공유 로그·점검·차단 JSON 다운로드 |
| POST /api/public/inspect | 이전 클라이언트 호환용 무상태 모의 패턴 검사 |

실시간 이벤트: `security_alert`, `scan_progress`, `metrics`, `blocks_changed`, `logs_cleared`.

## 점검 및 차단 범위

`SCAN_ALLOWLIST`는 쉼표로 구분한 승인된 CIDR 목록입니다. 기본 `127.0.0.0/8,::1/128`은 서버 자신만 허용합니다. 사이트에 접속한 방문자의 PC를 의미하지 않습니다. 외부 서버는 명시적으로 허가된 IP 범위를 설정해야 합니다. 모든 DNS 응답을 검사하고 검증된 IP로 연결하며 TLS 호스트명 검증을 유지합니다. 리디렉션은 따라가지 않습니다.

TCP 21,22,23,80,443,3306,8080과 입력 URL의 HTTP 보안 헤더를 점검합니다. 열린 포트가 침해를 뜻하지 않으며, 헤더 검사는 존재 여부만 확인합니다. 최대 4개 점검 작업이 실행되고 작업마다 최대 8개 점검 스레드를 사용합니다. 대기 슬롯이 가득 차면 429를 반환합니다. 재시작으로 중단된 작업은 interrupted로 표시합니다.

`FIREWALL_ENABLED=0`이 기본값입니다. 이 경우 차단 목록은 `/api/inspect`에서 적용되며 OS나 네트워크 전체를 차단하지 않습니다. Linux iptables/ip6tables와 Windows netsh 실행은 `FIREWALL_ENABLED=1` 및 필요한 OS 권한이 있을 때만 시도합니다. Render와 컨테이너 환경에서는 비활성 상태로 유지하세요. 루프백은 OS 차단에서 보호됩니다. 화면은 `disabled`, `protected-address`, `enforced`, `failed` 결과를 구분합니다. 실패한 OS 차단 해제는 DB 기록을 유지합니다. OS 규칙과 DB 변경은 원자적이지 않으며 재부팅 후 OS 규칙 확인이 필요합니다.

검사 API는 직접 연결된 `REMOTE_ADDR`를 사용합니다. 역방향 프록시 환경에서는 실제 방문자 IP와 다를 수 있습니다. 임의의 전달 헤더나 JSON IP를 신뢰해 자동 차단하지 않습니다. 이 앱은 인라인 역방향 프록시가 아니며, 실제 서비스 보호는 요청 검사 결과를 상위 서비스에서 반영하는 연동이 필요합니다. 시그니처 검사는 오탐과 우회 가능성이 있습니다.

Discord 알림은 `DISCORD_WEBHOOK_URL` 설정 시 실제 심각 위협과 열린 포트에 대해 전송합니다. 모의 검사는 알림이나 OS 차단을 실행하지 않습니다. 알림은 타임아웃이 있는 최선형 전송이며 영구 재시도 큐는 없습니다.

## 배포와 검증

[한국어 배포 안내](DEPLOY.ko.md)를 참고하세요. Render Blueprint는 저장소 루트 `render.yaml`을 사용합니다. `SECRET_KEY`만 자동 생성하며 별도 운영자 토큰은 필요하지 않습니다. Free 요금제는 유휴 시 중지되고 재시작·재배포 시 로컬 SQLite 기록이 사라질 수 있습니다. 영구 기록에는 `/data` 디스크가 필요합니다.

단일 프로세스/인스턴스를 사용하세요. SQLite, 메모리 차단 캐시와 요청 횟수 제한은 고가용성 다중 인스턴스 구성이 아닙니다. 실시간 차트는 현재 브라우저 세션이 수신한 5초당 이벤트 수이며 전체 패킷 트래픽이 아닙니다. 기록/내보내기는 현재 무제한이므로 장기 운영에는 보존 정책이 필요합니다. Tailwind/Chart.js/Socket.IO Client는 고정 버전 CDN에서 로드됩니다.

```bash
python -m pytest -q
node --check static/app.js
```

테스트는 로그인 없는 공유 접근, CSRF, 실시간 이벤트, 비동기 점검, 입력 검증, 차단·해제·실패 처리, 기록 보존·삭제를 검증합니다. 테스트에서 실제 외부 점검, OS 명령, 웹훅을 실행하지 않습니다.
