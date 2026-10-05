import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    d = tempfile.mkdtemp(prefix="pw-test-")
    monkeypatch.setenv("DATA_DIR", d)
    monkeypatch.setenv("OWNER_TOKEN", "test-owner-token-0123456789abcdef")
    monkeypatch.setenv("RETRY_BASE_S", "0")
    monkeypatch.setenv("MODELS_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "models"))
    from app import config
    config.reset_settings_cache()
    from app import db
    db.init()
    yield d
    config.reset_settings_cache()
