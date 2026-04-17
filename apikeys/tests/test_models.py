from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apikeys.models import APIKey, hash_key

pytestmark = pytest.mark.django_db


def _user(username="u") -> User:
    return User.objects.create_user(username, f"{username}@e.org", "pw", is_staff=True)


def test_generate_returns_raw_and_hashes_match():
    user = _user()
    key, raw = APIKey.generate(user=user, name="k", scopes=["stimuli:upload"])
    assert raw.startswith("webeval_")
    assert len(raw) > 40
    assert key.hashed_key == hash_key(raw)
    # prefix is the 8 random chars after "webeval_"
    assert key.prefix == raw[len("webeval_") : len("webeval_") + 8]


def test_generate_prefix_is_first_eight_random_chars():
    user = _user()
    key, raw = APIKey.generate(user=user, name="k", scopes=[])
    assert key.prefix == raw[len("webeval_") : len("webeval_") + 8]


def test_is_active_reflects_revocation():
    user = _user()
    key, _ = APIKey.generate(user=user, name="k", scopes=[])
    assert key.is_active()
    key.revoked_at = timezone.now()
    assert not key.is_active()
    assert key.status == "revoked"


def test_is_active_reflects_expiry():
    user = _user()
    key, _ = APIKey.generate(
        user=user, name="k", scopes=[],
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert not key.is_active()
    assert key.status == "expired"


def test_two_keys_have_distinct_hashes():
    user = _user()
    _, raw_a = APIKey.generate(user=user, name="a", scopes=[])
    _, raw_b = APIKey.generate(user=user, name="b", scopes=[])
    assert raw_a != raw_b
    assert hash_key(raw_a) != hash_key(raw_b)
