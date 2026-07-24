# Installation (local)

Get PANEL running on your own machine for development or a small local study. For a public/production server, see [Deployment](deployment.md).

## Requirements

- **Python 3.11+**
- Either **[uv](https://docs.astral.sh/uv/)** (recommended) or plain `pip` + `venv`
- Git

Everything else (Django, DRF, etc.) is installed for you. SQLite is the default database — no separate database server needed to get started.

## Option A — with uv (recommended)

```bash
git clone https://github.com/matteospanio/panel.git
cd panel

uv sync                       # create the venv and install dependencies
cp .env.example .env          # create your local config
uv run ./manage.py migrate    # set up the database (SQLite by default)
uv run ./manage.py createsuperuser
uv run ./manage.py runserver
```

## Option B — with pip + venv

The repository ships a pinned `requirements.txt` (exported from the lockfile) for environments without uv:

```bash
git clone https://github.com/matteospanio/panel.git
cd panel

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # add -r requirements-dev.txt for the test tools

cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

> The commands below use the `uv run ./manage.py …` form. With pip, drop the `uv run` prefix (and activate your venv first): `python manage.py …`.

## Open the app

| URL | What it is |
|---|---|
| http://127.0.0.1:8000/studio/ | The **researcher dashboard** (studio). Sign in at `/accounts/login/` or register at `/accounts/register/`. |
| http://127.0.0.1:8000/admin/ | The **Django admin** (staff/superusers) — for conditions/stimuli authoring and platform administration. |
| http://127.0.0.1:8000/s/&lt;slug&gt;/ | A **participant-facing study** (only when the study is active or in test). |

There is deliberately **no public index** of studies — each study is reached only by its direct `/s/<slug>/` link.

## First study in 60 seconds

1. Register a researcher account at `/accounts/register/` and open `/studio/`.
2. **New study** → give it a name. You now own a draft.
3. Add **conditions** and **stimuli** in the Django admin (linked from the study page), then open the studio's **drag-&-drop builder** to add questions.
4. Switch the study to **test** to rehearse the participant flow (test data is discarded/kept separately), then **activate** it.
5. Share the `/s/<slug>/` link; watch results come in live on the study overview.

See [Using the app](usage.md) for a fuller walkthrough with screenshots.

## Configuration

All settings are environment variables, documented in [`.env.example`](../.env.example). For local use the defaults are fine; the ones you may want early:

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | dev-only placeholder | Django secret. **Set a real one** before any non-local use. |
| `DEBUG` | `False` | Set `True` locally for tracebacks and auto-served media. |
| `DATABASE_URL` | SQLite file | e.g. `postgres://user:pass@host:5432/db` for Postgres. |
| `ACCOUNTS_ALLOW_REGISTRATION` | `True` | Set `False` to make the platform invitation-only. |
| `GEOIP_PATH` | unset | Path to a MaxMind GeoLite2-Country `.mmdb` to record participant country codes (optional; degrades gracefully). |

## Running the tests

```bash
uv run pytest -m "not selenium"     # fast suite (skips browser tests)
uv run pytest                       # everything, incl. browser-driven admin tests
uv run ruff check .                 # lint
```

## Troubleshooting

- **`SECRET_KEY` warning / insecure key** — copy `.env.example` to `.env` and set `SECRET_KEY` (generate one with `uv run python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`).
- **Static files look unstyled in the admin** — run `uv run ./manage.py collectstatic` (only needed when `DEBUG=False`).
- **A study returns 404 at `/s/<slug>/`** — the study is still a draft; switch it to *test* or *active* first.
