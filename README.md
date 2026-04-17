# webeval

![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Django 5.2](https://img.shields.io/badge/django-5.2-0C4B33?logo=django&logoColor=white)
![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)
![Modes: standard + pairwise](https://img.shields.io/badge/modes-standard%20%2B%20pairwise-6C5CE7)

webeval is a Django app for running anonymous online evaluation studies with audio, image, and text stimuli.

It was originally built for LLM-output evaluation, but the current architecture is broader than that: researchers can configure single-stimulus or pairwise-comparison studies, collect structured participant responses, and review results from the Django admin without building a separate dashboard.

## Features

- Standard single-stimulus studies and pairwise comparison studies
- Audio, image, and text stimuli in one experiment model
- Rating, multiple-choice, free-text, and Likert questions
- PsyToolkit-style pagination with author-controlled page breaks
- Balanced assignment strategies across conditions
- Optional audio playback check before the study begins
- Direct per-experiment participant links with no public study index
- Admin-native analytics, SVG charts, and CSV exports
- Reproducibility exports as printable HTML, JSON, and ZIP archives
- Experiment archive import for cloning or sharing studies across instances
- Lightweight participant metadata capture: device type, browser family, and country code

## Current Scope

webeval is currently best suited to anonymous, single-session studies where participants rate or compare media items in a guided flow.

Today the product is intentionally narrower than a full survey platform. It does not yet provide participant accounts, save-and-resume flows, conditional branching, longitudinal scheduling, or richer stimulus types such as video.

## Quick Start

### Requirements

- Python 3.11+
- `uv`

### Installation

```bash
uv sync
cp .env.example .env
uv run ./manage.py migrate
uv run ./manage.py createsuperuser
uv run ./manage.py runserver
```

Then open:

- `http://127.0.0.1:8000/admin/` for the staff interface
- `http://127.0.0.1:8000/s/<slug>/` for a participant-facing study

The default setup uses SQLite. Environment variables are documented in `.env.example`.

## How It Works

### Experiment lifecycle

Experiments move through three states:

- `draft`
- `active`
- `closed`

Conditions, stimuli, and questions can only be structurally edited while an experiment is in `draft`. This protects active studies from accidental mid-run changes.

### Participant flow

For standard studies, participants move through:

1. Consent
2. Optional audio check
3. Instructions
4. One or more stimulus pages
5. One or more demographic pages
6. Thanks

Questions are grouped into pages with a PsyToolkit-style `page_break_before` flag. Each page posts only the answers visible on that page.

### Study modes

#### Standard mode

Participants evaluate one stimulus at a time. Each session receives either all eligible stimuli or a balanced subset, depending on `stimuli_per_participant`.

#### Pairwise mode

Participants compare two stimuli side by side. Pairings are built across conditions using shared `prompt_group` values, and results can be summarized with win-rate charts and Bradley-Terry analysis.

### Stimulus types

- `audio`: uploaded audio file with validation, SHA-256 checksum, and duration extraction
- `image`: uploaded image file with validation and SHA-256 checksum
- `text`: inline text body with no uploaded media

### Question types

- Rating slider
- Multiple choice
- Free text
- Likert scale

Questions can be marked as required, split onto separate pages, and optionally show the originating stimulus prompt to participants.

## Admin and Exports

The admin UI is built on Django admin with django-unfold and contains the full staff workflow:

- experiment authoring
- condition, stimulus, and question management
- global summary cards on `/admin/`
- per-experiment detail views
- CSV exports for answers and demographics
- pairwise CSV exports for comparison studies
- SVG charts for mean ratings, pairwise win rates, and Bradley-Terry scores
- printable and machine-readable reproducibility exports
- ZIP archive export and import for study portability

## Project Layout

- `experiments/`: experiment models, admin, assignment strategies, exports, analytics, and charts
- `survey/`: participant sessions, response capture, flow control, metadata capture, and participant-facing views
- `core/`: Django settings, URL wiring, and project-level integration
- `tests/`: end-to-end, admin, pairwise, and regression coverage

## Running Tests

```bash
uv run pytest
```

Useful variants:

```bash
uv run pytest -m "not selenium"
uv run pytest -m selenium
uv run pytest -k <keyword>
uv run pytest --cov=experiments --cov=survey
uv run ./manage.py makemigrations --check --dry-run
```

## Configuration

The application is configured with environment variables via `django-environ`.

Common settings:

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`
- `GEOIP_PATH` for optional country lookup via MaxMind GeoLite2
- `STIMULUS_MAX_UPLOAD_BYTES`, `STIMULUS_ALLOWED_EXTENSIONS`, `STIMULUS_ALLOWED_MIME_TYPES`
- `STIMULUS_MAX_IMAGE_UPLOAD_BYTES`, `STIMULUS_ALLOWED_IMAGE_EXTENSIONS`

If `GEOIP_PATH` is unset or the database is missing, participant country lookup is skipped without breaking the app.

## Deployment Notes

webeval is a self-hosted Django application. The repository is ready for local development and research deployments, but production hardening is still the operator's responsibility.

For public deployments, you should at least provide:

- HTTPS
- a proper production database strategy
- media storage and backups
- secure admin credentials
- monitoring and log retention

## Contributing

Issues and pull requests are welcome.

If you contribute code, keep changes aligned with the existing architecture and add or update tests for behavior changes, especially around participant flow, assignment logic, exports, and admin views.

## License

This project is released under the MIT License. See `LICENSE`.
