import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location('launchpad_app', Path(__file__).parents[1] / 'main.py')
platform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(platform)


@pytest.fixture
def client(tmp_path, monkeypatch):
    projects = tmp_path / 'projects'
    projects.mkdir()
    (projects / 'Dockerfile').write_text('FROM scratch\n')
    monkeypatch.setattr(platform, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(platform, 'PROJECT_ROOT', projects)
    monkeypatch.setenv('LAUNCHPAD_PASSWORD', 'test-password-long-enough')
    with TestClient(platform.app) as api:
        api.auth = ('admin', 'test-password-long-enough')
        yield api


def test_auth_and_assets(client):
    assert client.get('/healthz', auth=('bad', 'bad')).status_code == 200
    for path in ('/', '/api/services', '/api/system', '/static/app.js'):
        assert client.get(path, auth=('bad', 'bad')).status_code == 401
    assert 'LaunchPad' in client.get('/').text
    assert client.get('/static/style.css').status_code == 200
    assert client.post('/api/services', headers={'Origin': 'https://other.test'}, json={'name': 'my-app', 'project': '.'}).status_code == 403


def test_projects_validation_and_secrets(client):
    assert client.post('/api/services', json={'name': 'escape', 'project': '..'}).status_code == 422
    assert client.post('/api/services', json={'name': 'bad-app', 'project': '.', 'environment': {'BAD': 'multi\nline'}}).status_code == 422
    response = client.post('/api/services', json={'name': 'my-app', 'project': '.', 'environment': {'SECRET_KEY': 'never-in-api'}})
    assert response.status_code == 201
    assert 'never-in-api' not in response.text
    assert response.json()['environment_keys'] == ['SECRET_KEY']
    assert client.post('/api/services', json={'name': 'my-app', 'project': '.'}).status_code == 409
    assert 'never-in-api' not in client.get('/api/services').text
    assert client.get('/api/services/missing/logs').status_code == 404


def test_busy_and_docker_failure_preserve_service(client):
    sid = client.post('/api/services', json={'name': 'my-app', 'project': '.'}).json()['id']
    platform.BUSY.add(sid)
    try:
        assert client.post(f'/api/services/{sid}/deploy').status_code == 409
        assert client.post(f'/api/services/{sid}/stop').status_code == 409
        assert client.delete(f'/api/services/{sid}').status_code == 409
    finally:
        platform.BUSY.discard(sid)
    with patch.object(platform, 'docker', side_effect=RuntimeError('unavailable')):
        assert client.get('/api/system').json()['docker_ready'] is False
        assert client.delete(f'/api/services/{sid}').status_code == 503
    assert len(client.get('/api/services').json()) == 1


def test_stop_delete_and_redaction(client):
    sid = client.post('/api/services', json={'name': 'my-app', 'project': '.', 'environment': {'KEY': 'secret-123'}}).json()['id']
    with patch.object(platform, 'owned_container', return_value='container-id'), patch.object(platform, 'docker', return_value='runtime secret-123'):
        assert client.get(f'/api/services/{sid}/logs').json()['runtime'] == 'runtime [REDACTED]'
        assert client.post(f'/api/services/{sid}/stop').json()['status'] == 'stopped'
        assert client.delete(f'/api/services/{sid}').json()['data_volume_retained'] is True
    assert client.get('/api/services').json() == []


def test_failed_deploy_persists_logs(client):
    import time
    sid = client.post('/api/services', json={'name': 'my-app', 'project': '.'}).json()['id']
    with patch.object(platform, 'docker', side_effect=RuntimeError('build refused')):
        response = client.post(f'/api/services/{sid}/deploy')
        assert response.status_code == 202
        for _ in range(100):
            history = client.get(f'/api/services/{sid}/deployments').json()
            if history and history[0]['status'] == 'failed':
                break
            time.sleep(.01)
        assert history[0]['status'] == 'failed'
        assert client.get('/api/services').json()[0]['status'] == 'failed'
        assert 'build refused' in client.get(f'/api/services/{sid}/logs').json()['build']
