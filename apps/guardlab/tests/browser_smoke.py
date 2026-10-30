"""Optional real-browser check: python tests/browser_smoke.py (requires Playwright/Chromium).

Uses temporary SQLite, a random local password and an ephemeral local server.
Never writes test records to the application database.
"""
import csv
import io
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory() as temp:
        password = secrets.token_urlsafe(32)
        runtime = dict(os.environ, APP_ENV='production', DASHBOARD_PASSWORD=password,
                       DATABASE_URL=f'sqlite:///{temp}/security.db', SCAN_ALLOWED_HOSTS='example.com')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        with open(f'{temp}/server.log', 'w') as log:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=runtime, stdout=log, stderr=log)
            try:
                for _ in range(80):
                    try:
                        urllib.request.urlopen(f'{base}/healthz', timeout=1)
                        break
                    except Exception:
                        time.sleep(.15)
                else:
                    raise RuntimeError('Server did not become ready')
                with sync_playwright() as p:
                    options = {'headless': True, 'args': ['--no-sandbox']}
                    if shutil.which('chromium'):
                        options['executable_path'] = shutil.which('chromium')
                    browser = p.chromium.launch(**options)
                    context = browser.new_context(http_credentials={'username': 'admin', 'password': password}, viewport={'width': 1440, 'height': 1120}, accept_downloads=True)
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base, wait_until='networkidle')
                    page.wait_for_function("document.querySelector('#metric-total').textContent === '0'")
                    assert page.title().startswith('GuardLab')
                    assert page.locator('#empty-logs').is_visible()
                    artifact_dir = Path(os.getenv('GUARDLAB_SCREENSHOTS', '/tmp/guardlab-screenshots'))
                    artifact_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(artifact_dir / 'guardlab-desktop.png'), full_page=True)
                    # New UI, password visibility and human-readable results.
                    password_form = page.locator('form[data-api="password-analysis"]')
                    password_form.locator('input').fill('LearningOnly123!')
                    password_form.locator('.password-toggle').click()
                    assert password_form.locator('input').get_attribute('type') == 'text'
                    password_form.locator('[type="submit"]').click()
                    page.wait_for_function("document.querySelector('#metric-total').textContent === '1'")
                    assert '100 / 100' in password_form.locator('.result-body').inner_text()
                    crypto = page.locator('form[data-api="crypto"]')
                    crypto.locator('select').select_option('base64_encode')
                    crypto.locator('textarea').fill('GuardLab 보안 학습')
                    crypto.locator('[type="submit"]').click()
                    crypto.locator('.result-output').wait_for()
                    page.wait_for_function("document.querySelector('#metric-total').textContent === '2'")
                    assert page.locator('#logs tr').count() == 2
                    page.locator('#log-filter').select_option('Crypto')
                    assert page.locator('#logs tr').count() == 1
                    page.locator('#log-search').fill('no-matching-record')
                    assert page.locator('#empty-logs strong').inner_text() == '검색 결과가 없어요'
                    page.locator('#log-search').fill('')
                    page.locator('#log-filter').select_option('')
                    with page.expect_download() as download_info:
                        page.locator('a[href="/api/logs/download"]').click()
                    download = download_info.value
                    assert download.suggested_filename == 'security_scan_logs.csv'
                    exported = Path(download.path()).read_bytes().decode('utf-8-sig')
                    assert len(list(csv.reader(io.StringIO(exported)))) == 3
                    assert 'LearningOnly123!' not in exported
                    # Network restrictions remain visible and handled, with no new record.
                    header = page.locator('form[data-api="header-check"]')
                    header.locator('input').fill('https://unapproved.test')
                    header.locator('[type="submit"]').click()
                    header.locator('.tool-result.error').wait_for()
                    assert '허용한 호스트' in header.locator('.result-body').inner_text()
                    assert page.locator('#logs tr').count() == 2
                    # Responsive navigation and layout.
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.evaluate('window.scrollTo(0,0)')
                    page.locator('#menu-toggle').click()
                    assert page.locator('#menu-toggle').get_attribute('aria-expanded') == 'true'
                    page.locator('.nav-item[href="#tools"]').click()
                    assert page.locator('#menu-toggle').get_attribute('aria-expanded') == 'false'
                    page.evaluate('window.scrollTo(0,0)')
                    page.wait_for_timeout(600)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(artifact_dir / 'guardlab-mobile.png'), full_page=True)
                    # Delete updates charts, totals and empty state.
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.locator('#clear').click()
                    page.wait_for_function("document.querySelector('#metric-total').textContent === '0'")
                    assert page.locator('#logs tr').count() == 0
                    assert page.locator('#empty-logs').is_visible()
                    # CDN outage keeps real summary and the core tools usable.
                    page.route('**/chart.umd.min.js', lambda route: route.abort())
                    page.reload(wait_until='networkidle')
                    page.wait_for_function("document.querySelector('#stats-status').textContent.includes('라이브러리')")
                    assert page.locator('#metric-total').inner_text() == '0'
                    assert '도구별: 없음' in page.locator('#stats-summary').text_content()
                    assert not errors, errors
                    print('PASS: desktop/mobile, login, diagnostics, filters, CSV, delete, CDN fallback; no browser JS errors.')
                    browser.close()
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    run()
