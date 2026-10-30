# GuardLab — 보안 학습 워크스페이스

GuardLab은 진단, 기록, 분석을 한 화면에 모은 단일 관리자용 보안 학습 웹 서비스입니다. FastAPI + SQLAlchemy + SQLite를 사용하며, 독립적인 반응형 디자인과 Chart.js 차트를 제공합니다.

## 실행

저장소 루트에서 `cd apps/guardlab` 후 아래 명령을 실행합니다.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

시작 시 `apps/guardlab/`에 `security.db` 및 `scan_logs` 테이블이 생성됩니다. `DATABASE_URL=sqlite:////absolute/path/security.db`로 SQLite 경로를 변경할 수 있습니다. 시각은 UTC로 저장하고 UI에서 현지 시각으로 표시합니다. 스타일은 프로젝트 내부 CSS로 제공하며, 차트에만 Chart.js CDN을 사용합니다. CDN 연결이 없으면 텍스트 통계를 확인할 수 있습니다.

- `POST /api/port-scan`: `{"target":"127.0.0.1","ports":[80,443]}`
- `POST /api/header-check`: `{"url":"https://example.com"}`
- `POST /api/password-analysis`: `{"password":"sample"}`
- `POST /api/crypto`: `{"text":"hello","operation":"sha256"}` (`base64_encode`, `base64_decode` 지원)
- `GET /api/logs`: 최근 50개, 시각 및 ID 내림차순
- `GET /api/logs/download`: 전체 이력 CSV 다운로드 (`security_scan_logs.csv`, 최신순, UTF-8 BOM 포함)
- `DELETE /api/logs`: 전체 삭제, 삭제 개수 반환

진단 결과 및 처리한 DNS/HTTP/Base64 오류는 이력에 저장됩니다. 요청 형식 검증 실패는 실행 이력이 아닙니다. 패스워드와 Crypto 입력·출력은 민감정보 유출 방지를 위해 저장하지 않고 대상에 `[REDACTED]`를 저장합니다. SHA-256은 해시, Base64는 인코딩이며 암호화가 아닙니다.

이 앱은 단일 관리자용 학습 도구입니다. 허가된 대상에만 사용하세요. 로컬 기본 모드에는 인증이 없으며, 외부 공개 시 아래 production 설정으로 인증과 진단 대상 제한을 활성화하세요. 전체 이력 삭제는 복구할 수 없습니다.

## 검증

```sh
pip install pytest
python -m pytest -q
```

테스트는 임시 SQLite와 로컬 TCP 서버, 모의 HTTP 응답을 사용하며 실제 DB를 변경하지 않습니다.

CSV는 한글 Excel 호환을 위해 UTF-8 BOM을 포함하며, 수식으로 해석될 수 있는 셀은 작은따옴표를 앞에 붙여 내보냅니다. 빈 이력은 컬럼 헤더만 포함합니다.

## 통계

`GET /api/stats`는 `by_tool` (전체 이력의 도구명→횟수) 및 `by_date` (오늘 포함 최근 14일의 UTC 날짜→횟수)를 반환합니다. 날짜는 오름차순이며 기록 없는 날은 0으로 채웁니다. 저장된 오류 이력도 집계합니다. 화면의 Statistics 섹션은 Chart.js CDN을 이용한 도넛·선 차트를 제공하며, 로드·새로고침·진단·이력 삭제 후 갱신합니다.

## 동시 처리 및 부하 제한

포트 스캔과 헤더 진단은 async 엔드포인트입니다. 포트 연결과 DNS 조회는 앱 수명주기에 관리되는 공유 `ThreadPoolExecutor(max_workers=20)`에서 처리하며, 중복 포트를 제거하고 포트당 연결 제한 시간을 0.5초로 설정합니다. 동시에 진행하는 포트 스캔은 프로세스당 4건, 요청당 최대 32개 포트입니다. DNS 대기 제한은 5초이며 OS DNS 호출 자체는 취소할 수 없어 종료까지 워커를 사용할 수 있습니다.

헤더 진단은 공유 `httpx.AsyncClient`와 최대 20개 연결을 사용하며 프로세스당 최대 20건이 진행됩니다. HTTP 타임아웃은 5초, 전체 요청 대기 제한은 6초입니다. 두 진단 모두 처리 슬롯을 0.1초 내 확보하지 못하면 HTTP 503을 반환합니다. 워커/연결 제한은 Uvicorn 프로세스별로 적용됩니다. 동기 SQLAlchemy 저장은 별도 작업 스레드로 넘겨 이벤트 루프에서 실행하지 않습니다. 종료 시 HTTP 클라이언트와 스레드 풀을 정리합니다.

## 웹 배포 (Render / Docker)

대시보드는 `/`에서 제공하는 FastAPI 웹 앱입니다. `apps/guardlab/render.yaml`은 **유료 Starter 인스턴스 + 1GB 영구 디스크**를 사용하는 배포 구성입니다. 비용과 요금은 Render에서 확인하세요. SQLite 파일 보존을 위해 무료 임시 파일시스템을 사용하지 않습니다.

1. 이 프로젝트 파일을 GitHub 저장소에 반영합니다.
2. Render에서 해당 저장소를 연결하고 Blueprint 경로를 `apps/guardlab/render.yaml`로 지정해 배포합니다.
3. `SCAN_ALLOWED_HOSTS`에 본인 소유/허가받은 호스트명 또는 IP를 쉼표로 입력합니다. 와일드카드는 지원하지 않습니다. 빈 값이면 네트워크 진단이 차단됩니다.
4. 배포 후 Render 환경 설정에서 자동 생성된 `DASHBOARD_PASSWORD`를 확인합니다. HTTPS 배포 URL에 접속하고 브라우저 로그인 창에 사용자명 `admin`과 해당 비밀번호를 입력합니다. 비밀번호를 채팅이나 Git에 저장하지 마세요.
5. `/healthz` 응답 및 인증 후 진단·이력·CSV·통계 기능을 확인합니다. 재시작 후에도 `/data/security.db`가 유지되는지 확인합니다.

프로덕션은 20자 이상의 비밀번호 없이는 시작하지 않습니다. 화면, 문서, 모든 API에 동일한 인증을 적용하고 `/healthz`만 공개합니다. HTTP Basic은 HTTPS에서만 사용하세요. 이 앱은 단일 관리자용이며 여러 사용자 간 이력을 분리하지 않습니다. 하나의 인스턴스/워커를 사용하고 영구 디스크를 별도로 백업하세요.

직접 운영하는 Docker 서버에서는 이미지 빌드 후 영구 볼륨을 `/data`에 연결하고 `DASHBOARD_PASSWORD` 및 `SCAN_ALLOWED_HOSTS`를 안전한 환경 변수로 제공하세요. HTTPS 역방향 프록시를 앞에 두어야 합니다. 이미지에는 DB나 로컬 가상환경을 포함하지 않습니다.

## GuardLab 웹 UI

- 홈: 전체·오늘·최근 14일 진단 횟수와 사용한 도구 수 (실제 DB 집계)
- 학습 인사이트: 날짜별 추이, 도구 비율 및 텍스트 통계
- 진단 도구: 포트·헤더·패스워드·해시/인코딩, 읽기 쉬운 결과와 원본 JSON 보기
- 활동 기록: 최근 50개 검색·도구 필터, 전체 CSV 다운로드 및 삭제
- 모바일 메뉴, 키보드 탐색, 빈 기록·오류·실행 중 상태 지원

스타일과 UI 로직은 `static/app.css`, `static/app.js`로 제공되며 외부 CSS CDN에 의존하지 않습니다. Chart.js CDN을 사용할 수 없으면 텍스트 통계를 확인할 수 있습니다. 예제 이력이나 통계는 실제 DB에 삽입하지 않습니다.

브라우저 검증(선택): Playwright 및 Chromium이 설치된 개발 환경에서 `apps/guardlab`에서 `python tests/browser_smoke.py`를 실행하세요. 임시 DB와 임시 포트로 데스크톱·모바일, 결과 표시, 검색·필터, CSV, 삭제, CDN 실패를 검증합니다. 배포 이미지에는 브라우저 의존성이 필요하지 않습니다.
