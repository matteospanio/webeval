"""Registry of scopes an API key may be granted.

Each staff API endpoint requires exactly one scope from this dict. Adding a
new scoped endpoint is a two-line change: append one entry here, and set
``permission_classes = [HasScope("<new-scope>")]`` on the view.
"""
from __future__ import annotations

SCOPES: dict[str, str] = {
    "stimuli:upload": "Upload stimuli to draft experiments",
    "pairwise-answers:read": "Export pairwise comparison answers",
}


def is_valid_scope(scope: str) -> bool:
    return scope in SCOPES
