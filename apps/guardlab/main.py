import asyncio
import base64
import binascii
import csv
import io
import os
import secrets
import hashlib
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import ScanLog


@asynccontextmanager
async def lifespan(app):
    if os.getenv('APP_ENV') == 'production' and len(os.getenv('DASHBOARD_PASSWORD', '')) < 20:
        raise RuntimeError('Production requires DASHBOARD_PASSWORD with at least 20 characters')
    Base.metadata.create_all(engine)
    app.state.scan_pool = ThreadPoolExecutor(max_workers=20, thread_name_prefix='port-scan')
    app.state.scan_slots = asyncio.Semaphore(4)
    app.state.header_slots = asyncio.Semaphore(20)
    try:
        async with httpx.AsyncClient(
            timeout=5, follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        ) as client:
            app.state.http_client = client
            yield
    finally:
        await run_in_threadpool(app.state.scan_pool.shutdown, wait=True, cancel_futures=True)


app = FastAPI(title='GuardLab — Security Learning Workspace', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=str(Path(__file__).parent / 'static')), name='static')
templates = Jinja2Templates(directory=str(Path(__file__).parent / 'templates'))


@app.middleware('http')
async def authenticate(request: Request, call_next):
    password = os.getenv('DASHBOARD_PASSWORD', '')
    protected = password or os.getenv('APP_ENV') == 'production'
    if request.url.path != '/healthz' and protected:
        authorized = False
        try:
            scheme, token = request.headers.get('authorization', '').split(' ', 1)
            credentials = base64.b64decode(token, validate=True).decode('utf-8')
            username, supplied = credentials.split(':', 1)
            authorized = (
                scheme.lower() == 'basic' and bool(password)
                and secrets.compare_digest(username.encode(), b'admin')
                and secrets.compare_digest(supplied.encode(), password.encode())
            )
        except (ValueError, UnicodeError, binascii.Error):
            pass
        if not authorized:
            return JSONResponse(
                {'detail': '로그인이 필요합니다.'}, status_code=401,
                headers={'WWW-Authenticate': 'Basic realm="GuardLab", charset="UTF-8"'},
            )
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path != '/healthz':
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/healthz', include_in_schema=False)
def health():
    return {'status': 'ok'}


def check_scan_target(host: str):
    allowed = {entry.strip().lower().rstrip('.') for entry in
               os.getenv('SCAN_ALLOWED_HOSTS', '').split(',') if entry.strip()}
    if (os.getenv('APP_ENV') == 'production' or allowed) and host.lower().rstrip('.') not in allowed:
        raise HTTPException(403, '배포 설정에서 허용한 호스트만 진단할 수 있습니다.')


def record(db, tool, target, summary):
    db.add(ScanLog(tool_type=tool, target=target, result_summary=summary))
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


@app.get('/')
def index(request: Request):
    return templates.TemplateResponse(request=request, name='index.html')


@app.get('/api/logs')
def logs(db: Session = Depends(get_db)):
    rows = db.scalars(select(ScanLog).order_by(ScanLog.created_at.desc(), ScanLog.id.desc()).limit(50))
    return [dict(id=r.id, tool_type=r.tool_type, target=r.target,
                 result_summary=r.result_summary,
                 created_at=r.created_at.replace(tzinfo=timezone.utc).isoformat()) for r in rows]


@app.get('/api/stats')
def stats(db: Session = Depends(get_db)):
    # Dates use the same UTC convention as the stored audit timestamps.
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=13)
    tool_rows = db.execute(
        select(ScanLog.tool_type, func.count(ScanLog.id))
        .group_by(ScanLog.tool_type).order_by(ScanLog.tool_type)
    )
    day = func.date(ScanLog.created_at)
    date_rows = db.execute(
        select(day, func.count(ScanLog.id))
        .where(ScanLog.created_at >= datetime.combine(start, time.min),
               ScanLog.created_at < datetime.combine(today + timedelta(days=1), time.min))
        .group_by(day).order_by(day)
    )
    counts = dict(date_rows.all())
    return {
        'by_tool': dict(tool_rows.all()),
        'by_date': {
            (start + timedelta(days=i)).isoformat(): counts.get(
                (start + timedelta(days=i)).isoformat(), 0
            ) for i in range(14)
        },
    }


def csv_text(value: str) -> str:
    # Prevent user-controlled cells from becoming spreadsheet formulas.
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


@app.get('/api/logs/download')
def download_logs(db: Session = Depends(get_db)):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['ID', 'Tool Type', 'Target', 'Result Summary', 'Created At'])
    rows = db.scalars(
        select(ScanLog).order_by(ScanLog.created_at.desc(), ScanLog.id.desc())
        .execution_options(yield_per=1000)
    )
    for row in rows:
        writer.writerow([
            row.id, csv_text(row.tool_type), csv_text(row.target),
            csv_text(row.result_summary),
            row.created_at.replace(tzinfo=timezone.utc).isoformat(),
        ])
    return Response(
        content=output.getvalue().encode('utf-8-sig'),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename=security_scan_logs.csv',
            'Cache-Control': 'no-store',
        },
    )


@app.delete('/api/logs')
def clear_logs(db: Session = Depends(get_db)):
    count = db.execute(delete(ScanLog)).rowcount
    db.commit()
    return {'deleted': count}


class PortInput(BaseModel):
    target: str = Field(min_length=1, max_length=253)
    ports: list[int] = Field(default=[22, 80, 443], min_length=1, max_length=32)


@asynccontextmanager
async def admission(semaphore):
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
    except TimeoutError:
        raise HTTPException(503, '진단 요청이 많습니다. 잠시 후 다시 시도하세요.')
    try:
        yield
    finally:
        semaphore.release()


def probe_port(address, port):
    try:
        with socket.socket(address[0], socket.SOCK_STREAM) as connection:
            connection.settimeout(0.5)
            endpoint = (address[4][0], port, *address[4][2:])
            return port, connection.connect_ex(endpoint) == 0, False
    except OSError:
        return port, False, True


@app.post('/api/port-scan')
async def port_scan(data: PortInput, request: Request, db: Session = Depends(get_db)):
    if any(p < 1 or p > 65535 for p in data.ports):
        raise HTTPException(422, '포트 범위는 1~65535입니다.')
    check_scan_target(data.target)
    async with admission(request.app.state.scan_slots):
        loop = asyncio.get_running_loop()
        pool = request.app.state.scan_pool
        try:
            addresses = await asyncio.wait_for(
                loop.run_in_executor(pool, lambda: socket.getaddrinfo(
                    data.target, None, type=socket.SOCK_STREAM)), timeout=5,
            )
            address = addresses[0]
        except (OSError, TimeoutError, IndexError):
            await run_in_threadpool(record, db, 'Port Scanner', data.target, 'Error: DNS lookup failed')
            raise HTTPException(400, '대상 주소를 확인할 수 없습니다.')
        results = await asyncio.gather(*[
            loop.run_in_executor(pool, probe_port, address, port)
            for port in sorted(set(data.ports))
        ])
        opened = [port for port, is_open, _ in results if is_open]
        errors = sum(failed for _, _, failed in results)
        summary = 'Open: ' + (', '.join(map(str, opened)) or 'none')
        if errors:
            summary += f'; Network errors: {errors}'
        await run_in_threadpool(record, db, 'Port Scanner', data.target, summary)
        return {'open_ports': opened, 'summary': summary}


class HeaderInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@app.post('/api/header-check')
async def header_check(data: HeaderInput, request: Request, db: Session = Depends(get_db)):
    try:
        url = httpx.URL(data.url)
    except httpx.InvalidURL:
        raise HTTPException(422, '유효한 HTTP(S) URL을 입력하세요.')
    if url.scheme not in ('http', 'https') or not url.host or url.username or url.password:
        raise HTTPException(422, '인증 정보가 없는 HTTP(S) URL을 입력하세요.')
    check_scan_target(url.host)
    names = ['content-security-policy', 'strict-transport-security', 'x-content-type-options',
             'x-frame-options', 'referrer-policy']
    async with admission(request.app.state.header_slots):
        try:
            response = await asyncio.wait_for(
                request.app.state.http_client.get(str(url)), timeout=6,
            )
        except (httpx.HTTPError, TimeoutError):
            await run_in_threadpool(record, db, 'Header Check', data.url, 'Error: HTTP request failed')
            raise HTTPException(400, '대상 서버에 연결할 수 없습니다.')
        present = {name: name in response.headers for name in names}
        score = sum(present.values()) * 20
        summary = f'Security Score: {score}/100'
        await run_in_threadpool(record, db, 'Header Check', data.url, summary)
        return {'headers': present, 'score': score, 'summary': summary}


class PasswordInput(BaseModel):
    password: str = Field(min_length=1, max_length=4096)


@app.post('/api/password-analysis')
def password_analysis(data: PasswordInput, db: Session = Depends(get_db)):
    value = data.password
    checks = [len(value) >= 12, bool(re.search('[a-z]', value)), bool(re.search('[A-Z]', value)),
              bool(re.search('[0-9]', value)), bool(re.search(r'[^a-zA-Z0-9]', value))]
    score = sum(checks) * 20
    summary = f'Security Score: {score}/100 (heuristic)'
    # Persist the fact of analysis without retaining a password or its hash.
    record(db, 'Password Analysis', '[REDACTED]', summary)
    return {'score': score, 'summary': summary}


class CryptoInput(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    operation: Literal['sha256', 'base64_encode', 'base64_decode'] = 'sha256'


@app.post('/api/crypto')
def crypto(data: CryptoInput, db: Session = Depends(get_db)):
    try:
        if data.operation == 'sha256':
            output = hashlib.sha256(data.text.encode()).hexdigest()
        elif data.operation == 'base64_encode':
            output = base64.b64encode(data.text.encode()).decode()
        else:
            output = base64.b64decode(data.text, validate=True).decode()
    except (ValueError, UnicodeError, binascii.Error):
        record(db, 'Crypto', '[REDACTED]', f'Error: {data.operation} invalid input')
        raise HTTPException(422, '유효한 UTF-8 Base64 입력이 아닙니다.')
    summary = f'{data.operation}: completed'
    record(db, 'Crypto', '[REDACTED]', summary)
    return {'output': output, 'summary': summary}
