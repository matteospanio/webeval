# Writing plugins & extending webeval

webeval is built to be extended **without forking the core**. One decorator — `@plugin` — registers your extension with the app; today three plugin *kinds* exist, all served by the same unified surface in [experiments/plugins.py](../experiments/plugins.py):

| Kind | Base class | What it adds |
|---|---|---|
| `question` | `QuestionComponent` | A brand-new question widget (rendered, validated, parsed end-to-end). |
| `strategy` | `StrategyBase` | How stimuli are selected/ordered per participant. |
| `pairwise_strategy` | `PairwiseStrategyBase` | How A/B comparison pairs are built. |

Plus an **integration** point: outbound webhooks and the REST API (see below).

Everything a plugin author needs comes from one import:

```python
from experiments.plugins import plugin, QuestionComponent, StrategyBase
```

## Where plugin code lives

Drop a **`webeval_plugins.py`** module in any installed Django app — it is auto-imported at startup (`experiments/apps.py` calls `autodiscover_modules("webeval_plugins")` in `ready()`), so registrations are picked up with **no core edits**. One module may register plugins of every kind.

- Quick in-tree addition: `<yourapp>/webeval_plugins.py`.
- Reusable extension: make a tiny Django app, put your plugins in its `webeval_plugins.py`, and add the app to `INSTALLED_APPS`.

List what's installed at any time:

```console
$ uv run ./manage.py plugins
KIND               KEY               LABEL                          IMPL                                          ORIGIN
question           constant_sum      Constant sum (allocate points) experiments.components.ConstantSumComponent  built-in
strategy           balanced_random   ...                            experiments.assignment.BalancedRandomStrategy built-in
...
```

`@plugin` fails **loudly at import time** (a `PluginError`) on a bad registration: a missing or over-long key, a key that shadows a built-in, or a collision with a different already-registered class (pass `replace=True` to `register()` to overwrite deliberately). Re-importing the same class is a harmless no-op.

---

## 1. A custom question type

A *component* bundles everything a question type needs: a config schema, a server-side renderer, and an answer parser. Subclass `QuestionComponent` and decorate it.

```python
# yourapp/webeval_plugins.py
from django.core.exceptions import ValidationError
from django.utils.html import format_html_join

from experiments.plugins import plugin, QuestionComponent


@plugin                                  # registers an instance at import time
class StarRatingComponent(QuestionComponent):
    type = "star_rating"                 # stored in Question.type (<= 16 chars, unique)
    label = "Star rating"                # shown in the admin + builder palette

    def default_config(self) -> dict:
        # Seeds the config when the component is dropped into the builder.
        return {"max": 5}

    def validate_config(self, config: dict) -> None:
        n = config.get("max", 5)
        if not isinstance(n, int) or not 2 <= n <= 10:
            raise ValidationError({"config": "star_rating 'max' must be 2–10."})

    def render(self, question, *, post=None) -> str:
        # Return the INNER widget HTML (the shared <fieldset>/legend wraps it).
        # `post` is request.POST on a validation-error re-render, so you can
        # repopulate; None on first display. ALWAYS return a safe string.
        current = (post or {}).get(f"q_{question.pk}", "")
        n = (question.config or {}).get("max", 5)
        return format_html_join(
            " ",
            '<label><input type="radio" name="q_{}" value="{}" {}> {}★</label>',
            (
                (question.pk, value, "checked" if str(value) == current else "", value)
                for value in range(1, n + 1)
            ),
        )

    def read_answer(self, post, question):
        # Return (answered, json_value, error) — the same contract as built-ins.
        raw = post.get(f"q_{question.pk}", "")
        if not raw:
            return False, None, None            # not answered
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return True, None, "must be a whole number"
        n = (question.config or {}).get("max", 5)
        if not 1 <= value <= n:
            return True, None, "is out of range"
        return True, value, None                # answered, stored as JSON int
```

That's it — the new type now works **end to end** with no other changes:

- it appears in the **admin** question-type dropdown (with a raw-JSON `plugin_config` field), in the **question bank**, and in the **studio drag-&-drop builder** palette;
- it **renders** inside the participant flow and **repopulates** on validation errors;
- answers are validated and stored like any built-in;
- they flow into **stats, exports, and the API** automatically (a numeric component is summarised like a rating; others get a response count).

### The interface

| Member | Required | Purpose |
|---|---|---|
| `type` | ✅ | Unique key stored in `Question.type` (≤ 16 chars; can't shadow a built-in). |
| `label` | ✅ | Human label for the admin / builder. |
| `render(question, *, post=None) -> str` | ✅ | Inner widget HTML; **must** be safe (use `format_html`). |
| `read_answer(post, question) -> (answered, value, error)` | ✅ | Parse from `request.POST`; `value` must be JSON-serialisable. |
| `validate_config(config) -> None` | optional | Raise `ValidationError` on a bad config dict. |
| `default_config() -> dict` | optional | Starter config for the builder. |

webeval ships a worked example, `ConstantSumComponent` (`constant_sum`: distribute N points across items), in [experiments/components.py](../experiments/components.py).

### Removing a plugin later

If studies were authored with your type and the plugin app is removed, those questions can no longer render. webeval fails safe: activation is blocked (`readiness_problems`), a Django system check warns on `manage.py check --database default` / `migrate` (`experiments.W001`), and the participant page shows a visible "question type not installed" notice instead of a silent gap. Re-install the plugin or migrate the affected questions.

---

## 2. A custom assignment strategy

A *strategy* decides which stimuli a participant sees and in what order. It only queries stimuli/conditions — it never touches participant models — so it stays pure and testable.

```python
# yourapp/webeval_plugins.py
import random

from experiments.plugins import plugin, StrategyBase


@plugin
class FirstNStrategy(StrategyBase):
    name = "first_n"      # selected via Experiment.assignment_strategy

    def select(self, experiment, n, counts, rng=None, participant_index=None):
        from experiments.models import Stimulus
        rng = rng or random.Random()
        pool = list(
            Stimulus.objects.filter(condition__experiment=experiment, is_active=True)
            .order_by("sort_order", "id")
        )
        return pool[: n or len(pool)]
```

The strategy then appears in the admin's strategy dropdown and is used whenever a study's `assignment_strategy` is set to `first_n`. Activation is blocked when a study names a strategy that is not registered on the server (and the standard/pairwise registries are checked against the study's mode). Built-ins to learn from: `balanced_random`, `block_random`, `counterbalanced`, `between_subject`; for `pairwise_strategy` subclass `PairwiseStrategyBase` (see `pairwise_balanced`).

### Registering without the decorator

For instances that need constructor arguments:

```python
from experiments.plugins import register

register(FirstNStrategy())                       # kind inferred from the base class
register(FirstNStrategy(), kind="strategy")      # or explicit
register(other, replace=True)                    # deliberate overwrite
```

---

## 3. Consuming webhooks

A study can POST an event (e.g. `session.completed`) to a URL you own, for downstream pipelines. Each delivery is **HMAC-signed** so you can verify authenticity. Configure them in the studio (a study → **Webhooks**); a minimal Flask receiver:

```python
import hashlib, hmac
from flask import Flask, request, abort

app = Flask(__name__)
SECRET = b"<the per-webhook secret shown in the studio>"

@app.post("/webeval")
def receive():
    body = request.get_data()
    sent = request.headers.get("X-Webhook-Signature", "")
    expected = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sent, expected):
        abort(401)
    event = request.get_json()      # {"event", "experiment", "session_id", ...}
    # ... enqueue downstream processing ...
    return "", 204
```

To **pull** data instead of receiving pushes, use the REST API with a scoped API key:

```bash
curl -H "Authorization: Token $WEBEVAL_API_TOKEN" \
     https://eval.example.org/api/v1/experiments/<slug>/results/
```

See the API key + scope table in the [README](../README.md#api-keys).

---

## Testing your plugin

Four tiers, smallest first — the bundled tests are good templates:

1. **Pure unit tests** — `read_answer` / `validate_config` / `select` are ordinary Python (`tests/test_components.py`, `experiments/tests/test_assignment.py`).
2. **DB-backed strategy tests** — `@pytest.mark.django_db` plus the factories in `experiments/tests/factories.py`.
3. **Registration tests** — use the `temporary_plugin` context manager so the registry is restored afterwards:

   ```python
   from experiments.plugins import temporary_plugin, get_plugin

   def test_my_strategy_registers():
       with temporary_plugin(FirstNStrategy()):
           assert get_plugin("strategy", "first_n")
   ```

4. **End-to-end flow test** — one participant submits an answer through your widget (`tests/test_component_flow.py` is the template).

---

## Appendix: legacy registration paths (supported forever)

The per-kind APIs predate `@plugin` and keep working:

- `@question_component` / `register_question_component` in `experiments/components.py`, auto-discovered from a `question_components` module in any installed app.
- `register_strategy` / `register_pairwise_strategy` in `experiments/assignment.py`, called from e.g. `AppConfig.ready`.

Two behavioural notes when migrating to `@plugin`:

- The unified path is **stricter**: it rejects duplicate keys registered by different classes (the legacy strategy helpers silently overwrite). If you relied on overwriting, pass `replace=True`.
- Keep exactly one registration per key: an app that keeps its old `question_components` registration *and* re-registers the same key from a different class in `webeval_plugins` now fails loudly at startup — delete the old one when you migrate.
- A dual-role class (subclassing two bases) must be registered twice with explicit `@plugin(kind=...)` calls.
