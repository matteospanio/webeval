"""Browser-driven end-to-end tests for the Django admin.

These use Selenium + Firefox (headless) to exercise the real admin UI the
researcher will use to set up a study: create an experiment, add a
condition, upload a stimulus, add a question, flip the experiment to
``active``, and verify the structural-edit lock kicks in afterwards.

Selenium tests are marked with the ``selenium`` pytest marker so you can
skip them with ``-m "not selenium"`` on slow machines. They also skip
gracefully at session start if a Firefox webdriver isn't available,
rather than failing with a driver traceback.

Requires:
  * Firefox installed (``/usr/bin/firefox`` or on ``$PATH``)
  * ``geckodriver`` on ``$PATH`` (Selenium 4.6+ can also auto-download it)
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

pytestmark = [pytest.mark.selenium, pytest.mark.django_db]


# --- browser fixture ------------------------------------------------------


_FIREFOX_BINARY_CANDIDATES = (
    os.environ.get("FIREFOX_BIN"),
    "/snap/firefox/current/usr/lib/firefox/firefox",
    "/usr/lib/firefox/firefox",
    "/usr/bin/firefox",
)

_CHROME_BINARY_CANDIDATES = (
    os.environ.get("CHROME_BIN"),
    "/snap/chromium/current/usr/lib/chromium-browser/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)


def _first_existing(paths) -> str | None:
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


@pytest.fixture(scope="session")
def browser():
    """Headless browser fixture. Tries Firefox first, then Chromium.

    Skips the whole test if neither can be started. The
    ``FIREFOX_BIN`` / ``CHROME_BIN`` env vars override the
    autodetected binary paths if your system installs them elsewhere.
    """
    pytest.importorskip("selenium")
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException

    driver = _try_firefox() or _try_chromium()
    if driver is None:
        pytest.skip(
            "No working headless browser found. Install Firefox or Chromium "
            "(or set FIREFOX_BIN / CHROME_BIN)."
        )

    driver.set_window_size(1400, 1000)
    driver.implicitly_wait(3)
    try:
        yield driver
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _try_firefox():
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.firefox.options import Options

    binary = _first_existing(_FIREFOX_BINARY_CANDIDATES)
    if binary is None:
        return None

    options = Options()
    options.add_argument("--headless")
    options.binary_location = binary
    try:
        return webdriver.Firefox(options=options)
    except (WebDriverException, Exception):
        return None


def _try_chromium():
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.options import Options

    binary = _first_existing(_CHROME_BINARY_CANDIDATES)
    if binary is None:
        return None

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.binary_location = binary
    try:
        return webdriver.Chrome(options=options)
    except (WebDriverException, Exception):
        return None


# --- helpers --------------------------------------------------------------


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser("admin", "a@e.org", "pw")


@pytest.fixture
def browser_tmpdir():
    """Temp directory readable by snap-confined browsers.

    Snap-packaged Firefox/Chromium can only see files under ``$HOME`` (not
    ``/tmp``), so ``tmp_path`` is useless for ``<input type="file">`` tests.
    We allocate a per-test directory under ``$HOME/.webeval-selenium-tmp/``
    and clean it up afterwards.
    """
    # Must be a non-hidden path inside $HOME: snap-confined Firefox can only
    # read files under the user's home and its `home` interface excludes
    # dotfiles/dotdirs by default, so ~/.cache or ~/.tmp won't work.
    root = Path.home() / "webeval-selenium-tmp"
    root.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="t", dir=root))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _click_submit(form_element) -> None:
    """Click the primary submit control of a form.

    Unfold renders ``<button type="submit">`` where stock admin used
    ``<input type="submit">``; we accept either. The scoped selector is
    important because Unfold's page chrome often carries extra forms
    (theme switcher, language picker, object-action dropdowns) whose
    submit controls would otherwise shadow the intended target.
    """
    form_element.find_element(
        "css selector",
        'button[type="submit"], input[type="submit"]',
    ).click()


def _login(browser, live_server, username: str, password: str) -> None:
    # Explicit ``?next=/admin/`` because Unfold's overridden login template
    # doesn't inject a hidden ``next`` field, so without it Django would
    # bounce post-login to ``LOGIN_REDIRECT_URL`` (``/accounts/profile/``).
    browser.get(live_server.url + reverse("admin:login") + "?next=/admin/")
    browser.find_element("name", "username").send_keys(username)
    browser.find_element("name", "password").send_keys(password)
    # Scope the submit to the form that actually contains the login fields,
    # not any other form on the themed page.
    login_form = browser.find_element(
        "xpath", "//form[.//input[@name='username']]"
    )
    _click_submit(login_form)


def _submit_add_form(browser) -> None:
    """Click 'Save' on a Django admin add/change form."""
    browser.find_element("name", "_save").click()


def _go(browser, live_server, path: str) -> None:
    browser.get(live_server.url + path)


# --- tests ----------------------------------------------------------------


def test_login_and_create_experiment(browser, live_server, admin_user):
    """Staff can log in and create a draft experiment via the admin UI."""
    _login(browser, live_server, "admin", "pw")
    assert "/admin" in browser.current_url

    # The /admin/ summary section injected by core.context_processors
    # should render as soon as the user lands.
    admin_html = browser.page_source.lower()
    assert "at a glance" in admin_html
    # Curated sidebar sections — no second "All applications" block.
    assert "experiments" in admin_html
    assert "participants" in admin_html

    _go(browser, live_server, "/admin/experiments/experiment/add/")
    browser.find_element("name", "name").send_keys("Selenium Study")
    # prepopulated_fields normally fills slug from name via JS, but we set
    # it explicitly so the test doesn't depend on admin JS timing.
    slug = browser.find_element("name", "slug")
    slug.clear()
    slug.send_keys("selenium-study")
    browser.find_element("name", "description").send_keys(
        "Created through the admin in a headless browser."
    )
    browser.find_element("name", "consent_text").send_keys(
        "I agree to participate."
    )
    _submit_add_form(browser)

    # Back on the changelist — success banner should mention the slug.
    assert "was added successfully" in browser.page_source.lower()

    from experiments.models import Experiment

    exp = Experiment.objects.get(slug="selenium-study")
    assert exp.state == Experiment.State.DRAFT


def test_full_crud_flow(browser, live_server, admin_user, browser_tmpdir):
    """Create experiment → condition → stimulus → question → activate."""
    from experiments.models import Condition, Experiment, Question, Stimulus

    _login(browser, live_server, "admin", "pw")

    # 1. Experiment.
    _go(browser, live_server, "/admin/experiments/experiment/add/")
    browser.find_element("name", "name").send_keys("CRUD Study")
    slug_el = browser.find_element("name", "slug")
    slug_el.clear()
    slug_el.send_keys("crud-study")
    browser.find_element("name", "consent_text").send_keys("Consent body")
    _submit_add_form(browser)
    exp = Experiment.objects.get(slug="crud-study")

    # 2. Condition.
    _go(browser, live_server, "/admin/experiments/condition/add/")
    _select_option(browser, "experiment", str(exp.pk))
    browser.find_element("name", "name").send_keys("GPT-v1")
    _submit_add_form(browser)
    cond = Condition.objects.get(experiment=exp, name="GPT-v1")

    # 3. Stimulus (with a real temp file for upload).
    audio_path = browser_tmpdir / "clip.mp3"
    audio_path.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 2048)
    _go(browser, live_server, "/admin/experiments/stimulus/add/")
    _select_option(browser, "condition", str(cond.pk))
    browser.find_element("name", "title").send_keys("clip-a")
    browser.find_element("name", "audio").send_keys(str(audio_path))
    _submit_add_form(browser)
    stim = Stimulus.objects.get(condition=cond, title="clip-a")
    assert stim.sha256  # auto-computed on save

    # 4. Question (stimulus-section rating) — uses QuestionAdminForm's
    # per-type helper fields, not a raw JSON config textarea.
    _go(browser, live_server, "/admin/experiments/question/add/")
    _select_option(browser, "experiment", str(exp.pk))
    _select_option(browser, "section", "stimulus")
    _select_option(browser, "type", "rating")
    browser.find_element("name", "prompt").send_keys("How much do you like this?")
    browser.find_element("name", "rating_min").send_keys("0")
    browser.find_element("name", "rating_max").send_keys("100")
    browser.find_element("name", "rating_step").send_keys("1")
    _submit_add_form(browser)
    q = Question.objects.get(experiment=exp, prompt="How much do you like this?")
    assert q.type == Question.Type.RATING
    assert q.config == {"min": 0, "max": 100, "step": 1}

    # 5. Activate the experiment via the admin change form.
    _go(browser, live_server, f"/admin/experiments/experiment/{exp.pk}/change/")
    _select_option(browser, "state", "active")
    _submit_add_form(browser)

    exp.refresh_from_db()
    assert exp.state == Experiment.State.ACTIVE


def test_structural_edit_blocked_once_active(
    browser, live_server, admin_user
):
    """Adding a Condition to an active experiment surfaces a validation error."""
    from experiments.models import Experiment
    from experiments.tests.factories import ExperimentFactory

    exp = ExperimentFactory(slug="locked-study", name="Locked study")
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    _login(browser, live_server, "admin", "pw")
    _go(browser, live_server, "/admin/experiments/condition/add/")
    _select_option(browser, "experiment", str(exp.pk))
    browser.find_element("name", "name").send_keys("Late condition")
    _submit_add_form(browser)

    # We should still be on the add form with an error banner, not redirected.
    assert "/add/" in browser.current_url
    page = browser.page_source.lower()
    assert "draft state" in page or "errornote" in page


def test_delete_experiment_cascades(browser, live_server, admin_user):
    """Deleting an experiment through the admin removes it and its children."""
    from experiments.models import Condition, Experiment
    from experiments.tests.factories import (
        ConditionFactory,
        ExperimentFactory,
        StimulusFactory,
    )

    exp = ExperimentFactory(slug="doomed-study", name="Doomed study")
    cond = ConditionFactory(experiment=exp, name="doomed-c")
    StimulusFactory(condition=cond, title="doomed-s")

    _login(browser, live_server, "admin", "pw")
    _go(browser, live_server, f"/admin/experiments/experiment/{exp.pk}/delete/")
    # Click the red "Yes, I'm sure" confirmation button. Scope to the form
    # that carries the hidden ``post=yes`` marker so we don't accidentally
    # hit an admin-actions dropdown or theme-toggle button.
    confirm_form = browser.find_element(
        "xpath", "//form[.//input[@name='post'][@value='yes']]"
    )
    _click_submit(confirm_form)

    assert not Experiment.objects.filter(pk=exp.pk).exists()
    assert not Condition.objects.filter(experiment=exp).exists()


# --- low-level helpers ----------------------------------------------------


def _select_option(browser, name: str, value: str) -> None:
    """Pick an <option value=...> from a <select name=...>."""
    from selenium.webdriver.support.ui import Select

    Select(browser.find_element("name", name)).select_by_value(value)
