# GuardLab, LaunchPad & E-SOC

보안 학습 웹 서비스와 Docker 배포 관리 플랫폼을 프로젝트별로 나눈 저장소입니다.

| 프로젝트 | 기능 | 실행 안내 |
| --- | --- | --- |
| **[GuardLab](apps/guardlab/)** | 포트·헤더·패스워드·해시 진단, SQLite 이력, CSV 다운로드, Chart.js 통계 | [GuardLab README](apps/guardlab/README.md) |
| **[E-SOC](e-soc/)** | 한국어 소개 홈페이지, 실시간 보안 관제 콘솔, 초보자 모드, Flask·Socket.IO | [E-SOC 배포 안내](e-soc/DEPLOY.ko.md) |
| **[LaunchPad](apps/launchpad/)** | Docker 프로젝트 등록, 빌드·배포, 중지·재시작, 배포 로그·이력, 영구 볼륨 | [LaunchPad README](apps/launchpad/README.md) |

## 저장소 구조

```text
apps/
├── guardlab/
│   ├── main.py          # FastAPI API, 비동기 진단, 인증
│   ├── database.py      # SQLAlchemy + SQLite
│   ├── models.py        # ScanLog 모델
│   ├── static/          # CSS / JavaScript
│   ├── templates/       # 웹 대시보드
│   ├── tests/           # API / 실제 브라우저 검증
│   ├── Dockerfile
│   ├── render.yaml
│   ├── requirements.txt
│   └── README.md
└── launchpad/
    ├── main.py          # Docker 관리 API, 배포 작업, 인증
    ├── static/          # CSS / JavaScript
    ├── templates/       # 배포 관리 대시보드
    ├── tests/           # API / 실제 Docker / 브라우저 검증
    ├── requirements.txt
    └── README.md
```

각 프로젝트에 별도의 의존성 파일, 웹 화면, 테스트, 실행 문서가 있습니다. Python 3.12 이상을 사용하세요. LaunchPad에는 로컬 Docker Engine이 필요합니다.

## GuardLab 빠른 시작

```bash
cd apps/guardlab
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

로컬 학습 모드입니다. 외부 배포 시 production 인증과 진단 허용 대상을 설정하세요. [배포 안내](apps/guardlab/README.md)를 참고하세요.

## LaunchPad 빠른 시작

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

관리자 사용자명은 `admin`입니다. LaunchPad에 GuardLab을 등록할 때 프로젝트 경로는 **`apps/guardlab`**으로 입력합니다. LaunchPad는 신뢰하는 단일 관리자가 본인 Docker 서버에서 사용하는 MVP입니다. 공개 도메인·HTTPS 자동 발급이나 멀티테넌트 호스팅은 제공하지 않습니다.

## 전체 테스트

저장소 루트의 가상환경에서:

```bash
pip install -r apps/guardlab/requirements.txt -r apps/launchpad/requirements.txt pytest
python -m pytest -q
```

실제 브라우저 및 Docker 검증은 각 프로젝트 README를 참고하세요. 테스트는 임시 DB를 사용하며 사용자 진단 기록을 변경하지 않습니다.

비밀번호, `.env`, SQLite DB, 배포 상태 디렉터리, 가상환경과 캐시는 Git에 포함하지 않습니다. 두 프로젝트의 상태와 데이터는 실행 환경에서 별도로 보관·백업하세요.

## E-SOC Render 배포

새 Web Service에서 Root Directory를 `e-soc`, Runtime을 Docker, Dockerfile Path를 `./Dockerfile`로 설정하세요. 환경변수 `SECRET_KEY`와 `ADMIN_TOKEN`은 서로 다른 32자 이상의 무작위 값으로 지정해야 합니다. 홈페이지는 `/`, 콘솔은 `/console`, 헬스체크는 `/healthz`입니다.

Free 인스턴스는 체험용이며 재시작·재배포 시 SQLite 기록이 사라질 수 있습니다. 기록 보존에는 영구 디스크(`/data`)를 지원하는 요금제가 필요합니다. [E-SOC 안내](e-soc/README.md)와 [배포 문서](e-soc/DEPLOY.ko.md)를 참고하세요.
