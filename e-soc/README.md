# Enterprise Security Operations Center (E-SOC)

A runnable Flask / Socket.IO security console with SQLite persistence, concurrent TCP and HTTP audits, signature inspection, opt-in OS firewall enforcement, Discord embeds, and a Korean beginner experience. Tailwind, Socket.IO Client and Chart.js are loaded from versioned CDN URLs; host these assets locally for offline or tightly controlled deployments.

## Start locally

```bash
cd e-soc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python app.py
```

Open http://127.0.0.1:5000 for the Korean website. Choose **콘솔 열기** (or visit `/console`) for the login-free educational console. The operator interface is `/operator` and requires `ADMIN_TOKEN`. If omitted, a temporary token is printed only at command-line startup. The application factory generates an inaccessible random token if a production process manager omits configuration, so configure it explicitly. `.env.example` documents configuration; the app does not automatically load .env files. Keep secrets outside source control.

Use **모의 위협 검사** to practice without sending payloads to another system. Enable **초보자 모드** for Korean explanations covering what each concept means, why it matters, and what to do next. The glossary is a local educational reference, not a remote AI service. Preferences persist in localStorage; operator credentials do not.

For actual audits, set `SCAN_ALLOWLIST` to comma-separated authorized IPv4/IPv6 CIDRs, e.g. `127.0.0.0/8,::1/128,192.0.2.40/32`. Every DNS answer must be allowed; the HTTP connection pins a validated IP, retains TLS hostname validation, and never follows redirects. Only root URLs are accepted. Seven TCP ports (21,22,23,80,443,3306,8080) and the supplied URL's HTTP port are examined. Timeout/unreachable differs from refused/closed. Header auditing checks presence, not policy correctness; open ports are exposure findings, not proof of compromise.

## Operations and API

Authenticate with `POST /api/login` JSON `{"token":"..."}`. Preserve the session cookie and use the returned `csrf` value as `X-CSRF-Token` for mutations. Same-origin authenticated Socket.IO connections receive `security_alert`, `scan_progress`, `metrics`, and `logs_cleared` events.

| Route | Function |
| --- | --- |
| GET /api/state | Latest 100 events, 20 scans, all blocks, cumulative metrics |
| POST /api/scans | JSON `target`; returns 202 with persistent job ID |
| POST /api/simulate | JSON `payload`; educational detection with no blocking/webhooks |
| POST /api/inspect | JSON `payload`; real inspection verdict, 403 on block |
| DELETE /api/blocks/ID | Remove app block and confirmed OS rule |
| DELETE /api/logs | Permanently delete events; scans/blocks retained |
| GET /api/export | Download JSON events, scans and blocks |

The inspection API inspects the direct authenticated caller IP (`REMOTE_ADDR`), never an untrusted forwarded header. It is an integration primitive, not an inline reverse proxy. An upstream service must authenticate, supply the actual input and honor denied verdicts. Do not deploy behind a proxy and assume source attribution works: design a trusted client-identity integration first. This version does not accept arbitrary caller-supplied source IPs. Application blocking applies to the inspection endpoint, while the management plane remains available for remediation. Loopback is never blocked at OS level. Signature matching has false positives and bypasses and complements parameterized SQL, output encoding, safe process execution, and secure application design.

`FIREWALL_ENABLED=1` enables real OS changes for detected real requests; default is off. Linux uses `iptables` / `ip6tables`; Windows uses `netsh advfirewall`. Commands use validated IPs and argument arrays, never a shell. Run with the narrowly scoped OS privileges necessary for firewall administration. Rules affect inbound traffic to the host, so use a dedicated host and maintain an independent recovery console. The UI distinguishes disabled, protected-address, enforced and failed outcomes. Unblock deletes successfully installed rules before deleting DB records; failures retain records. Database app blocks survive restarts. OS rules depend on host firewall persistence; verify/reconcile them after reboots or external rule changes. Firewall and DB updates are not an atomic transaction.

Set `DISCORD_WEBHOOK_URL` to an HTTPS `discord.com/api/webhooks/…` URL to enable rich alerts for real critical detections and open-port findings. Delivery uses bounded timeouts; failures are logged without exposing credentials. Notifications are best effort with no durable retry queue. No real OS commands or webhooks are run by tests.

## Deployment scope

Run one process with the included gevent Socket.IO server, behind a TLS reverse proxy configured for WebSocket upgrades and suitable connection timeouts. Set stable `SECRET_KEY`, `ADMIN_TOKEN`, `COOKIE_SECURE=1` and persistent `DATABASE_URL` (default `instance/esoc.db`). Bind with `HOST` and `PORT`. Back up SQLite and test restoration. Default rate limiting and the block cache are process-local. Use a single instance; multi-instance operation needs a shared Socket.IO message queue, shared rate-limit store, shared block invalidation, external job queue, migrations and a production database. SQLite writes are suitable for modest single-node use, not high-ingest enterprise workloads.

A maximum of four scan jobs run concurrently, each using eight short-lived probe threads; queue admission fails with 429 when full. Scan resolution and probing run outside request handling. Persisted unfinished scans are marked interrupted on restart. Client traffic charts show five-second event counts received during the current browser session, including simulations; they are not packet telemetry and do not backfill gaps. Event storage and export are currently unbounded: define retention and archive policies before sustained ingestion.

This implementation is a functional single-node foundation, not a certified enterprise NG-WAF/XDR appliance. Before enterprise deployment add organizational SSO/RBAC, immutable audit trails, durable alert delivery, telemetry collectors, HA, retention, observability and a reviewed inline enforcement architecture. No claim of comprehensive threat coverage is made.

## Verify

```bash
source .venv/bin/activate
python -m pytest -q
node --check static/app.js
```

Tests cover authentication/CSRF, authenticated live events, encoded payload signatures, persistence/export/deletion, asynchronous scan completion, allowlist rejection, safe firewall construction and unblock behavior. They mock network probes and firewall execution.

## Login-free public console

`/console` is an anonymous educational experience. `/api/public/inspect` performs rate-limited, stateless signature checks; submitted strings are not stored or executed. Each browser tab keeps up to 100 demonstration results in memory, and refresh resets them. JSON export and clearing affect only that tab. Actual logs, scans, firewall actions and authenticated Socket.IO streams stay restricted to the `/operator` interface and authenticated APIs. Public mode never opens an operator Socket.IO connection.
