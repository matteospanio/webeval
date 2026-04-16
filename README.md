# webeval

A web app to collect human evaluations of LLM outputs.

This repository contains the code for a Django web app that runs **PsyToolkit-style** evaluation surveys: a researcher configures an experiment in the admin, and participants walk through a single-column, per-page questionnaire answering questions about each stimulus. Stimuli come in three flavours — **audio clips**, **images**, and **text-only prompts** — so a single study can mix kinds (e.g. "rate this generated MIDI clip" and "rate this generated lyric") without a parallel model hierarchy.

The survey consists of a section with the stimuli and per-stimulus questions, followed by a section with demographic questions about the participants. The survey is in English.

The app is anonymous: no login, and participants can submit multiple sessions. Surveys are shared via **direct per-experiment links** (`/s/<slug>/`); there is no public index of active studies. A non-active experiment at that URL renders a friendly "not currently accepting responses" page instead of leaking a 404.

Staff use Django admin (themed with django-unfold) to manage the experiment lifecycle, upload stimuli, and edit questions. The `/admin/` landing page shows summary cards (total experiments by state, total sessions started and completed, total answers collected); per-experiment statistics, CSV exports, and the mean-rating SVG chart all live under `/admin/experiments/experiment/<slug>/…`, reachable from the Experiment change view's **Statistics** fieldset and the changelist "Shortcuts" column.

At the beginning of the survey, participants are asked to give their consent to the processing of their data. The collected data is anonymous and will be used for research purposes only. Any publication of the results will publish only aggregated data, without any personal information. The collected data will be stored securely and will be deleted after the end of the research project.

## How it works

### Per-survey direct link

Each experiment has a unique URL at `/s/<slug>/`. That is the *only* entry point for participants — there is no landing page listing every active study, so a researcher can share one study without leaking the others. Draft and closed experiments at the same URL show a polite "this survey is not currently accepting responses" message rather than a 404.

### PsyToolkit-style paged flow

Inside the survey the participant walks through a fixed sequence of pages:

1. Consent page (tick a checkbox, then Next).
2. Instructions page (Next).
3. One or more **stimulus pages** per assigned stimulus — the stimulus media stays visible on every page that hosts a question about it, so the participant can re-listen / re-view while answering.
4. One or more **demographic pages**.
5. A thanks page.

The author controls how questions are grouped into pages by checking **"Start new page before this question"** (`page_break_before`) on a Question. A question with that flag starts its own page; a question without it joins the previous page. This is the same mechanism PsyToolkit uses: you can put one item on a page, or pack ten items onto a page, without a global "questions-per-page" constant.

Every Next button POSTs only the current page's answers. If the participant abandons the survey their partial responses are discarded silently; the `/admin/` summary counts the session as "started but not completed." There is no separate "review and submit" step — the last Next on the last demographic page finishes the session.

A thin PsyToolkit-style progress bar sits below the header and fills from 0% to 100% across all the pages the participant will walk (consent, instructions, every stimulus page, every demographic page, thanks). No step-counter text, no "question X of N"; just a visual cue.

### Stimulus kinds

`Stimulus.kind` is one of:

* **audio** — an uploaded audio file (`audio/*`), validated for extension and max size, with SHA-256 and duration auto-computed on save.
* **image** — an uploaded image file (`png`, `jpg`, `jpeg`, `webp`, `gif`), validated for extension and max size, with SHA-256 auto-computed on save.
* **text** — a `text_body` the participant reads inline (rendered with `|linebreaks`). No file upload.

The kind discriminator decides which media block the `play.html` template renders (audio tag, figure+img, or blockquote); everything else (assignment strategy, question rendering, per-page cursor) is kind-agnostic.

### Admin summary dashboard

When staff open `/admin/` they see a card grid summarising the project state:

* Total experiments, broken down by draft / active / closed.
* Total sessions started by participants.
* Completed sessions (and the completion rate).
* Total answers collected from completed sessions.

Each Experiment change view carries a **Statistics** fieldset with live per-experiment counts and links into a dedicated, admin-mounted **Details** view (`/admin/experiments/experiment/<slug>/details/`) that shows the same stats as Unfold-themed cards, the per-stimulus mean-rating table, the embedded SVG chart, and download links for the long-format answers CSV, the demographics CSV, and the JSON/printable reproducibility bundles. Everything staff-facing lives inside `/admin/` — there is no separate dashboard app.

## Details

- Musical stimuli will last 20 seconds
- users cannot skip listening
- we track duration of listening and timestamps of question responses
- stimuli sampling logic will be configurable (each user sees N random stimuli from pool)
- Balanced assignment across conditions (e.g. generation methods)
- The database will be django's default (SQLite)
- experiment logic layer will include (Experiment, Condition, Stimulus assignment strategy)
- Add response metadata:
    - device type
    - browser
    - approx location
- Log drop-off rates, completion time and any possible metrics that make sense

## Functional requirements

### Participant-facing survey

- Users can:
    - Access the survey via public URL (no auth required).
    - Read and accept a conset form before proceeding
    - Listen to audio stimuli (music players).
    - Answer:
        - Quality evaluation questions (using a scale from 0 to 100), e.g. "Is the music free of inharmonious notes, unnatural
rhythms, and awkward phrasing?", "Is the sample musically / harmonically interesting?", "Subjectively, how much do you like the generation?"
    - Complete demographic questionnaire (age, gender, musical background, etc.)
    - Submit responses
- System must:
    - Allow multiple submussions per user
    - Randomize:
        - Order of stimuli for each user
        - Order of questions
    - Validate required fields and input formats
    - Store responses atomically in a database

### Stimuli management

- Admin users can:
    - Upload new audio stimuli (with metadata like title, description, generation method)
    - View a list of existing stimuli and their metadata
    - Edit or delete stimuli
    - Group stimuli into survey/experiments
    - Activate/deactivate stimuli

### Admin panel

- Password-protected access with django's built-in auth system
- Features:
    - CRUD operations for:
        - Surveys
        - Stimuli
        - Questions (that could have different types: rating scales, multiple choice, free text)
    - View responses:
        - Tabular format
        - Filter/search
    - Export responses as CSV
    - Print/export the survey questions and stimuli for documentation purposes
    - export the survey questions and stimuli in a machine-readable format (e.g. JSON) for reproducibility and sharing with other researchers
    - Dashboard
        - Visualize aggregated statistics (e.g. average ratings per stimulus, demographic breakdowns)
        - Charts and graphs for insights

### Data collection

- Store:
    - resoibses to all survey questions
    - timestamps
    - session identifier
- Ensure
    - No personally identifiable information is required
    - Data is stored securely

### Consent & ethics

- Before survey:
    - Show informed consent page
    - Require explicit agreement (checkbox)
- Store consent flag+timestamp
- Provide link to privacy policy and contact info for questions/withdrawal requests

## Non-functional requirements

### Performance
- Handle 20 concurrent users
- Audio streaming should be smooth with minimal buffering

### Security
- Admin panel protected via authentication
- Protect against common web vulnerabilities (CSRF, XSS, SQL injection)
- Secure storage of collected data (HTTPS required)

### Data protection
- GDPR compliance:
    - Right to withdraw
    - Data minimization
    - retention policy (delete data after project end)
- Data deletion mechanism after project end

### Reliability

- Backup data regularly

### Usability

- Mobile-friendly design
- Simple, low-friction UX
- Accessible audio controls and survey interface
