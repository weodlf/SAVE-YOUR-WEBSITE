"""Bounded auditing, signature inspection, and explicit firewall enforcement."""
import http.client
import ipaddress
import platform
import re
import socket
import ssl
import subprocess
from urllib.parse import unquote, urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

PORTS = (21, 22, 23, 80, 443, 3306, 8080)
HEADERS = ('X-Content-Type-Options', 'X-Frame-Options', 'Content-Security-Policy')
SIGNATURES = {
    'SQL Injection': r"(?:\bunion\s+(?:all\s+)?select\b|\bor\s+['\"]?1['\"]?\s*=\s*['\"]?1|;\s*(?:drop|delete)\b)",
    'Cross-site Scripting': r'<\s*(?:script|iframe)\b|\bon\w+\s*=|javascript\s*:',
    'Command Injection': r'(?:;|\|\||&&|\$\()\s*(?:cat|id|whoami|curl|wget|sh|bash)\b|`[^`]+`',
    'Path Traversal': r'(?:\.\.[/\\]){1,}',
}

def inspect_payload(payload):
    for _ in range(3):
        payload = unquote(payload)
    return [kind for kind, pattern in SIGNATURES.items() if re.search(pattern, payload, re.I)]

def resolve_target(target, allowed):
    parsed = urlsplit(target if '://' in target else 'https://' + target)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('HTTP 또는 HTTPS 주소만 입력해 주세요.')
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('경로나 쿼리 없이 서버 주소를 입력해 주세요.')
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    networks = [ipaddress.ip_network(value.strip()) for value in allowed.split(',') if value.strip()]
    if not addresses or any(not any(ipaddress.ip_address(ip) in net for net in networks) for ip in addresses):
        raise ValueError('승인된 SCAN_ALLOWLIST 범위에 속한 서버만 점검할 수 있어요.')
    return parsed, addresses[0], port

def port_probe(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=1.2):
            return {'port': port, 'status': 'open'}
    except ConnectionRefusedError:
        return {'port': port, 'status': 'closed'}
    except OSError:
        return {'port': port, 'status': 'unreachable'}

def audit_headers(parsed, ip, port):
    # Connect to the validated IP while preserving hostname verification and SNI.
    conn = http.client.HTTPConnection(ip, port, timeout=4)
    try:
        conn.connect()
        if parsed.scheme == 'https':
            conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=parsed.hostname)
        host = parsed.hostname if port in (80, 443) else f'{parsed.hostname}:{port}'
        conn.request('GET', '/', headers={'Host': host, 'User-Agent': 'E-SOC/1.0', 'Connection': 'close'})
        response = conn.getresponse()
        values = {name.lower(): value for name, value in response.getheaders()}
        return {'http_status': response.status, 'headers': {h: values.get(h.lower()) for h in HEADERS}}
    except (OSError, http.client.HTTPException) as exc:
        return {'error': str(exc), 'headers': {}}
    finally:
        conn.close()

def audit(parsed, ip, port, progress):
    results = {'ports': [], 'http': {}}
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix='probe') as pool:
        jobs = {pool.submit(port_probe, ip, p): 'port' for p in PORTS}
        jobs[pool.submit(audit_headers, parsed, ip, port)] = 'http'
        for index, future in enumerate(as_completed(jobs), 1):
            result = future.result()
            if jobs[future] == 'port':
                results['ports'].append(result)
            else:
                results['http'] = result
            progress(round(index / len(jobs) * 100))
    results['ports'].sort(key=lambda item: item['port'])
    return results

def firewall_command(ip, remove=False):
    address = ipaddress.ip_address(ip)
    if platform.system() == 'Windows':
        if remove:
            return ['netsh', 'advfirewall', 'firewall', 'delete', 'rule', f'name=E-SOC-{address}']
        return ['netsh', 'advfirewall', 'firewall', 'add', 'rule', f'name=E-SOC-{address}', 'dir=in', 'action=block', f'remoteip={address}']
    if platform.system() != 'Linux':
        raise ValueError('Unsupported firewall platform')
    return ['ip6tables' if address.version == 6 else 'iptables', '-w', '5', '-D' if remove else '-I', 'INPUT', '-s', str(address), '-j', 'DROP']

def enforce(ip, enabled, remove=False):
    if not enabled:
        return 'disabled'
    try:
        subprocess.run(firewall_command(ip, remove), check=True, timeout=8, capture_output=True, text=True)
        return 'removed' if remove else 'enforced'
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return f'failed: {type(exc).__name__}'

def discord_alert(url, title, description):
    if not url:
        return
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'discord.com' or not parsed.path.startswith('/api/webhooks/'):
        raise ValueError('Discord webhook URL must use https://discord.com/api/webhooks/')
    response = requests.post(url, json={'embeds': [{'title': title[:256], 'description': description[:2000], 'color': 16729434}]}, timeout=4, allow_redirects=False)
    response.raise_for_status()
