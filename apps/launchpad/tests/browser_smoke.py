"""Optional real Chromium + Docker UI check; uses only temporary services/data."""
import json
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

ROOT = Path(__file__).resolve().parents[3]
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']


def run():
    sid = None
    with tempfile.TemporaryDirectory() as temp:
        password = secrets.token_urlsafe(32)
        child_password = secrets.token_urlsafe(32)
        environment = dict(os.environ, LAUNCHPAD_STATE=temp, LAUNCHPAD_PASSWORD=password, PROJECT_ROOT=str(ROOT))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        with open(f'{temp}/server.log', 'w') as output:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'apps.launchpad.main:app', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=environment, stdout=output, stderr=output)
            try:
                for _ in range(80):
                    try:
                        urllib.request.urlopen(base + '/healthz', timeout=1)
                        break
                    except Exception:
                        time.sleep(.15)
                else:
                    raise RuntimeError('LaunchPad did not start')
                with sync_playwright() as p:
                    options = {'headless': True, 'args': ['--no-sandbox']}
                    if shutil.which('chromium'):
                        options['executable_path'] = shutil.which('chromium')
                    browser = p.chromium.launch(**options)
                    context = browser.new_context(http_credentials={'username': 'admin', 'password': password}, viewport={'width': 1440, 'height': 1080})
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base, wait_until='networkidle')
                    page.wait_for_function("document.querySelector('#connection').textContent.includes('Docker 연결됨')")
                    assert page.locator('#empty').is_visible()
                    page.locator('#new-project').click()
                    page.locator('input[name="name"]').fill('guardlab-browser')
                    page.locator('input[name="project"]').fill('apps/guardlab')
                    page.locator('textarea[name="environment"]').fill(f'DASHBOARD_PASSWORD={child_password}\nSCAN_ALLOWED_HOSTS=example.com')
                    page.locator('#project-form [type="submit"]').click()
                    page.locator('.service-card').wait_for()
                    rows = context.request.get(base + '/api/services').json()
                    sid = rows[0]['id']
                    page.locator('.service-actions button').filter(has_text='배포하기').click()
                    page.locator('.badge.running').wait_for(timeout=180000)
                    assert page.locator('#running').inner_text() == '1'
                    artifact = Path(os.getenv('LAUNCHPAD_SCREENSHOTS', '/tmp/launchpad-screenshots'))
                    artifact.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(artifact / 'launchpad-desktop.png'), full_page=True)
                    page.locator('.service-actions button').filter(has_text='로그').click()
                    page.wait_for_function("document.querySelector('#build-logs').textContent.includes('Ready: HTTP')")
                    assert child_password not in page.locator('#build-logs').inner_text()
                    page.screenshot(path=str(artifact / 'launchpad-console.png'), full_page=True)
                    page.locator('[data-close="logs-dialog"]').click()
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.evaluate('window.scrollTo(0,0)')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(artifact / 'launchpad-mobile.png'), full_page=True)
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.locator('.service-actions .remove').click()
                    page.locator('#empty').wait_for()
                    assert not errors, errors
                    print('PASS: LaunchPad desktop/mobile, create, actual deploy, running status, logs/history and delete, no browser JS errors.')
                    browser.close()
            finally:
                server.terminate()
                server.wait(timeout=20)
                if sid:
                    for args in (['rm','-f',f'launchpad-{sid}'], ['volume','rm',f'launchpad-{sid}-data'], ['image','rm',f'launchpad-{sid}']):
                        subprocess.run(DOCKER + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    run()
