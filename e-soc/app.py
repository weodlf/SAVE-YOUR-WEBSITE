import atexit
import hmac
import ipaddress
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from flask import Flask, jsonify, render_template, request, session
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from engine import audit, resolve_target, inspect_payload, enforce, discord_alert
from models import db, SecurityLog, BlockedIP, ScanHistory, serialize


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=os.getenv('SECRET_KEY') or secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URL', 'sqlite:///esoc.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False, MAX_CONTENT_LENGTH=16384,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE') == '1',
        ADMIN_TOKEN=os.getenv('ADMIN_TOKEN') or secrets.token_urlsafe(32),
        SCAN_ALLOWLIST=os.getenv('SCAN_ALLOWLIST', '127.0.0.0/8,::1/128'),
        FIREWALL_ENABLED=os.getenv('FIREWALL_ENABLED') == '1',
        DISCORD_WEBHOOK_URL=os.getenv('DISCORD_WEBHOOK_URL', ''))
    if config:
        app.config.update(config)
    if os.getenv('APP_ENV') == 'production' and not app.config.get('TESTING'):
        for name in ('SECRET_KEY', 'ADMIN_TOKEN'):
            if not os.getenv(name) or len(os.environ[name]) < 32:
                raise RuntimeError(f'{name} must be configured with at least 32 characters in production')
    db.init_app(app)
    sio = SocketIO(app, async_mode='gevent', cors_allowed_origins=None, max_http_buffer_size=16384)
    limiter = Limiter(get_remote_address, app=app, default_limits=['180 per minute'], storage_uri='memory://')
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='esoc-job')
    slots = threading.BoundedSemaphore(4)
    # gevent queues must be touched on their owning hub, not native probe threads.
    from gevent import get_hub
    hub = get_hub()
    owner_thread = threading.get_ident()
    def emit(name, data, **kwargs):
        if threading.get_ident() == owner_thread:
            sio.emit(name, data, **kwargs)
        else:
            hub.loop.run_callback_threadsafe(lambda: sio.emit(name, data, **kwargs))

    lock = threading.RLock()
    blocked = set()
    atexit.register(pool.shutdown, wait=False, cancel_futures=True)
    with app.app_context():
        db.create_all()
        blocked.update(row.ip for row in db.session.scalars(db.select(BlockedIP)))
        for row in db.session.scalars(db.select(ScanHistory).where(ScanHistory.status.in_(['queued', 'running']))):
            row.status = 'interrupted'
        db.session.commit()
    app.extensions['socketio'] = sio
    app.extensions['job_pool'] = pool
    app.extensions['rate_limiter'] = limiter

    def protected(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get('authenticated'):
                return jsonify(error='먼저 운영자 인증을 해 주세요.'), 401
            if request.method != 'GET' and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', secrets.token_hex(32))):
                return jsonify(error='CSRF 토큰이 올바르지 않아요. 다시 로그인해 주세요.'), 403
            return fn(*args, **kwargs)
        return wrapped

    def record(ip, kind, severity, message, simulated=False):
        row = SecurityLog(ip=ip, kind=kind, severity=severity, message=message, simulated=simulated)
        db.session.add(row)
        db.session.commit()
        emit('security_alert', serialize(row), to='operators')
        if severity == 'critical' and not simulated:
            pool.submit(notify, kind, message)
        emit('metrics', metrics(), to='operators')
        return row

    def notify(title, message):
        try:
            discord_alert(app.config['DISCORD_WEBHOOK_URL'], title, message)
        except Exception:
            app.logger.warning('Discord alert delivery failed; consult webhook configuration')

    def metrics():
        rows = db.session.execute(db.select(SecurityLog.kind, db.func.count()).group_by(SecurityLog.kind)).all()
        return {'events': sum(count for _, count in rows), 'blocked': db.session.query(BlockedIP).count(),
                'scans': db.session.query(ScanHistory).count(), 'types': dict(rows),
                'simulated': db.session.query(SecurityLog).filter_by(simulated=True).count()}

    @app.after_request
    def headers(response):
        response.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store',
            'Content-Security-Policy': "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' https://cdn.jsdelivr.net; connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response

    @app.errorhandler(429)
    def rate_error(_):
        return jsonify(error='요청이 너무 많아요. 잠시 후 다시 시도해 주세요.'), 429

    @app.errorhandler(413)
    def size_error(_):
        return jsonify(error='입력 크기가 제한을 초과했어요.'), 413

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/')
    def index():
        return render_template('home.html')

    @app.get('/console')
    def console():
        return render_template('index.html', public_mode=True)

    @app.get('/operator')
    def operator():
        return render_template('index.html', public_mode=False)

    @app.post('/api/public/inspect')
    @limiter.limit('30 per minute')
    def public_inspect():
        data = request.get_json(silent=True)
        payload = data.get('payload') if isinstance(data, dict) else None
        if not isinstance(payload, str) or len(payload) > 4096:
            return jsonify(error='입력은 4,096자 이내로 작성해 주세요.'), 400
        return jsonify(detected=inspect_payload(payload), simulated=True)


    @app.post('/api/login')
    @limiter.limit('10 per minute')
    def login():
        token = (request.get_json(silent=True) or {}).get('token', '')
        if not isinstance(token, str) or not hmac.compare_digest(token, app.config['ADMIN_TOKEN']):
            return jsonify(error='운영자 토큰을 확인해 주세요.'), 401
        session.clear()
        session.update(authenticated=True, csrf=secrets.token_hex(32))
        return jsonify(csrf=session['csrf'])

    @app.get('/api/state')
    @protected
    def state():
        return jsonify(metrics=metrics(), csrf=session['csrf'],
            logs=[serialize(r) for r in db.session.scalars(db.select(SecurityLog).order_by(SecurityLog.id.desc()).limit(100))],
            scans=[serialize(r) for r in db.session.scalars(db.select(ScanHistory).order_by(ScanHistory.id.desc()).limit(20))],
            blocked=[serialize(r) for r in db.session.scalars(db.select(BlockedIP))],
            firewall_enabled=app.config['FIREWALL_ENABLED'])

    def scan_job(scan_id, target):
        with app.app_context():
            try:
                parsed, ip, port = resolve_target(target, app.config['SCAN_ALLOWLIST'])
                def progress(value):
                    row = db.session.get(ScanHistory, scan_id)
                    row.status, row.progress = 'running', value
                    db.session.commit()
                    emit('scan_progress', serialize(row), to='operators')
                result = audit(parsed, ip, port, progress)
                row = db.session.get(ScanHistory, scan_id)
                row.status, row.results = 'completed', result
                db.session.commit()
                for item in result['ports']:
                    if item['status'] == 'open':
                        severity = 'critical' if item['port'] in (21, 23, 3306) else 'medium'
                        record(ip, 'Open Ports', severity, f"TCP {item['port']} open; verify intended exposure.")
                        if severity != 'critical':
                            notify('Open port detected', f"{ip}:{item['port']}")
                http = result['http']
                if not http.get('error'):
                    missing = [key for key, value in http['headers'].items() if not value]
                    if missing:
                        record(ip, 'Security Headers', 'medium', 'Missing: ' + ', '.join(missing))
            except Exception as exc:
                db.session.rollback()
                row = db.session.get(ScanHistory, scan_id)
                row.status, row.results = 'failed', {'error': str(exc)}
                db.session.commit()
            finally:
                emit('scan_progress', serialize(db.session.get(ScanHistory, scan_id)), to='operators')
                emit('metrics', metrics(), to='operators')
                db.session.remove()
                slots.release()

    @app.post('/api/scans')
    @protected
    @limiter.limit('6 per minute')
    def scans():
        target = (request.get_json(silent=True) or {}).get('target', '')
        if not isinstance(target, str) or not 1 <= len(target) <= 255:
            return jsonify(error='유효한 서버 주소를 입력해 주세요.'), 400
        if not slots.acquire(blocking=False):
            return jsonify(error='점검 작업이 가득 찼어요. 잠시 후 시도해 주세요.'), 429
        row = ScanHistory(target=target)
        try:
            db.session.add(row)
            db.session.commit()
            result = serialize(row)
            pool.submit(scan_job, row.id, target)
        except Exception:
            slots.release()
            raise
        return jsonify(result), 202

    @app.post('/api/simulate')
    @protected
    def simulate():
        payload = (request.get_json(silent=True) or {}).get('payload', '')
        if not isinstance(payload, str) or len(payload) > 4096:
            return jsonify(error='입력은 4,096자 이내로 작성해 주세요.'), 400
        kinds = inspect_payload(payload)
        for kind in kinds or ['Normal Traffic']:
            record('192.0.2.10', kind, 'critical' if kinds else 'info', '교육용 페이로드 검사 · 실제 차단 없음', True)
        return jsonify(detected=kinds, simulated=True)

    @app.post('/api/inspect')
    @protected
    def inspect():
        # Integrators must use the returned verdict to deny upstream requests.
        payload = (request.get_json(silent=True) or {}).get('payload', '')
        if not isinstance(payload, str) or len(payload) > 4096:
            return jsonify(error='Invalid payload'), 400
        ip = str(ipaddress.ip_address(request.remote_addr))
        with lock:
            if ip in blocked:
                return jsonify(allowed=False, reason='blocked'), 403
        kinds = inspect_payload(payload)
        if kinds:
            with lock:
                if ip not in blocked:
                    address = ipaddress.ip_address(ip)
                    status = 'protected-address' if address.is_loopback else enforce(ip, app.config['FIREWALL_ENABLED'])
                    db.session.add(BlockedIP(ip=ip, reason=', '.join(kinds), os_status=status))
                    db.session.commit()
                    blocked.add(ip)
            for kind in kinds:
                record(ip, kind, 'critical', 'Malicious request denied by inspection API')
        else:
            record(ip, 'Normal Traffic', 'info', 'Inspection passed')
        return jsonify(allowed=not kinds, detected=kinds), 403 if kinds else 200

    @app.delete('/api/blocks/<int:block_id>')
    @protected
    def unblock(block_id):
        with lock:
            row = db.session.get(BlockedIP, block_id)
            if not row:
                return jsonify(error='차단 기록을 찾을 수 없어요.'), 404
            if row.os_status == 'enforced':
                status = enforce(row.ip, True, remove=True)
                if status != 'removed':
                    return jsonify(error=status), 502
            blocked.discard(row.ip)
            db.session.delete(row)
            db.session.commit()
        emit('metrics', metrics(), to='operators')
        return jsonify(ok=True)

    @app.delete('/api/logs')
    @protected
    def clear():
        db.session.execute(db.delete(SecurityLog))
        db.session.commit()
        emit('logs_cleared', {}, to='operators')
        emit('metrics', metrics(), to='operators')
        return jsonify(ok=True)

    @app.get('/api/export')
    @protected
    def export():
        response = jsonify(logs=[serialize(r) for r in db.session.scalars(db.select(SecurityLog))],
            scans=[serialize(r) for r in db.session.scalars(db.select(ScanHistory))],
            blocked=[serialize(r) for r in db.session.scalars(db.select(BlockedIP))])
        response.headers['Content-Disposition'] = 'attachment; filename=esoc-export.json'
        return response

    @sio.on('connect')
    def connect(auth=None):
        if not session.get('authenticated'):
            return False
        from flask_socketio import join_room
        join_room('operators')

    return app

if __name__ == '__main__':
    app = create_app()
    if not os.getenv('ADMIN_TOKEN'):
        print('Temporary operator token (set ADMIN_TOKEN for persistent use):', app.config['ADMIN_TOKEN'])
    app.extensions['socketio'].run(app, host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '5000')), debug=False)
