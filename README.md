# PANEL

![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Django 5.2](https://img.shields.io/badge/django-5.2-0C4B33?logo=django&logoColor=white)
![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)
![Modes: standard + pairwise](https://img.shields.io/badge/modes-standard%20%2B%20pairwise-6C5CE7)

**PANEL is a self-hosted framework for human evaluation of AI systems.**

Run rigorous, privacy-respecting studies where people rate or compare the outputs of LLMs, generative audio/image/video models, text-to-speech, RAG pipelines, agents — or any other AI system — in single-stimulus or pairwise designs, then analyse the results online and pull them out as CSV, JSON, or webhooks.

It is **AI-evaluation-first**, but the survey engine underneath is general: the same building blocks design studies with **non-AI targets** too (media, products, UX research, psychophysics, A/B copy). If you can show it to a person and ask a question about it, you can evaluate it here.

Because you **host it yourself**, participant data never leaves infrastructure you control — a privacy- and compliance-friendly alternative to shipping your evaluation data to a third-party SaaS. PANEL ships the features you need to run studies responsibly (consent versioning, data-subject requests, retention, audit trails) — see [GDPR & privacy](#gdpr--privacy).

📚 **[Documentation](docs/README.md)** · [Install](docs/installation.md) · [Deploy on a server](docs/deployment.md) · [Using the app](docs/usage.md) · [Writing plugins](docs/plugins.md) · [GDPR & privacy](docs/gdpr.md)

## Features

- Multi-user platform: per-study ownership with owner/editor/viewer roles, collaboration by invitation, and a researcher dashboard at `/studio/` (separate from the Django admin)
- Standard single-stimulus studies and pairwise comparison studies
- Audio, video, image, text, HTML-snippet, and embedded-URL stimuli in one experiment model
- Rating, multiple-choice, free-text, Likert, numeric, matrix/grid, and ranking questions
- PsyToolkit-style pagination with author-controlled page breaks
- Conditional display / skip logic: show a question only when earlier answers match
- Save & continue later: resume an in-progress session from a private link, on any device
- Pre-task screening / eligibility flows that screen out ineligible participants before the main task
- Attention checks and automatic quality flags (failed check, speeder, straight-lining, duplicate) with one-click "exclude flagged" exports
- Stable per-browser participant IDs, with optional one-submission-per-participant enforcement and duplicate-session detection
- Completion codes (fixed or unique) for crowdsourcing platforms, external-id capture (e.g. `PROLIFIC_PID`), and compensation tracking with a reconciliation CSV
- Participant-visible withdrawal & data deletion via a private link (erases answers, leaves an anonymized tombstone, drops out of results)
- Bot protection (consent honeypot), private studies (shared access code or single-use invite links), and optional participant codes for a stable cross-device identity
- Longitudinal / multi-phase studies: chain studies into ordered phases a participant returns to over time, with an optional minimum gap between phases and a return link + reusable participant code shown on completion
- Authoring productivity: one-click study duplication (deep copy incl. media + skip logic), activation-readiness checks that block going live on an incomplete study, a preview/pilot phase whose data is kept out of the real dataset, per-study branding (accent color, logo, custom CSS), and a reusable question bank
- Plugin question types: add a custom question widget (config validation + server render + answer parsing) in one self-contained class via a decorator/registry — no core changes, auto-discovered at startup; ships a constant-sum (allocate points) example
- Drag-&-drop question builder in the studio: drag question types (built-ins and plugins) from a palette onto a canvas, reorder by dragging, edit inline, and save — no Django admin needed, available to non-staff editors
- Online results & analysis: per-question summaries for every question type (choice/Likert distributions, rating/numeric stats, matrix breakdowns, ranking mean-ranks) viewed in the studio, ready-to-use across-condition tests (one-way ANOVA / chi-square with p-values, no scipy required), and segmentation by device, country, condition, or cohort
- Page/question response times, a cross-experiment comparison view, power & sample-size analysis (including from pilot data), and an append-only raw participant-flow event log (viewable in the admin, exportable as CSV)
- Compliance & governance: study metadata (IRB #, legal basis, data contact, retention window), consent-version tracking tied to each session, an append-only audit trail of edits/exports/destructive actions, automated retention sweeps, PII redaction of free-text in exports, and a data-subject-request workflow (export or erase a participant's data across your studies)
- Production-ready: a Docker image + Compose stack (app + Postgres + Redis), env-driven Postgres / S3 object storage / WhiteNoise static serving, a `/healthz` health check, REST-API rate limiting, hardened-security toggles, and backup/retention commands
- Integrations: a scoped REST API to pull aggregate results and submitted answers as JSON, outbound HMAC-signed webhooks fired on participant completion for downstream pipelines, and per-study operator email notifications
- Pluggable assignment strategies: balanced-random, block randomization, counterbalanced ordering, and between-subject (each participant sees one condition)
- Optional audio playback check before the study begins
- Direct per-experiment participant links with no public study index
- Admin-native analytics, SVG charts, and CSV exports
- Reproducibility exports as printable HTML, JSON, and ZIP archives
- Experiment archive import for cloning or sharing studies across instances
- Lightweight participant metadata capture: device type, browser family, and country code

## What you can evaluate

- **AI model outputs** — compare LLM completions, generative audio/image/video, TTS voices, or RAG/agent responses; rate quality dimensions or pick the better of two.
- **Human-subject research** — perception/psychophysics studies, media or product preference, UX copy A/B tests, questionnaire research.
- **Anything you can present** — audio, video, images, text, raw HTML, or an embedded URL, asked about with rating, choice, Likert, numeric, matrix, ranking, or your own custom question types.

Studies can be anonymous and single-session, multi-phase/longitudinal with return visits, public or invite-only, and crowdsourcing-platform-ready (completion codes, external IDs).

## GDPR & privacy

PANEL is built to be **self-hosted**, so you remain the sole data controller — participant data never leaves infrastructure you run. The platform is designed around data-protection good practice and ships concrete features that help you meet GDPR (and similar) obligations:

- **Data minimisation by design.** Participants are anonymous by default (no account). Client IP addresses are used only for an *offline* country lookup and are **never stored** — sessions keep just a coarse device type, browser family, and 2-letter country code. ([survey/metadata.py](survey/metadata.py))
- **Lawful basis & consent.** Every study records a consent text and a lawful basis (GDPR Art. 6); each session is stamped with a hash of the exact consent wording it agreed to, so data stays tied to its consent version.
- **Right to erasure (Art. 17).** Participants can withdraw and delete their own data from a private link at any time; researchers can erase a participant's data across studies from the data-subject-request tool.
- **Right of access / portability (Arts. 15 & 20).** Export one participant's full data as JSON, or a study's data as CSV/JSON.
- **Storage limitation (Art. 5(1)(e)).** Per-study retention windows + a scheduled `purge_expired_data` command delete data past its retention period.
- **Records of processing & accountability (Art. 30).** Append-only audit trails log access changes and every edit/export/destructive action (who, what, when, from where).
- **Special-category / free-text care.** Mark free-text questions as containing PII to redact them from exports by default.
- **Security.** Scoped, hashed, auditable API keys; rate limiting; HTTPS/secure-cookie/HSTS toggles; HMAC-signed webhooks.

> **Compliance is a shared responsibility.** PANEL gives you the tooling; whether a given deployment is *compliant* also depends on how you configure and operate it (hosting region, consent wording, retention policy, DPA with any processors, etc.). It is not legal advice. See **[docs/gdpr.md](docs/gdpr.md)** for the full feature-to-article mapping and an operator checklist.

## Quick Start

> New here? The **[installation guide](docs/installation.md)** walks through local setup and the **[deployment guide](docs/deployment.md)** covers putting PANEL on a VPS or other Python host.

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

- `http://127.0.0.1:8000/studio/` for the researcher dashboard (sign in at `/accounts/login/`, or register at `/accounts/register/`)
- `http://127.0.0.1:8000/admin/` for the staff/superuser interface
- `http://127.0.0.1:8000/s/<slug>/` for a participant-facing study

The default setup uses SQLite. Environment variables are documented in `.env.example`.

## How It Works

### Experiment lifecycle

Experiments move through four states:

- `draft` — author freely; structural edits (conditions, stimuli, questions) are allowed only here
- `test` — a preview / pilot phase: walk the real participant flow, but sessions started here are marked as preview and kept out of stats and exports
- `active` — collecting real data
- `closed` — finished

Conditions, stimuli, and questions can only be structurally edited while an experiment is in `draft`. This protects active studies from accidental mid-run changes.

Leaving `draft` for the preview or live phase runs **activation-readiness checks**: an empty or inconsistent study (no conditions, no active stimulus, no per-stimulus question, missing consent text, more stimuli-per-participant than exist, …) is blocked until the gaps are fixed. Promoting a test study to active lets you either discard the preview data or keep it — kept preview sessions are promoted into the real dataset.

### Authoring productivity

- **Duplicate a study** in one click (from the studio or the admin): a faithful deep copy into a new draft you own — every authored field, the raw media of audio/image/video stimuli and pairwise prompts, and skip-logic / eligibility references remapped to the cloned questions.
- **Question bank:** save any question to a personal (or shared) bank, then insert saved questions into any draft study you own.
- **Branding:** give a study an accent color, a header logo, and optional custom CSS applied to its participant pages.
- **Drag-&-drop builder:** author a draft study's questions visually at `/studio/<slug>/build/` — drag a type (built-in or plugin) from the palette onto the canvas, drag cards to reorder, edit each inline, and save. The server reconciles the posted set (create/update/delete/reorder) and validates every question, so plugin configs and skip logic are enforced. Available to non-staff editors; conditions and stimuli are still edited in the admin.

### Participant flow

For standard studies, participants move through:

1. Consent
2. Optional screening / eligibility (ineligible participants are screened out here)
3. Optional audio check
4. Instructions
5. One or more stimulus pages
6. One or more demographic pages
7. Thanks

Questions are grouped into pages with a PsyToolkit-style `page_break_before` flag. Each page posts only the answers visible on that page.

### Study modes

#### Standard mode

Participants evaluate one stimulus at a time. Each session receives either all eligible stimuli or a balanced subset, depending on `stimuli_per_participant`.

#### Pairwise mode

Participants compare two stimuli side by side. Pairings are built across conditions using shared `prompt_group` values, and results can be summarized with win-rate charts and Bradley-Terry analysis.

### Longitudinal / multi-phase studies

A study can be linked to an earlier one with the **Follows** field, turning the two into ordered phases of a longitudinal study. Both phases must collect a participant code — that code is the identity key that ties a participant's visits together across devices and over time.

- A participant can only start a follow-up phase once they have completed the previous phase under the same participant code; otherwise they see an "earlier phase required" page.
- An optional **phase gap** (in hours) holds a later phase shut until enough time has passed since the previous phase was completed, for spaced return visits.
- After finishing a phase, the thanks page advertises the next phase: its name, when it opens, a direct return link, and the participant code to reuse.

Phases form a chain (each study follows at most one predecessor); self-references and cycles are rejected at validation time. The chain is configured from the admin's "Longitudinal (multi-phase)" fieldset and shown read-only on the studio overview.

### Stimulus types

- `audio`: uploaded audio file with validation, SHA-256 checksum, and duration extraction
- `video`: uploaded video file with validation, SHA-256 checksum, duration extraction, and watch-time tracking
- `image`: uploaded image file with validation and SHA-256 checksum
- `text`: inline text body with no uploaded media
- `html`: researcher-authored HTML snippet rendered inline
- `embed`: external URL shown in a sandboxed iframe (e.g. a hosted player or widget)

### Question types

- Rating slider
- Multiple choice
- Free text
- Likert scale
- Numeric input (optional min/max, integer-only, unit label)
- Matrix / grid (several rows sharing one answer scale)
- Ranking / ordering (assign each item a unique rank; no-JS rank selects)

Questions can be marked as required, split onto separate pages, and optionally show the originating stimulus prompt to participants.

A question may also carry **skip logic** (`visible_if`): show it only when earlier answers in the same section match, e.g. `{"question": 12, "op": "eq", "value": "Yes"}` (or `{"all": [...]}` / `{"any": [...]}`). Cross-page branching is fully server-side; same-page dependents are revealed live by a small progressive-enhancement script.

### Results & analysis

The study overview shows a **per-question results** section for every question type — choice/Likert distributions (with bars), rating/numeric summary stats (mean, median, SD, range), matrix per-row breakdowns, ranking mean-ranks, and response counts for free-text/plugin types — computed over real (submitted, non-preview) sessions.

For per-stimulus questions it also runs a **ready-to-use across-condition test**: a one-way ANOVA for rating/numeric/Likert outcomes (a t-test when there are two conditions) or a chi-square test for choice outcomes, each with a p-value and a significance flag. P-values are computed from hand-rolled special functions, so this works with no extra dependencies (scipy is only needed for the optional `analysis` extra). Results can be **segmented** by device, country, condition, or cohort (ISO submission week), and each question shows the **median time** participants spent on its page (captured server-side).

Beyond a single study, the studio offers a **cross-experiment comparison** table (key metrics for every study you own, side by side), a **power & sample-size** calculator that can estimate the observed effect and required n from your **pilot data**, and a **raw event log** (`started` / `page_submit` / `screened_out` / `completed`) viewable in the admin and exportable as CSV.

### Compliance & governance

Each study carries **compliance metadata** — IRB / ethics number, lawful basis for processing, data-protection contact, and a **retention window** — shown on the studio overview and edited in the admin.

- **Consent versioning:** every session records a hash of the exact consent wording it agreed to, so data stays tied to its consent version even after the text is later edited.
- **Audit trail:** an append-only log records edits, exports, and destructive actions (who, what, when, from where), viewable in the admin.
- **Retention:** `uv run ./manage.py purge_expired_data` deletes sessions past each study's retention window (use `--dry-run` first); wire it into cron.
- **PII handling:** mark a free-text question as containing PII and its answers are redacted from CSV exports unless explicitly included (`?include_pii=1`).
- **Data-subject requests:** look up a participant by code or external id across your studies and either export their data as JSON or erase it (for the studies you manage).

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

## Accounts, roles & collaboration

PANEL is multi-user. Each study has an **owner**, and access is scoped by role:

- **Owner** — full control: edit, results/exports, manage collaborators, transfer ownership, lifecycle.
- **Editor** — edit the study (structure, stimulus/prompt uploads) and view results.
- **Viewer** — view results and exports only.

Researchers work from the **studio dashboard** at `/studio/` (outside the Django
admin): they see only studies they own or collaborate on, create new studies,
view live stats/exports, and manage access. Owners invite collaborators by email
from a study's **Access** page; the invitee follows a single-use, expiring link
(`/accounts/invite/<token>/`) and signs in or registers to accept. Every
access-control change is recorded in an append-only audit log.

The same ownership rules are enforced everywhere: the Django admin changelists
and per-study views are scoped to a non-superuser's own/shared studies, and the
REST API checks that a key's user has edit/view access to the target study.
Superusers retain full visibility. Studies created before this model existed
(no owner) remain accessible to staff users until an owner is assigned, so
upgrading is non-breaking.

Platform admins manage users, groups and the access/audit changelists from the
Django admin under **Users & access**.

## Extending PANEL: plugins

> Full guide with worked examples (custom question types, assignment strategies, consuming webhooks): **[docs/plugins.md](docs/plugins.md)**.

New question widgets, assignment strategies, and pairwise strategies are **plugins** — you add them without touching the core. One decorator covers every kind: drop a `panel_plugins.py` module in any installed app, subclass the matching base class, and decorate it with `@plugin` (the kind is inferred from the base class). `uv run ./manage.py plugins` lists everything installed.

A question *component* bundles the four things a question type needs (config validation, participant-facing rendering, answer parsing, and a label):

```python
from django.utils.html import format_html
from experiments.plugins import plugin, QuestionComponent


@plugin
class YesNoComponent(QuestionComponent):
    type = "yes_no"            # the stored Question.type value (≤16 chars)
    label = "Yes / No"

    def validate_config(self, config):
        ...                    # raise ValidationError on a bad config dict

    def render(self, question, *, post=None):
        checked = (post or {}).get(f"q_{question.pk}", "")
        return format_html(
            '<label><input type="radio" name="q_{0}" value="yes" {1}> Yes</label>'
            '<label><input type="radio" name="q_{0}" value="no" {2}> No</label>',
            question.pk,
            "checked" if checked == "yes" else "",
            "checked" if checked == "no" else "",
        )

    def read_answer(self, post, question):
        raw = post.get(f"q_{question.pk}", "")
        if not raw:
            return False, None, None          # (answered, value, error)
        if raw not in ("yes", "no"):
            return True, None, "has an invalid value"
        return True, raw, None
```

`ExperimentsConfig.ready()` auto-discovers the module at startup. The new type then works **end-to-end** with no other changes: it appears in the admin question-type dropdown (with a raw-JSON `plugin_config` field), in the question bank, and in the studio drag-&-drop builder palette; renders inside the standard survey page; validates and stores answers like any built-in type; and flows into stats/exports. Built-in types are untouched, and a bad registration fails loudly at startup with a clear `PluginError`. PANEL ships one worked example — `constant_sum` (allocate a fixed number of points across items) in [experiments/components.py](experiments/components.py). (The older `question_components.py` + `@question_component` / `register_strategy` paths keep working — see the appendix in [docs/plugins.md](docs/plugins.md).)

## Project Layout

- `accounts/`: identity + access — profiles, per-study memberships/roles, invitations, the permission helper, the access audit log, and auth/invite views
- `studio/`: the researcher-facing dashboard (study list, creation, results, and access management) outside the Django admin
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
- `STIMULUS_MAX_VIDEO_UPLOAD_BYTES`, `STIMULUS_ALLOWED_VIDEO_EXTENSIONS`

If `GEOIP_PATH` is unset or the database is missing, participant country lookup is skipped without breaking the app.

## API keys

The staff REST API (`/api/v1/…`) is authenticated with per-user, scoped,
auditable API keys. Staff users manage their own keys at **Admin →
sidebar → Account → API keys** (`/admin/api-keys/`):

- **Create** a key with a name, one or more scopes, and an optional
  expiry. The raw key is shown exactly once — copy it then.
- **Rotate** creates a new key with the same name + scopes and revokes
  the old one immediately.
- **Revoke** invalidates a key; subsequent requests fail with 401.
- **Events** per key: creation, rotation, revocation, every successful
  use (with method, path, and response status), and every failed
  authentication attempt (with reason: `unknown`, `revoked`, `expired`,
  `user_not_staff`, …).

Available scopes are declared in [`apikeys/scopes.py`](apikeys/scopes.py):

| Scope                     | Grants                                     |
|---------------------------|--------------------------------------------|
| `stimuli:upload`          | `POST /api/v1/experiments/<slug>/stimuli/` |
| `prompts:upload`          | `POST /api/v1/experiments/<slug>/prompts/` |
| `pairwise-answers:read`   | `GET  /api/v1/experiments/<slug>/pairwise-answers/` |
| `answers:read`            | `GET  /api/v1/experiments/<slug>/answers/` (submitted per-stimulus answers as JSON; PII redacted unless `?include_pii=1`) |
| `results:read`            | `GET  /api/v1/experiments/<slug>/results/` (aggregate per-question results as JSON) |

Every endpoint is object-scoped: a key only reaches experiments its user can access. The API is rate-limited (see the deploy section).

### Webhooks

Each study can register outbound **webhooks** (studio → a study → *Webhooks*, or the admin) that fire on participant events (e.g. `session.completed`) by POSTing a JSON payload to a URL you control — for downstream pipelines, Slack/Zapier relays, etc. Deliveries are signed:

```
X-Webhook-Signature: sha256=HMAC_SHA256(secret, raw_body)
```

Verify the signature with the per-webhook secret shown in the studio. Delivery is best-effort and synchronous; per-hook status is recorded for debugging. A study can also set an **operator email** to be notified on each completion.

Superusers also see a cross-user overview at `/admin/api-keys/?scope=all`
and can revoke any key. The full event log is additionally browsable via
the standard admin changelists for `APIKey` and `APIKey event`.

The wire format is unchanged — clients keep sending
`Authorization: Token <key>`. Scripts read the key from
`PANEL_API_TOKEN`:

```
export PANEL_API_TOKEN=panel_…
uv run python scripts/batch_upload_stimuli.py --slug my-study ./clips/
```

**Upgrading from `rest_framework.authtoken`**: this release drops the old
single-token-per-user system. After deploying, any existing
`PANEL_API_TOKEN` will 401 — log into the admin, create a new key with
the scopes you need, and update your `.env`. The orphaned
`authtoken_token` SQLite table can be left in place or dropped at your
leisure.

## Production deployment

PANEL ships a production Docker image and a `docker-compose.yml` (app + Postgres + Redis). The fastest path:

```bash
cp .env.example .env          # set SECRET_KEY, ALLOWED_HOSTS, SECURE_DEPLOY=True, …
docker compose up --build     # builds the image, runs migrations, serves on :8000
docker compose run --rm web python manage.py createsuperuser
```

All production behaviour is env-driven and off by default: `DATABASE_URL` (Postgres), `USE_S3` (object storage), `USE_WHITENOISE` (static), `SECURE_DEPLOY` (HTTPS/HSTS/secure cookies), `REDIS_URL` (shared rate-limit cache). A `GET /healthz` endpoint reports DB liveness for your load balancer.

👉 **Full guide** — Docker *and* a manual gunicorn + Postgres + nginx VPS setup with HTTPS, backups, retention, and a production checklist: **[docs/deployment.md](docs/deployment.md)**.

## Documentation

Full guides live in [`docs/`](docs/README.md):

- [Installation](docs/installation.md) — run locally (uv or pip)
- [Deployment](docs/deployment.md) — VPS / Docker, with HTTPS, backups, and a checklist
- [Using the app](docs/usage.md) — studio + participant walkthrough, with screenshots
- [Writing plugins & extending](docs/plugins.md) — custom question types, strategies, webhooks
- [GDPR & privacy](docs/gdpr.md) — feature-to-article mapping + operator checklist

Contributor-facing architecture notes are in [CLAUDE.md](CLAUDE.md).

## Contributing

Issues and pull requests are welcome.

If you contribute code, keep changes aligned with the existing architecture and add or update tests for behavior changes, especially around participant flow, assignment logic, exports, and admin views.

## License

This project is released under the MIT License. See `LICENSE`.
