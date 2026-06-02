# Deployment

Put webeval on a server you control. Two supported paths:

- **[A. Docker Compose](#a-docker-compose-easiest)** — one command brings up app + Postgres + Redis. Easiest, and the recommended default.
- **[B. Manual](#b-manual-vps-gunicorn--postgres--nginx)** — gunicorn + Postgres + nginx on a plain VPS, for when you can't run Docker.

Either way, the golden rules for a public deployment are: a real `SECRET_KEY`, `DEBUG=False`, your domain in `ALLOWED_HOSTS`, HTTPS in front, and `SECURE_DEPLOY=True`.

All production behaviour is environment-driven and **off by default**, so nothing here affects local development or the test suite. Every variable below is documented in [`.env.example`](../.env.example).

---

## A. Docker Compose (easiest)

The repo ships a production `Dockerfile` and a `docker-compose.yml` (app + Postgres + Redis).

```bash
git clone https://github.com/matteospanio/webeval.git
cd webeval
cp .env.example .env
```

Edit `.env` and set at least:

```ini
SECRET_KEY=<a long random string>
DEBUG=False
ALLOWED_HOSTS=eval.example.org
SECURE_DEPLOY=True
CSRF_TRUSTED_ORIGINS=https://eval.example.org
POSTGRES_PASSWORD=<a strong password>
# Optional: email so invitations/notifications actually send
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# DEFAULT_FROM_EMAIL=webeval <no-reply@example.org>
```

Bring it up:

```bash
docker compose up --build -d
docker compose run --rm web python manage.py createsuperuser
```

The image installs the production dependencies, runs `collectstatic`, applies migrations on start, and serves via gunicorn on port 8000. Compose wires `DATABASE_URL` to the bundled Postgres and `REDIS_URL` to Redis automatically, and persists uploads on the `media` volume (unless you use S3 — see below).

Put a TLS-terminating reverse proxy (nginx, Caddy, or your provider's load balancer) in front of port 8000. A minimal Caddy config:

```caddyfile
eval.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Point your uptime monitor / load balancer health check at **`/healthz`** (returns 200 when the database is reachable, 503 otherwise).

### Object storage (optional)

To store uploaded media in S3-compatible object storage instead of the `media` volume, set in `.env`:

```ini
USE_S3=True
AWS_STORAGE_BUCKET_NAME=webeval-media
AWS_S3_REGION_NAME=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
# Non-AWS (MinIO, Cloudflare R2, DigitalOcean Spaces):
# AWS_S3_ENDPOINT_URL=https://<endpoint>
```

---

## B. Manual VPS (gunicorn + Postgres + nginx)

For a plain Ubuntu/Debian VPS without Docker. Adjust paths/users to taste.

### 1. System packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql nginx
```

### 2. App user, code, and dependencies

```bash
sudo useradd -m -d /opt/webeval webeval
sudo -iu webeval
git clone https://github.com/matteospanio/webeval.git app && cd app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt gunicorn psycopg[binary] whitenoise
```

(Or install [uv](https://docs.astral.sh/uv/) and run `uv sync --no-dev --group production`.)

### 3. Database

```bash
sudo -u postgres createuser webeval --pwprompt
sudo -u postgres createdb webeval --owner webeval
```

### 4. Configuration

Create `/opt/webeval/app/.env`:

```ini
SECRET_KEY=<a long random string>
DEBUG=False
ALLOWED_HOSTS=eval.example.org
DATABASE_URL=postgres://webeval:<password>@localhost:5432/webeval
SECURE_DEPLOY=True
CSRF_TRUSTED_ORIGINS=https://eval.example.org
USE_WHITENOISE=True
# REDIS_URL=redis://localhost:6379/0   # recommended if you run >1 worker
```

### 5. Migrate, collect static, create an admin

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### 6. Run gunicorn under systemd

`/etc/systemd/system/webeval.service`:

```ini
[Unit]
Description=webeval
After=network.target postgresql.service

[Service]
User=webeval
WorkingDirectory=/opt/webeval/app
EnvironmentFile=/opt/webeval/app/.env
ExecStart=/opt/webeval/app/.venv/bin/gunicorn core.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now webeval
```

### 7. nginx + HTTPS

`/etc/nginx/sites-available/webeval`:

```nginx
server {
    server_name eval.example.org;
    client_max_body_size 60M;            # allow stimulus uploads

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # required for SECURE_DEPLOY
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/webeval /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d eval.example.org      # provisions + auto-renews TLS
```

WhiteNoise serves static files from the app process, so no nginx `location /static/` block is required. (If you use S3 for media, uploads go there; otherwise they live under `MEDIA_ROOT`.)

---

## Operations

### Rate limiting

The REST API is rate-limited (defaults: 30/min anonymous, 240/min per key; override with `API_THROTTLE_ANON` / `API_THROTTLE_USER`). Set `REDIS_URL` so limits are shared across gunicorn workers.

### Backups

- **Database:** `pg_dump` on a schedule, e.g. `pg_dump -U webeval webeval | gzip > backup-$(date +%F).sql.gz`. Superusers can also download a full JSON dump at `/admin/database-export.json`.
- **Media:** back up `MEDIA_ROOT` (or rely on your object-storage provider's versioning when `USE_S3=True`).

### Data retention

Schedule the retention sweep so each study's retention window is honoured:

```bash
# crontab for the webeval user — nightly at 03:00
0 3 * * * cd /opt/webeval/app && .venv/bin/python manage.py purge_expired_data
```

Run `purge_expired_data --dry-run` first to preview. See [GDPR & privacy](gdpr.md).

### Upgrades

```bash
git pull
uv sync --no-dev --group production     # or: pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart webeval          # or: docker compose up --build -d
```

### Production checklist

- [ ] `SECRET_KEY` set to a long random value, kept out of version control
- [ ] `DEBUG=False`
- [ ] `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` set to your domain
- [ ] `SECURE_DEPLOY=True` and TLS terminated in front (proxy sends `X-Forwarded-Proto`)
- [ ] Postgres (not SQLite) with a strong password and its own backups
- [ ] `REDIS_URL` set if you run more than one worker
- [ ] `purge_expired_data` scheduled; retention windows configured per study
- [ ] Email (`EMAIL_*`) configured so invitations and notifications send
- [ ] A superuser created; researcher self-registration disabled if undesired (`ACCOUNTS_ALLOW_REGISTRATION=False`)
