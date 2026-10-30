import importlib.util
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

from fastapi.testclient import TestClient
import httpx

root = Path(__file__).resolve().parents[3]
with tempfile.TemporaryDirectory() as temporary:
    os.environ['LAUNCHPAD_STATE'] = temporary
    password = secrets.token_urlsafe(32)
    app_password = secrets.token_urlsafe(32)
    os.environ['LAUNCHPAD_PASSWORD'] = password
    os.environ['PROJECT_ROOT'] = str(root)
    spec = importlib.util.spec_from_file_location('launchpad', root / 'apps/launchpad/main.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sid = None
    try:
        with TestClient(module.app) as api:
            api.auth = ('admin', password)
            assert api.get('/api/system').json()['docker_ready']
            created = api.post('/api/services', json={'name': 'guardlab-check', 'project': 'apps/guardlab', 'environment': {'DASHBOARD_PASSWORD': app_password, 'SCAN_ALLOWED_HOSTS': 'example.com'}})
            assert created.status_code == 201, created.text
            sid = created.json()['id']
            assert api.post(f'/api/services/{sid}/deploy').status_code == 202
            print('GuardLab image build started.', flush=True)
            last_status = None
            for _ in range(650):
                row = api.get('/api/services').json()[0]
                if row['status'] != last_status:
                    print('Deployment state:', row['status'], flush=True)
                    last_status = row['status']
                if row['status'] in ('running', 'failed'):
                    break
                time.sleep(1)
            if row['status'] != 'running':
                print(api.get(f'/api/services/{sid}/logs').json()['build'][-6000:], flush=True)
                raise AssertionError('GuardLab deployment failed')
            base = f'http://127.0.0.1:{row["host_port"]}'
            with httpx.Client(trust_env=False, auth=('admin', app_password), timeout=5) as client:
                assert client.get(base).status_code == 200
                assert client.post(base + '/api/crypto', json={'text': 'persist-check'}).status_code == 200
                assert len(client.get(base + '/api/logs').json()) == 1
            assert api.post(f'/api/services/{sid}/stop').json()['status'] == 'stopped'
            restarted = api.post(f'/api/services/{sid}/start')
            assert restarted.status_code == 200, restarted.text
            base = f'http://127.0.0.1:{restarted.json()["host_port"]}'
            with httpx.Client(trust_env=False, auth=('admin', app_password), timeout=5) as client:
                assert len(client.get(base + '/api/logs').json()) == 1
            history = api.get(f'/api/services/{sid}/deployments').json()
            assert history[0]['status'] == 'running'
            print('PASS: actual Docker build, HTTP readiness, authenticated GuardLab, diagnostics, stop/start, SQLite persistence and deployment history.', flush=True)
            assert api.delete(f'/api/services/{sid}').status_code == 200
    finally:
        if sid:
            # Remove only this check's uniquely named test assets.
            for args in (['rm','-f',f'launchpad-{sid}'], ['volume','rm',f'launchpad-{sid}-data'], ['image','rm',f'launchpad-{sid}']):
                subprocess.run(module.DOCKER + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
