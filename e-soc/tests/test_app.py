import time
from unittest.mock import patch
import pytest
from app import create_app
from engine import inspect_payload, resolve_target, firewall_command, enforce, audit
from models import db, ScanHistory, BlockedIP

@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret',
                      'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "test.db"}', 'RATELIMIT_ENABLED': False})
    yield app
    app.extensions['job_pool'].shutdown(wait=True)

@pytest.fixture
def client(app):
    client = app.test_client()
    response = client.get('/api/state')
    client.csrf = {'X-CSRF-Token': response.json['csrf']}
    return client

@pytest.mark.parametrize('payload,kind',[("' OR 1=1 --",'SQL Injection'), ('%253Cscript%253Ealert(1)','Cross-site Scripting'), ('; whoami','Command Injection'), ('..%2f..%2fetc/passwd','Path Traversal')])
def test_signatures(payload, kind):
    assert kind in inspect_payload(payload)
    assert inspect_payload('hello world') == []

def test_public_csrf_and_socket(app, client):
    assert app.test_client().get('/api/state').status_code == 200
    assert client.post('/api/simulate', json={'payload': 'hello'}).status_code == 403
    anonymous_socket = app.extensions['socketio'].test_client(app)
    assert anonymous_socket.is_connected()
    anonymous_socket.disconnect()
    socket = app.extensions['socketio'].test_client(app, flask_test_client=client)
    assert socket.is_connected()
    client.post('/api/simulate', json={'payload': '<script>x</script>'}, headers=client.csrf)
    assert any(event['name'] == 'security_alert' for event in socket.get_received())
    state = client.get('/api/state').json
    assert state['metrics']['simulated'] == 1
    assert state['blocked'] == []
    assert state['logs'][0]['simulated']
    socket.disconnect()

def test_persistence_clear_and_export(client):
    client.post('/api/simulate', json={'payload': 'hello'}, headers=client.csrf)
    exported = client.get('/api/export')
    assert exported.json['logs'][0]['kind'] == 'Normal Traffic'
    assert 'attachment' in exported.headers['Content-Disposition']
    assert client.delete('/api/logs', headers=client.csrf).status_code == 200
    assert client.get('/api/state').json['metrics']['events'] == 0

def test_scan_async(app, client):
    result = {'ports': [{'port': 23, 'status': 'open'}], 'http': {'headers': {'X-Frame-Options': None}}}
    with patch('app.audit', return_value=result), patch('app.resolve_target', return_value=(None, '127.0.0.1', 80)):
        response = client.post('/api/scans', json={'target': 'http://127.0.0.1'}, headers=client.csrf)
        assert response.status_code == 202
        for _ in range(100):
            state = client.get('/api/state').json
            if state['scans'][0]['status'] == 'completed' and len(state['logs']) == 2:
                break
            time.sleep(.02)
        assert state['scans'][0]['results'] == result
        assert len(state['logs']) == 2

def test_target_allowlist():
    with patch('socket.getaddrinfo', return_value=[(2,1,6,'',('169.254.169.254',80))]):
        with pytest.raises(ValueError):
            resolve_target('http://example.com', '127.0.0.0/8')
    with pytest.raises(ValueError):
        resolve_target('file:///etc/passwd', '127.0.0.0/8')

def test_firewall_never_shell():
    with pytest.raises(ValueError):
        firewall_command('1.2.3.4; id')
    with patch('engine.subprocess.run') as run:
        assert enforce('192.0.2.1', False) == 'disabled'
        run.assert_not_called()
        assert enforce('192.0.2.1', True) == 'enforced'
        assert run.call_args.kwargs.get('shell', False) is False

def test_inspection_blocks_then_unblocks(client):
    with patch('app.enforce', return_value='disabled'):
        response = client.post('/api/inspect', json={'payload': '; whoami'}, headers=client.csrf, environ_base={'REMOTE_ADDR': '192.0.2.50'})
        assert response.status_code == 403
        blocks = client.get('/api/state').json['blocked']
        assert blocks[0]['os_status'] == 'disabled'
        assert client.delete(f"/api/blocks/{blocks[0]['id']}", headers=client.csrf).status_code == 200
        assert client.get('/api/state').json['blocked'] == []

def test_headers(client):
    response = client.get('/')
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']


def test_public_console_without_login(app):
    anonymous = app.test_client()
    html = anonymous.get('/console').get_data(as_text=True)
    assert 'loginPanel' not in html
    assert 'IP 차단 등록' in html
    assert anonymous.get('/operator').status_code == 302
    response = anonymous.post('/api/public/inspect', json={'payload': "' OR 1=1 --"})
    assert response.status_code == 200
    assert response.json == {'detected': ['SQL Injection'], 'simulated': True}
    with app.app_context():
        from models import SecurityLog
        assert db.session.query(SecurityLog).count() == 0
        assert db.session.query(BlockedIP).count() == 0


def test_shared_operations_need_csrf_but_no_login(app):
    anonymous = app.test_client()
    assert anonymous.get('/api/state').status_code == 200
    assert anonymous.get('/api/export').status_code == 200
    for path, method in [('/api/scans', 'POST'), ('/api/inspect', 'POST'),
                         ('/api/logs', 'DELETE'), ('/api/blocks', 'POST'),
                         ('/api/blocks/1', 'DELETE')]:
        assert anonymous.open(path, method=method, json={}).status_code == 403
    response = anonymous.get('/api/state')
    headers = {'X-CSRF-Token': response.json['csrf']}
    assert anonymous.delete('/api/logs', headers=headers).status_code == 200
    assert anonymous.delete('/api/blocks/9999', headers=headers).status_code == 404
    other = app.test_client()
    assert other.delete('/api/logs', headers=headers).status_code == 403
    assert anonymous.post('/api/login', json={}).status_code == 404
    for payload in ({'payload': 'x' * 4097}, {'payload': 123}, [], None):
        assert anonymous.post('/api/public/inspect', json=payload).status_code == 400
        assert anonymous.post('/api/simulate', json=payload, headers=headers).status_code == 400


def test_manual_block_is_shared_and_reversible(app, client):
    with patch('app.enforce', return_value='disabled') as firewall:
        response = client.post('/api/blocks', json={'ip': '192.0.2.50'}, headers=client.csrf)
        assert response.status_code == 201
        block = response.json
        assert block['os_status'] == 'disabled'
        duplicate = client.post('/api/blocks', json={'ip': '192.0.2.50'}, headers=client.csrf)
        assert duplicate.json['id'] == block['id']
        assert firewall.call_count == 1
        assert app.test_client().get('/api/state').json['blocked'][0]['ip'] == '192.0.2.50'
        denied = client.post('/api/inspect', json={'payload': 'hello'}, headers=client.csrf,
                             environ_base={'REMOTE_ADDR': '192.0.2.50'})
        assert denied.status_code == 403
        assert denied.json['reason'] == 'blocked'
        assert client.delete(f"/api/blocks/{block['id']}", headers=client.csrf).status_code == 200
        assert not app.test_client().get('/api/state').json['blocked']


@pytest.mark.parametrize('value', ['1.2.3.4; id', '0.0.0.0', '224.0.0.1', 'fe80::1%eth0', None, 123])
def test_manual_block_rejects_invalid_addresses(client, value):
    assert client.post('/api/blocks', json={'ip': value}, headers=client.csrf).status_code == 400


def test_failed_os_unblock_retains_shared_record(client):
    with patch('app.enforce', return_value='enforced'):
        block = client.post('/api/blocks', json={'ip': '192.0.2.80'}, headers=client.csrf).json
    with patch('app.enforce', return_value='failed: TimeoutExpired'):
        response = client.delete(f"/api/blocks/{block['id']}", headers=client.csrf)
        assert response.status_code == 502
        assert client.get('/api/state').json['blocked'][0]['ip'] == '192.0.2.80'
