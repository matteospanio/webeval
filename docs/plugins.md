# Writing plugins & extending webeval

webeval is built to be extended **without forking the core**. Two registries let you drop in new behaviour, both modelled on the same Pythonic pattern (a base class + a registry + a decorator), and both auto-discovered at startup:

1. **Question-type components** — brand-new question widgets ([experiments/components.py](../experiments/components.py)).
2. **Assignment strategies** — how stimuli are selected/ordered per participant ([experiments/assignment.py](../experiments/assignment.py)).

Plus an **integration** point: outbound webhooks your own services consume.

## Where plugin code lives

Anything imported at startup works, but the convention is:

- For a quick in-tree addition, drop it in an existing app's `question_components.py` (it is auto-imported — see below).
- For something reusable, make it a tiny Django app and add it to `INSTALLED_APPS`; put your components in `<yourapp>/question_components.py`.

`experiments/apps.py` calls `autodiscover_modules("question_components")` in `ready()`, so **any installed app** that defines a `question_components` module has its registrations picked up automatically — no core edits.

---

## 1. A custom question type

A *component* bundles everything a question type needs: a config schema, a server-side renderer, and an answer parser. Subclass `QuestionComponent` and register it.

```python
# yourapp/question_components.py
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from experiments.components import QuestionComponent, question_component


@question_component                      # registers an instance at import time
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
        buttons = format_html("")
        for value in range(1, n + 1):
            buttons += format_html(
                '<label><input type="radio" name="q_{}" value="{}" {}> {}★</label> ',
                question.pk, value, "checked" if str(value) == current else "", value,
            )
        return buttons

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

- it appears in the **admin** question-type dropdown (with a raw-JSON `plugin_config` field) and in the **studio drag-&-drop builder** palette;
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

### Registering without the decorator

```python
from experiments.components import register_question_component
register_question_component(StarRatingComponent())
```

---

## 2. A custom assignment strategy

A *strategy* decides which stimuli a participant sees and in what order. It only queries stimuli/conditions — it never touches participant models — so it stays pure and testable.

```python
# yourapp/apps.py  (call this from AppConfig.ready), or any module imported at startup
import random
from experiments.assignment import StrategyBase, register_strategy


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


register_strategy(FirstNStrategy())
```

The strategy then appears in the admin's strategy dropdown and is used whenever a study's `assignment_strategy` is set to `first_n`. Built-ins to learn from: `balanced_random`, `block_random`, `counterbalanced`, `between_subject`.

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

Treat components/strategies as ordinary Python — unit-test `read_answer` / `validate_config` / `select` directly, and write one participant-flow test that submits an answer. The bundled tests are good templates: `tests/test_components.py`, `tests/test_component_flow.py`, and `experiments/tests/` for strategies.
