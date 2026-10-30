# LaunchPad

Render와 비슷한 흐름으로 프로젝트를 등록하고 Docker 이미지를 빌드·실행하는 **단일 서버·단일 관리자용 배포 플랫폼 MVP**입니다. GuardLab과 별도의 웹 앱이며 같은 저장소의 `apps/launchpad/`에서 독립적으로 실행합니다.

## 제공 기능

- 반응형 관리 대시보드와 관리자 HTTP Basic 인증
- 프로젝트 등록: 서버의 로컬 경로, Dockerfile, 포트, 준비 확인 경로, 환경변수
- 백그라운드 빌드 및 배포 (최대 2개 동시 작업, 같은 서비스의 중복 작업 차단)
- 컨테이너 실행, 중지, 재시작, 다시 빌드, 삭제
- HTTP 2xx 준비 확인 후 배포 성공 판정
- 실행 중/중지/비정상 종료 상태 확인, 최근 20개 배포 이력
- 빌드 로그 최근 64KB, 실행 로그 최근 150줄, 알려진 환경변수 값 마스킹
- 서비스별 256MB 메모리, 0.5 CPU, 128 PID 제한 및 권한 최소화
- `/data` 영구 Docker 볼륨: 서비스 삭제 시에도 보존
- 프로젝트·배포 이력을 SQLite에 저장, 빌드 로그를 로컬 파일로 저장

## 실행

Python 3.12 이상, Docker Engine(Unix socket `/var/run/docker.sock`), 디스크 공간이 필요합니다. 관리자 계정은 Docker socket에 접근할 수 있어야 합니다.

저장소 루트에서:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r apps/launchpad/requirements.txt
read -rsp '관리자 비밀번호 (20자 이상): ' LAUNCHPAD_PASSWORD
export LAUNCHPAD_PASSWORD
printf '\n'
export PROJECT_ROOT="$PWD"
python -m uvicorn apps.launchpad.main:app --host 127.0.0.1 --port 9000
```

브라우저에서 서버의 `http://127.0.0.1:9000`에 접속하고 사용자명 `admin`, 설정한 비밀번호로 로그인합니다. 원격 서버라면 SSH 포트 포워딩 또는 HTTPS 역방향 프록시를 설정하세요. 컨트롤 플레인을 인증 없는 공개 HTTP로 운영하지 마세요.

`PROJECT_ROOT`는 등록 가능한 프로젝트의 최상위 디렉터리입니다. 경로 이탈과 외부 Dockerfile 심볼릭 링크를 거부합니다. `LAUNCHPAD_STATE`로 상태 디렉터리를 변경할 수 있으며 기본값은 `apps/launchpad/.state/`입니다. 상태 디렉터리는 0700, DB는 0600 권한으로 생성됩니다. 프로젝트와 상태 경로를 백업하세요.

## GuardLab 배포 예시

먼저 GuardLab 저장소가 `PROJECT_ROOT`인지 확인합니다. 새 프로젝트 대화상자에서:

- 서비스 이름: `guardlab`
- 프로젝트 경로: `apps/guardlab`
- 컨테이너 포트: `8000`
- HTTP 준비 확인 경로: `/healthz`
- 환경변수: `DASHBOARD_PASSWORD`에 직접 만든 20자 이상의 비밀번호, `SCAN_ALLOWED_HOSTS`에 진단을 허용할 호스트를 입력

`프로젝트 등록` 후 카드의 `배포하기`를 누릅니다. Docker 이미지 빌드, 컨테이너 시작, HTTP 준비 확인이 끝나면 `실행 중`으로 표시됩니다. 앱에 접속할 때는 GuardLab의 사용자명 `admin`과 **DASHBOARD_PASSWORD**를 사용합니다. LaunchPad의 관리자 비밀번호와는 별개입니다. `/data/security.db`는 중지·재시작·다시 빌드 후에도 유지됩니다.

배포 주소는 **서버의 localhost 포트**입니다. 다른 PC의 브라우저에서 `127.0.0.1`은 그 PC를 의미하므로 원격 서버 주소가 아닙니다. 외부 공개에는 배포된 서비스별 역방향 프록시와 도메인·TLS 설정이 필요합니다. 이 MVP는 공개 주소를 자동 발급하지 않습니다.

## 운영 범위와 제한

이 도구는 관리자와 배포 코드 모두를 신뢰하는 환경에 사용하세요. Docker socket 접근은 호스트 관리자에 준하는 권한입니다. Dockerfile은 임의 코드를 실행할 수 있으며 프로젝트 경로 제한이나 컨테이너 리소스 제한은 적대적인 코드의 안전한 실행을 보장하지 않습니다. 다른 사람의 저장소나 여러 고객의 코드를 호스팅하는 서비스로 공개하면 안 됩니다.

환경변수 값은 서버 소유자만 접근 가능한 SQLite에 **평문 저장**됩니다. API에는 키 이름만 반환합니다. 임시 env 파일은 0600 권한으로 생성하고 컨테이너 생성 후 지웁니다. 앱이 임의로 출력한 비밀정보를 완벽히 탐지할 수는 없습니다. 별도 비밀관리 서비스나 암호화 저장소는 아직 없습니다.

빌드는 10분 제한입니다. 빌드 실패 시 기존 컨테이너는 제거하지 않지만 서비스 카드에는 실패한 최신 배포 상태가 표시됩니다. 빌드 성공 후에는 기존 컨테이너를 제거하고 새 컨테이너를 시작하므로 **무중단 배포와 자동 롤백은 지원하지 않습니다**. 배포 중 관리 프로세스가 재시작되면 진행 중이던 배포를 실패로 표시하며, 관리자는 로그를 확인하고 다시 배포해야 합니다.

배포 볼륨과 이미지가 자동 삭제되지 않으므로 디스크 사용량을 모니터링하세요. 이름은 `launchpad-<service-id>-data`, 이미지는 `launchpad-<service-id>`입니다. DB를 제거하기 전에 남은 볼륨을 확인하고 필요한 데이터를 백업하세요. 로그 파일과 배포 이력도 주기적으로 보관·정리해야 합니다.

현재 범위에 포함되지 않는 기능: GitHub OAuth·웹훅·원격 저장소 자동 clone, 멀티테넌트·팀·결제, 스케일링, 자동 도메인·HTTPS 발급, 자동 롤백. 로컬 프로젝트 기반 배포는 실제 Docker 작업이며 데모 상태를 꾸며 표시하지 않습니다.

관리 API의 생성·수정 요청은 동일 출처를 요구합니다. 역방향 프록시를 사용하는 경우 외부 host/scheme이 일관되게 전달되도록 구성하세요. Docker에는 host networking, privileged 모드, 호스트 디렉터리의 임의 쓰기 마운트를 허용하지 않습니다.

## API

| 메서드 | 경로 | 기능 |
|---|---|---|
| GET | `/api/system` | Docker 연결 및 프로젝트 루트 |
| GET / POST | `/api/services` | 서비스 목록 / 등록 |
| POST | `/api/services/{id}/deploy` | 비동기 빌드·배포, 202 반환 |
| POST | `/api/services/{id}/stop` | 중지 |
| POST | `/api/services/{id}/start` | 기존 컨테이너 시작·HTTP 준비 확인 |
| GET | `/api/services/{id}/logs` | 빌드·실행 로그 |
| GET | `/api/services/{id}/deployments` | 최근 배포 이력 |
| DELETE | `/api/services/{id}` | 컨테이너·등록 정보 삭제 (볼륨 유지) |

## 검증

저장소 루트에서 `python -m pytest apps/launchpad/tests/test_platform.py -q`를 실행합니다. Docker CLI가 실패하거나 서비스를 처리 중일 때도 등록 정보가 보존되는지, 인증, 경로 제한, 비밀값 비노출, 오류 배포 이력을 확인합니다.

선택적 실제 Docker 통합 검증: `python apps/launchpad/tests/docker_smoke.py`. 이 검증은 GuardLab 이미지를 빌드하고 임시 서비스·볼륨으로 진단을 실행한 뒤 재시작 후 SQLite 기록 보존까지 확인합니다. 검증용 컨테이너·이미지·볼륨만 종료 후 제거하며 Docker 빌드 캐시는 남을 수 있습니다.

관리형 클라우드에서 프록시 CA가 필요한 경우 빌드의 `proxy_ca` BuildKit secret 및 실행 시 읽기 전용 CA 마운트를 지원합니다. 프로젝트 Dockerfile은 자체 네트워크 단계에서 해당 CA를 사용해야 합니다. TLS 검증을 비활성화하지 않습니다.

브라우저 통합 검증(선택): Playwright 및 Chromium이 설치된 환경에서 `python apps/launchpad/tests/browser_smoke.py`를 실행하면 실제 UI의 프로젝트 등록·GuardLab 배포·로그·모바일·삭제를 확인합니다. 임시 DB와 검증 전용 Docker 리소스만 사용합니다.
