# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Django web app to collect anonymous human evaluations of LLM outputs. Participants walk through a **PsyToolkit-style paged survey** answering quality + demographic questions about each stimulus. Stimuli can be audio clips, images, or text-only prompts (one `Stimulus` model with a `kind` discriminator). No login required, multiple submissions allowed; each survey has a direct shareable link `/s/<slug>/` and there is no public index of active studies. Staff use Django admin (themed with django-unfold) to configure experiments; the `/admin/` index carries global summary cards and each Experiment change view embeds per-experiment stats with links into admin-mounted detail/CSV/chart views (no separate dashboard app). See [README.md](README.md) for the full product description.

## Tooling

- Package manager: `uv` (see [pyproject.toml](pyproject.toml), [uv.lock](uv.lock)). Python `>=3.11`. The project is non-packaged: `[tool.uv] package = false` tells uv there is no wheel to build.
- Install deps: `uv sync`
- Run everything through `uv run` so the project venv is used.

## Common commands

- Dev server: `uv run ./manage.py runserver`
- Migrations: `uv run ./manage.py makemigrations` / `uv run ./manage.py migrate`
- Create admin user: `uv run ./manage.py createsuperuser`
- Run full test suite: `uv run pytest`
- Fast path (skip browser-driven tests): `uv run pytest -m "not selenium"`
- Only the Selenium admin tests: `uv run pytest -m selenium`
- Focused test run: `uv run pytest -k <keyword>` or `uv run pytest path/to/test_file.py::TestCase::test_method`
- Coverage: `uv run pytest --cov=experiments --cov=survey`
- Migration drift check: `uv run ./manage.py makemigrations --check --dry-run`
- Purge one experiment's participant data: `uv run ./manage.py purge_experiment <slug> --yes`

Tests use `pytest-django` (configured in [pyproject.toml](pyproject.toml) under `[tool.pytest.ini_options]`) — do **not** use `./manage.py test`. An autouse `conftest.py` fixture redirects `MEDIA_ROOT` to a tmp dir so test uploads never hit the real `media/` directory.

## Architecture

Three Django apps sit as siblings of [core/](core/) (the project package):

- **[experiments/](experiments/)** — domain models, admin, and all staff-side stats/export surfaces. Owns `Experiment`, `Condition`, `Stimulus` (with `kind ∈ {audio, image, text}`), `Question` (with `page_break_before`), plus the pluggable assignment strategy ([experiments/assignment.py](experiments/assignment.py)), reproducibility-export builder ([experiments/exports.py](experiments/exports.py)), aggregate stats ([experiments/stats.py](experiments/stats.py)), SVG charts ([experiments/charts.py](experiments/charts.py)), and CSV exports ([experiments/csv_exports.py](experiments/csv_exports.py)). `ExperimentAdmin.get_urls()` mounts per-experiment detail/CSV/chart views under `/admin/experiments/experiment/<slug>/…` so there is **no separate dashboard app** — everything staff-facing lives inside the admin.
- **[survey/](survey/)** — participant-facing flow. Owns `ParticipantSession` (with `current_assignment_index`, `current_page_index`, `demographic_page_index` cursors), `StimulusAssignment`, `Response`, the flow state machine + PsyToolkit pagination helpers ([survey/flow.py](survey/flow.py)), UA + GeoIP metadata capture ([survey/metadata.py](survey/metadata.py)), and the vanilla-JS audio tracker ([survey/static/survey/js/audio-tracker.js](survey/static/survey/js/audio-tracker.js)). Routes live at `/s/<slug>/…`; there is no landing page (`/` returns 404). Inactive experiments render `survey/templates/survey/unavailable.html` instead of 404ing.
- **[apikeys/](apikeys/)** — self-service, scoped, audited API keys for the staff REST API. Owns `APIKey` (SHA-256 hashed, per-user, rotatable, revocable, optional expiry) and `APIKeyEvent` (append-only audit log: `created`/`rotated`/`revoked`/`used`/`auth_failed`). Replaces `rest_framework.authtoken` entirely; the wire format stays `Authorization: Token <key>`. Scopes are declared in [apikeys/scopes.py](apikeys/scopes.py); views opt in with `permission_classes = [HasScope("<scope>")]` and inherit `LogAPIKeyUsageMixin` to record every call. Staff UI is mounted at `/admin/api-keys/` (templates in `apikeys/templates/admin/apikeys/`, URLs in [apikeys/admin_urls.py](apikeys/admin_urls.py)); superusers additionally have read-only changelists at `/admin/apikeys/apikey/` and `/admin/apikeys/apikeyevent/`.

The [core/](core/) package holds settings ([core/settings.py](core/settings.py)), URL wiring ([core/urls.py](core/urls.py)), and WSGI/ASGI entry points.

### Admin theme

The staff admin is themed with **[django-unfold](https://github.com/unfoldadmin/django-unfold)** (`django-unfold>=0.40,<1.0`). Four things make this work:

1. `"unfold"` and `"unfold.contrib.forms"` are prepended to `INSTALLED_APPS` **before** `"django.contrib.admin"` so Unfold's template overrides win the template search.
2. Every `ModelAdmin` / `TabularInline` in [experiments/admin.py](experiments/admin.py) and [survey/admin.py](survey/admin.py) inherits from `unfold.admin.ModelAdmin` / `unfold.admin.TabularInline` instead of the stock admin classes. All standard options (`list_display`, `fieldsets`, `inlines`, `form`, `actions`, `prepopulated_fields`) are inherited unchanged.
3. The `UNFOLD["SIDEBAR"]` dict in [core/settings.py](core/settings.py) uses `show_all_applications=False` and supplies a **curated navigation list** with three groups (Overview / Studies / Participants) and Material-Symbols icons. This is deliberate: without `show_all_applications=False` Unfold renders a second auto-generated "all apps" list below the curated one, producing two sidebars. Sidebar links use `reverse_lazy(...)` — plain `reverse()` crashes at settings import time because URLconf isn't loaded yet.
4. [templates/admin/index.html](templates/admin/index.html) extends Unfold's `admin/base.html` and adds a "summary cards" grid above the app list. The cards read from `webeval_summary` in the template context, which is populated by the [core/context_processors.py](core/context_processors.py) `admin_summary` processor — registered in `TEMPLATES[0]["OPTIONS"]["context_processors"]`. The processor short-circuits when the request isn't under `/admin/` or the user isn't staff, so it costs nothing on participant pages.

The Experiment changelist "Shortcuts" column ([experiments/admin.py](experiments/admin.py) `ExperimentAdmin.shortcuts`) renders a per-row link cluster (details page + JSON + printable export + participant survey link). Per-experiment stats live entirely inside the admin: `ExperimentAdmin.live_stats` is a readonly field on the change form, and `ExperimentAdmin.get_urls()` mounts four staff-only views under `/admin/experiments/experiment/<slug>/…` — `details/` (the Unfold-themed detail page at [experiments/templates/admin/experiments/experiment/details.html](experiments/templates/admin/experiments/experiment/details.html)), `answers.csv`, `demographics.csv`, and `chart/mean-ratings.svg`. Reverse names are `admin:experiments_experiment_details` / `_answers_csv` / `_demographics_csv` / `_chart_mean_ratings`.

Participant-facing survey pages are **not** themed with Unfold — they keep the Pico.css shell from [templates/base.html](templates/base.html) with a custom PsyToolkit-inspired layer on top (`.survey-header`, `.progress-bar`, stacked radio lists, narrow-column content). Unfold only touches staff pages.

### Key architectural invariants

- **Experiment lifecycle is draft → active → closed.** Structural edits (add/remove `Condition`, `Stimulus`, `Question`) are blocked once the experiment leaves draft. This is enforced in `experiments/models.py` via `_ensure_draft()` called from `clean()` **and** `delete()` of each child model, plus `_ALLOWED_TRANSITIONS` in `Experiment.clean()`. Admin inlines additionally use `_ReadOnlyWhenLockedMixin` so the UI reflects the lock. `_ensure_draft()` checks the parent's **committed** state (via a DB lookup), not the in-memory instance, because Django admin inlines re-validate every child row when the parent is being saved — reading the in-memory state would wrongly block a legitimate draft→active transition on an experiment that already has children.
- **Single `Question` model with `type` + `section` + `page_break_before` + JSONField `config`.** `type ∈ {rating, choice, text}` determines the widget and validation (`_validate_question_config` in [experiments/models.py](experiments/models.py)); `section ∈ {stimulus, demographic}` decides whether the question runs per-stimulus or once at the end. `page_break_before` is the author-controlled PsyToolkit page-break: a true value starts a new page before that question. Rating `min`/`max`/`step` are admin-configurable and rating may carry optional `min_label`/`max_label` strings for PsyToolkit-style scale anchors.
- **Single `Stimulus` model with a `kind` discriminator.** `kind ∈ {audio, image, text}`: `audio` requires the `audio` `FileField` (extension + size validated via [experiments/validators.py](experiments/validators.py) audio helpers; SHA-256 + mutagen duration auto-computed on save); `image` requires the `image` `FileField` (extension + size validated via the image helpers; SHA-256 auto-computed on save, no duration); `text` requires a non-empty `text_body` (no file, no hash, no duration). `Stimulus.clean()` enforces these rules and rejects cross-kind contamination. `save()` hashes whichever media field exists via `_media_field()`, and only probes duration when `kind == AUDIO`.
- **Pluggable assignment strategies.** `StrategyBase.select(experiment, n, counts, rng)` in [experiments/assignment.py](experiments/assignment.py) is strategy-only (no DB access) and kind-agnostic (strategies never read `Stimulus.kind`); the `counts` dict is produced by `survey/views.py::_fetch_counts()`. Strategies register themselves in `_REGISTRY` and are looked up by name from `Experiment.assignment_strategy`. Ship strategy: `balanced_random` — pick the least-used condition (random tiebreak), then the least-historically-used stimulus in that condition.
- **Participant flow is a paged state machine.** [survey/flow.py](survey/flow.py) holds `STEP_URL_NAMES`, `required_step_url()`, plus two PsyToolkit helpers: `paginate_questions(questions)` groups an ordered question sequence into `list[list[Question]]` pages (the first question always starts a new page; subsequent questions start a new page iff `page_break_before=True`); `progress_percent(session, ...)` returns 0..100 across the full survey (consent + instructions + all stimulus pages + all demographic pages + thanks). `ParticipantSession` carries three cursors — `current_assignment_index`, `current_page_index`, `demographic_page_index` — advanced by each page POST in [survey/views.py](survey/views.py) `_save_page_answers()` / `demographics()`. Every view calls `_expect_step(session, step)` and redirects out-of-order navigation. Non-active experiments at `/s/<slug>/` render `survey/unavailable.html`, never 404. Question order is randomized once per participant (`random.Random(str(session.id))`) and cached on the in-memory session instance for refresh stability.
- **Completion is the last POST.** The last page of the demographics section (or of the stimuli section, if there are no demographic questions) calls `_finish_session()` which stamps `submitted_at`, flips `last_step = DONE`, and clears the session cookie. There is no separate "review & submit" step.
- **CSV exports exclude abandoned sessions.** Both [experiments/csv_exports.py](experiments/csv_exports.py) helpers filter on `session__submitted_at__isnull=False`. The long-format answers CSV additionally filters `stimulus__isnull=False` so demographic rows don't leak in.
- **Minimal JS.** The only hand-written JS module is [survey/static/survey/js/audio-tracker.js](survey/static/survey/js/audio-tracker.js), which accumulates playback time on `timeupdate` (guarded against seek deltas) and reports via `navigator.sendBeacon` with a `fetch` fallback. It no-ops when there is no `#stimulus-audio` element on the page, so image/text stimuli don't need a separate code path. Everything else is server-rendered; charts are matplotlib-SVG, and the rating widget is a native `<input type="range">` + `<output>`.

### Data model summary

```
Experiment ─── Condition ─── Stimulus {kind: audio|image|text,
    │                            audio?, image?, text_body?}
    │
    └── Question (section: stimulus|demographic,
    │             type: rating|choice|text,
    │             page_break_before: bool)
    │
    └── ParticipantSession ─── StimulusAssignment (listen_duration_ms)
           │  (current_assignment_index, current_page_index,
           │   demographic_page_index)
           │
           └── Response (stimulus nullable; JSON-encoded answer)
```

`ParticipantSession.last_step` drives the top-level flow state machine (consent → instructions → stimuli → demographics → done). Within STIMULI and DEMOGRAPHICS the three integer cursors on the session drive the per-page PsyToolkit pagination. `Response.stimulus` is nullable because demographic answers aren't tied to a clip.

## Configuration

Settings are env-driven via `django-environ` ([core/settings.py](core/settings.py)). A committed `.env.example` lists every variable; `.env` is gitignored. Notable env vars:

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL` — standard Django.
- `GEOIP_PATH` — path to a MaxMind GeoLite2-Country `.mmdb`. Optional: if the file is missing, `survey/metadata.py` returns an empty `country_code` without raising.
- `STIMULUS_MAX_UPLOAD_BYTES`, `STIMULUS_ALLOWED_EXTENSIONS`, `STIMULUS_ALLOWED_MIME_TYPES` — audio upload validation.
- `STIMULUS_MAX_IMAGE_UPLOAD_BYTES`, `STIMULUS_ALLOWED_IMAGE_EXTENSIONS` — image upload validation (kind=image).

Database is SQLite by default (README pins this); swap only if asked.

## Testing conventions

- Factories live in [experiments/tests/factories.py](experiments/tests/factories.py) — `ExperimentFactory`, `ConditionFactory`, `StimulusFactory` (kind=audio, ships a minimal fake MP3 blob), `ImageStimulusFactory` (kind=image, synthesises a PNG via Pillow), `TextStimulusFactory` (kind=text), `RatingQuestionFactory`, `ChoiceQuestionFactory`, `TextQuestionFactory`.
- Factories produce **draft** experiments; flip `state = Experiment.State.ACTIVE` and `save(update_fields=["state"])` before hitting participant views that require `require_active=True`.
- End-to-end test at [tests/test_end_to_end.py](tests/test_end_to_end.py) runs one participant through the full flow and verifies the admin details page + CSV see the data. Use this as the template for any new cross-app integration test.
- Admin-embedded stats, CSV exports, chart endpoint, and the purge management command are covered in [tests/test_admin_stats.py](tests/test_admin_stats.py). The fixtures in that file are also a ready-made way to populate an experiment with a handful of completed + abandoned sessions if you need one.
- Browser-driven admin CRUD tests live in [tests/test_admin_selenium.py](tests/test_admin_selenium.py). They spin up a headless Firefox (fallback: Chromium) via `pytest-django`'s `live_server` fixture. Snap-confined browsers can only read files under `$HOME` and can't see dotdirs, so the `browser_tmpdir` fixture writes stimulus uploads into `~/webeval-selenium-tmp/` rather than `/tmp`. Override the browser binary with `FIREFOX_BIN` / `CHROME_BIN` env vars; the whole module auto-skips if no driver starts.
- The selenium helpers (`_login`, `_click_submit`) scope their XPath to the form carrying the expected `name` attribute (e.g. `//form[.//input[@name='username']]`) rather than picking the first `button[type="submit"]` on the page. This is deliberate: Unfold's themed pages carry extra forms (theme toggle, object-action dropdowns) whose submit controls would otherwise shadow the intended target. Also note the `?next=/admin/` passed to the login URL — Unfold's login template doesn't inject the hidden `next` field, so without it Django bounces post-login to `LOGIN_REDIRECT_URL` (`/accounts/profile/`).
