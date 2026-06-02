# webeval

![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Django 5.2](https://img.shields.io/badge/django-5.2-0C4B33?logo=django&logoColor=white)
![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)
![Modes: standard + pairwise](https://img.shields.io/badge/modes-standard%20%2B%20pairwise-6C5CE7)

webeval is a Django app for running anonymous online evaluation studies with audio, image, and text stimuli.

It was originally built for LLM-output evaluation, but the current architecture is broader than that: researchers can configure single-stimulus or pairwise-comparison studies, collect structured participant responses, and review results from the Django admin without building a separate dashboard.

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
- Online results & analysis: per-question summaries for every question type (choice/Likert distributions, rating/numeric stats, matrix breakdowns, ranking mean-ranks) viewed in the studio, ready-to-use across-condition tests (one-way ANOVA / chi-square with p-values, no scipy required), and segmentation by device or country
- Page/question response times, a cross-experiment comparison view, power & sample-size analysis (including from pilot data), and an append-only raw participant-flow event log (viewable in the admin, exportable as CSV)
- Pluggable assignment strategies: balanced-random, block randomization, counterbalanced ordering, and between-subject (each participant sees one condition)
- Optional audio playback check before the study begins
- Direct per-experiment participant links with no public study index
- Admin-native analytics, SVG charts, and CSV exports
- Reproducibility exports as printable HTML, JSON, and ZIP archives
- Experiment archive import for cloning or sharing studies across instances
- Lightweight participant metadata capture: device type, browser family, and country code

## Current Scope

webeval is currently best suited to anonymous, single-session studies where participants rate or compare media items in a guided flow.

Today the product is intentionally narrower than a full survey platform. It does not yet provide participant accounts or longitudinal scheduling.

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

For per-stimulus questions it also runs a **ready-to-use across-condition test**: a one-way ANOVA for rating/numeric/Likert outcomes (a t-test when there are two conditions) or a chi-square test for choice outcomes, each with a p-value and a significance flag. P-values are computed from hand-rolled special functions, so this works with no extra dependencies (scipy is only needed for the optional `analysis` extra). Results can be **segmented** by device or country, and each question shows the **median time** participants spent on its page (captured server-side).

Beyond a single study, the studio offers a **cross-experiment comparison** table (key metrics for every study you own, side by side), a **power & sample-size** calculator that can estimate the observed effect and required n from your **pilot data**, and a **raw event log** (`started` / `page_submit` / `screened_out` / `completed`) viewable in the admin and exportable as CSV.

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

webeval is multi-user. Each study has an **owner**, and access is scoped by role:

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

## Extending webeval: custom question types (plugins)

New question widgets are **plugins** — you can add one without touching the core. A *component* bundles the four things a question type needs (config validation, participant-facing rendering, answer parsing, and a label), mirroring the existing pluggable assignment strategies.

Drop a `question_components.py` module in any installed app and register a component:

```python
from django.utils.html import format_html
from experiments.components import QuestionComponent, question_component


@question_component
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

`ExperimentsConfig.ready()` auto-discovers the module at startup. The new type then works **end-to-end** with no other changes: it appears in the admin question-type dropdown (with a raw-JSON `plugin_config` field), renders inside the standard survey page, validates and stores answers like any built-in type, and flows into stats/exports. Built-in types are untouched. webeval ships one worked example — `constant_sum` (allocate a fixed number of points across items) in [experiments/components.py](experiments/components.py).

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
| `pairwise-answers:read`   | `GET  /api/v1/experiments/<slug>/pairwise-answers/` |

Superusers also see a cross-user overview at `/admin/api-keys/?scope=all`
and can revoke any key. The full event log is additionally browsable via
the standard admin changelists for `APIKey` and `APIKey event`.

The wire format is unchanged — clients keep sending
`Authorization: Token <key>`. Scripts read the key from
`WEBEVAL_API_TOKEN`:

```
export WEBEVAL_API_TOKEN=webeval_…
uv run python scripts/batch_upload_stimuli.py --slug my-study ./clips/
```

**Upgrading from `rest_framework.authtoken`**: this release drops the old
single-token-per-user system. After deploying, any existing
`WEBEVAL_API_TOKEN` will 401 — log into the admin, create a new key with
the scopes you need, and update your `.env`. The orphaned
`authtoken_token` SQLite table can be left in place or dropped at your
leisure.

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
