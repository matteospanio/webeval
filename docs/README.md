# PANEL documentation

PANEL is a **self-hosted framework for human evaluation of AI systems** (and any other targets you can show a person). These guides take you from zero to a running, production-grade evaluation platform.

## Guides

| Guide | What it covers |
|---|---|
| [Installation](installation.md) | Run PANEL locally on your machine (uv or pip), in minutes. |
| [Deployment](deployment.md) | Put PANEL on a VPS or any Python host — Docker Compose, or a manual gunicorn + Postgres + nginx setup, with HTTPS. |
| [Using the app](usage.md) | A walkthrough of the researcher studio and the participant flow, with screenshots. |
| [Writing plugins & extending](plugins.md) | Add custom question types, assignment strategies, and more — with worked examples. |
| [GDPR & privacy](gdpr.md) | The data-protection features mapped to GDPR, plus an operator checklist. |

## What PANEL is

- **AI-evaluation-first.** Designed for rating and pairwise comparison of model outputs — LLMs, generative audio/image/video, TTS, RAG, agents.
- **General-purpose underneath.** The same engine runs human-subject research, media/product preference, UX A/B tests, and psychophysics.
- **Self-hosted.** You are the only data controller; nothing is sent to a third-party SaaS.
- **Multi-user.** Researchers own studies, invite collaborators by role, and work from a dashboard (the *studio*) — no need to touch the Django admin for day-to-day work.

## Concept map

```
Experiment ── Condition ── Stimulus        (what participants see: audio/image/text/video/html/embed)
    │
    ├── Question        (rating / choice / text / likert / numeric / matrix / ranking / your plugin)
    │
    └── ParticipantSession ── Response      (who answered, and what)
```

A study moves through **draft → test → active → closed**. You build it in draft (in the studio's drag-&-drop builder), rehearse it in *test* (preview data is kept out of results), then activate it to collect real data. See [Using the app](usage.md).

## Project layout

| Path | Role |
|---|---|
| `experiments/` | Domain models, admin, analysis/stats, charts, CSV/exports, assignment strategies, question-component plugins, webhooks. |
| `survey/` | Participant flow (state machine), response capture, metadata, flagging. |
| `studio/` | Researcher dashboard (study list, builder, results, access, exports) — outside the Django admin. |
| `accounts/` | Identity, per-study roles, invitations, permission helper, access + audit logs. |
| `apikeys/` | Scoped, hashed, audited API keys for the REST API. |
| `core/` | Settings, URL wiring, health check, project-level views. |

For contributor-facing architecture notes, see [CLAUDE.md](../CLAUDE.md) at the repo root.
