"""Generate the screenshots referenced by docs/usage.md.

What it does:
  1. points Django at a throwaway temporary SQLite database (your real data is
     never touched), runs migrations, and seeds a small demo study with a few
     completed sessions so the results pages have something to show;
  2. serves the app with `manage.py runserver` (DEBUG=True, so local static
     files are served);
  3. drives a headless browser via Selenium to log in and screenshot each page
     into docs/screenshots/.

Run it from the repo root, in an environment that has a browser (all CSS/JS
is vendored — no network needed beyond localhost):

    uv run python docs/capture_screenshots.py

Browser selection: Chromium/Chrome is tried first, then Firefox. Override the
binary with the CHROME_BIN or FIREFOX_BIN environment variables. The script is
deterministic and safe to re-run.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHOTS = REPO / "docs" / "screenshots"
HOST, PORT = "127.0.0.1", "8077"
BASE = f"http://{HOST}:{PORT}"
USERNAME, PASSWORD = "demo", "demo-pass-12345"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PAGES = [
    ("01-studio-dashboard.png", "/studio/"),
    ("02-question-builder.png", "/studio/demo-evaluation/build/"),
    ("03-study-overview.png", "/studio/demo-evaluation/"),
    ("04-participant-survey.png", "/s/demo-evaluation/"),
    ("05-results-analysis.png", "/studio/demo-evaluation/"),
    ("06-stimuli-authoring.png", "/studio/demo-evaluation/stimuli/"),
]


def _seed():
    """Create the demo researcher + an active study with a little data."""
    import django

    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.utils import timezone

    call_command("migrate", "--noinput", verbosity=0)

    User = get_user_model()
    from accounts.services import grant_owner_membership
    from experiments.models import Condition, Experiment, Question, Stimulus
    from survey.models import ParticipantSession, Response

    user = User.objects.create_user(USERNAME, "demo@example.org", PASSWORD)
    exp = Experiment.objects.create(
        name="Demo evaluation",
        slug="demo-evaluation",
        owner=user,
        consent_text="Thanks for taking part in this short evaluation.",
        description="Comparing two model variants.",
    )
    grant_owner_membership(exp, user, actor=user)
    for cond_name in ("Model A", "Model B"):
        cond = Condition.objects.create(experiment=exp, name=cond_name)
        Stimulus.objects.create(
            condition=cond, kind=Stimulus.Kind.TEXT,
            title=f"{cond_name} sample", text_body=f"An example output from {cond_name}.",
        )
    rating = Question.objects.create(
        experiment=exp, section=Question.Section.STIMULUS, type=Question.Type.RATING,
        prompt="How good is this output?", sort_order=0,
        config={"min": 0, "max": 100, "step": 1},
    )
    Question.objects.create(
        experiment=exp, section=Question.Section.STIMULUS, type=Question.Type.CHOICE,
        prompt="Is it factually correct?", sort_order=1,
        config={"choices": ["Yes", "No", "Unsure"], "multi": False},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    stimuli = list(Stimulus.objects.filter(condition__experiment=exp))
    import random

    rng = random.Random(1)
    for i in range(18):
        s = ParticipantSession.objects.create(
            experiment=exp, last_step=ParticipantSession.Step.DONE,
            consented_at=timezone.now(), submitted_at=timezone.now(),
            device_type=rng.choice(["desktop", "mobile"]), country_code="US",
        )
        for stim in stimuli:
            base = 75 if stim.condition.name == "Model A" else 55
            Response.objects.create(
                session=s, stimulus=stim, question=rating,
                answer_value=str(max(0, min(100, int(rng.gauss(base, 12))))),
            )


def _make_driver():
    from selenium import webdriver

    chrome_bin = os.environ.get("CHROME_BIN")
    try:
        opts = webdriver.ChromeOptions()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--window-size=1280,1600")
        if chrome_bin:
            opts.binary_location = chrome_bin
        return webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001 - fall back to Firefox
        print(f"Chromium unavailable ({exc}); trying Firefox…")
        opts = webdriver.FirefoxOptions()
        opts.add_argument("--headless")
        if os.environ.get("FIREFOX_BIN"):
            opts.binary_location = os.environ["FIREFOX_BIN"]
        return webdriver.Firefox(options=opts)


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    db_fd, db_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(db_fd)
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "core.settings",
           "DATABASE_URL": f"sqlite:///{db_path}", "DEBUG": "True"}
    os.environ.update(env)

    _seed()

    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"{HOST}:{PORT}", "--noreload"],
        cwd=REPO, env=env,
    )
    try:
        time.sleep(4)  # let the server boot
        driver = _make_driver()
        try:
            # Log in.
            driver.get(f"{BASE}/accounts/login/?next=/studio/")
            driver.find_element("name", "username").send_keys(USERNAME)
            driver.find_element("name", "password").send_keys(PASSWORD)
            driver.find_element("css selector", "button[type=submit]").click()
            time.sleep(1)
            for filename, path in PAGES:
                driver.get(f"{BASE}{path}")
                time.sleep(1.5)
                driver.save_screenshot(str(SHOTS / filename))
                print(f"captured {filename}")
        finally:
            driver.quit()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        os.unlink(db_path)
    print(f"Done — screenshots in {SHOTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
