import socket
from datetime import datetime
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from database import Base, get_db
from models import ScanLog


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}', connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(main, 'engine', engine)
    def db():
        with sessions() as session:
            yield session
    main.app.dependency_overrides[get_db] = db
    with TestClient(main.app) as client:
        yield client, sessions
    main.app.dependency_overrides.clear()
    engine.dispose()


def test_tools_and_history(client):
    api, sessions = client
    assert api.get('/').status_code == 200
    assert api.get('/api/logs').json() == []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = api.post('/api/port-scan', json={'target': '127.0.0.1', 'ports': [port]})
        assert result.json()['open_ports'] == [port]
    with patch.object(main.httpx.AsyncClient, 'get', return_value=httpx.Response(200, headers={'x-frame-options': 'DENY'})):
        assert api.post('/api/header-check', json={'url': 'https://example.com'}).json()['score'] == 20
    assert api.post('/api/password-analysis', json={'password': 'Secret123!abc'}).json()['score'] == 100
    assert api.post('/api/crypto', json={'text': 'hello', 'operation': 'base64_encode'}).json()['output'] == 'aGVsbG8='
    rows = api.get('/api/logs').json()
    assert [r['tool_type'] for r in rows] == ['Crypto', 'Password Analysis', 'Header Check', 'Port Scanner']
    assert rows[0]['target'] == rows[1]['target'] == '[REDACTED]'
    assert all(r['created_at'].endswith('+00:00') for r in rows)
    # New sessions read persisted data.
    with sessions() as db:
        assert db.get(ScanLog, rows[0]['id']).tool_type == 'Crypto'
    assert api.delete('/api/logs').json() == {'deleted': 4}
    assert api.get('/api/logs').json() == []
    assert api.delete('/api/logs').json() == {'deleted': 0}


def test_latest_50_and_tie_order(client):
    api, sessions = client
    with sessions() as db:
        db.add_all([ScanLog(tool_type='Crypto', target=str(i), result_summary='ok', created_at=datetime(2026, 1, 1)) for i in range(55)])
        db.commit()
    rows = api.get('/api/logs').json()
    assert len(rows) == 50
    assert [r['target'] for r in rows] == [str(i) for i in range(54, 4, -1)]


def test_failures_and_validation(client):
    api, _ = client
    assert api.post('/api/port-scan', json={'target': 'localhost', 'ports': [0]}).status_code == 422
    assert api.post('/api/header-check', json={'url': 'file:///etc/passwd'}).status_code == 422
    assert api.get('/api/logs').json() == []
    assert api.post('/api/crypto', json={'text': '%%%','operation': 'base64_decode'}).status_code == 422
    with patch.object(main.socket, 'getaddrinfo', side_effect=socket.gaierror()):
        assert api.post('/api/port-scan', json={'target': 'invalid.test'}).status_code == 400
    with patch.object(main.httpx.AsyncClient, 'get', side_effect=httpx.ConnectError('offline')):
        assert api.post('/api/header-check', json={'url': 'https://example.com'}).status_code == 400
    assert len(api.get('/api/logs').json()) == 3


def test_csv_empty_and_download_link(client):
    import csv
    import io

    api, _ = client
    response = api.get('/api/logs/download')
    assert response.status_code == 200
    assert response.headers['content-disposition'] == 'attachment; filename=security_scan_logs.csv'
    assert response.headers['content-type'] == 'text/csv; charset=utf-8'
    assert response.headers['cache-control'] == 'no-store'
    assert response.content.startswith(b'\xef\xbb\xbf')
    assert list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')))) == [
        ['ID', 'Tool Type', 'Target', 'Result Summary', 'Created At']
    ]
    assert 'href="/api/logs/download"' in api.get('/').text


def test_csv_all_rows_escaping_and_formula_protection(client):
    import csv
    import io

    api, sessions = client
    with sessions() as db:
        db.add_all([
            ScanLog(tool_type='헤더 진단', target=f'대상,{i}',
                    result_summary='줄1\n"줄2"', created_at=datetime(2026, 1, 1))
            for i in range(55)
        ])
        db.add(ScanLog(tool_type='=1+1', target='  @SUM(1)',
                       result_summary='+1', created_at=datetime(2026, 1, 2)))
        db.commit()
    response = api.get('/api/logs/download')
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows) == 57
    assert rows[1][1:4] == ["'=1+1", "'  @SUM(1)", "'+1"]
    assert rows[2][1:4] == ['헤더 진단', '대상,54', '줄1\n"줄2"']
    assert rows[-1][2] == '대상,0'
    assert rows[1][4] == '2026-01-02T00:00:00+00:00'
    assert len(api.get('/api/logs').json()) == 50


def test_stats_empty_and_chart_markup(client):
    from datetime import timedelta, timezone

    api, _ = client
    data = api.get('/api/stats').json()
    today = datetime.now(timezone.utc).date()
    assert data['by_tool'] == {}
    assert data['by_date'] == {
        (today - timedelta(days=i)).isoformat(): 0 for i in range(13, -1, -1)
    }
    page = api.get('/').text
    assert 'chart.js@4.4.8' in page
    assert 'id="tool-chart"' in page and 'id="date-chart"' in page


def test_stats_aggregates_all_logs_and_date_boundaries(client):
    from datetime import time, timedelta, timezone

    api, sessions = client
    today = datetime.now(timezone.utc).date()
    midnight = datetime.combine(today, time.min)
    with sessions() as db:
        for offset, tool, count in [(0, 'Crypto', 55), (13, 'Header Check', 2), (14, 'Port Scanner', 3)]:
            db.add_all([ScanLog(tool_type=tool, target='test', result_summary='ok',
                                created_at=midnight - timedelta(days=offset)) for _ in range(count)])
        db.add(ScanLog(tool_type='Crypto', target='test', result_summary='ok',
                       created_at=midnight - timedelta(days=1, microseconds=1)))
        db.commit()
    data = api.get('/api/stats').json()
    assert data['by_tool'] == {'Crypto': 56, 'Header Check': 2, 'Port Scanner': 3}
    assert data['by_date'][today.isoformat()] == 55
    assert data['by_date'][(today - timedelta(days=2)).isoformat()] == 1
    assert data['by_date'][(today - timedelta(days=13)).isoformat()] == 2
    assert sum(data['by_date'].values()) == 58
    assert list(data['by_date']) == sorted(data['by_date'])
    api.delete('/api/logs')
    assert api.get('/api/stats').json()['by_tool'] == {}
    assert sum(api.get('/api/stats').json()['by_date'].values()) == 0
    api.post('/api/crypto', json={'text': 'test'})
    assert api.get('/api/stats').json()['by_tool'] == {'Crypto': 1}


def test_port_probes_run_in_parallel_and_handle_socket_errors(client):
    import threading

    api, _ = client
    barrier = threading.Barrier(3, timeout=3)
    def probe(address, port):
        barrier.wait()  # A serial implementation cannot pass this barrier.
        return port, port == 443, port == 22
    with patch.object(main, 'probe_port', side_effect=probe):
        result = api.post('/api/port-scan', json={'target': '127.0.0.1', 'ports': [443, 80, 22, 443]})
    assert result.status_code == 200
    assert result.json()['open_ports'] == [443]
    assert 'Network errors: 1' in result.json()['summary']
    with patch.object(main.socket, 'socket', side_effect=OSError('unreachable')):
        assert main.probe_port((socket.AF_INET, 0, 0, '', ('127.0.0.1', 0)), 80) == (80, False, True)


def test_async_headers_overlap_and_do_not_block_stats(client):
    import asyncio

    api, _ = client
    async def scenario():
        started = 0
        both_started = asyncio.Event()
        release = asyncio.Event()
        async def slow_get(url):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await release.wait()
            return httpx.Response(200, headers={'x-frame-options': 'DENY'})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as caller:
            with patch.object(main.app.state.http_client, 'get', side_effect=slow_get):
                tasks = [asyncio.create_task(caller.post('/api/header-check', json={'url': 'https://example.com'})) for _ in range(2)]
                try:
                    await asyncio.wait_for(both_started.wait(), 3)
                    stats = await asyncio.wait_for(caller.get('/api/stats'), 3)
                    assert stats.status_code == 200
                finally:
                    release.set()
                    responses = await asyncio.gather(*tasks)
                assert all(r.status_code == 200 and r.json()['score'] == 20 for r in responses)
    api.portal.call(scenario)
    assert len(api.get('/api/logs').json()) == 2


def test_header_timeout_and_overload(client):
    api, _ = client
    with patch.object(main.app.state.http_client, 'get', side_effect=TimeoutError):
        assert api.post('/api/header-check', json={'url': 'https://example.com'}).status_code == 400
    async def occupy():
        for _ in range(20):
            await main.app.state.header_slots.acquire()
    async def release():
        for _ in range(20):
            main.app.state.header_slots.release()
    api.portal.call(occupy)
    try:
        assert api.post('/api/header-check', json={'url': 'https://example.com'}).status_code == 503
    finally:
        api.portal.call(release)
    assert len(api.get('/api/logs').json()) == 1


def test_production_auth_and_target_limits(client, monkeypatch):
    api, _ = client
    password = 'test-only-long-password-123'
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('DASHBOARD_PASSWORD', password)
    assert api.get('/healthz').status_code == 200
    for path in ['/', '/docs', '/openapi.json', '/api/logs', '/api/logs/download', '/api/stats']:
        response = api.get(path)
        assert response.status_code == 401
        assert 'Basic' in response.headers['www-authenticate']
    assert api.delete('/api/logs').status_code == 401
    assert api.get('/', auth=('admin', 'wrong')).status_code == 401
    auth = ('admin', password)
    assert api.get('/', auth=auth).status_code == 200
    assert api.get('/api/stats', auth=auth).status_code == 200
    assert api.post('/api/port-scan', auth=auth, json={'target': 'unapproved.test'}).status_code == 403
    assert api.post('/api/header-check', auth=auth, json={'url': 'https://unapproved.test'}).status_code == 403
    monkeypatch.setenv('SCAN_ALLOWED_HOSTS', 'example.com')
    with patch.object(main.app.state.http_client, 'get', return_value=httpx.Response(200)):
        assert api.post('/api/header-check', auth=auth, json={'url': 'https://example.com'}).status_code == 200
    monkeypatch.delenv('DASHBOARD_PASSWORD')
    assert api.get('/').status_code == 401


def test_production_refuses_missing_password(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('DASHBOARD_PASSWORD', raising=False)
    with pytest.raises(RuntimeError, match='DASHBOARD_PASSWORD'):
        with TestClient(main.app):
            pass


def test_guardlab_web_assets(client):
    api, _ = client
    page = api.get('/')
    assert 'GuardLab' in page.text
    assert 'id="log-search"' in page.text
    assert 'id="log-filter"' in page.text
    assert 'id="menu-toggle"' in page.text
    assert page.text.count('data-api=') == 4
    css = api.get('/static/app.css')
    js = api.get('/static/app.js')
    assert css.status_code == js.status_code == 200
    assert 'text/css' in css.headers['content-type']
    assert 'function renderLogs' in js.text
    assert 'prefers-reduced-motion' in css.text
