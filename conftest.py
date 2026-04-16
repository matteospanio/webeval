"""Shared pytest fixtures and pytest-django configuration.

App-specific factories live in each app's ``tests/factories.py``; this file
only holds cross-cutting fixtures.
"""
from __future__ import annotations

import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_media_root(settings, tmp_path_factory):
    """Route all stimulus uploads made during a test into a fresh temp dir.

    Without this, every test that saves a Stimulus writes into ``./media/``
    and pollutes the working tree, since pytest-django only rolls back the
    database, not the filesystem.
    """
    media = tmp_path_factory.mktemp("media")
    settings.MEDIA_ROOT = str(media)
    yield
