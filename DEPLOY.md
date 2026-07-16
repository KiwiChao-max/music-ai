# DEPLOY.md --- `music-ai` production deployment

This doc covers the two supported paths for getting `music-ai` into a
production-ish environment, plus the hardening checklist that both share.

## TL;DR

* **Easy path**: `docker compose up -d` (Postgres + Redis + API + worker
  on one box). Good for staging, demos, small teams.
* **Bare-metal path**: Debian/Ubuntu + `systemd` units for `uvicorn` and
  `celery worker` behind `nginx` with TLS from Let's Encrypt. Good when
  the team already runs the rest of their stack on VMs.

In both cases the WebSocket progress channel and Prometheus `/metrics`
endpoint are exposed by the same process as the REST API.

---

## 0. Prerequisites

| Component   | Version           | Notes |
| ----------- | ----------------- | ----- |
| Python      | 3.12              | Demucs / Basic Pitch wheels target 3.12. |
| PostgreSQL  | 14+               | We use JSONB on `analysis.metadata`. |
| Redis       | 6+                | Pub/sub for the WebSocket + Celery broker. |
| Node.js     | 20+               | Only needed for the frontend build. |
| FFmpeg      | 5+                | Optional --- only if you accept compressed uploads. |
| nginx       | 1.20+             | Frontend serving + reverse-proxy to the API. |

`openssl`, `curl`, `git`, and `build-essential` (for `psycopg2` wheel
fallback) should already be on the box.

---

## 1. Easy path --- Docker Compose

Suitable for staging, demos, and a small single-server deployment.

```bash
git clone https://github.com/KiwiChao-max/music-ai.git
cd music-ai
cp .env.example .env             # then edit secrets
docker compose up -d --build
docker compose logs -f api       # watch the API come up
```

The compose file starts four containers:

* `postgres` --- data persisted in the named volume `postgres_data`.
* `redis` --- broker + pub/sub; data persisted in `redis_data`.
* `api` --- `uvicorn app.main:app`; storage mounted in `backend_storage`.
* `worker` --- Celery consumer; reuses the same image as `api`.

The API container runs `alembic upgrade head` on boot, so first
deployments apply the latest schema automatically.

### Compose `.env` reference

```dotenv
POSTGRES_USER=music_ai
POSTGRES_PASSWORD=change-me-please
POSTGRES_DB=music_ai
POSTGRES_PORT=5432
REDIS_PORT=6379
BACKEND_PORT=8000

# Required in production (the API refuses to boot with a placeholder):
JWT_SECRET=$(openssl rand -hex 48)
AUTH_REQUIRED=true
APP_ENV=production

# Optional: bootstrap admin (idempotent --- only creates if missing)
BOOTSTRAP_ADMIN_EMAIL=admin@your-domain
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=$(openssl rand -base64 24)
```

### Reverse proxy in front of the compose stack

The simplest production setup keeps `BACKEND_PORT=8000` bound to
`127.0.0.1` only and lets nginx terminate TLS. The included
`docker-compose.override.yml` example is in Section3.

---

## 2. Bare-metal path --- systemd + nginx

For a single-VM deployment that doesn't need the container runtime.

### 2.1 Create a service user

```bash
sudo useradd --system --home /opt/music-ai --shell /bin/bash music-ai
sudo mkdir -p /opt/music-ai /var/lib/music-ai
sudo chown -R music-ai:music-ai /opt/music-ai /var/lib/music-ai
```

### 2.2 Backend install

```bash
sudo -u music-ai git clone https://github.com/KiwiChao-max/music-ai.git /opt/music-ai/app
cd /opt/music-ai/app/backend
sudo -u music-ai python3.12 -m venv .venv
sudo -u music-ai .venv/bin/pip install --upgrade pip
sudo -u music-ai .venv/bin/pip install -r requirements.txt
# Pre-download the Basic Pitch model so the first user request isn't slow.
sudo -u music-ai .venv/bin/python warmup_basic_pitch.py
```

#### 2.2.1 `.env` (same variables as the compose `.env`)

Place at `/etc/music-ai/backend.env`, mode `0600`, owner `music-ai`:

```ini
APP_ENV=production
AUTH_REQUIRED=true
JWT_SECRET=...                              # openssl rand -hex 48
DATABASE_URL=postgresql+psycopg2://music-ai:...@127.0.0.1:5432/music_ai
REDIS_URL=redis://127.0.0.1:6379/0
STORAGE_DIR=/var/lib/music-ai/storage
MAX_UPLOAD_BYTES=104857600                  # 100 MB
BOOTSTRAP_ADMIN_EMAIL=admin@your-domain
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=...                # initial password; rotate after first login
```

### 2.3 API systemd unit

`/etc/systemd/system/music-ai-api.service`:

```ini
[Unit]
Description=music-ai FastAPI
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=music-ai
Group=music-ai
WorkingDirectory=/opt/music-ai/app/backend
EnvironmentFile=/etc/music-ai/backend.env
ExecStart=/opt/music-ai/app/backend/.venv/bin/uvicorn \
    app.main:app \
    --host 127.0.0.1 --port 8000 \
    --proxy-headers --forwarded-allow-ips=127.0.0.1 \
    --workers 2
Restart=always
RestartSec=5
# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/music-ai

[Install]
WantedBy=multi-user.target
```

### 2.4 Worker systemd unit

`/etc/systemd/system/music-ai-worker.service`:

```ini
[Unit]
Description=music-ai Celery worker
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=music-ai
Group=music-ai
WorkingDirectory=/opt/music-ai/app/backend
EnvironmentFile=/etc/music-ai/backend.env
ExecStart=/opt/music-ai/app/backend/.venv/bin/celery \
    -A app.celery_app:celery worker \
    --loglevel=info --concurrency=2
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/music-ai

[Install]
WantedBy=multi-user.target
```

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now music-ai-api music-ai-worker
sudo systemctl status music-ai-api music-ai-worker
```

### 2.5 Frontend build

```bash
cd /opt/music-ai/app/frontend
sudo -u music-ai npm ci
sudo -u music-ai npm run build           # outputs to dist/
sudo install -d -o music-ai -g music-ai /var/lib/music-ai/web
sudo -u music-ai cp -r dist/. /var/lib/music-ai/web/
```

### 2.6 nginx

`/etc/nginx/sites-available/music-ai.conf`:

```nginx
# HTTP -> HTTPS redirect.
server {
    listen 80;
    listen [::]:80;
    server_name music-ai.your-domain;
    return 301 https://$host$request_uri;
}

upstream music_ai_api {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name music-ai.your-domain;

    ssl_certificate     /etc/letsencrypt/live/music-ai.your-domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/music-ai.your-domain/privkey.pem;

    # Sensible defaults.
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    client_max_body_size 100m;            # match MAX_UPLOAD_BYTES

    # Static frontend.
    root /var/lib/music-ai/web;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;  # SPA fallback
    }

    # API + WebSocket share the same upstream; nginx transparently
    # upgrades the upgrade header.
    location /api/ {
        proxy_pass http://music_ai_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;            # long-running progress connections
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/music-ai.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 2.7 TLS via Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d music-ai.your-domain
```

`certbot` drops a systemd timer that auto-renews.

### 2.8 Migrations

Apply the schema on first boot and after every upgrade:

```bash
sudo -u music-ai -E /opt/music-ai/app/backend/.venv/bin/alembic upgrade head
```

For a one-shot migration container in the compose world, the
`command:` on the `api` service already does this on startup.

---

## 3. Production checklist

The dev defaults are intentionally permissive. Before exposing the API
to the public internet, work through this list:

- [ ] Set `AUTH_REQUIRED=true` so anonymous requests are rejected.
- [ ] Generate a real `JWT_SECRET` (>= 32 bytes of entropy) and keep it
      out of git. The API refuses to boot with the placeholder in
      `APP_ENV=production`.
- [ ] Set `MAX_UPLOAD_BYTES` to a sane value (100 MB is the default;
      larger files mean you'll need to bump `client_max_body_size` in
      nginx too).
- [ ] Provision the bootstrap admin via env vars, then change the
      password on first login. Delete the env vars from your secret
      store after the first boot.
- [ ] Backups: nightly `pg_dump` of the `music_ai` database, plus a
      snapshot of the `STORAGE_DIR` uploads volume.
- [ ] TLS: terminate at nginx (or a load balancer), redirect HTTP ->
      HTTPS, and enable HSTS (the example config does this).
- [ ] CORS: the default allow-list is `http://localhost:5173` for
      development. Set `CORS_ALLOW_ORIGINS` to your real frontend
      origin(s) in production.
- [ ] Rate-limit: front the API with nginx's `limit_req` zone (or a
      managed edge) to slow down upload spam.
- [ ] Firewall: only expose 80/443 publicly. Postgres + Redis should
      listen on 127.0.0.1 (or be on a private network) only.
- [ ] Observability: scrape `/metrics` with Prometheus, alert on
      `up{job="music-ai"} == 0` and on a 5xx rate from the API.

---

## 4. Observability

### 4.1 Health

* `GET /healthz` --- liveness. 200 if the process is up. Used by k8s
  liveness probes / `systemd` watchdog.
* `GET /readyz` --- readiness. 200 only when Postgres AND Redis are
  reachable; 503 otherwise. Used by k8s readiness probes.
* `GET /metrics` --- Prometheus text format. Scrape every 15 s.

### 4.2 Logs

* API logs to stdout (one JSON line per request when you set
  `LOG_JSON=true`).
* Worker logs go to stdout in the same format.
* In systemd, `journalctl -u music-ai-api -f` is your friend.
* In compose, `docker compose logs -f api worker` works the same way.

### 4.3 Prometheus scrape config

```yaml
scrape_configs:
  - job_name: music-ai
    metrics_path: /metrics
    static_configs:
      - targets: ["music-ai.your-domain"]
```

The `music_ai_tasks_total{status="..."}` gauge is the most useful
single number to graph --- it tells you how many tasks are in flight.

---

## 5. Scaling

The current code is single-tenant enough to run multiple workers:

* API: bump `--workers 2` (or 4) on the systemd unit. The WebSocket
  upgrade still works because uvicorn hands them off to whichever
  worker owns the connection.
* Worker: start as many `music-ai-worker` instances as you have CPU.
  The `--concurrency` flag controls threads per process.
* Postgres: the schema is small; `pgbouncer` is helpful once you cross
  ~50 concurrent API workers.
* Redis: the pub/sub channel is per-task, so a single-node Redis can
  serve thousands of concurrent watchers.
* Storage: once you have > 100 GB of uploads, move `STORAGE_DIR` to
  S3 (or any S3-compatible object store) and update `file_service`.

---

## 6. Backup & restore

```bash
# Snapshot the database.
pg_dump --no-owner --clean -h 127.0.0.1 -U music-ai music_ai \
    | gzip > /var/backups/music-ai-$(date -u +%Y%m%d).sql.gz

# Snapshot the storage directory.
tar -czf /var/backups/music-ai-storage-$(date -u +%Y%m%d).tgz \
    -C /var/lib/music-ai storage

# Restore.
gunzip -c /var/backups/music-ai-20260101.sql.gz | psql -h 127.0.0.1 -U music_ai music_ai
tar -xzf /var/backups/music-ai-storage-20260101.tgz -C /var/lib/music-ai
```

Add a cron entry to run the snapshot commands nightly.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `401 Unauthorized` from the API after login | JWT secret rotated while the user was logged in | Sign in again; the access token is the only thing that needs to be replaced. |
| `503` on `/readyz` | Postgres or Redis is down | `systemctl status postgresql redis-server`; check the `pg_isready` and `redis-cli ping` output. |
| Upload succeeds but the worker never picks it up | Worker can't reach the broker | `redis-cli ping` from the worker host; check `REDIS_URL` env var. |
| WebSocket disconnects every 30 s | nginx `proxy_read_timeout` is too low | Set it to `600s` (see example config). |
| `Internal Server Error` on `/api/auth/login` after a deploy | `JWT_SECRET` was rotated but clients still hold old tokens | Old refresh tokens can no longer be decoded; users must log in again. |

---

## 8. First-login runbook

1. SSH into the box.
2. `curl -fsS https://music-ai.your-domain/healthz` --- expect `{"status":"ok"}`.
3. `curl -fsS https://music-ai.your-domain/readyz` --- expect 200.
4. Open the SPA, log in with the bootstrap admin credentials.
5. Change the admin password, create a regular user for daily use.
6. Upload a short (<= 30 s) test clip and confirm the detail page
   reaches `FINISHED` with a playable stem.
