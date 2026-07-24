# GDPR & privacy

PANEL is designed to make running **privacy-respecting** human-evaluation studies straightforward, and to give a self-hosting operator the tools needed to meet GDPR (and comparable regimes such as UK GDPR) obligations.

> **This is not legal advice, and PANEL is not "GDPR-certified".** Compliance is a property of *a deployment and how it is operated*, not of software alone. PANEL provides the technical building blocks; you are responsible for lawful configuration (hosting region, consent wording, retention policy, processor agreements, responding to requests, etc.). Use the [checklist](#operator-checklist) below.

## Why self-hosting matters

You host PANEL yourself, so **you are the sole data controller**. Participant data is stored in your database and your media storage — it is never transmitted to PANEL's authors or any third-party SaaS. That removes an entire class of cross-border-transfer and sub-processor questions that hosted survey platforms raise.

## What participant data is collected

PANEL is **data-minimal by default**. For an anonymous study, a `ParticipantSession` stores:

- the answers the participant gives;
- a coarse **device type** (`desktop`/`mobile`/`tablet`) and **browser family** parsed from the User-Agent;
- an optional 2-letter **country code**.

Crucially, the client **IP address is never stored** — it is used only, in-memory, for an *offline* MaxMind country lookup and then discarded ([survey/metadata.py](../survey/metadata.py)). There is no precise geolocation and no stored raw User-Agent.

Optional identifiers appear only if you enable them: a long-lived `panel_pid` cookie (for duplicate detection / return visits), a participant-entered code (longitudinal studies), or an external platform id such as `PROLIFIC_PID` you pass in the link.

> Researcher/operator security logs (access changes, API-key usage, the audit trail) **do** record the IP and User-Agent of *staff* actions — that's deliberate, for accountability of the people running studies, and is separate from participant data.

## Feature → GDPR mapping

| GDPR principle / right | How PANEL supports it |
|---|---|
| **Lawful basis** (Art. 6) | Per-study `legal_basis` field (consent, legitimate interest, …) recorded as study metadata. |
| **Consent** (Arts. 6–7) | A consent step gates every study; each session is stamped with `consent_version` (a hash of the exact consent text), so data stays tied to the wording agreed to even after the text is edited. |
| **Transparency** (Arts. 13–14) | Consent text + privacy contact / policy URL shown to participants before they take part. |
| **Data minimisation** (Art. 5(1)(c)) | Anonymous by default; no IP stored; coarse metadata only; mark free-text questions as PII. |
| **Storage limitation** (Art. 5(1)(e)) | Per-study `retention_days` + the `purge_expired_data` command delete data past its window. |
| **Right of access** (Art. 15) | Data-subject-request tool finds a participant across your studies and exports their data as JSON; CSV/JSON study exports. |
| **Right to erasure** (Art. 17) | Participant self-service withdrawal (private link) **and** operator erasure via the data-subject-request tool, both leaving an anonymised tombstone. |
| **Right to data portability** (Art. 20) | JSON/CSV exports of a participant's or a study's data. |
| **Records of processing & accountability** (Art. 30, Art. 5(2)) | Append-only audit trail of edits/exports/destructive actions, plus an access-control change log. |
| **Security of processing** (Art. 32) | Self-hosting; HTTPS/HSTS/secure-cookie toggles; scoped, hashed, auditable API keys; rate limiting; HMAC-signed webhooks. |
| **Special-category data** (Art. 9) | PII flag redacts free-text from exports by default; you decide whether to collect such data and on what basis. |

## How to exercise each right (operator)

- **Access / portability:** Studio → **Data-subject request** → enter the participant's code or external id → **Export** (JSON).
- **Erasure:** same screen → **Erase** (acts on studies you manage; reuses the withdrawal anonymisation).
- **Participant self-erasure:** the "Withdraw & delete my data" link shown on every in-progress page and on the thanks page.
- **Retention:** set `retention_days` per study; schedule `python manage.py purge_expired_data` (see [Deployment](deployment.md#data-retention)).
- **Audit:** Django admin → *Audit events* / *Access events*.

## Operator checklist

- [ ] Host in a region appropriate for your participants; document it.
- [ ] Set a clear **consent text** and a **lawful basis** on each study; include a privacy contact / policy URL.
- [ ] Collect the minimum necessary; mark any free-text that may contain personal data as **PII**.
- [ ] Set a **retention window** per study and schedule `purge_expired_data`.
- [ ] Put a **Data Processing Agreement** in place with any processor you do use (e.g. an object-storage or email provider).
- [ ] Have a process to handle access/erasure requests promptly (the DSR tool helps).
- [ ] Run behind **HTTPS** with `SECURE_DEPLOY=True`; restrict admin/studio access; keep API keys scoped and rotated.
- [ ] Back up data **and** be able to delete it (retention + DSR).
- [ ] If you process special-category data or do large-scale monitoring, assess whether a **DPIA** is required.

## Cookies

Participant-facing pages use a first-party session cookie (functional) and, when enabled, the long-lived `panel_pid` identification cookie. Disclose these in your consent/privacy text. The studio/admin use standard Django session + CSRF cookies for authenticated researchers.
