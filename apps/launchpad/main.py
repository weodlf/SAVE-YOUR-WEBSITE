"""LaunchPad: trusted, single-admin Docker deployment control plane."""
import base64
import binascii
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.getenv('PROJECT_ROOT', ROOT.parents[1])).resolve()
STATE = Path(os.getenv('LAUNCHPAD_STATE', ROOT / '.state')).resolve()
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']
LOCK = threading.Lock()
BUSY = set()
SLOTS = threading.BoundedSemaphore(2)


def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    connection = sqlite3.connect(STATE / 'launchpad.db', timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def connection():
    con = db()
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    with connection() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS services (
          id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, project TEXT NOT NULL,
          port INTEGER NOT NULL, health_path TEXT NOT NULL, environment TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'created', host_port INTEGER,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deployments (
          id TEXT PRIMARY KEY, service_id TEXT NOT NULL, status TEXT NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT, error TEXT
        );
        UPDATE services SET status='failed' WHERE status IN ('building', 'starting');
        UPDATE deployments SET status='failed', error='Manager restarted during deployment'
          WHERE status IN ('building', 'starting');
        ''')
    (STATE / 'launchpad.db').chmod(0o600)


@asynccontextmanager
async def lifespan(app):
    if len(os.getenv('LAUNCHPAD_PASSWORD', '')) < 20:
        raise RuntimeError('Set LAUNCHPAD_PASSWORD (at least 20 characters) before startup')
    init_db()
    app.state.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='deploy')
    yield
    app.state.executor.shutdown(wait=True, cancel_futures=True)


app = FastAPI(title='LaunchPad — Deployment Workspace', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
templates = Jinja2Templates(directory=ROOT / 'templates')


@app.middleware('http')
async def auth(request, call_next):
    if request.url.path != '/healthz':
        valid = False
        try:
            scheme, token = request.headers.get('authorization', '').split(' ', 1)
            user, password = base64.b64decode(token, validate=True).decode().split(':', 1)
            expected = os.getenv('LAUNCHPAD_PASSWORD', '')
            valid = (scheme.lower() == 'basic' and bool(expected)
                     and secrets.compare_digest(user.encode(), b'admin')
                     and secrets.compare_digest(password.encode(), expected.encode()))
        except (ValueError, UnicodeError, binascii.Error):
            pass
        if not valid:
            return JSONResponse({'detail': 'Administrator login required'}, 401,
                                headers={'WWW-Authenticate': 'Basic realm="LaunchPad", charset="UTF-8"'})
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'Cross-origin changes are not allowed'}, 403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.get('/healthz')
def health():
    return {'status': 'ok'}


@app.get('/')
def index(request: Request):
    return templates.TemplateResponse(request=request, name='index.html')


def docker(args, timeout=30, output=None):
    env = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    result = subprocess.run(DOCKER + args, env=env, timeout=timeout,
                            stdout=output or subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise RuntimeError('Docker command failed; inspect deployment logs')
    return result.stdout or ''


def service(sid):
    with connection() as con:
        row = con.execute('SELECT * FROM services WHERE id=?', (sid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Service not found')
    return dict(row)


def public(row):
    result = dict(row)
    result['environment_keys'] = list(json.loads(result.pop('environment')))
    return result


def update(sid, status, host_port=None):
    with connection() as con:
        con.execute('UPDATE services SET status=?,host_port=?,updated_at=? WHERE id=?',
                    (status, host_port, now(), sid))


def validate_project(relative):
    path = (PROJECT_ROOT / relative).resolve()
    if not path.is_relative_to(PROJECT_ROOT) or not path.is_dir():
        raise HTTPException(422, 'Project must be an existing directory inside PROJECT_ROOT')
    dockerfile = (path / 'Dockerfile').resolve()
    if not dockerfile.is_relative_to(path) or not dockerfile.is_file():
        raise HTTPException(422, 'Project needs a Dockerfile inside its directory')
    return path


class ServiceInput(BaseModel):
    name: str = Field(pattern=r'^[a-z][a-z0-9-]{1,39}$')
    project: str = Field(min_length=1, max_length=300)
    port: int = Field(default=8000, ge=1, le=65535)
    health_path: str = Field(default='/healthz', pattern=r'^/[a-zA-Z0-9_./-]*$', max_length=200)
    environment: dict[str, str] = Field(default_factory=dict, max_length=30)

    @field_validator('environment')
    @classmethod
    def valid_env(cls, value):
        for key, text in value.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or '\n' in text or '\r' in text or '\x00' in text or len(text) > 8192:
                raise ValueError('Environment requires variable names and single-line values up to 8192 characters')
        return value


@app.get('/api/system')
def system():
    try:
        docker(['info', '--format', '{{.ServerVersion}}'], timeout=5)
        ready = True
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        ready = False
    return {'docker_ready': ready, 'project_root': str(PROJECT_ROOT), 'max_parallel_deployments': 2,
            'publish_scope': 'localhost', 'persistent_mount': '/data'}


@app.get('/api/services')
def list_services():
    with connection() as con:
        rows = con.execute('SELECT * FROM services ORDER BY created_at DESC').fetchall()
    results = []
    for source in rows:
        row = dict(source)
        if row['status'] in ('running', 'stopped', 'degraded'):
            try:
                cid = owned_container(row['id'])
                state = docker(['inspect', '--format', '{{.State.Status}}', cid], timeout=5).strip() if cid else 'missing'
                observed = 'running' if state == 'running' else 'stopped' if state == 'exited' and row['status'] == 'stopped' else 'degraded'
                if observed != row['status']:
                    update(row['id'], observed, row['host_port'] if observed == 'running' else None)
                    row['status'] = observed
                    if observed != 'running':
                        row['host_port'] = None
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                row['status'] = 'degraded'
        results.append(public(row))
    return results


@app.post('/api/services', status_code=201)
def create(data: ServiceInput):
    validate_project(data.project)
    sid = uuid.uuid4().hex
    with connection() as con:
        try:
            con.execute('INSERT INTO services (id,name,project,port,health_path,environment,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                        (sid, data.name, data.project, data.port, data.health_path, json.dumps(data.environment), 'created', now(), now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'This service name already exists')
    return public(service(sid))


@contextmanager
def exclusive(sid):
    with LOCK:
        if sid in BUSY:
            raise HTTPException(409, 'Service has an operation in progress')
        BUSY.add(sid)
    try:
        yield
    finally:
        with LOCK:
            BUSY.discard(sid)


def name_for(sid):
    return f'launchpad-{sid}'


def owned_container(sid):
    """Only ever operate on this control plane's labeled containers."""
    raw = docker(['ps', '-a', '--filter', f'name=^/{name_for(sid)}$',
                  '--filter', f'label=launchpad.service={sid}', '--format', '{{.ID}}'])
    return raw.strip()


def remove_container(sid):
    cid = owned_container(sid)
    if cid:
        docker(['rm', '-f', cid])


def build_and_run(sid, job):
    row = service(sid)
    log_path = STATE / f'{job}.log'
    try:
        with log_path.open('w') as log:
            log.write('Building project image…\n'); log.flush()
            project = validate_project(row['project'])
            args = ['build', '--tag', name_for(sid)]
            cert = os.getenv('CODEX_PROXY_CERT')
            if cert and Path(cert).is_file():
                args += ['--secret', f'id=proxy_ca,src={cert}']
            args += [str(project)]
            docker(args, timeout=600, output=log)
            log.write('\nBuild succeeded. Starting container…\n'); log.flush()
            update(sid, 'starting')
            with connection() as con:
                con.execute('UPDATE deployments SET status=? WHERE id=?', ('starting', job))
            # A failed build leaves the existing container untouched. Successful builds replace it.
            remove_container(sid)
            with tempfile.NamedTemporaryFile(mode='w', dir=STATE, suffix='.env') as env_file:
                for key, value in json.loads(row['environment']).items():
                    env_file.write(f'{key}={value}\n')
                env_file.flush()
                args = ['run', '-d', '--name', name_for(sid), '--label', f'launchpad.service={sid}',
                        '--restart', 'unless-stopped', '--memory', '256m', '--cpus', '0.5', '--pids-limit', '128',
                        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                        '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2',
                        '--publish', f'127.0.0.1::{row["port"]}', '--env-file', env_file.name,
                        '--mount', f'type=volume,src={name_for(sid)}-data,dst=/data']
                if cert and Path(cert).is_file():
                    args += ['--mount', f'type=bind,src={cert},dst=/run/proxy-ca.pem,readonly',
                             '--env', 'SSL_CERT_FILE=/run/proxy-ca.pem', '--env', 'REQUESTS_CA_BUNDLE=/run/proxy-ca.pem']
                args.append(name_for(sid))
                docker(args, output=log)
            raw = docker(['inspect', '--format', '{{json .NetworkSettings.Ports}}', name_for(sid)])
            port = int(json.loads(raw)[f'{row["port"]}/tcp'][0]['HostPort'])
            update(sid, 'starting', port)
            healthy = False
            with httpx.Client(trust_env=False, timeout=2, follow_redirects=False) as client:
                for _ in range(30):
                    try:
                        response = client.get(f'http://127.0.0.1:{port}{row["health_path"]}')
                        if 200 <= response.status_code < 300:
                            healthy = True
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
            if not healthy:
                raise RuntimeError('Container failed HTTP readiness (30 attempts); inspect runtime logs')
            update(sid, 'running', port)
            log.write(f'\nReady: HTTP health check passed. Local port {port}.\n')
        with connection() as con:
            con.execute('UPDATE deployments SET status=?,finished_at=? WHERE id=?', ('running', now(), job))
    except Exception as exc:
        error = 'Deployment failed: ' + (str(exc) if isinstance(exc, (RuntimeError, subprocess.TimeoutExpired)) else type(exc).__name__)
        with log_path.open('a') as log:
            log.write('\n' + error + '\n')
        update(sid, 'failed')
        with connection() as con:
            con.execute('UPDATE deployments SET status=?,finished_at=?,error=? WHERE id=?', ('failed', now(), error, job))
    finally:
        with LOCK:
            BUSY.discard(sid)
        SLOTS.release()


@app.post('/api/services/{sid}/deploy', status_code=202)
def deploy(sid: str, request: Request):
    service(sid)
    with LOCK:
        if sid in BUSY:
            raise HTTPException(409, 'Service is already busy')
        if not SLOTS.acquire(blocking=False):
            raise HTTPException(429, 'Two deployments are already in progress')
        BUSY.add(sid)
    job = uuid.uuid4().hex
    try:
        with connection() as con:
            con.execute('INSERT INTO deployments (id,service_id,status,started_at) VALUES (?,?,?,?)', (job, sid, 'building', now()))
        update(sid, 'building')
        request.app.state.executor.submit(build_and_run, sid, job)
    except Exception:
        with LOCK:
            BUSY.discard(sid)
        SLOTS.release()
        raise
    return {'deployment_id': job, 'status': 'building'}


@app.post('/api/services/{sid}/stop')
def stop(sid: str):
    service(sid)
    with exclusive(sid):
        try:
            cid = owned_container(sid)
            if cid:
                docker(['stop', '--time', '10', cid], timeout=20)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            raise HTTPException(503, 'Docker stop failed')
        update(sid, 'stopped')
    return public(service(sid))


@app.post('/api/services/{sid}/start')
def start(sid: str):
    row = service(sid)
    with exclusive(sid):
        try:
            cid = owned_container(sid)
            if not cid:
                raise HTTPException(409, 'No retained container. Deploy the service first.')
            docker(['start', cid])
            raw = docker(['inspect', '--format', '{{json .NetworkSettings.Ports}}', cid])
            port = int(json.loads(raw)[f'{row["port"]}/tcp'][0]['HostPort'])
            healthy = False
            with httpx.Client(trust_env=False, timeout=2) as client:
                for _ in range(15):
                    try:
                        if 200 <= client.get(f'http://127.0.0.1:{port}{row["health_path"]}').status_code < 300:
                            healthy = True
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
            if not healthy:
                update(sid, 'failed')
                raise HTTPException(502, 'HTTP readiness failed; inspect runtime logs')
            update(sid, 'running', port)
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError):
            raise HTTPException(503, 'Docker start failed; inspect runtime logs')
    return public(service(sid))


@app.get('/api/services/{sid}/deployments')
def deployments(sid: str):
    service(sid)
    with connection() as con:
        rows = con.execute('SELECT * FROM deployments WHERE service_id=? ORDER BY started_at DESC LIMIT 20', (sid,)).fetchall()
    return [dict(row) for row in rows]


@app.get('/api/services/{sid}/logs')
def logs(sid: str):
    row = service(sid)
    with connection() as con:
        job = con.execute('SELECT id FROM deployments WHERE service_id=? ORDER BY started_at DESC LIMIT 1', (sid,)).fetchone()
    build = ''
    if job:
        path = STATE / f'{job["id"]}.log'
        if path.exists():
            with path.open('rb') as source:
                source.seek(max(0, path.stat().st_size - 65536))
                build = source.read().decode('utf-8', errors='replace')
    try:
        cid = owned_container(sid)
        runtime = docker(['logs', '--tail', '150', cid]) if cid else 'No container yet.'
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        runtime = 'Runtime logs unavailable.'
    for value in json.loads(row['environment']).values():
        if len(value) >= 4:
            build = build.replace(value, '[REDACTED]')
            runtime = runtime.replace(value, '[REDACTED]')
    return {'build': build, 'runtime': runtime}


@app.delete('/api/services/{sid}')
def delete_service(sid: str):
    service(sid)
    with exclusive(sid):
        try:
            remove_container(sid)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            raise HTTPException(503, 'Docker is unavailable; service was not deleted')
        with connection() as con:
            jobs = con.execute('SELECT id FROM deployments WHERE service_id=?', (sid,)).fetchall()
            con.execute('DELETE FROM deployments WHERE service_id=?', (sid,))
            con.execute('DELETE FROM services WHERE id=?', (sid,))
        for job in jobs:
            (STATE / f'{job["id"]}.log').unlink(missing_ok=True)
    # Persistent data volume and images are intentionally retained.
    return {'deleted': sid, 'data_volume_retained': True}
